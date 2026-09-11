import json
import threading
import time

import pytest

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityContext, CapabilityEntry
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.execution_control import ExecutionControl, ExecutionOwnershipLost, ExecutionReplayUncertain
from app.v1.runtime import WorkflowRuntime
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow
from app.v1.store import V1Store
from tests.test_p0_assignment_regressions import COMPLETE, command, response


def begin_agent(store, project, **kwargs):
    return store.begin_agent_run(project_id=project['id'], instruction_revision=1, instruction_snapshot='test',
                                 context_snapshot={}, capabilities=['project.command.run'],
                                 platform_policy=store.latest_platform_policy(), runtime_policy=store.policy, **kwargs)


@pytest.mark.parametrize('receipt_status', ['completed', 'started'])
def test_legacy_unanswered_intent_is_adopted_or_blocked_without_duplicate_effect(store, tmp_path, receipt_status):
    project = store.create_project('legacy', '', str(tmp_path / 'project'))
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    publish_planned_workflow(store, project['id'], wf)
    task = create_planned_task(store, project['id'], 'legacy')
    task = store.claim_task(task['id'], 'old-worker')
    run = store.begin_run(task, {})
    old = begin_agent(store, project, kind='column', task_id=task['id'], column_run_id=run['id'])
    args = command("with open('once.txt','a') as f: f.write('x')")
    store.add_agent_message(old['id'], 'assistant', '', [{'id': 'one', 'type': 'function', 'function': {'name': 'project.command.run', 'arguments': json.dumps(args)}}])
    key = f"{old['id']}:one"
    if receipt_status == 'completed':
        store.registry.dispatch('project.command.run', args, CapabilityContext(project['id'], project, store, execution_key=key))
    else:
        store.start_execution_receipt(project['id'], key, 'project.command.run', args)
    store.finish_agent_run(old['id'], 'failed', '', 'lost answer', 1, 1)
    store.recover_task_from_exception(task, run['id'], 'lost answer', error_code='LLM_CONNECTION_ERROR', error_category='provider_transient')
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_tasks SET next_retry_at=NULL WHERE id=?', (task['id'],))
    runtime = WorkflowRuntime(store, store.registry, 'new-worker', AgentCore(store, store.registry, lambda *a, **kw: response('column.complete', COMPLETE)))
    if receipt_status == 'started':
        with pytest.raises(ExecutionReplayUncertain):
            runtime.step(task['id'])
        assert not (tmp_path / 'project/once.txt').exists()
        assert store.get_task(task['id'])['control_state'] == 'paused'
    else:
        runtime.step(task['id'])
        assert store.get_task(task['id'])['status'] == 'done'
        assert (tmp_path / 'project/once.txt').read_text() == 'x'
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE capability='project.command.run'").fetchone()[0] == 1


def test_control_transaction_serializes_takeover_and_rejects_late_old_owner(store, tmp_path):
    project = store.create_project('original', '', str(tmp_path / 'project'))
    queued = store.create_conversation_job(project['id'], 'control', True)
    job = store.claim_conversation_job(queued['id'], 'owner-a')
    entered, attempted, committed = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def takeover():
        try:
            assert entered.wait(5)
            attempted.set()
            with store.tx(immediate=True) as db:
                db.execute("UPDATE v1_conversation_jobs SET claim_owner='owner-b' WHERE id=?", (job['id'],))
                db.execute("UPDATE v1_conversation_agents SET lease_owner='owner-b' WHERE project_id=?", (project['id'],))
            committed.set()
        except BaseException as exc:
            errors.append(exc)
    def handler(args, ctx):
        entered.set()
        assert attempted.wait(5)
        assert not committed.wait(0.05)
        with store.tx(immediate=True) as db:
            db.execute('UPDATE v1_projects SET name=? WHERE id=?', (args['name'], project['id']))
        return {'updated': True}
    store.registry.register(CapabilityEntry(id='test.control', description='control', input_schema={}, output_schema={}, side_effect_kind='control', handler=handler))
    ctx = CapabilityContext(project['id'], project, store, execution_key='first', execution_control=ExecutionControl(time.monotonic()+30, validate_owner=lambda: store.assert_conversation_owner(job)))
    contender = threading.Thread(target=takeover)
    contender.start()
    assert store.registry.dispatch('test.control', {'name': 'valid-before-takeover'}, ctx).ok
    contender.join(5)
    assert not contender.is_alive() and not errors and committed.is_set()
    with pytest.raises(ExecutionOwnershipLost):
        store.registry.dispatch('test.control', {'name': 'stale'}, ctx)
    assert store.get_project(project['id'])['name'] == 'valid-before-takeover'


