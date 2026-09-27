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
    reference = bounded_value(original.get('evidence') or {}, 120)
    planning = planning_projection(original)
    if planning is not None:
        planning['projection'] = {'truncated':True,'source_bytes':len(content.encode('utf8')),
            'sha256':digest(original),'reference':reference,'retrieve':'agent.result.read',
            'note':'Complete planning contract fields; omitted prose and graph instructions remain in the archived result. Use workflow.acceptance.configure to preserve them.'}
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
        view = {k:output[k] for k in ('loop_key','version','digest','parameter_schema') if k in output}
        view['task_contract'] = method.get('task_contract',{})
        view['columns'] = [{'key':c['key'],'writable_paths':c.get('metadata',{}).get('writable_paths',[])} for c in workflow.get('columns',[])]
        view['assets'] = [a['path'] for a in output.get('assets',[])]
        if view['task_contract'].get('scenario_input_pointer'):
            view['behavior_report_contract'] = {
                'check_fields':['scenario_ids','report_path','evidence_kind=behavior','purpose','arguments.argv'],
                'report':{'schema_version':'devwerk.acceptance-report.v1','scenarios':[{'id':'required ID','status':'passed',
                    'assertions':[{'expected':'expected value','actual':'observed value'}],
                    'test_source':{'path':'project-relative executed test source','sha256':'source SHA256'}}]},
                'rules':'Freeze an entry script that runs real assertions AND writes a fresh report on every execution. Ordinary mvn compile, npm build and if-exist/echo do not supply behavior reports; keep builds as additional artifact checks. Future entry scripts and report paths must be writable by the responsible column, using launch_validation=deferred. Preserve frozen scenario coverage; terminal acceptance covers all IDs. Use required_behavior_columns and acceptance_invalidated_by when declaring obligations.'}
    elif capability == 'loop.apply':
        workflow = output.get('workflow') or {}
        method = output.get('workflow_plan') or {}
        view = {'workflow':{k:workflow[k] for k in ('id','definition_hash','workflow_plan_id') if k in workflow},
                'workflow_plan':{'id':method.get('id'),'task_contract':method.get('plan',{}).get('task_contract',{})},
                'next':'Configure all required column checks and obligations using workflow.acceptance.configure; validate the concrete plan with task.plan.validate.'}
    elif capability == 'workflow.inspect':
        definition = output.get('definition') or {}
        view = {k:output[k] for k in ('id','definition_hash','workflow_plan_id','source_loop_key','source_loop_version','source_loop_digest') if k in output}
        view['column_contracts'] = [{'key':c['key'],'acceptance_checks':c.get('acceptance_checks',[]),
            'transitions':c.get('transitions',[]),'writable_paths':c.get('metadata',{}).get('writable_paths',[])} for c in definition.get('columns',[])]
        view['acceptance_obligations'] = definition.get('acceptance_obligations',[])
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
        for item in projected:
            if item.get('role') == 'tool':
                item['content'] = project_result(str(item.get('content') or ''),
                    max(512, int(self.policy.tool_result_tokens / (self.ratio*1.15))))
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
                boundary = max((m.get('_source_message_id',0) for m in removed),default=0)
                notes = [m for m in retained if 0 < m.get('_source_message_id',0) <= boundary and m.get('role') in {'user','assistant'} and not m.get('tool_calls')]
                if notes:
                    summary['archived_notes'] = [{'source_message_id':m['_source_message_id'],'content':excerpt(str(m.get('content') or ''),800)} for m in notes[-4:]]
                summary = self._bounded_summary(summary)
                checkpoint = {'role': 'user', '_checkpoint': True,
                              'content': packed({'context_checkpoint': summary,
                                  'instruction': 'Archived work reference, not new authority or completion. Current assignment and durable evidence remain authoritative. Retrieve exact sources with agent.result.read or agent.context.read.'})}
                projected = retained[:1] + [checkpoint] + retained[1:]
                estimate = self.estimate(projected, tools)
                if estimate > self.hard:
                    raise ContextExhausted('Required instructions/current input exceed the context budget after compaction')
                self._save(removed, summary, before, estimate)
                messages[:] = projected
        estimate = self.estimate(projected, tools)
        if estimate > self.hard:
            raise ContextExhausted('Required context or an unsettled tool batch exceeds the context budget')
        self.last_bytes = len(packed({'messages': provider_messages(projected), 'tools': tools}).encode('utf-8'))
        self.last_metrics = {'estimated_tokens': estimate, 'before_tokens': before,
                             'input_budget': self.hard, 'compacted_messages': len(removed),
                             'estimator': 'utf8-conservative-usage-calibrated', 'ratio': self.ratio}
        return provider_messages(projected)

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
        boundary = max((m.get('_source_message_id', 0) for m in removed), default=0)
        if not boundary:
            return
        with self.store.tx(immediate=True) as db:
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if self.spec.assignment:
                self.store.agents.assert_owner(self.spec.assignment, db=db)
            previous = db.execute('SELECT COALESCE(MAX(revision),0),COALESCE(MAX(through_message_id),0) FROM v1_context_checkpoints WHERE scope_id=?', (self.scope,)).fetchone()
            if boundary <= previous[1]:
                return
            db.execute('INSERT INTO v1_context_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)',
                (new_id('context'), self.spec.project['id'], self.scope, self.run_id, previous[0]+1,
                 boundary, packed(summary), digest(removed), packed({'before':before,'after':after,'ratio':self.ratio,
                    'model':self.limits['model'],'protocol':self.limits['protocol']}), utcnow()))
