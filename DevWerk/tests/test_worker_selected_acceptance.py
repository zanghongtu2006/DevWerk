import sys

import pytest

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityContext
from app.v1.domain import AcceptanceCheck, AgentModelResponse, AgentToolCall, ToolResult, Transition
from app.v1.execution_ledger import ledger_entry
from app.v1.runtime import WorkflowRuntime, _column_completion_contract
from app.v1.completion_protocol import parse_completion_submission
from app.v1.services.completion_admission import CompletionAdmissionService
from tests.helpers import agent_workflow, create_planned_task, workflow_plan


def test_behavior_column_rejects_completion_without_a_real_command():
    workflow = agent_workflow()
    contract = _column_completion_contract(workflow, workflow.column('work'), ['work'])
    submission = parse_completion_submission(
        {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'claimed done'}, contract, [])
    decision = CompletionAdmissionService().evaluate(submission, contract, [])
    assert not decision.accepted
    assert decision.rejection.code == 'behavior_evidence_required'


@pytest.mark.parametrize('argv', [['cmd', '/c', 'del', 'temporary.txt'], ['rm', 'temporary.txt']])
def test_behavior_column_rejects_cleanup_as_verification(argv):
    workflow = agent_workflow()
    contract = _column_completion_contract(workflow, workflow.column('work'), ['work'])
    ledger = [ledger_entry('agent', 'cleanup', 'project.command.run', 'process',
                           ToolResult(ok=True, capability='project.command.run', output={'returncode': 0}),
                           arguments={'argv': argv})]
    submission = parse_completion_submission(
        {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'claimed verified'},
        contract, ledger)
    decision = CompletionAdmissionService().evaluate(submission, contract, ledger)
    assert not decision.accepted
    assert decision.rejection.code == 'behavior_evidence_required'


def test_fixed_behavior_check_does_not_accept_runtime_behavior_feedback(store, tmp_path):
    project = store.create_project('fixed-check', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    column = workflow.column('work')
    column.executor.capabilities.extend(['project.command.run', 'task.feedback.record'])
    column.acceptance_checks = [AcceptanceCheck(
        key='fixed', capability='project.command.run',
        evidence_kind='behavior', purpose='Run the fixed behavior check',
        arguments={'argv': [sys.executable, '-c', 'print("fixed")']})]
    method = workflow_plan(workflow)
    method.task_contract.required_behavior_columns = ['work']
    saved = store.create_workflow_plan(project['id'], method)
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'fixed check')
    ctx = CapabilityContext(project_id=project['id'], project=project, store=store)
    with pytest.raises(ValueError, match='frozen acceptance check'):
        store.feedback.record(ctx, {
            'task_id': task['id'], 'dedupe_key': 'wrong-receipt',
            'responsible_column': 'work', 'description': 'Must use fixed check',
            'checks': [{'column': 'work', 'check_key': 'runtime.behavior'}],
        })


@pytest.mark.parametrize('write_after_command', [False, True])
def test_behavior_receipt_spans_attempts_but_not_later_file_writes(store, tmp_path, write_after_command):
    from tests.test_agent_recovery_and_fencing import begin_agent

    project = store.create_project('resumed behavior', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    workflow.column('work').executor.capabilities.append('project.command.run')
    method = workflow_plan(workflow)
    method.task_contract.required_behavior_columns = ['work']
    saved = store.create_workflow_plan(project['id'], method)
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'resumed work')
    owned = store.claim_task(task['id'], 'test-worker')
    run = store.begin_run(owned, {})
    prior = begin_agent(store, project, kind='column', task_id=task['id'],
                        column_run_id=run['id'], column_attempt_id=run['attempt_id'])
    success = ToolResult(ok=True, capability='project.command.run', output={'exit_code': 0})
    store.record_tool_invocation(agent_run_id=prior['id'], tool_call_id='verified',
        capability='project.command.run', arguments={'argv': ['mvn', 'test']},
        result=success.model_dump(mode='json'), ok=True)
    if write_after_command:
        written = ToolResult(ok=True, capability='project.files.write', output={'path': 'src/Changed.java'})
        store.record_tool_invocation(agent_run_id=prior['id'], tool_call_id='changed',
            capability='project.files.write', arguments={'path': 'src/Changed.java', 'content': 'changed'},
            result=written.model_dump(mode='json'), ok=True)
    resumed_attempt = 'resumed-attempt'
    with store.tx(immediate=True) as db:
        db.execute('''INSERT INTO v1_column_attempts
            (id,project_id,task_id,column_run_id,attempt_no,status,input_json,
             runtime_policy_revision,runtime_policy_hash,started_at,created_at)
            SELECT ?,project_id,task_id,column_run_id,attempt_no+1,'running',input_json,
                   runtime_policy_revision,runtime_policy_hash,started_at,created_at
            FROM v1_column_attempts WHERE id=?''', (resumed_attempt, run['attempt_id']))
        store.feedback.record_column_evidence(db, owned, run['id'], resumed_attempt)
        receipt = db.execute('''SELECT column_attempt_id,result_json FROM v1_acceptance_results
            WHERE task_id=? AND check_key='runtime.behavior' ''', (task['id'],)).fetchone()
    if write_after_command:
        assert receipt is None
    else:
        assert receipt['column_attempt_id'] == resumed_attempt
        assert prior['id'] in receipt['result_json']


