"""Bounded provider views over immutable execution evidence.

Compaction changes the working transcript, never operations, receipts or ownership.
The byte estimator starts conservatively and calibrates to Provider usage;
it is not advertised as a model-specific tokenizer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

from app.v1.storage_support import new_id, utcnow
from app.v1.domain import ToolResult
from app.v1.protocols.conversation.conversation_context_contracts import CurrentTaskPlanContractContext
from app.v1.protocols.tools.planning.capability_contracts import TaskPlanContractReceipt
from app.v1.protocols.context.context_result_contracts import (
    AgentResultReducedView, AppliedWorkflowPlanReference, AppliedWorkflowReference,
    ArchivedValueReference,
    LoopApplyPlanningProjection, LoopColumnProjection, LoopInspectPlanningProjection,
    MemoryReadReducedView, PagedTextView, PlanningTruncationProjection, ToolResultView,
    WorkflowColumnProjection, WorkflowEdgeProjection, WorkflowInspectPlanningProjection,
)
from app.v1.protocols.context.context_state_contracts import CompactContextSummary, TaskPlanContextSummary, TaskSummaryWithFailure
from app.v1.protocols.context.context_checkpoint_contracts import (CheckpointCoverage, CheckpointMetrics,
    AssistantMessageFact, ContextCheckpointMessage, ContextCheckpointPayload,
    ContextMaintenanceMessage, ContextRunMetrics, DeterministicContextSummary,
    MinimalToolResultProjection, PendingWorkflowPublication, PublicationArguments,
    PublicationNextAction, PreviousCheckpointFact, ProviderBudgetEnvelope,
    RequiredOverflowMetrics, SummaryToolCallFact, ToolMessageFact, TruncationProjection)


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()


def coverage_ranges(message_ids):
    """Compress only adjacent real IDs; a gap may contain another message."""
    ranges = []
    for identity in sorted(set(message_ids)):
        if ranges and identity == ranges[-1][1] + 1:
            ranges[-1][1] = identity
        else:
            ranges.append([identity, identity])
    return CheckpointCoverage(ranges=ranges, excluded_message_count=len(set(message_ids))).model_dump(mode='json')


def covered_message_ids(payload):
    """None means a legacy or malformed checkpoint: replay raw history."""
    try:
        value = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(value, dict) or value.get('version') != 2 or value.get('mode') != 'exact_message_ids':
            return None
        ranges = value['ranges']
        if not isinstance(ranges, list):
            return None
        covered = set()
        for pair in ranges:
            if (not isinstance(pair, list) or len(pair) != 2 or
                    any(type(identity) is not int or identity <= 0 for identity in pair) or
                    pair[1] < pair[0] or pair[1]-pair[0] > 100000):
                return None
            covered.update(range(pair[0],pair[1]+1))
        if len(covered) != value.get('excluded_message_count'):
            return None
        return covered
    except (ValueError, TypeError, KeyError):
        return None


def provider_messages(messages):
    return [{k: v for k, v in m.items() if not k.startswith('_')} for m in messages]


def context_input_tokens(usage, protocol):
    value = usage.get('input_tokens', usage.get('prompt_tokens'))
    if not isinstance(value, int):
        return None
    if protocol == 'anthropic':
        value += int(usage.get('cached_input_tokens') or usage.get('cache_read_input_tokens') or 0)
        value += int(usage.get('cache_creation_input_tokens') or 0)
    return value


def excerpt(text, limit):
    if len(text) <= limit:
        return text
    head = max(0, limit // 4)
    return text[:head] + '\n[archived; use agent.result.read]\n' + text[-max(1, limit-head):]


def bounded_value(value, limit, depth=0):
    if isinstance(value, str):
        return excerpt(value, limit)
    if depth > 8:
        return ArchivedValueReference(archived_sha256=digest(value)).model_dump(mode='json')
    if isinstance(value, dict):
        return {k: bounded_value(v, limit, depth+1) for k, v in list(value.items())[:48]}
    if isinstance(value, list):
        return [bounded_value(v, limit, depth+1) for v in value[:24]]
    return value


def project_result(content, max_bytes):
    if len(content.encode('utf-8')) <= max_bytes:
        return content
    try:
        original = json.loads(content)
    except (ValueError, TypeError):
        original = ToolResultView(output=content).model_dump(mode='json', exclude_unset=True)
    if not isinstance(original, dict):
        original = ToolResultView(output=original).model_dump(mode='json', exclude_unset=True)
    if original.get('capability') == 'agent.result.read' and isinstance(original.get('output'), dict):
        output = original['output']
        if isinstance(output.get('content'), str) and isinstance(output.get('offset'), int):
            from app.v1.context.context_projection import slice_source_text
            source, offset = output['content'], output['offset']
            total = output.get('total_characters', offset + len(source))
            budget = max_bytes
            while budget >= 4:
                page, _ = slice_source_text(source, offset=0,
                    max_characters=len(source), max_bytes=budget)
                next_offset = offset + len(page)
                view = ToolResultView.from_value(original).with_output(
                    PagedTextView.model_validate(output).page(page, next_offset, total)
                )
                encoded = packed(view)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                budget //= 2
            reduced = AgentResultReducedView.model_validate(output)
            reduced.content = ''
            reduced.next_offset = offset
            return packed(ToolResultView.from_value(original).with_output(
                reduced.model_dump(mode='json', exclude_unset=True)
            ))
    if original.get('capability') == 'project.files.read' and isinstance(original.get('output'), dict):
        output = original['output']
        if isinstance(output.get('content'), str) and isinstance(output.get('offset'), int):
            from app.v1.context.context_projection import slice_source_text
            source, offset = output['content'], output['offset']
            total = output.get('total_characters', offset + len(source))
            budget = max_bytes
            while budget >= 4:
                page, _ = slice_source_text(source, offset=0,
                                            max_characters=len(source), max_bytes=budget)
                next_offset = offset + len(page)
                view = ToolResultView.from_value(original).with_output(
                    PagedTextView.model_validate(output).page(page, next_offset, total)
                )
                encoded = packed(view)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                budget //= 2
    if original.get('capability') == 'project.memory.search' and isinstance(original.get('output'), list):
        view = copy.deepcopy(original)
        while view['output'] and len(packed(view).encode('utf-8')) > max_bytes:
            view['output'].pop()
            for item in view['output']:
                if isinstance(item, dict):
                    item['search_more_matches'] = True
                    item['search_next_offset'] = item.get('search_offset',0)+len(view['output'])
        if len(packed(view).encode('utf-8')) <= max_bytes:
            return packed(view)
        return packed(ToolResultView(
            ok=original.get('ok'), capability='project.memory.search', output=[],
            projection={'truncated':True,
                        'retrieve':'project.memory.search with a narrower query'},
        ).model_dump(mode='json', exclude_unset=True))
    if original.get('capability') == 'project.memory.read' and isinstance(original.get('output'), dict):
        output = original['output']
        if isinstance(output.get('content'), str) and isinstance(output.get('offset'), int):
            from app.v1.context.context_projection import slice_source_text
            source = output['content']
            offset = output['offset']
            budget = min(max_bytes, len(source.encode('utf-8')))
            while budget >= 4:
                page, _ = slice_source_text(source, offset=0, max_characters=len(source), max_bytes=budget)
                next_offset = offset + len(page)
                view = ToolResultView.from_value(original).with_output(
                    PagedTextView.model_validate(output).page(page, next_offset, output['total_characters'])
                )
                encoded = packed(view)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                compact_output = MemoryReadReducedView.from_source(output)
                compact_output.content = page
                compact_output.next_offset = next_offset if next_offset < output['total_characters'] else None
                compact_output.content_complete = next_offset >= output['total_characters']
                compact_output.metadata_omitted = True
                compact = ToolResultView(
                    ok=original.get('ok'), capability=original.get('capability'),
                    output=compact_output.model_dump(mode='json', exclude_unset=True),
                ).model_dump(mode='json', exclude_unset=True)
                encoded = packed(compact)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                budget //= 2
            reduced = MemoryReadReducedView.from_source(output)
            reduced.content = ''
            reduced.next_offset = offset
            reduced.projection_deferred = True
            return packed(ToolResultView.from_value(original).with_output(
                reduced.model_dump(mode='json', exclude_unset=True)
            ))
    reference = bounded_value(original.get('evidence') or {}, 120)
    planning = planning_projection(original)
    if planning is not None:
        planning = ToolResultView.from_value(planning).with_projection(PlanningTruncationProjection(
            source_bytes=len(content.encode('utf8')), sha256=digest(original),
            reference=reference))
        if len(packed(planning).encode('utf8')) <= max_bytes:
            return packed(planning)
    for limit in (2400, 1200, 600, 200, 40):
        view = bounded_value(original, limit)
        view = ToolResultView.from_value(view).with_projection(TruncationProjection(
            source_bytes=len(content.encode('utf-8')),
            sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(), reference=reference))
        encoded = packed(view)
        if len(encoded.encode('utf-8')) <= max_bytes:
            return encoded
    # Retain outcome/identity even for huge lists with many short fields.
    output = original.get('output')
    return MinimalToolResultProjection(
        ok=original.get('ok'), status=original.get('status'), capability=original.get('capability'),
        error=bounded_value(original.get('error'), 160),
        exit_code=output.get('exit_code') if isinstance(output, dict) else None,
        projection=TruncationProjection(
            sha256=digest(original), source_bytes=len(content.encode('utf-8')), reference=reference,
        ),
    ).model_dump_json()


def planning_projection(result):
    """Keep authoring contracts intact instead of truncating their schema strings."""
    capability, output = result.get('capability'), result.get('output')
    if not result.get('ok') or not isinstance(output,dict):
        return None
    if result.get('projection') and capability in {'loop.inspect','loop.apply','workflow.inspect'}:
        return copy.deepcopy(result)
    if capability == 'loop.inspect':
        bundle = output.get('bundle') or {}
        method = bundle.get('workflow_plan') or {}
        workflow = bundle.get('workflow') or {}
        view = LoopInspectPlanningProjection.model_validate(output)
        view.flow_unit = method.get('flow_unit')
        view.task_contract = method.get('task_contract',{})
        view.columns = [LoopColumnProjection(key=c['key'],
            writable_paths=c.get('metadata',{}).get('writable_paths',[]))
            for c in workflow.get('columns',[])]
    elif capability == 'loop.apply':
        workflow = output.get('workflow') or {}
        method = output.get('workflow_plan') or {}
        view = LoopApplyPlanningProjection(
            workflow=AppliedWorkflowReference.model_validate(workflow),
            workflow_plan=AppliedWorkflowPlanReference(
                id=method.get('id'), flow_unit=method.get('plan',{}).get('flow_unit'),
                task_contract=method.get('plan',{}).get('task_contract',{})))
    elif capability == 'workflow.inspect':
        definition = output.get('definition') or {}
        view = WorkflowInspectPlanningProjection.model_validate(output)
        view.column_contracts = [WorkflowColumnProjection(key=c['key'],
            acceptance_checks=c.get('acceptance_checks',[]),
            transitions=[WorkflowEdgeProjection(outcome=edge.get('outcome'),
                target=edge.get('target'), required_checks=edge.get('required_checks',[]))
                for edge in c.get('transitions',[])])
            for c in definition.get('columns',[])]
    else:
        return None
    return ToolResultView.from_value(result).with_output(view.model_dump(mode='json', exclude_unset=True))


class ContextExhausted(RuntimeError):
    error_code = 'LLM_CONTEXT_WINDOW_EXCEEDED'
    error_category = 'context_exhausted'


class ContextManager:
    def __init__(self, store, spec, run_id, model_complete):
        self.store, self.spec, self.run_id = store, spec, run_id
        self.policy = store.policy.context
        resolver = getattr(model_complete, 'context_limits', None)
        self.limits = resolver(spec.kind) if resolver else {
            'window': self.policy.fallback_window_tokens,
            'output': self.policy.fallback_output_tokens, 'protocol': 'test', 'model': 'injected'}
        self.hard = int(self.limits['window'] * (1-self.policy.safety_fraction)) - self.limits['output']
        if self.hard <= 1024:
            raise ContextExhausted('Configured context window has no room after output reservation')
        self.model_complete = model_complete
        # A conservative cold start; Provider usage calibrates subsequent turns.
        self.ratio = self.policy.cold_start_tokens_per_byte
        self.last_bytes = 0
        self.scope = (spec.assignment or {}).get('id') or spec.agent_session_id or run_id
        self.last_metrics = {}
        with self.store.connect() as db:
            usage = db.execute('''SELECT ratio FROM v1_context_usage_calibrations
                WHERE project_id=? AND scope_id=? AND model=? AND protocol=?''',
                (spec.project['id'], self.scope, self.limits['model'], self.limits['protocol'])).fetchone()
            row = db.execute('SELECT metrics_json FROM v1_context_checkpoints WHERE scope_id=? ORDER BY revision DESC LIMIT 1',(self.scope,)).fetchone()
        if usage:
            self.ratio = max(self.policy.observed_tokens_per_byte_floor, float(usage['ratio']))
        elif row:
            metrics = json.loads(row[0])
            if metrics.get('model') == self.limits['model'] and metrics.get('protocol') == self.limits['protocol']:
                self.ratio = max(self.policy.observed_tokens_per_byte_floor,
                                 float(metrics.get('ratio') or self.ratio))

    def estimate(self, messages, tools):
        # Include converted-schema inflation, role framing and adapter overhead.
        size = len(ProviderBudgetEnvelope(messages=provider_messages(messages),
            tools=tools).model_dump_json().encode('utf-8'))
        return math.ceil(size * self.ratio * 1.15) + 512

    def observe(self, response):
        actual = context_input_tokens(response.usage or {}, self.limits['protocol'])
        if actual and self.last_bytes:
            # The old upward-only floor treated large Tool schemas as permanent
            # context pressure even when Provider usage showed ample headroom.
            # Retain a margin and the existing Provider overflow retry path.
            self.ratio = max(self.policy.observed_tokens_per_byte_floor,
                actual / self.last_bytes * self.policy.observed_usage_margin)
            with self.store.tx(immediate=True) as db:
                db.execute('''INSERT INTO v1_context_usage_calibrations
                    (project_id,scope_id,model,protocol,ratio,updated_at) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(project_id,scope_id,model,protocol)
                    DO UPDATE SET ratio=excluded.ratio,updated_at=excluded.updated_at''',
                    (self.spec.project['id'], self.scope, self.limits['model'],
                     self.limits['protocol'], self.ratio, utcnow()))
        self.last_metrics['context_input_tokens'] = actual

    @staticmethod
    def groups(messages):
        """Only completed assistant/tool batches may be archived as native calls."""
        groups, i = [], 0
        while i < len(messages):
            m = messages[i]
            if m.get('role') == 'assistant' and m.get('tool_calls'):
                end = i+1
                while end < len(messages) and messages[end].get('role') == 'tool':
                    end += 1
                expected = {c['id'] for c in m['tool_calls']}
                returned = {t.get('tool_call_id') for t in messages[i+1:end]}
                if expected == returned:
                    groups.append((i, end))
                i = end
            else:
                i += 1
        return groups

    def prepare(self, messages, tools, *, force=False):
        control = self.spec.execution_control
        if control:
            control.check()
        before = self.estimate(messages, tools)
        projected = copy.deepcopy(messages)
        self._refresh_conversation_authority(projected)
        self._refresh_runtime_failures(projected)
        for item in projected:
            if item.get('role') == 'tool':
                item['content'] = project_result(str(item.get('content') or ''),
                    max(512, int(self.policy.tool_result_tokens / (self.ratio*1.15))))
        self._project_memory(projected, tools, force=force)
        self._project_reference_dialogue(projected, tools, force=force)
        self._project_execution_references(projected, tools, force=force)
        estimate = self.estimate(projected, tools)
        compact = force or estimate >= self.hard*self.policy.soft_fraction
        removed = []
        if compact:
            target = self.hard * (0.4 if force else self.policy.target_fraction)
            # Whole groups, including bulky code-writing arguments, are replaced.
            groups = self.groups(projected)
            remove_indices = set()
            for start, end in groups:
                if (start, end) == groups[-1] and removed and self.estimate(
                    [m for j, m in enumerate(projected) if j not in remove_indices], tools) <= target:
                    break  # Keep the most recent complete batch when it fits.
                if self.estimate([m for j, m in enumerate(projected) if j not in remove_indices], tools) <= target and not (force and not removed):
                    break
                removed.extend(projected[start:end])
                remove_indices.update(range(start, end))
            if removed:
                retained = [m for j, m in enumerate(projected) if j not in remove_indices]
                previous = [m for m in retained if m.get('_checkpoint')]
                retained = [m for m in retained if not m.get('_checkpoint')]
                summary = self._summarize(previous + removed)
                summary = self._bounded_summary(summary)
                checkpoint = ContextCheckpointMessage(content=ContextCheckpointPayload(
                    context_checkpoint=summary).model_dump_json()).model_dump(mode='json', by_alias=True)
                projected = retained[:1] + [checkpoint] + retained[1:]
                estimate = self.estimate(projected, tools)
                if estimate > self.hard:
                    raise self._required_overflow(projected, tools)
                self._save(removed, summary, before, estimate)
                messages[:] = projected
        estimate = self.estimate(projected, tools)
        if estimate > self.hard:
            raise self._required_overflow(projected, tools)
        self.last_bytes = len(ProviderBudgetEnvelope(messages=provider_messages(projected),
            tools=tools).model_dump_json().encode('utf-8'))
        self.last_metrics = ContextRunMetrics(estimated_tokens=estimate, before_tokens=before,
            input_budget=self.hard, compacted_messages=len(removed),
            ratio=self.ratio).model_dump(mode='json')
        return provider_messages(projected)

    def _refresh_conversation_authority(self, messages):
        if self.spec.kind != 'conversation' or not self.spec.conversation_job_id:
            return
        job = self.store.get_conversation_job(self.spec.conversation_job_id)
        contract = self.store.intents.contract(job)
        requirement = None
        current = None
        if job.get('requirement_id'):
            requirement = self.store.scopes.requirement_version(
                job['project_id'], job['requirement_id'], job.get('requirement_revision'))
            current = self.store.agents.get_requirement(job['project_id'], job['requirement_id'])
        allowed = bool(job['start_task'] and contract['phase'] == 'execution'
                       and requirement and requirement['status'] == 'active'
                       and current and current['status'] == 'active'
                       and current['revision'] == requirement['revision'])
        for item in reversed(messages):
            if item.get('role') != 'user':
                continue
            try:
                payload = json.loads(item.get('content') or '')
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get('authoritative_current_request'), dict):
                continue
            if payload['authoritative_current_request'].get('message_id') != job['user_message_id']:
                continue
            state = payload.get('authoritative_project_state')
            if not isinstance(state, dict):
                return
            state['turn_contract'] = contract
            state['work_intent'] = self.store.intents.context(job)
            state['requirement'] = requirement
            try:
                workflow = self.store.get_workflow(job['project_id'])
            except KeyError:
                workflow = None
            state['active_workflow'] = ({key:workflow.get(key) for key in
                ('id','revision','name','requirement_id','requirement_revision',
                 'definition_hash','workflow_plan_id')} if workflow else None)
            planning_contract = self._current_task_plan_contract(
                job['project_id'], workflow['id'] if workflow else None)
            if planning_contract is not None:
                state.update(CurrentTaskPlanContractContext(
                    task_plan_contract=planning_contract,
                ).model_dump(mode='json', exclude_none=True))
            else:
                state.pop('task_plan_contract', None)
            state['workflow_plans'] = [{key:plan.get(key) for key in
                ('id','created_at','content_hash')} for plan in
                self.store.list_workflow_plans(job['project_id'], 5)]
            pending_plan = self._pending_workflow_plan(job['project_id'])
            if pending_plan:
                state['pending_workflow_plan'] = pending_plan
            else:
                state.pop('pending_workflow_plan', None)
            state['task_plans'] = [TaskPlanContextSummary.from_plan(plan).model_dump(mode='json')
                for plan in self.store.list_task_plans(job['project_id'], 5)]
            state['tasks'] = [
                TaskSummaryWithFailure.from_task(task, self.store.feedback.failure_detail(
                    job['project_id'], task['id']))
                if task['status'] in {'failed','recovering'} else task
                for task in self.store.task_summaries(
                    job['project_id'], self.policy.task_summary_limit)]
            for key in ('conversation_job', 'turn_authority'):
                if isinstance(state.get(key), dict):
                    state[key]['task_graph_mutation_allowed'] = allowed
            item['content'] = packed(payload)
            return

    def _current_task_plan_contract(self, project_id, workflow_revision_id):
        if not workflow_revision_id:
            return None
        with self.store.connect() as db:
            row = db.execute('''SELECT result_json FROM v1_tool_invocations
                WHERE project_id=? AND agent_run_id=? AND capability='task.plan.contract'
                AND ok=1 ORDER BY sequence DESC LIMIT 1''',
                (project_id, self.run_id)).fetchone()
        if not row:
            return None
        result = ToolResult.model_validate_json(row['result_json'])
        contract = TaskPlanContractReceipt.model_validate(result.output)
        return contract if contract.workflow_revision_id == workflow_revision_id else None

    def _pending_workflow_plan(self, project_id):
        """Keep an uncommitted control-plane step visible after transcript compaction."""
        with self.store.connect() as db:
            saved = db.execute('''SELECT sequence,result_json FROM v1_tool_invocations
                WHERE project_id=? AND agent_run_id=? AND capability='workflow.plan.save' AND ok=1
                ORDER BY sequence DESC LIMIT 1''', (project_id, self.run_id)).fetchone()
            if not saved:
                return None
            plan_id = (json.loads(saved['result_json']).get('output') or {}).get('id')
            if not plan_id:
                return None
            publications = db.execute('''SELECT arguments_json FROM v1_tool_invocations
                WHERE project_id=? AND agent_run_id=? AND capability='workflow.publish' AND ok=1
                AND sequence>?''', (project_id, self.run_id, saved['sequence'])).fetchall()
        if any(json.loads(row['arguments_json']).get('workflow_plan_id') == plan_id
               for row in publications):
            return None
        return PendingWorkflowPublication(id=plan_id,
            next_action=PublicationNextAction(arguments=PublicationArguments(
                workflow_plan_id=plan_id))).model_dump(mode='json')

    def _refresh_runtime_failures(self, messages):
        if not (self.spec.kind == 'column' and self.spec.assignment and
                self.spec.task_id and self.spec.column_run_id):
            return
        failures = self.store.feedback.latest_failed_checks(
            self.spec.task_id, self.spec.column_run_id)
        previous_failure = self.store.feedback.latest_agent_failure(
            self.spec.task_id, self.spec.column_run_id, exclude_agent_run_id=self.run_id)
        column_key = self.store.get_project_task(
            self.spec.project['id'], self.spec.task_id)['current_column']
        feedback = self.store.feedback.pending_for_column(self.spec.task_id, column_key)
        for item in reversed(messages):
            if item.get('role') != 'user':
                continue
            try:
                payload = json.loads(item.get('content') or '')
            except (TypeError, ValueError):
                continue
            assignment = payload.get('current_assignment') if isinstance(payload, dict) else None
            if not isinstance(assignment, dict) or assignment.get('id') != self.spec.assignment['id']:
                continue
            contract_input = assignment.get('input')
            if not isinstance(contract_input, dict):
                return
            if failures:
                contract_input['unresolved_acceptance_failures'] = failures
            else:
                contract_input.pop('unresolved_acceptance_failures', None)
            if previous_failure:
                contract_input['previous_agent_failure'] = previous_failure
            else:
                contract_input.pop('previous_agent_failure', None)
            if feedback:
                contract_input['unresolved_task_feedback'] = feedback
            else:
                contract_input.pop('unresolved_task_feedback', None)
            item['content'] = packed(payload)
            return

    def _project_memory(self, messages, tools, *, force):
        from app.v1.context.context_projection import slice_source_text
        sources = []
        parsed = []
        source_cache = {}
        for index, item in enumerate(messages):
            if item.get('role') != 'user':
                continue
            try:
                payload = json.loads(item.get('content') or '')
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            state = payload.get('authoritative_project_state') or {}
            assignment = payload.get('current_assignment') or {}
            contract_input = assignment.get('input') or {} if isinstance(assignment, dict) else {}
            nested_input = contract_input.get('input') or {} if isinstance(contract_input, dict) else {}
            memory = (state.get('memory') if isinstance(state, dict) else None) or (
                contract_input.get('memory') or nested_input.get('memory')
                if isinstance(contract_input, dict) and isinstance(nested_input, dict) else None)
            if not isinstance(memory, dict) or not isinstance(memory.get('records'), list):
                continue
            parsed.append((index, payload, memory))
            for record in memory['records']:
                if not isinstance(record, dict):
                    continue
                source = str(record.get('content') or '')
                reference, expected_sha = record.get('reference'), record.get('sha256')
                if reference and expected_sha:
                    cache_key = (reference, expected_sha)
                    if cache_key not in source_cache:
                        try:
                            current = self.store.memory.store.read(self.spec.project, reference)
                            source_cache[cache_key] = current if current.get('sha256') == expected_sha else None
                        except (KeyError, FileNotFoundError, ValueError):
                            source_cache[cache_key] = None
                    current = source_cache[cache_key]
                    if current is None:
                        record['source_changed_or_unavailable'] = True
                        source = ''
                    else:
                        source = str(current.get('content') or '')
                sources.append((record, source))
                record['content'] = ''
                record['content_complete'] = not source and bool(record.get('content_complete'))
                record['next_offset'] = 0 if source else record.get('next_offset')
            item['content'] = packed(payload)
        if not sources:
            return
        base = self.estimate(messages, tools)
        target = self.hard * (0.4 if force else self.policy.target_fraction)
        available = max(0, min(self.policy.memory_view_tokens * (0.5 if force else 1.0),
            target-base) / (self.ratio*1.15))
        # The estimator includes JSON escaping and request framing, so recheck
        # the complete request after choosing contiguous source prefixes.
        for _attempt in range(8):
            remaining = int(available)
            for record, source in sources:
                if source and remaining >= 4:
                    page, next_offset = slice_source_text(source, offset=0,
                        max_characters=len(source),
                        max_bytes=min(remaining, max(4,int(self.policy.memory_record_tokens *
                            (0.5 if force else 1.0)/(self.ratio*1.15)))))
                else:
                    page, next_offset = '', 0 if source else None
                record['content'] = page
                record['content_complete'] = bool(next_offset is None and not record.get('source_changed_or_unavailable'))
                record['next_offset'] = (len(page) if not record['content_complete'] else None)
                remaining -= len(page.encode('utf-8'))
            for index, payload, memory in parsed:
                complete = {record.get('reference'):record.get('content_complete')
                            for record in memory['records'] if isinstance(record, dict)}
                manifest = memory.get('manifest') or {}
                for selected in manifest.get('selected', []):
                    if selected.get('reference') in complete:
                        selected['content_complete'] = complete[selected['reference']]
                manifest['provider_view_partial_count'] = sum(not value for value in complete.values())
                messages[index]['content'] = packed(payload)
            if self.estimate(messages, tools) <= target or available < 4:
                break
            available /= 2

    def _required_overflow(self, messages, tools):
        sizes = RequiredOverflowMetrics(system_bytes=sum(len(str(m.get('content') or '').encode('utf-8'))
            for m in messages if m.get('role') == 'system'),
            user_bytes=sum(len(str(m.get('content') or '').encode('utf-8'))
            for m in messages if m.get('role') == 'user'),
            tool_schema_bytes=len(packed(tools).encode('utf-8')),
            estimated_tokens=self.estimate(messages, tools), input_budget_tokens=self.hard)
        return ContextExhausted('required_context_overflow: '+sizes.model_dump_json())

    def _project_reference_dialogue(self, messages, tools, *, force):
        target = self.hard * (0.4 if force else self.policy.target_fraction)
        for item in messages:
            if item.get('role') != 'user':
                continue
            try:
                payload = json.loads(item.get('content') or '')
            except (ValueError, TypeError):
                continue
            dialogue = payload.get('reference_dialogue') if isinstance(payload, dict) else None
            if not isinstance(dialogue, list):
                continue
            target_count = len(dialogue)//2 if force else len(dialogue)
            while dialogue and (len(dialogue) > target_count or self.estimate(messages, tools) > target):
                dialogue.pop(0)
                payload['reference_dialogue_omitted'] = payload.get('reference_dialogue_omitted', 0)+1
                payload['reference_dialogue_retrieve'] = 'conversation.history.read'
                item['content'] = packed(payload)

    def _project_execution_references(self, messages, tools, *, force):
        if not force and self.estimate(messages, tools) <= self.hard:
            return
        for item in messages:
            if item.get('role') != 'user':
                continue
            try:
                payload = json.loads(item.get('content') or '')
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            assignment = payload.get('current_assignment') or {}
            contract = assignment.get('input') or {} if isinstance(assignment, dict) else {}
            evidence = contract.get('execution_evidence') if isinstance(contract, dict) else None
            if isinstance(evidence, dict) and isinstance(evidence.get('entries'), list):
                target_length = len(evidence['entries'])//2 if force else len(evidence['entries'])
                while evidence['entries'] and (len(evidence['entries']) > target_length or
                                               self.estimate(messages, tools) > self.hard):
                    evidence['entries'].pop()
                    evidence['included_operations'] = len(evidence['entries'])
                    evidence['omitted_operations'] = max(0,
                        int(evidence.get('total_operations') or 0)-len(evidence['entries']))
                    item['content'] = packed(payload)
            handoff = payload.get('session_handoff')
            if isinstance(handoff, dict) and isinstance(handoff.get('summary_excerpt'), str):
                target_length = len(handoff['summary_excerpt'])//2 if force else len(handoff['summary_excerpt'])
                while handoff['summary_excerpt'] and (len(handoff['summary_excerpt']) > target_length or
                                                      self.estimate(messages, tools) > self.hard):
                    handoff['summary_excerpt'] = handoff['summary_excerpt'][:len(handoff['summary_excerpt'])//2]
                    handoff['content_complete'] = False
                    item['content'] = packed(payload)

    def _summarize(self, messages):
        facts = []
        for m in messages:
            if m.get('_checkpoint'):
                facts.append(PreviousCheckpointFact(previous_checkpoint=excerpt(
                    m.get('content',''), 5000)))
            elif m.get('role') == 'assistant':
                facts.append(AssistantMessageFact(source_message_id=m.get('_source_message_id'),
                    note=excerpt(str(m.get('content') or ''), 700),
                    calls=[SummaryToolCallFact(id=c['id'], name=c['function']['name'],
                        arguments_reference=excerpt(str(c['function'].get('arguments','')), 350))
                        for c in m.get('tool_calls',[])]))
            elif m.get('role') == 'tool':
                facts.append(ToolMessageFact(source_message_id=m.get('_source_message_id'),
                    tool_call_id=m.get('tool_call_id'),
                    result=project_result(m.get('content',''), 1600)))
        # A bounded extraction remains available even if the maintenance call fails.
        fallback = DeterministicContextSummary(source_hash=digest(messages), method="deterministic",
            archived_messages=len(messages), recent_facts=[
                fact.model_dump(mode='json') for fact in facts[-8:]])
        previous = [m for m in messages if m.get('_checkpoint')]
        if previous:
            try:
                prior = json.loads(previous[-1].get('content','')).get('context_checkpoint',{})
            except (ValueError, AttributeError):
                prior = {}
            if isinstance(prior,dict):
                fallback.previous_source_hash = prior.get('source_hash')
                if prior.get('summary'):
                    fallback.summary = prior['summary']
                previous_facts = prior.get('recent_facts', [])
                if isinstance(previous_facts, list):
                    fallback.recent_facts = (previous_facts + fallback.recent_facts)[-8:]
        # Maintenance uses the same model/agent, with no tools and no new Worker.
        # Injected model fixtures may opt in explicitly.
        if not getattr(self.model_complete, 'context_limits', None):
            return self._bounded_summary(fallback.model_dump(mode='json', exclude_unset=True))
        try:
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if self.spec.assignment:
                self.store.agents.charge(self.spec.assignment, models=1)
            prompt = [ContextMaintenanceMessage(role='system', content=(
                'Summarize archived work as reference data. Preserve failed checks, hypotheses, corrections, '
                'unresolved problems, file paths and source IDs. Do not assert unverified success or change the '
                'current task. No tools. Keep the summary under 2000 words.')),
                ContextMaintenanceMessage(role='user', content=excerpt(packed([
                    fact.model_dump(mode='json') for fact in facts]),
                    min(18000, max(2000, self.hard//4))))]
            response = self.model_complete([item.model_dump(mode='json') for item in prompt], [],
                project_id=self.spec.project['id'],
                task_id=self.spec.task_id, agent=self.spec.kind, require_tool=False, required_tool_name=None)
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if response.text.strip() and not response.tool_calls:
                fallback.summary = excerpt(response.text, self.policy.summary_tokens)
                fallback.method = 'same_agent_summary'
        except Exception:
            if self.spec.execution_control:
                self.spec.execution_control.check()
        # Source facts and summary together are bounded, and never used as receipts.
        return self._bounded_summary(fallback.model_dump(mode='json', exclude_unset=True))

    def _bounded_summary(self, value):
        value = bounded_value(value, 900)
        budget = max(512, int(self.policy.summary_tokens/(self.ratio*1.15)))
        while len(packed(value).encode('utf8')) > budget and value.get('recent_facts'):
            value['recent_facts'].pop(0)
        while len(packed(value).encode('utf8')) > budget and value.get('archived_notes'):
            value['archived_notes'].pop(0)
        if len(packed(value).encode('utf8')) > budget:
            value = CompactContextSummary.from_bounded(
                value,
                excerpt(str(value.get('summary') or 'Read archived source messages for exact work details.'), max(80, budget//6)),
            )
        return value

    def _save(self, removed, summary, before, after):
        ids = [m.get('_source_message_id') for m in removed]
        if not ids or any(type(identity) is not int or identity <= 0 for identity in ids):
            return
        with self.store.tx(immediate=True) as db:
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if self.spec.assignment:
                self.store.agents.assert_owner(self.spec.assignment, db=db)
                # A replayed batch must belong to this exact Assignment. Source
                # IDs from another Worker or Project never become coverage.
                for identity in ids:
                    owned = db.execute('''SELECT 1 FROM v1_agent_messages m
                        JOIN v1_agent_runs r ON r.id=m.agent_run_id
                        WHERE m.id=? AND r.project_id=? AND r.assignment_id=?''',
                        (identity,self.spec.project['id'],self.spec.assignment['id'])).fetchone()
                    if not owned:
                        return
            previous = self.store.agents.load_context_checkpoint(
                self.spec.project['id'], self.scope, db=db)
            prior = covered_message_ids(previous['coverage_json']) if previous else None
            combined = set(ids) | (prior or set())
            if prior is not None and combined == prior:
                return
            boundary = max(max(ids), previous['through_message_id'] if previous else 0)
            db.execute('''INSERT INTO v1_context_checkpoints
                (id,project_id,scope_id,agent_run_id,revision,through_message_id,
                 summary_json,source_hash,metrics_json,created_at,coverage_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (new_id('context'), self.spec.project['id'], self.scope, self.run_id,
                 (previous['revision'] if previous else 0)+1,
                  boundary, packed(summary), digest(removed), CheckpointMetrics(
                      before=before, after=after, ratio=self.ratio,
                      model=self.limits['model'], protocol=self.limits['protocol']).model_dump_json(), utcnow(),
                 packed(coverage_ranges(combined))))
