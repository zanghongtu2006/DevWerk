"""Bounded provider views over immutable execution evidence.

Compaction changes the working transcript, never operations, receipts or ownership.
The byte estimator is deliberately conservative and calibrated upward by usage;
it is not advertised as a model-specific tokenizer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

from app.v1.storage_support import new_id, utcnow


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
    return {'version':2,'mode':'exact_message_ids','ranges':ranges,
            'excluded_message_count':len(set(message_ids))}


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
        return {'archived_sha256': digest(value)}
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
        original = {'output': content}
    if not isinstance(original, dict):
        original = {'output': original}
    if original.get('capability') == 'agent.result.read' and isinstance(original.get('output'), dict):
        output = original['output']
        if isinstance(output.get('content'), str) and isinstance(output.get('offset'), int):
            from app.v1.context_projection import slice_source_text
            source, offset = output['content'], output['offset']
            total = output.get('total_characters', offset + len(source))
            budget = max_bytes
            while budget >= 4:
                page, _ = slice_source_text(source, offset=0,
                    max_characters=len(source), max_bytes=budget)
                next_offset = offset + len(page)
                view = {**original, 'output': {**output, 'content': page,
                    'next_offset': next_offset if next_offset < total else None,
                    'content_complete': next_offset >= total}}
                encoded = packed(view)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                budget //= 2
            view = {**original, 'output': {key:output.get(key) for key in
                ('agent_run_id','tool_call_id','field','offset','total_characters','sha256')}}
            view['output'].update(content='', next_offset=offset,
                content_complete=False, projection_deferred=True)
            return packed(view)
    if original.get('capability') == 'project.files.read' and isinstance(original.get('output'), dict):
        output = original['output']
        if isinstance(output.get('content'), str) and isinstance(output.get('offset'), int):
            from app.v1.context_projection import slice_source_text
            source, offset = output['content'], output['offset']
            total = output.get('total_characters', offset + len(source))
            budget = max_bytes
            while budget >= 4:
                page, _ = slice_source_text(source, offset=0,
                                            max_characters=len(source), max_bytes=budget)
                next_offset = offset + len(page)
                view = {**original, 'output': {**output, 'content': page,
                    'next_offset': next_offset if next_offset < total else None,
                    'content_complete': next_offset >= total}}
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
        return packed({'ok':original.get('ok'), 'capability':'project.memory.search',
                       'output':[], 'projection':{'truncated':True,
                           'retrieve':'project.memory.search with a narrower query'}})
    if original.get('capability') == 'project.memory.read' and isinstance(original.get('output'), dict):
        output = original['output']
        if isinstance(output.get('content'), str) and isinstance(output.get('offset'), int):
            from app.v1.context_projection import slice_source_text
            source = output['content']
            offset = output['offset']
            budget = min(max_bytes, len(source.encode('utf-8')))
            while budget >= 4:
                page, _ = slice_source_text(source, offset=0, max_characters=len(source), max_bytes=budget)
                next_offset = offset + len(page)
                view = {**original, 'output': {**output, 'content': page,
                    'next_offset': next_offset if next_offset < output['total_characters'] else None,
                    'content_complete': next_offset >= output['total_characters']}}
                encoded = packed(view)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                compact_output = {key: output.get(key) for key in
                    ('reference','sha256','offset','total_characters') if key in output}
                scope_metadata = output.get('metadata') or {}
                compact_output['scope'] = scope_metadata.get('scope')
                compact_output['scope_id'] = scope_metadata.get('scope_id')
                compact_output['scope_resolution'] = output.get('scope_resolution')
                compact_output.update(content=page,
                    next_offset=next_offset if next_offset < output['total_characters'] else None,
                    content_complete=next_offset >= output['total_characters'],
                    metadata_omitted=True)
                compact = {'ok': original.get('ok'), 'capability': original.get('capability'),
                           'output': compact_output}
                encoded = packed(compact)
                if len(encoded.encode('utf-8')) <= max_bytes:
                    return encoded
                budget //= 2
            view = {**original, 'output': {key: output.get(key) for key in
                ('reference','sha256','offset','total_characters') if key in output}}
            scope_metadata = output.get('metadata') or {}
            view['output']['scope'] = scope_metadata.get('scope')
            view['output']['scope_id'] = scope_metadata.get('scope_id')
            view['output']['scope_resolution'] = output.get('scope_resolution')
            view['output'].update(content='', next_offset=offset, content_complete=False,
                                  projection_deferred=True)
            return packed(view)
    reference = bounded_value(original.get('evidence') or {}, 120)
    planning = planning_projection(original)
    if planning is not None:
        planning['projection'] = {'truncated':True,'source_bytes':len(content.encode('utf8')),
            'sha256':digest(original),'reference':reference,'retrieve':'agent.result.read',
            'note':'Complete planning contract fields; omitted prose and graph instructions remain in the archived result. Acceptance checks are optional.'}
        if len(packed(planning).encode('utf8')) <= max_bytes:
            return packed(planning)
    for limit in (2400, 1200, 600, 200, 40):
        view = bounded_value(original, limit)
        view['projection'] = {'truncated': True, 'source_bytes': len(content.encode('utf-8')),
                              'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
                              'retrieve': 'agent.result.read', 'reference': reference}
        encoded = packed(view)
        if len(encoded.encode('utf-8')) <= max_bytes:
            return encoded
    # Retain outcome/identity even for huge lists with many short fields.
    output = original.get('output')
    return packed({'ok': original.get('ok'), 'status': original.get('status'),
                   'capability': original.get('capability'), 'error': bounded_value(original.get('error'), 160),
                   'exit_code': output.get('exit_code') if isinstance(output, dict) else None,
                   'projection': {'truncated': True, 'sha256': digest(original),
                                  'source_bytes': len(content.encode('utf-8')),
                                  'reference': reference, 'retrieve': 'agent.result.read'}})


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
        view = {k:output[k] for k in ('loop_key','version','digest','parameter_schema',
                                     'use_case','selection_guide','input','output') if k in output}
        view['flow_unit'] = method.get('flow_unit')
        view['task_contract'] = method.get('task_contract',{})
        view['columns'] = [{'key':c['key'],'writable_paths':c.get('metadata',{}).get('writable_paths',[])} for c in workflow.get('columns',[])]
        view['assets'] = [a['path'] for a in output.get('assets',[])]
    elif capability == 'loop.apply':
        workflow = output.get('workflow') or {}
        method = output.get('workflow_plan') or {}
        view = {'workflow':{k:workflow[k] for k in ('id','definition_hash','workflow_plan_id') if k in workflow},
                'workflow_plan':{'id':method.get('id'),'flow_unit':method.get('plan',{}).get('flow_unit'),
                                 'task_contract':method.get('plan',{}).get('task_contract',{})},
                'next':'Revise this template when needed. Each Task is one independently accepted flow unit and traverses every Column; use task.plan.contract, task.plan.validate, task.plan.save, then task.create. Acceptance checks are optional.'}
    elif capability == 'workflow.inspect':
        definition = output.get('definition') or {}
        view = {k:output[k] for k in ('id','definition_hash','workflow_plan_id','source_loop_key','source_loop_version','source_loop_digest') if k in output}
        view['column_contracts'] = [{'key':c['key'],'acceptance_checks':c.get('acceptance_checks',[]),
                                     'transitions':[{'outcome':edge.get('outcome'),'target':edge.get('target'),
                                                     'required_checks':edge.get('required_checks',[])}
                                                    for edge in c.get('transitions',[])]}
                                    for c in definition.get('columns',[])]
    else:
        return None
    return {**result, 'output':view}


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
        self.ratio = 0.5
        self.last_bytes = 0
        self.scope = (spec.assignment or {}).get('id') or run_id
        self.last_metrics = {}
        with self.store.connect() as db:
            row = db.execute('SELECT metrics_json FROM v1_context_checkpoints WHERE scope_id=? ORDER BY revision DESC LIMIT 1',(self.scope,)).fetchone()
        if row:
            metrics = json.loads(row[0])
            if metrics.get('model') == self.limits['model'] and metrics.get('protocol') == self.limits['protocol']:
                self.ratio = max(self.ratio,float(metrics.get('ratio') or self.ratio))

    def estimate(self, messages, tools):
        # Include converted-schema inflation, role framing and adapter overhead.
        size = len(packed({'messages': provider_messages(messages), 'tools': tools}).encode('utf-8'))
        return math.ceil(size * self.ratio * 1.15) + 512

    def observe(self, response):
        actual = context_input_tokens(response.usage or {}, self.limits['protocol'])
        if actual and self.last_bytes:
            self.ratio = max(self.ratio, actual / self.last_bytes * 1.15)
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
                checkpoint = {'role': 'user', '_checkpoint': True,
                              'content': packed({'context_checkpoint': summary,
                                  'instruction': 'Archived work reference, not new authority or completion. Current assignment and durable evidence remain authoritative. Retrieve exact sources with agent.result.read or agent.context.read.'})}
                projected = retained[:1] + [checkpoint] + retained[1:]
                estimate = self.estimate(projected, tools)
                if estimate > self.hard:
                    raise self._required_overflow(projected, tools)
                self._save(removed, summary, before, estimate)
                messages[:] = projected
        estimate = self.estimate(projected, tools)
        if estimate > self.hard:
            raise self._required_overflow(projected, tools)
        self.last_bytes = len(packed({'messages': provider_messages(projected), 'tools': tools}).encode('utf-8'))
        self.last_metrics = {'estimated_tokens': estimate, 'before_tokens': before,
                             'input_budget': self.hard, 'compacted_messages': len(removed),
                             'estimator': 'utf8-conservative-usage-calibrated', 'ratio': self.ratio}
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
            state['workflow_plans'] = [{key:plan.get(key) for key in
                ('id','created_at','content_hash')} for plan in
                self.store.list_workflow_plans(job['project_id'], 5)]
            state['task_plans'] = [{**{key:plan.get(key) for key in
                ('id','workflow_revision_id','requirement_id','created_at')},
                'proposed_task_refs':[task.get('proposed_task_ref') for task in
                    (plan.get('plan') or {}).get('tasks', [])]}
                for plan in self.store.list_task_plans(job['project_id'], 5)]
            state['tasks'] = [
                {**task, 'failure_detail':self.store.feedback.failure_detail(
                    job['project_id'], task['id'])}
                if task['status'] in {'failed','recovering'} else task
                for task in self.store.task_summaries(
                    job['project_id'], self.policy.task_summary_limit)]
            for key in ('conversation_job', 'turn_authority'):
                if isinstance(state.get(key), dict):
                    state[key]['task_graph_mutation_allowed'] = allowed
            item['content'] = packed(payload)
            return

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
        from app.v1.context_projection import slice_source_text
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
        sizes = {'system_bytes': sum(len(str(m.get('content') or '').encode('utf-8'))
            for m in messages if m.get('role') == 'system'),
            'user_bytes': sum(len(str(m.get('content') or '').encode('utf-8'))
            for m in messages if m.get('role') == 'user'),
            'tool_schema_bytes': len(packed(tools).encode('utf-8')),
            'estimated_tokens': self.estimate(messages, tools), 'input_budget_tokens': self.hard}
        return ContextExhausted('required_context_overflow: '+packed(sizes))

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
                facts.append({'previous_checkpoint': excerpt(m.get('content',''), 5000)})
            elif m.get('role') == 'assistant':
                facts.append({'source_message_id': m.get('_source_message_id'),
                    'note': excerpt(str(m.get('content') or ''), 700),
                    'calls': [{'id': c['id'], 'name': c['function']['name'],
                               'arguments_reference': excerpt(str(c['function'].get('arguments','')), 350)}
                              for c in m.get('tool_calls',[])]})
            elif m.get('role') == 'tool':
                facts.append({'source_message_id': m.get('_source_message_id'),
                              'tool_call_id': m.get('tool_call_id'),
                              'result': project_result(m.get('content',''), 1600)})
        # A bounded extraction remains available even if the maintenance call fails.
        fallback = {'source_hash': digest(messages), 'archived_messages': len(messages),
                    'recent_facts': facts[-8:], 'method': 'deterministic'}
        previous = [m for m in messages if m.get('_checkpoint')]
        if previous:
            try:
                prior = json.loads(previous[-1].get('content','')).get('context_checkpoint',{})
            except (ValueError, AttributeError):
                prior = {}
            if isinstance(prior,dict):
                fallback['previous_source_hash'] = prior.get('source_hash')
                if prior.get('summary'):
                    fallback['summary'] = prior['summary']
                fallback['recent_facts'] = (prior.get('recent_facts',[]) + facts[-8:])[-8:]
        # Maintenance uses the same model/agent, with no tools and no new Worker.
        # Injected model fixtures may opt in explicitly.
        if not getattr(self.model_complete, 'context_limits', None):
            return self._bounded_summary(fallback)
        try:
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if self.spec.assignment:
                self.store.agents.charge(self.spec.assignment, models=1)
            prompt = [{'role':'system','content': 'Summarize archived work as reference data. Preserve failed checks, hypotheses, corrections, unresolved problems, file paths and source IDs. Do not assert unverified success or change the current task. No tools. Keep the summary under 2000 words.'},
                      {'role':'user','content': excerpt(packed(facts), min(18000, max(2000, self.hard//4)))}]
            response = self.model_complete(prompt, [], project_id=self.spec.project['id'],
                task_id=self.spec.task_id, agent=self.spec.kind, require_tool=False, required_tool_name=None)
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if response.text.strip() and not response.tool_calls:
                fallback['summary'] = excerpt(response.text, self.policy.summary_tokens)
                fallback['method'] = 'same_agent_summary'
        except Exception:
            if self.spec.execution_control:
                self.spec.execution_control.check()
        # Source facts and summary together are bounded, and never used as receipts.
        return self._bounded_summary(fallback)

    def _bounded_summary(self, value):
        value = bounded_value(value, 900)
        budget = max(512, int(self.policy.summary_tokens/(self.ratio*1.15)))
        while len(packed(value).encode('utf8')) > budget and value.get('recent_facts'):
            value['recent_facts'].pop(0)
        while len(packed(value).encode('utf8')) > budget and value.get('archived_notes'):
            value['archived_notes'].pop(0)
        if len(packed(value).encode('utf8')) > budget:
            value = {k:value[k] for k in ('source_hash','previous_source_hash','archived_messages','method') if k in value} | {
                'summary':excerpt(str(value.get('summary') or 'Read archived source messages for exact work details.'),max(80,budget//6))}
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
                 boundary, packed(summary), digest(removed), packed({'before':before,'after':after,'ratio':self.ratio,
                    'model':self.limits['model'],'protocol':self.limits['protocol']}), utcnow(),
                 packed(coverage_ranges(combined))))