def test_worker_selected_commands_complete_script_free_workflow(store, tmp_path):
    project = store.create_project('script-free', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    work = workflow.column('work')
    work.executor.capabilities = ['project.command.run']
    work.transitions = [Transition(outcome='success', target='accept'),
                        Transition(outcome='failure', target='failed')]
    accept = work.model_copy(deep=True)
    accept.key, accept.name = 'accept', 'Independent acceptance'
    accept.transitions = [Transition(outcome='success', target='done'),
                          Transition(outcome='failure', target='failed')]
    workflow.columns.append(accept)
    method = workflow_plan(workflow)
    method.task_contract.required_behavior_columns = ['work', 'accept']
    method.task_contract.acceptance_invalidated_by = ['work']
    saved_method = store.create_workflow_plan(project['id'], method)
    revision = store.publish_workflow(project['id'], workflow, saved_method['id'])
    assert revision['source_loop_key'] is None
    task = create_planned_task(store, project['id'], 'deliver and accept')
    replies = iter([
        AgentModelResponse(tool_calls=[AgentToolCall(id='work-test', name='project.command.run',
            arguments={'argv': [sys.executable, '-c', 'print("work passed")']})]),
        AgentModelResponse(tool_calls=[AgentToolCall(id='work-done', name='column.complete',
            arguments={'outcome': 'success', 'output': {'delivered': True}, 'summary': 'implemented'})]),
        AgentModelResponse(tool_calls=[AgentToolCall(id='accept-test', name='project.command.run',
            arguments={'argv': [sys.executable, '-c', 'print("accept passed")']})]),
        AgentModelResponse(tool_calls=[AgentToolCall(id='accept-done', name='column.complete',
            arguments={'outcome': 'success', 'output': {'delivered': True}, 'summary': 'accepted'})]),
    ])
    runtime = WorkflowRuntime(store, store.registry, 'test-worker',
                              AgentCore(store, store.registry, lambda *_a, **_kw: next(replies)))
    runtime.step(task['id'])
    with store.tx(immediate=True) as db:
        assert db.execute("SELECT count(*) FROM v1_acceptance_results WHERE task_id=?", (task['id'],)).fetchone()[0] == 1
        db.execute("DELETE FROM v1_acceptance_results WHERE task_id=?", (task['id'],))
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    with store.connect() as db:
        evidence = db.execute('SELECT check_key,ok FROM v1_acceptance_results WHERE task_id=?',
                              (task['id'],)).fetchall()
    assert [(row['check_key'], row['ok']) for row in evidence] == [
        ('runtime.behavior', 1), ('runtime.behavior', 1)]


def test_worker_selected_behavior_feedback_requires_fresh_acceptance(store, tmp_path):
    project = store.create_project('script-free-repair', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    work = workflow.column('work')
    work.executor.capabilities = ['project.command.run']
    work.transitions = [Transition(outcome='success', target='accept')]
    accept = work.model_copy(deep=True)
    accept.key, accept.name = 'accept', 'Independent acceptance'
    accept.executor.capabilities = ['project.command.run', 'task.feedback.record']
    accept.transitions = [Transition(outcome='success', target='done'),
                          Transition(outcome='rework', target='work', allows_unresolved_failures=True)]
    workflow.columns.append(accept)
    method = workflow_plan(workflow)
    method.task_contract.required_behavior_columns = ['work', 'accept']
    method.task_contract.acceptance_invalidated_by = ['work']
    method.task_contract.feedback_columns = ['accept']
    saved_method = store.create_workflow_plan(project['id'], method)
    store.publish_workflow(project['id'], workflow, saved_method['id'])
    task = create_planned_task(store, project['id'], 'repair and recheck')
    calls = [
        ('project.command.run', {'argv': [sys.executable, '-c', 'print("first implementation")']}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'first implementation'}),
        ('project.command.run', {'argv': [sys.executable, '-c', 'raise SystemExit(4)']}),
        ('task.feedback.record', {'task_id': task['id'], 'dedupe_key': 'failed-acceptance',
            'responsible_column': 'work', 'description': 'Acceptance command failed',
            'checks': [{'column': 'accept', 'check_key': 'runtime.behavior'}]}),
        ('column.complete', {'outcome': 'rework', 'output': {'delivered': False}, 'summary': 'repair required'}),
        ('project.command.run', {'argv': [sys.executable, '-c', 'print("repaired implementation")']}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'repaired'}),
        ('project.command.run', {'argv': [sys.executable, '-c', 'print("acceptance passed")']}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'accepted'}),
    ]
    responses = iter(AgentModelResponse(tool_calls=[AgentToolCall(
        id=f'call-{index}', name=name, arguments=args)]) for index, (name, args) in enumerate(calls))
    runtime = WorkflowRuntime(store, store.registry, 'test-worker',
                              AgentCore(store, store.registry, lambda *_a, **_kw: next(responses)))
    for _ in range(4):
        runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    feedback = store.feedback.list(project['id'], task['id'])
    assert len(feedback) == 1 and feedback[0]['state'] == 'resolved'
    assert feedback[0]['verification']
