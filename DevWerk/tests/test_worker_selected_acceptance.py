import sys

from app.v1.agent import AgentCore
from app.v1.domain import AgentModelResponse, AgentToolCall, Transition
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
