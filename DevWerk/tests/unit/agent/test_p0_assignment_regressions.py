"""Fault injection against real SQLite, Runtime and subprocess effects.

Only provider responses and the asynchronous adapter are deterministic doubles.
"""
from __future__ import annotations

from tests.support.paths import REPO_ROOT

import asyncio
import json
import sqlite3
import sys
import threading
import time
from uuid import uuid4

import pytest
import requests

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityContext, CapabilityEntry
from app.v1.conversations.conversation import ConversationGateway
from app.v1.domain import (
    AcceptanceCheck,
    AgentModelResponse,
    AgentToolCall,
    EdgeCheck,
    PollWaitPolicy,
    TimerWaitPolicy,
    ToolResult,
)
from app.v1.execution.execution_control import (
    ExecutionBudgetExceeded,
    ExecutionControl,
    ExecutionOwnershipLost,
    ExecutionReplayUncertain,
)
from app.v1.policy import ExecutionLimits
from app.v1.protocols.tools.core.capability_protocol import ToolError
from app.v1.execution.runtime import WorkflowRuntime
from app.v1.store import V1Store
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
    wf.column('work').acceptance_checks = [AcceptanceCheck(
        key='result', capability='project.command.run',
        arguments=command("from pathlib import Path; assert Path('result.txt').read_text() == 'xx'"))]
    wf.column('work').transitions[0].required_checks = [EdgeCheck(column='work', check_key='result')]
    args = command("with open('result.txt','a') as f: f.write('x')")
    project, task, runtime = setup(store, tmp_path, wf, [response('project.command.run', args), response('column.await', {'provider': 'timer'}), response('project.command.run', args), response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    runtime.reconcile_await(due(store))
    assert (tmp_path / 'project/result.txt').read_text() == 'xx'
    assert store.get_task(task['id'])['status'] == 'done'
    with store.connect() as db:
        assignment = dict(db.execute('SELECT * FROM v1_agent_assignments').fetchone())
        assert (assignment['status'], assignment['resumes'], assignment['model_calls']) == ('completed', 2, 4)
        assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE capability='project.command.run'").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM v1_acceptance_results WHERE task_id=?", (task['id'],)).fetchone()[0] == 1
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
    from app.v1.execution.execution_control import ExecutionReplayUncertain
    wf = agent_workflow()
    wf.column('work').acceptance_checks = [AcceptanceCheck(
        key='delivery', capability='project.command.run',
        arguments=command("with open('acceptance-once.txt','a') as f: f.write('x')"))]
    wf.column('work').transitions[0].required_checks = [EdgeCheck(column='work', check_key='delivery')]
    _, task, runtime = setup(store, tmp_path, wf, [response('column.complete', COMPLETE)] * 2)
    # Exit the actual Worker process, bypassing all Python exception handlers,
    # after the command effect and before committing its terminal receipt.
    child = '''
import os, sys
from app.v1.store import V1Store
from app.v1.capabilities import build_core_registry
from app.v1.agent import AgentCore
from app.v1.execution.runtime import WorkflowRuntime
from tests.unit.agent.test_p0_assignment_regressions import response, COMPLETE
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
                             cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
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
    wf.column('work').transitions[0].required_checks = [EdgeCheck(column='work', check_key='delivery')]
    _, task, runtime = setup(store, tmp_path, wf, [response('project.command.run', command('raise SystemExit(1)')), response('project.command.run', command("from pathlib import Path; Path('delivery.txt').write_text('ready')")), response('column.complete', COMPLETE)])
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'


def test_noop_cannot_satisfy_frozen_delivery_check(store, tmp_path):
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    wf.column('work').acceptance_checks = [AcceptanceCheck(key='delivery', capability='project.files.read', arguments={'path': 'missing.txt'})]
    wf.column('work').transitions[0].required_checks = [EdgeCheck(column='work', check_key='delivery')]
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
        return ToolResult(ok=False, capability='test.control', error=ToolError(type='Rejected', message='reject'))
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
    from app.v1.execution.execution_control import ExecutionControl
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
    wf.column('work').transitions[0].required_checks = [EdgeCheck(column='work', check_key='content')]
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


def test_explicit_retry_after_assignment_budget_exhaustion_gets_new_run_and_budget(store, tmp_path):
    wf = agent_workflow()
    project, task, runtime = setup(store, tmp_path, wf, [])
    claimed = store.claim_task(task['id'], 'worker-1')
    run = store.begin_run(claimed, {})
    old = store.agents.assign(claimed, run, wf.column('work'))
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_agent_assignments SET status='blocked',model_calls=100,result_json=? WHERE id=?",
                   (json.dumps({'code': 'execution_budget_exceeded'}), old['id']))
        db.execute("UPDATE v1_agent_instances SET active_assignment_id=NULL WHERE id=?", (old['agent_instance_id'],))
        db.execute("UPDATE v1_column_attempts SET status='interrupted' WHERE column_run_id=?", (run['id'],))
        db.execute("UPDATE v1_column_runs SET status='interrupted',error='Assignment budget exhausted' WHERE id=?", (run['id'],))
        db.execute("UPDATE v1_tasks SET status='recovering',control_state='paused',error='Assignment budget exhausted',failure_code='execution_budget_exceeded',"
                   "lease_owner=NULL,lease_until=NULL WHERE id=?", (task['id'],))
    first_input = store.agents.send(project['id'], old['agent_instance_id'],
                                    'Keep the failed check detail', assignment_id=old['id'])
    second_input = store.agents.send(project['id'], old['agent_instance_id'],
                                     'Run the current browser check', assignment_id=old['id'])

    retried = store.retry_task(task['id'], column_key='work', clear_context=True)
    assert retried['status'] == 'pending'
    claimed_again = store.claim_task(task['id'], 'worker-2')
    retry_failure = runtime._input_for(claimed_again, wf, wf.column('work'))['retry_failure']
    assert retry_failure['column_run_id'] == run['id']
    assert retry_failure['failure_code'] == 'execution_budget_exceeded'
    assert retry_failure['runtime_error'] == 'Assignment budget exhausted'
    new_run = store.begin_run(claimed_again, {})
    fresh = store.agents.assign(claimed_again, new_run, wf.column('work'))
    assert new_run['id'] != run['id']
    assert fresh['id'] != old['id']
    assert fresh['agent_instance_id'] == old['agent_instance_id']
    assert fresh['model_calls'] == 0
    pending = {m['id']: m for m in store.agents.messages(project['id'], fresh['agent_instance_id'])}
    assert {pending[first_input['id']]['assignment_id'], pending[second_input['id']]['assignment_id']} == {fresh['id']}
    with pytest.raises(ValueError, match='superseded'):
        store.agents.send(project['id'], fresh['agent_instance_id'], 'stale input', assignment_id=old['id'])
    agent = store.begin_agent_run(project_id=project['id'], instruction_revision=1,
        instruction_snapshot='test', context_snapshot={}, capabilities=[],
        platform_policy=store.latest_platform_policy(), runtime_policy=store.policy,
        kind='column', task_id=task['id'], column_run_id=new_run['id'],
        agent_session_id=fresh['session_id'])
    consumed = store.agents.consume_messages(fresh, agent['id'])
    assert {item['message_id'] for item in consumed['payload']} == {first_input['id'], second_input['id']}
    assert store.agents.consume_messages(fresh, agent['id']) == []
    with store.connect() as db:
        assert db.execute('SELECT status FROM v1_column_runs WHERE id=?', (run['id'],)).fetchone()[0] == 'interrupted'


# ---------------------------------------------------------------------------
# Agent recovery and fencing
# ---------------------------------------------------------------------------

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


@pytest.mark.parametrize('capability', ['project.command.run', 'project.files.write'])
def test_retry_reconciles_only_interrupted_command_without_reexecuting(store, tmp_path, capability):
    project = store.create_project('interrupted command', '', str(tmp_path / 'project'))
    wf = agent_workflow()
    wf.column('work').executor.capabilities.append('project.command.run')
    publish_planned_workflow(store, project['id'], wf)
    task = create_planned_task(store, project['id'], 'interrupted command')
    task = store.claim_task(task['id'], 'old-worker')
    run = store.begin_run(task, {})
    old = begin_agent(store, project, kind='column', task_id=task['id'], column_run_id=run['id'])
    args = (command("with open('once.txt','a') as f: f.write('x')")
            if capability == 'project.command.run' else {'path': 'once.txt', 'content': 'x'})
    key = store.agents.prepare_operations(project['id'], run['id'], old['id'], 1,
        [AgentToolCall(id='old-call', name=capability, arguments=args)])[0]
    store.start_execution_receipt(project['id'], key, capability, args)
    store.finish_agent_run(old['id'], 'failed', '', 'lost command exit', 1, 1)
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_column_runs SET status='interrupted' WHERE id=?", (run['id'],))
        db.execute("UPDATE v1_tasks SET status='recovering',control_state='paused',"
                   "failure_code='effect_outcome_unknown',lease_owner=NULL,lease_until=NULL WHERE id=?", (task['id'],))
    if capability == 'project.files.write':
        with pytest.raises(ValueError, match='Unreconciled side effect'):
            store.retry_task(task['id'])
        assert store.get_task(task['id'])['status'] == 'recovering'
        expected_status = 'started'
    else:
        retried = store.retry_task(task['id'])
        assert retried['status'] == 'pending'
        assert retried['control_state'] == 'active'
        expected_status = 'failed'
        result = store.registry.dispatch(capability, args,
            CapabilityContext(project['id'], project, store, execution_key=key))
        assert not result.ok and result.error['type'] == 'PriorExecutionFailed'
        assert 'partial' in result.error['message']
        assert store.agents.operation(key)['result_json'] is None
        with store.connect() as db:
            event = db.execute("SELECT data_json FROM v1_events WHERE task_id=? AND type='task.retry_requested' "
                               "ORDER BY id DESC LIMIT 1", (task['id'],)).fetchone()
        assert key in json.loads(event[0])['interrupted_command_receipts']
    with store.connect() as db:
        receipt = db.execute('SELECT status FROM v1_execution_receipts WHERE execution_key=?', (key,)).fetchone()
    assert receipt['status'] == expected_status
    assert not (tmp_path / 'project/once.txt').exists()


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
    assert 'Retain this design decision' not in seen[0]  # archived, not active Assignment dialogue
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
    pages, after = [], 0
    while page := store.agents.context_page(project['id'], worker['id'], after=after, limit=50):
        assert len(json.dumps(page,ensure_ascii=False).encode('utf8')) < 14000
        pages.extend(page)
        after = page[-1]['id']
    assert len(pages) >= 20
    assert sum('context-' in p['content'] for p in pages) == 20


# ---------------------------------------------------------------------------
# Persistent Agent identity
# ---------------------------------------------------------------------------

def assignment(store, project, workflow, *, worker_key="developer", requirement=None):
    task = create_planned_task(store, project["id"], "work " + uuid4().hex)
    if requirement:
        store.agents.attach_task(task["id"], requirement["id"])
    task = store.claim_task(task["id"], "test-worker")
    run = store.begin_run(task, {})
    return task, run, store.agents.assign(task, run, workflow.column("work"), worker_key=worker_key)


def test_worker_idle_then_new_assignment_preserves_identity_and_context(store, tmp_path):
    project = store.create_project("delivery", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task, run, first = assignment(store, project, workflow)
    store.agents.finish(first, "completed", {"summary": "first output"})
    evidence = store.prepare_terminal_evidence(task, run["id"], "done", {}, None)
    store.finish_run(task, run["id"], {}, "success", "done", terminal="done", terminal_artifact=evidence)
    _, _, second = assignment(store, project, workflow)
    assert first["id"] != second["id"]
    assert first["agent_instance_id"] == second["agent_instance_id"]
    assert first["session_id"] == second["session_id"]


def test_one_worker_has_only_one_active_assignment(store, tmp_path):
    project = store.create_project("delivery", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    _, _, first = assignment(store, project, workflow)
    worker = store.agents.get_worker(project["id"], first["agent_instance_id"])
    assert worker["active_assignment_id"] == first["id"]
    with pytest.raises(ExecutionOwnershipLost):
        store.agents.assert_owner({**first, "generation": first["generation"] + 1})


def test_durable_worker_message_is_not_claimed_as_consumed_on_send(store, tmp_path):
    project = store.create_project("delivery", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    _, _, active = assignment(store, project, workflow)
    message = store.agents.send(project["id"], active["agent_instance_id"], "Use the new label", assignment_id=active["id"])
    assert message["state"] == "pending"
    assert message["consumed_by_run_id"] is None
    assert store.agents.messages(project["id"], active["agent_instance_id"])[0]["id"] == message["id"]


@pytest.mark.parametrize('status', ['completed', 'failed', 'cancelled'])
def test_late_assignment_input_settles_without_fabricating_consumption(store, tmp_path, status):
    project = store.create_project('late input', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    _, _, active = assignment(store, project, workflow)
    worker_id = active['agent_instance_id']
    scoped = store.agents.send(project['id'], worker_id, 'arrived during final model call', assignment_id=active['id'])
    future = store.agents.send(project['id'], worker_id, 'input for the next assignment')
    store.agents.finish(active, status)
    messages = {m['id']: m for m in store.agents.messages(project['id'], worker_id)}
    assert messages[scoped['id']]['state'] == 'failed'
    assert messages[scoped['id']]['consumed_by_run_id'] is None
    assert 'before message consumption' in messages[scoped['id']]['last_error']
    assert messages[future['id']]['state'] == 'pending'
    with pytest.raises(ValueError, match='Assignment has ended'):
        store.agents.send(project['id'], worker_id, 'too late', assignment_id=active['id'])
    with pytest.raises(KeyError):
        store.mailbox_service.redeliver(project['id'], scoped['id'], 'retry')


@pytest.mark.parametrize('close_requirement', [False, True])
def test_retirement_settles_unconsumed_worker_input(store, tmp_path, close_requirement):
    project = store.create_project('retirement', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    task, run, active = assignment(store, project, workflow)
    worker_id = active['agent_instance_id']
    message = store.agents.send(project['id'], worker_id, 'future input')
    evidence = store.prepare_terminal_evidence(task, run['id'], 'done', {}, None)
    store.finish_run(task, run['id'], {}, 'success', 'done', terminal='done', terminal_artifact=evidence)
    if close_requirement:
        store.agents.close_requirement(project['id'], active['requirement_id'], 'completed')
    else:
        store.agents.set_lifecycle(project['id'], worker_id, 'retired')
    result = store.agents.messages(project['id'], worker_id)[0]
    assert result['state'] == 'failed' and result['consumed_by_run_id'] is None
    assert store.agents.get_worker(project['id'], worker_id)['lifecycle'] == ('available' if close_requirement else 'retired')
    with pytest.raises(KeyError):
        store.mailbox_service.redeliver(project['id'], message['id'], 'retry')
