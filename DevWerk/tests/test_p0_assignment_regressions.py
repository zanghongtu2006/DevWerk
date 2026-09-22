"""Fault injection against real SQLite, Runtime and subprocess effects.

Only provider responses and the asynchronous adapter are deterministic doubles.
"""
import asyncio
import json
import sqlite3
import sys

import pytest
import requests

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityEntry, CapabilityContext
from app.v1.conversation import ConversationGateway
from app.v1.domain import AgentModelResponse, AgentToolCall, AcceptanceCheck, TimerWaitPolicy, PollWaitPolicy, ToolResult
from app.v1.execution_control import ExecutionBudgetExceeded
from app.v1.policy import ExecutionLimits
from app.v1.runtime import WorkflowRuntime
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow, task_plan


COMPLETE = {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'Delivered'}


def response(name, arguments=None):
    return AgentModelResponse(tool_calls=[AgentToolCall(id='call', name=name, arguments=arguments or {})])


def setup(store, tmp_path, workflow, responses):
    project = store.create_project('delivery', '', str(tmp_path / 'project'))
    publish_planned_workflow(store, project['id'], workflow)
    task = create_planned_task(store, project['id'], 'delivery')
    replies = iter(responses)
    core = AgentCore(store, store.registry, lambda *a, **kw: next(replies), policy=store.policy)
    return project, task, WorkflowRuntime(store, store.registry, 'worker', core)


def due(store):
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_await_handles SET next_check_at='2000-01-01T00:00:00+00:00' WHERE status='pending'")
    return store.due_await_handles()[0]


def command(code):
    return {'argv': [sys.executable, '-c', code]}