def test_claim_reserves_shared_worker_before_assignment_exists(store, tmp_path):
    project = store.create_project('single writer', '', str(tmp_path / 'project'))
    wf = agent_workflow()
    wf.column('work').executor.worker_key = 'shared'
    publish_planned_workflow(store, project['id'], wf)
    first = create_planned_task(store, project['id'], 'first')
    second = create_planned_task(store, project['id'], 'second')
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_scheduling_entries SET resources_json='[]',wip_limit=4 WHERE project_id=?", (project['id'],))
    assert store.claim_task(first['id'], 'worker-a') is not None
    assert store.claim_task(second['id'], 'worker-b') is None
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v1_agent_assignments').fetchone()[0] == 0


def test_leaf_cannot_call_main_planning_even_if_capability_is_injected(store, tmp_path):
    project = store.create_project('leaf', '', str(tmp_path / 'project'))
    result = store.registry.dispatch('requirement.create', {'scope_key': 'forged', 'objective': 'forged'},
        CapabilityContext(project['id'], project, store, column_run_id='column', user_initiated=True))
    assert not result.ok and result.error['type'] == 'LeafCapabilityDenied'
    assert store.agents.list_requirements(project['id']) == []


def test_additive_session_migration_preserves_legacy_transcript_and_owner_scope(store, tmp_path):
    project = store.create_project('legacy context', '', str(tmp_path / 'project'))
    wf = agent_workflow()
    wf.column('work').metadata['agent_session_key'] = 'implementer'
    publish_planned_workflow(store, project['id'], wf)
    task = create_planned_task(store, project['id'], 'legacy context')
    session = store.get_or_create_agent_session(project['id'], task['id'], 'implementer')
    old = begin_agent(store, project, kind='column', task_id=task['id'], agent_session_id=session['id'])
    store.add_agent_message(old['id'], 'user', 'Retain this design decision', [])
    store.finish_agent_run(old['id'], 'succeeded', 'saved', None, 1, 0)
    with store.connect() as db:
        before = [tuple(r) for r in db.execute('SELECT * FROM v1_agent_sessions')]
    restarted = V1Store(str(store.path), registry=store.registry)
    seen = []
    def model(messages, *args, **kwargs):
        seen.append(json.dumps(messages))
        return response('column.complete', COMPLETE)
    WorkflowRuntime(restarted, store.registry, 'new', AgentCore(restarted, store.registry, model)).step(task['id'])
    assert 'Retain this design decision' in seen[0]
    with restarted.connect() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM v1_agent_sessions')] == before
    worker = next(w for w in restarted.agents.list_workers(project['id']) if w['role'] == 'leaf')
    assert 'Retain this design decision' in json.dumps(restarted.agents.context_page(project['id'], worker['id']))


def test_worker_context_window_preserves_explicit_snapshot_and_source_records(store, tmp_path):
    project = store.create_project('window', '', str(tmp_path / 'project'))
    wf = agent_workflow()
    publish_planned_workflow(store, project['id'], wf)
    task = create_planned_task(store, project['id'], 'window')
    WorkflowRuntime(store, store.registry, 'worker', AgentCore(store, store.registry, lambda *a, **kw: response('column.complete', COMPLETE))).step(task['id'])
    worker = next(w for w in store.agents.list_workers(project['id']) if w['role'] == 'leaf')
    page = store.agents.context_page(project['id'], worker['id'], limit=50)
    store.agents.save_context_snapshot(project['id'], worker['id'], page[-1]['id'], {'decision': 'Preserve the API contract'})
    run = begin_agent(store, project, kind='column', task_id=task['id'], agent_session_id=worker['active_session_id'])
    for i in range(20):
        store.add_agent_message(run['id'], 'user', f'context-{i} '+('x'*500), [])
    store.policy = store.policy.model_copy(update={'context': store.policy.context.model_copy(update={'worker_context_max_characters': 3000})})
    history = store.agents.session_history(project['id'], worker['active_session_id'])
    payload = json.dumps(history)
    assert 'Preserve the API contract' in payload and 'older_messages_omitted' in payload
    assert len(payload) < 3000
    assert len(store.agents.context_page(project['id'], worker['id'], limit=50)) >= 20