def test_new_identical_operation_after_await_executes_again(store, tmp_path):
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    wf.column('work').wait_policy = TimerWaitPolicy(delay_seconds=1)
    args = command("with open('result.txt','a') as f: f.write('x')")
    project, task, runtime = setup(store, tmp_path, wf, [response('project.command.run', args), response('column.await', {'provider': 'timer'}), response('project.command.run', args), response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    runtime.reconcile_await(due(store))
    assert (tmp_path / 'project/result.txt').read_text() == 'xx'
    assert store.get_task(task['id'])['status'] == 'done'
    with store.connect() as db:
        assignment = dict(db.execute('SELECT * FROM v1_agent_assignments').fetchone())
        assert (assignment['status'], assignment['resumes'], assignment['model_calls']) == ('completed', 2, 4)
        assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE capability='project.command.run'").fetchone()[0] == 2
    assert store.agents.get_worker(project['id'], assignment['agent_instance_id'])['active_assignment_id'] is None


@pytest.mark.parametrize('crash_point', ['answer_operation', 'record_tool_invocation'])
def test_committed_effect_replays_after_missing_answer_without_reexecution(store, tmp_path, monkeypatch, crash_point):
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    project, task, runtime = setup(store, tmp_path, wf, [response('project.command.run', command("with open('once.txt','a') as f: f.write('x')")), response('column.complete', COMPLETE)])
    target = store.agents if crash_point == 'answer_operation' else store
    original = getattr(target, crash_point)
    crashed = False
    def fail_once(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise requests.ConnectionError('injected loss after committed receipt')
        return original(*args, **kwargs)
    monkeypatch.setattr(target, crash_point, fail_once)
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'recovering'
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_tasks SET next_retry_at=NULL WHERE id=?', (task['id'],))
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    assert (tmp_path / 'project/once.txt').read_text() == 'x'
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE capability='project.command.run'").fetchone()[0] == 1


def test_accepted_wait_survives_crash_before_handle_creation(store, tmp_path, monkeypatch):
    wf = agent_workflow()
    wf.column('work').wait_policy = TimerWaitPolicy(delay_seconds=1)
    _, task, runtime = setup(store, tmp_path, wf, [response('column.await', {'provider': 'timer'}), response('column.complete', COMPLETE)])
    original = store.finish_agent_run
    crashed = False
    def fail_once(run_id, status, *args, **kwargs):
        nonlocal crashed
        if status == 'waiting' and not crashed:
            crashed = True
            raise requests.ConnectionError('injected wait handoff loss')
        return original(run_id, status, *args, **kwargs)
    monkeypatch.setattr(store, 'finish_agent_run', fail_once)
    runtime.step(task['id'])
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_tasks SET next_retry_at=NULL WHERE id=?', (task['id'],))
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'waiting'
    runtime.reconcile_await(due(store))
    assert store.get_task(task['id'])['status'] == 'done'


def test_unsettled_runtime_acceptance_effect_is_not_reexecuted(store, tmp_path):
    import subprocess
    from pathlib import Path
    from app.v1.execution_control import ExecutionReplayUncertain
    wf = agent_workflow()
    wf.column('work').acceptance_checks = [AcceptanceCheck(
        key='delivery', capability='project.command.run',
        arguments=command("with open('acceptance-once.txt','a') as f: f.write('x')"))]
    _, task, runtime = setup(store, tmp_path, wf, [response('column.complete', COMPLETE)] * 2)
    # Exit the actual Worker process, bypassing all Python exception handlers,
    # after the command effect and before committing its terminal receipt.
    child = '''
import os, sys
from app.v1.store import V1Store
from app.v1.capabilities import build_core_registry
from app.v1.agent import AgentCore
from app.v1.runtime import WorkflowRuntime
from tests.test_p0_assignment_regressions import response, COMPLETE
store = V1Store(sys.argv[1], registry=build_core_registry())
finish = store.finish_execution_receipt
def interrupt(project_id, execution_key, *args, **kwargs):
    if ':acceptance-' in execution_key:
        os._exit(73)
    return finish(project_id, execution_key, *args, **kwargs)
store.finish_execution_receipt = interrupt
core = AgentCore(store, store.registry, lambda *a, **kw: response('column.complete', COMPLETE))
WorkflowRuntime(store, store.registry, 'crashing-worker', core).step(sys.argv[2])
'''
    process = subprocess.run([sys.executable, '-c', child, str(store.path), task['id']],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert process.returncode == 73, process.stderr
    assert store.get_task(task['id'])['status'] == 'running'
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (task['id'],))
    assert store.recover_expired_task_leases() == [task['id']]
    with pytest.raises(ExecutionReplayUncertain, match='acceptance effect requires reconciliation'):
        runtime.step(task['id'])
    assert (tmp_path / 'project/acceptance-once.txt').read_text() == 'x'
    assert store.get_task(task['id'])['control_state'] == 'paused'
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE execution_key LIKE '%:acceptance-%'").fetchone()[0] == 1


def test_await_budget_is_cumulative_for_assignment(store, tmp_path):
    store.policy = store.policy.model_copy(update={'execution': ExecutionLimits(agent_max_iterations=1, agent_max_tool_calls=1)})
    wf = agent_workflow()
    wf.column('work').wait_policy = TimerWaitPolicy(delay_seconds=1)
    _, task, runtime = setup(store, tmp_path, wf, [response('column.await', {'provider': 'timer'})] * 10)
    runtime.step(task['id'])
    with pytest.raises(ExecutionBudgetExceeded):
        runtime.reconcile_await(due(store))
    assert store.get_task(task['id'])['control_state'] == 'paused'
    with store.connect() as db:
        assert db.execute('SELECT model_calls FROM v1_agent_assignments').fetchone()[0] == 1


def test_settled_async_receipt_replaces_awaiting_invocation_in_ledger(store, tmp_path):
    store.registry.register(CapabilityEntry(id='test.async', description='async', input_schema={}, output_schema={}, side_effect_kind='process',
        handler=lambda a, c: ToolResult(ok=True, status='awaiting', capability='test.async', await_handle_draft={'provider': 'test', 'poll_capability': 'test.poll', 'poll_arguments': {}}, checkpoint={'job': 'one'})))
    store.registry.register(CapabilityEntry(id='test.poll', description='poll', input_schema={}, output_schema={}, side_effect_kind='read', handler=lambda a, c: {'status': 'succeeded', 'output': {'delivered': True}}))
    wf = agent_workflow()
    wf.column('work').executor.capabilities = ['test.async', 'test.poll']
    wf.column('work').wait_policy = PollWaitPolicy(poll_capability='test.poll', poll_interval_seconds=1)
    project, task, runtime = setup(store, tmp_path, wf, [response('test.async'), response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    handle = due(store)
    runtime.reconcile_await(handle)
    assert store.get_task(task['id'])['status'] == 'done'
    assert runtime._column_action_ledger(project['id'], handle['run_id'])[0]['status'] == 'completed'


def test_frozen_acceptance_allows_corrected_command_after_exploration_failure(store, tmp_path):
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    wf.column('work').acceptance_checks = [AcceptanceCheck(key='delivery', capability='project.command.run', arguments=command("from pathlib import Path; assert Path('delivery.txt').read_text() == 'ready'"))]
    _, task, runtime = setup(store, tmp_path, wf, [response('project.command.run', command('raise SystemExit(1)')), response('project.command.run', command("from pathlib import Path; Path('delivery.txt').write_text('ready')")), response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'


def test_noop_cannot_satisfy_frozen_delivery_check(store, tmp_path):
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    wf.column('work').acceptance_checks = [AcceptanceCheck(key='delivery', capability='project.files.read', arguments={'path': 'missing.txt'})]
    _, task, runtime = setup(store, tmp_path, wf, [response('project.command.run', command("print('done')"))] + [response('column.complete', COMPLETE)] * 6)
    with pytest.raises(RuntimeError):
        runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] != 'done'
    assert store.get_task(task['id'])['terminal_artifact_id'] is None


def test_control_failure_rolls_back_partial_business_write(store, tmp_path):
    project = store.create_project('original', '', str(tmp_path / 'project'))
    def handler(args, ctx):
        with store.tx(immediate=True) as db:
            db.execute("UPDATE v1_projects SET name='partial' WHERE id=?", (project['id'],))
        return ToolResult(ok=False, capability='test.control', error={'message': 'reject'})
    store.registry.register(CapabilityEntry(id='test.control', description='control', input_schema={}, output_schema={}, side_effect_kind='control', handler=handler))
    result = store.registry.dispatch('test.control', {}, CapabilityContext(project_id=project['id'], project=project, store=store))
    assert not result.ok
    assert store.get_project(project['id'])['name'] == 'original'


def test_durable_queue_recovers_failed_claim_without_restart(store, tmp_path, monkeypatch):
    project = store.create_project('queue', '', str(tmp_path / 'project'))
    gateway = ConversationGateway(store, store.registry, agent_core=AgentCore(store, store.registry, lambda *a, **kw: AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': 'received'}))))
    original = store.claim_conversation_job
    calls = 0
    def claim(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError('database is locked')
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'claim_conversation_job', claim)
    async def run():
        await gateway.start()
        try:
            first = await gateway.submit(project['id'], 'first', False)
            second = await gateway.submit(project['id'], 'second', False)
            for _ in range(100):
                await asyncio.sleep(0.03)
                await gateway._enqueue_governance_jobs()
                if all(store.get_conversation_job(item['job']['id'])['status'] == 'succeeded' for item in [first, second]):
                    return
            pytest.fail('accepted jobs were not rediscovered')
        finally:
            await gateway.stop()
    asyncio.run(run())


def test_notification_does_not_create_repair_and_explicit_repair_reuses_worker(store, tmp_path):
    wf = agent_workflow()
    wf.column('work').executor.worker_key = 'developer'
    project, first, runtime = setup(store, tmp_path, wf, [response('column.complete', COMPLETE)])
    runtime.step(first['id'])
    requirement_id = store.get_task(first['id'])['requirement_id']
    plan = task_plan(first['workflow_revision_id'], wf, task_ref='repair', title='Follow up on review')
    n = 0
    interrupt_reply = True
    def main_model(*args, **kwargs):
        nonlocal n, interrupt_reply
        n += 1
        if n == 1:
            return response('task.plan.save', {'plan': plan.model_dump(mode='json')})
        if n == 2:
            plans = store.list_task_plans(project['id'])
            saved = next(p for p in plans if p['plan']['tasks'][0]['proposed_task_ref'] == 'repair')
            return response('task.create', {'task_plan_id': saved['id'], 'proposed_task_ref': 'repair'})
        if interrupt_reply:
            interrupt_reply = False
            raise requests.ConnectionError('reply interrupted after task creation')
        return AgentModelResponse(text=json.dumps({'mode': 'work_result', 'task_ids': [t['id'] for t in store.list_tasks(project['id']) if t['proposed_task_ref'] == 'repair']}))
    gateway = ConversationGateway(store, store.registry, agent_core=AgentCore(store, store.registry, main_model))
    job_id = store.enqueue_governance_jobs()[0]
    job = store.claim_conversation_job(job_id, 'test-main')
    assert job['trigger_kind'] == 'mailbox'
    from app.v1.execution_control import ExecutionControl
    import time
    gateway._process(job, ExecutionControl(time.monotonic()+30, validate_owner=lambda: store.assert_conversation_owner(job)))
    assert store.get_conversation_job(job_id)['status'] == 'succeeded'
    assert n == 0 and len(store.list_tasks(project['id'])) == 1
    # Redeliver the same source events with a different plan envelope. The
    # actual proposed work is unchanged, so a second Task must not be created.
    for message_id in job['mailbox_ids']:
        with pytest.raises(ValueError, match='acknowledged'):
            store.mailbox_service.redeliver(project['id'], message_id, 'duplicate notification')
    plan = plan.model_copy(update={'objective': 'The same feedback was redelivered'})
    n = 0
    assert store.enqueue_governance_jobs() == []
    assert n == 0 and len(store.list_tasks(project['id'])) == 1
    saved = store.create_task_plan(project['id'], plan)
    repair = store.create_task(project['id'], task_plan_id=saved['id'], proposed_task_ref='repair')
    assert repair['requirement_id'] == requirement_id
    assert len(store.list_tasks(project['id'])) == 2
    worker = next(w for w in store.agents.list_workers(project['id']) if w['role'] == 'leaf')
    message = store.agents.send(project['id'], worker['id'], 'Preserve the earlier interface')
    seen = []
    def repair_model(messages, *args, **kwargs):
        seen.append(json.dumps(messages, ensure_ascii=False))
        return response('column.complete', COMPLETE)
    WorkflowRuntime(store, store.registry, 'worker', AgentCore(store, store.registry, repair_model)).step(repair['id'])
    assert store.get_task(repair['id'])['status'] == 'done'
    detail = store.agents.inspect_worker(project['id'], worker['id'])
    assert len(detail['assignments']) == 2
    assert len({a['session_id'] for a in detail['assignments']}) == 1
    assert 'Preserve the earlier interface' in seen[0]
    assert first['id'] in seen[0]  # Previous work survives in this Worker's own context.
    consumed = next(m for m in detail['messages'] if m['id'] == message['id'])
    assert consumed['consumed_by_run_id'] and consumed['state'] == 'acknowledged'
    assert store.mailbox_service.deliveries(project['id'], message['id']) == []
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_project_mailbox WHERE event_type='agent.message'").fetchone()[0] == 0


def test_worker_message_is_not_delivered_to_main(store, tmp_path):
    wf = agent_workflow()
    project, task, runtime = setup(store, tmp_path, wf, [response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    worker = next(w for w in store.agents.list_workers(project['id']) if w['role'] == 'leaf')
    sent = store.agents.send(project['id'], worker['id'], 'next assignment input')
    for job_id in store.enqueue_governance_jobs():
        assert sent['id'] not in store.get_conversation_job(job_id)['mailbox_ids']
    assert store.agents.messages(project['id'], worker['id'])[0]['state'] == 'pending'


def test_retired_worker_and_closed_requirement_do_not_revive(store, tmp_path):
    wf = agent_workflow()
    project, task, runtime = setup(store, tmp_path, wf, [response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    finished = store.get_task(task['id'])
    store.agents.close_requirement(project['id'], finished['requirement_id'], 'completed')
    worker = next(w for w in store.agents.list_workers(project['id']) if w['role'] == 'leaf')
    assert worker['lifecycle'] == 'available'
    with pytest.raises(ValueError):
        store.agents.send(project['id'], worker['id'], 'late input')
    job_id = store.enqueue_governance_jobs()[0]
    job = store.get_conversation_job(job_id)
    mailbox = store.mailbox(project['id'], state='delivered')
    assert store.agents.turn_requirement(job, mailbox)['status'] == 'completed'


def test_completion_recovery_rechecks_workspace_after_interrupted_task_commit(store, tmp_path, monkeypatch):
    wf = agent_workflow()
    wf.column('work').acceptance_checks = [AcceptanceCheck(key='content', capability='project.command.run', arguments=command("from pathlib import Path; assert Path('delivery.txt').read_text() == 'ready'"))]
    _, task, runtime = setup(store, tmp_path, wf, [response('column.complete', COMPLETE)] * 6)
    artifact = tmp_path / 'project/delivery.txt'
    artifact.write_text('ready')
    original = store.finish_run
    crashed = False
    def finish(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise requests.ConnectionError('lost Task commit after accepted completion')
        return original(*args, **kwargs)
    monkeypatch.setattr(store, 'finish_run', finish)
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'recovering'
    artifact.write_text('changed')
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_tasks SET next_retry_at=NULL WHERE id=?', (task['id'],))
    with pytest.raises(RuntimeError):
        runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] != 'done'


def test_unknown_effect_blocks_new_task_and_main_direct_write(store, tmp_path):
    wf = agent_workflow()
    project, first, _ = setup(store, tmp_path, wf, [])
    second = create_planned_task(store, project['id'], 'second')
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET status='recovering',control_state='paused',failure_code='effect_outcome_unknown' WHERE id=?", (first['id'],))
        db.execute("UPDATE v1_scheduling_entries SET resources_json='[]' WHERE project_id=?", (project['id'],))
    assert store.claim_task(second['id'], 'new-worker') is None
    result = store.registry.dispatch('project.command.run', command("print('must not execute')"),
        CapabilityContext(project['id'], project, store, agent_run_id='main'))
    assert not result.ok
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE capability='project.command.run'").fetchone()[0] == 0


def test_repeated_await_without_progress_stops_before_model_budget(store, tmp_path):
    wf = agent_workflow()
    wf.column('work').wait_policy = TimerWaitPolicy(delay_seconds=1)
    _, task, runtime = setup(store, tmp_path, wf, [response('column.await', {'provider': 'timer'})] * 10)
    runtime.step(task['id'])
    runtime.reconcile_await(due(store))
    runtime.reconcile_await(due(store))
    with pytest.raises(ExecutionBudgetExceeded, match='without execution progress'):
        runtime.reconcile_await(due(store))
    with store.connect() as db:
        assert db.execute('SELECT model_calls FROM v1_agent_assignments').fetchone()[0] == 4
    assert store.get_task(task['id'])['control_state'] == 'paused'
