from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import CapabilityContext, build_core_registry
from app.v1.contracts import ContractError
from app.v1.conversation import ConversationGateway
from app.v1.domain import (
    AgentExecutor,
    AgentModelResponse,
    AgentToolCall,
    CapabilityStep,
    ToolResult,
)
from app.v1.execution_ledger import ledger_entry, repeats_failed_operation
from app.v1.runtime import RuntimeExecutionError, WorkflowRuntime, _failure_message
from tests.helpers import (
    agent_workflow,
    create_planned_task,
    publish_planned_workflow,
    sequence_workflow,
    task_plan,
    workflow_plan,
)
from tests.test_conversation_intent_contract import turn
from tests.test_p0_assignment_regressions import begin_agent

def failed_task_with_user_grant(store, tmp_path):
    project = store.create_project('supervision', '', str(tmp_path / 'project'))
    user_job, ctx = turn(store, project, '交付这个项目')
    grant = store.registry.dispatch('conversation.execution.authorize',
        {'request':'begin','scope_summary':'交付这个项目'}, ctx)
    assert grant.ok, grant.error
    requirement = grant.output['requirement']
    store.finish_conversation_job(user_job['id'], None, ctx.agent_run_id, {})
    publish_planned_workflow(store, project['id'], agent_workflow())
    task = create_planned_task(store, project['id'], 'deliver')
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET requirement_id=?,requirement_revision=?,status='failed',error='test failure' WHERE id=?",
                   (requirement['id'], requirement['revision'], task['id']))
        store._mailbox(db, project['id'], 'task.failed', task['id'], None,
                       {'error':'test failure'})
    return project, requirement, task


def blocked_task_with_user_grant(store, tmp_path):
    project = store.create_project('blocked-supervision', '', str(tmp_path / 'blocked-project'))
    user_job, ctx = turn(store, project, '交付这个项目')
    grant = store.registry.dispatch('conversation.execution.authorize',
        {'request':'begin','scope_summary':'交付这个项目'}, ctx)
    assert grant.ok, grant.error
    requirement = grant.output['requirement']
    store.finish_conversation_job(user_job['id'], None, ctx.agent_run_id, {})
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    task = create_planned_task(store, project['id'], 'deliver')
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_tasks SET requirement_id=?,requirement_revision=? WHERE id=?',
                   (requirement['id'],requirement['revision'],task['id']))
    claimed = store.claim_task(task['id'], 'blocked-worker')
    column_run = store.begin_run(claimed, {})
    assignment = store.agents.assign(claimed, column_run, workflow.column('work'))
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_agent_assignments SET status='blocked',result_json=? WHERE id=?",
                   ('{"code":"execution_budget_exceeded"}', assignment['id']))
        db.execute('UPDATE v1_agent_instances SET active_assignment_id=NULL WHERE id=?',
                   (assignment['agent_instance_id'],))
        db.execute("UPDATE v1_column_attempts SET status='interrupted' WHERE column_run_id=?", (column_run['id'],))
        db.execute("UPDATE v1_column_runs SET status='interrupted' WHERE id=?", (column_run['id'],))
        db.execute("UPDATE v1_tasks SET status='recovering',control_state='paused',"
                   "failure_code='execution_budget_exceeded',lease_owner=NULL,lease_until=NULL WHERE id=?",
                   (task['id'],))
        store._mailbox(db, project['id'], 'task.runtime_blocked', task['id'], column_run['id'],
                       {'error':'ExecutionBudgetExceeded'})
    return project, task, assignment


def claim_event(store, project):
    event_id = store.enqueue_governance_jobs()[0]
    job = store.claim_conversation_job(event_id, 'supervision-test')
    mailbox = [item for item in store.mailbox(project['id'], state='received')
               if item['id'] in job['mailbox_ids']]
    return job, mailbox


def test_supervision_grant_is_same_task_only_and_never_expands_scope(store, tmp_path):
    project, requirement, task = failed_task_with_user_grant(store, tmp_path)
    job, mailbox = claim_event(store, project)
    contract = store.bind_supervision_job(job['id'], mailbox)
    assert contract['phase'] == 'supervision'
    assert contract['supervised_task_id'] == task['id']
    main = store.agents.main(project['id'])
    run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
    ctx = CapabilityContext(project['id'], project, store, agent_run_id=run['id'],
                            agent_instance_id=main['id'])
    forbidden = store.registry.dispatch('project.files.write',
        {'path':'surprise.txt','content':'bad'}, ctx)
    assert not forbidden.ok
    assert forbidden.error['type'] == 'SupervisionMutationDisabled'
    assert not (tmp_path / 'project' / 'surprise.txt').exists()
    cross_task = store.registry.dispatch('task.reopen', {'task_id':'tsk_other'}, ctx)
    assert not cross_task.ok
    assert cross_task.error['type'] == 'SupervisionTaskMismatch'
    assert store.registry.dispatch('task.inspect', {'task_id':task['id']}, ctx).ok
    assert store.intents.denial(ctx, 'task.reopen', {'task_id':task['id']}, 'control') is None

    store.agents.close_requirement(project['id'], requirement['id'], 'cancelled')
    assert store.intents.denial(ctx, 'task.reopen', {'task_id':task['id']}, 'control') == 'SupervisionScopeExpired'
    assert store.intents.denial(ctx, 'workflow.plan.save', {'plan':{}}, 'control') == 'SupervisionScopeExpired'


def test_supervision_repairs_failed_task_with_same_scope_successor(store, tmp_path):
    from app.v1.conversation_report import render_report
    import json

    project, _, task = failed_task_with_user_grant(store, tmp_path)
    job, mailbox = claim_event(store, project)
    assert store.bind_supervision_job(job['id'], mailbox)
    run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
    ctx = CapabilityContext(project['id'], project, store, agent_run_id=run['id'],
                            agent_instance_id=store.agents.main(project['id'])['id'])
    unrelated = store.registry.dispatch('task.create', {'task_plan_id':'other', 'proposed_task_ref':'other'}, ctx)
    assert not unrelated.ok and unrelated.error['type'] == 'SupervisionMutationDisabled'

    corrected = agent_workflow(instruction='Complete the corrected task.')
    plan = store.registry.dispatch('workflow.plan.save',
        {'plan':workflow_plan(corrected).model_dump(mode='json')}, ctx)
    assert plan.ok, plan.error
    revision = store.registry.dispatch('workflow.publish',
        {'workflow_plan_id':plan.output['id'], 'workflow':corrected.model_dump(mode='json')}, ctx)
    assert revision.ok, revision.error
    repair = task_plan(revision.output['id'], corrected, task_ref=task['proposed_task_ref'])
    repair.repair_of_task_id = task['id']
    unrelated_plan = task_plan(revision.output['id'], corrected, task_ref='unrelated')
    unrelated_plan.repair_of_task_id = task['id']
    rejected_plan = store.registry.dispatch('task.plan.save',
        {'plan':unrelated_plan.model_dump(mode='json')}, ctx)
    assert not rejected_plan.ok and rejected_plan.error['type'] == 'SupervisionRepairPlanMismatch'
    saved = store.registry.dispatch('task.plan.save', {'plan':repair.model_dump(mode='json')}, ctx)
    assert saved.ok, saved.error
    wrong_target = store.registry.dispatch('task.successor',
        {'task_id':'tsk_other', 'task_plan_id':saved.output['id']}, ctx)
    assert not wrong_target.ok and wrong_target.error['type'] == 'SupervisionTaskMismatch'
    successor = store.registry.dispatch('task.successor',
        {'task_id':task['id'], 'task_plan_id':saved.output['id']}, ctx)
    assert successor.ok, successor.error
    assert successor.output['rerun_of_task_id'] == task['id']
    store.record_tool_invocation(agent_run_id=run['id'], tool_call_id='successor',
        capability='task.successor', arguments={'task_id':task['id'], 'task_plan_id':saved.output['id']},
        result=successor.model_dump(mode='json'), ok=True)
    rendered, _ = render_report(store, project['id'], run['id'],
        json.dumps({'mode':'work_result', 'tool_call_ids':['successor']}))
    assert '待执行' in rendered
    assert store.get_task(successor.output['id'])['status'] == 'pending'
    repeated = store.registry.dispatch('task.successor',
        {'task_id':task['id'], 'task_plan_id':saved.output['id']}, ctx)
    assert not repeated.ok and repeated.error['type'] == 'SupervisionSuccessorAlreadyQueued'


def test_supervision_agent_queues_successor_and_reports_its_state(store, tmp_path):
    project, _, task = failed_task_with_user_grant(store, tmp_path)
    job, _ = claim_event(store, project)
    corrected = agent_workflow(instruction='Complete the corrected task.')
    calls = 0

    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            name = 'workflow.plan.save'
            args = {'plan':workflow_plan(corrected).model_dump(mode='json')}
        elif calls == 2:
            name = 'workflow.publish'
            args = {'workflow_plan_id':store.list_workflow_plans(project['id'], 1)[0]['id'],
                    'workflow':corrected.model_dump(mode='json')}
        elif calls == 3:
            name = 'task.plan.save'
            repair = task_plan(store.get_workflow(project['id'])['id'], corrected,
                               task_ref=task['proposed_task_ref'])
            repair.repair_of_task_id = task['id']
            args = {'plan':repair.model_dump(mode='json')}
        elif calls == 4:
            name = 'task.successor'
            args = {'task_id':task['id'],
                    'task_plan_id':store.list_task_plans(project['id'], 1)[0]['id']}
        else:
            raise AssertionError('a queued successor must end the notification turn')
        return AgentModelResponse(tool_calls=[AgentToolCall(id=f'repair-{calls}', name=name, arguments=args)])

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))
    gateway._process(job)
    result = store.get_conversation_job(job['id'])
    assert result['status'] == 'succeeded'
    assert calls == 4
    assert len(result['result']['task_ids']) == 1
    successor = store.get_task(result['result']['task_ids'][0])
    assert successor['rerun_of_task_id'] == task['id']
    assert successor['status'] == 'pending'
    assert '修复任务' in result['result']['reply']


def test_supervision_wakes_project_agent_but_not_on_closed_scope(store, tmp_path):
    project, requirement, task = failed_task_with_user_grant(store, tmp_path)
    job, _ = claim_event(store, project)
    calls = []

    def model(messages, tools, **kwargs):
        calls.append({item['function']['name'] for item in tools})
        if len(calls) == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='inspect-failure', name='task.inspect', arguments={'task_id':task['id']})])
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id='finish-review', name='conversation.reply',
            arguments={'mode':'discussion','message':'I inspected the failed Task.'})])

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))
    gateway._process(job)
    finished = store.get_conversation_job(job['id'])
    assert finished['status'] == 'succeeded'
    assert finished['agent_run_id']
    assert len(calls) == 2
    assert 'conversation.execution.authorize' in calls[0]
    assert finished['result']['reply'].startswith('deliver：失败')
    assert 'test failure' in finished['result']['reply']
    assert all(item['state'] == 'acknowledged' for item in store.mailbox(project['id'], state='acknowledged'))

    with store.tx(immediate=True) as db:
        store._mailbox(db, project['id'], 'task.failed', task['id'], None,
                       {'error':'old failure'})
    event, _ = claim_event(store, project)
    store.agents.close_requirement(project['id'], requirement['id'], 'cancelled')
    gateway._process(event)
    assert len(calls) == 2
    assert store.get_conversation_job(event['id'])['result']['llm_used'] is False


def test_supervision_reopens_only_failed_task_under_prior_grant(store, tmp_path):
    project, _, task = failed_task_with_user_grant(store, tmp_path)
    job, _ = claim_event(store, project)
    calls = 0

    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='read-task',name='task.inspect',arguments={'task_id':task['id']})])
        if calls == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='reopen-task',name='task.reopen',arguments={'task_id':task['id']})])
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id='report',name='conversation.reply',
            arguments={'mode':'work_result','tool_call_ids':['reopen-task']})])

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))
    gateway._process(job)
    result = store.get_conversation_job(job['id'])
    assert result['status'] == 'succeeded'
    assert calls == 2
    assert store.get_task(task['id'])['status'] == 'pending'
    assert '待执行' in result['result']['reply']
    assert [item['capability'] for item in store.tool_invocations(project['id'], result['agent_run_id'])] == [
        'task.inspect','task.reopen']


def test_supervision_can_recover_only_once_per_notification(store, tmp_path):
    project, _, task = failed_task_with_user_grant(store, tmp_path)
    job, mailbox = claim_event(store, project)
    assert store.bind_supervision_job(job['id'], mailbox)
    run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
    ctx = CapabilityContext(project['id'], project, store, agent_run_id=run['id'])

    wrong_action = store.registry.dispatch('task.retry', {'task_id':task['id']}, ctx)
    assert not wrong_action.ok
    assert wrong_action.error['type'] == 'SupervisionTaskNotRecoverable'
    reopened = store.registry.dispatch('task.reopen', {'task_id':task['id']}, ctx)
    assert reopened.ok, reopened.error
    assert store.intents.denial(ctx, 'workflow.plan.save', {'plan':{}}, 'control') == 'SupervisionTaskNotFailed: Workflow repair requires a failed Task'
    store.record_tool_invocation(agent_run_id=run['id'], tool_call_id='first-reopen',
        capability='task.reopen', arguments={'task_id':task['id']},
        result=reopened.model_dump(mode='json'), ok=True)

    repeated = store.registry.dispatch('task.retry',
        {'task_id':task['id'], 'column_key':'work'}, ctx)
    assert not repeated.ok
    assert repeated.error['type'] == 'SupervisionRecoveryAlreadyRequested'
    with pytest.raises(ValueError, match='already pending'):
        store.retry_task(task['id'], column_key='work')
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_events WHERE task_id=? AND type='task.reopened'",
            (task['id'],)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM v1_events WHERE task_id=? AND type='task.retry_requested'",
            (task['id'],)).fetchone()[0] == 0


def test_supervision_hands_diagnosis_to_blocked_worker_then_stops_after_retry(store, tmp_path):
    project, task, old_assignment = blocked_task_with_user_grant(store, tmp_path)
    job, mailbox = claim_event(store, project)
    assert store.bind_supervision_job(job['id'], mailbox)
    calls = 0

    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='send-diagnosis', name='agent.worker.send', arguments={
                    'worker_id':old_assignment['agent_instance_id'],
                    'assignment_id':old_assignment['id'],
                    'content':'Inspect the failed lockout receipt before changing the code.'})])
        if calls == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='retry-after-input', name='task.retry', arguments={
                    'task_id':task['id'], 'column_key':'work', 'clear_context':False})])
        raise AssertionError('a successful supervision retry must end the notification turn')

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))
    gateway._process(job)
    assert calls == 2
    result = store.get_conversation_job(job['id'])
    assert result['status'] == 'succeeded'
    assert result['result']['business_outcome'] == 'completed'
    assert store.get_task(task['id'])['status'] == 'pending'
    messages = store.agents.messages(project['id'], old_assignment['agent_instance_id'])
    assert len(messages) == 1
    assert messages[0]['state'] == 'pending'
    claimed = store.claim_task(task['id'], 'new-worker')
    new_run = store.begin_run(claimed, {})
    fresh = store.agents.assign(claimed, new_run, agent_workflow().column('work'))
    assert fresh['id'] != old_assignment['id']
    assert fresh['agent_instance_id'] == old_assignment['agent_instance_id']
    queued = store.agents.messages(project['id'], fresh['agent_instance_id'])
    assert queued[0]['assignment_id'] == fresh['id']


def test_user_worker_input_to_pending_task_ends_turn_after_handoff(store, tmp_path):
    from app.v1.agent_models import AgentRunSpec
    from app.v1.agent_runner import AgentExecutionRunner
    from app.v1.execution_ledger import ledger_entry
    from app.v1.domain import ToolResult

    project, task, assignment = blocked_task_with_user_grant(store, tmp_path)
    runner = AgentExecutionRunner(store=store, registry=store.registry,
                                  model_complete=lambda *_a, **_kw: None,
                                  runtime_policy=store.policy, platform_policy=None)
    entry = ledger_entry('arun_user', 'send-feedback', 'agent.worker.send', 'control',
                         ToolResult(ok=True, capability='agent.worker.send', output={'id': 'input'}),
                         arguments={'worker_id': assignment['agent_instance_id'],
                                    'content': 'Repair the test and verify it.'})
    user_turn = AgentRunSpec(kind='conversation', project=project, instruction='',
                             instruction_revision=1, context={}, capability_ids=[],
                             user_initiated=True)
    assert not runner._active_worker_handoff(user_turn, [entry])
    store.retry_task(task['id'], column_key='work')
    assert store.get_task(task['id'])['status'] == 'pending'
    assert runner._active_worker_handoff(user_turn, [entry])
    assert not runner._active_worker_handoff(
        AgentRunSpec(kind='conversation', project=project, instruction='',
                     instruction_revision=1, context={}, capability_ids=[],
                     user_initiated=False, supervision_turn=True), [entry])


def test_supervision_cannot_finish_as_recovered_after_only_queuing_worker_input(store, tmp_path):
    project, task, assignment = blocked_task_with_user_grant(store, tmp_path)
    job, mailbox = claim_event(store, project)
    assert store.bind_supervision_job(job['id'], mailbox)
    run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
    ctx = CapabilityContext(project['id'], project, store, agent_run_id=run['id'],
                            agent_instance_id=store.agents.main(project['id'])['id'])
    sent = store.registry.dispatch('agent.worker.send', {
        'worker_id': assignment['agent_instance_id'], 'assignment_id': assignment['id'],
        'content': 'Inspect the runtime failure before continuing.'}, ctx)
    assert sent.ok, sent.error
    store.record_tool_invocation(agent_run_id=run['id'], tool_call_id='queued-input',
        capability='agent.worker.send', arguments={}, result=sent.model_dump(mode='json'), ok=True)
    premature = store.registry.dispatch('conversation.reply', {
        'mode': 'work_result', 'tool_call_ids': ['queued-input']}, ctx)
    assert not premature.ok
    assert 'SupervisionRecoveryNotQueued' in str(premature.error)
    assert store.get_task(task['id'])['status'] == 'recovering'
    retried = store.registry.dispatch('task.retry', {'task_id': task['id']}, ctx)
    assert retried.ok, retried.error
    store.record_tool_invocation(agent_run_id=run['id'], tool_call_id='queued-retry',
        capability='task.retry', arguments={'task_id': task['id']},
        result=retried.model_dump(mode='json'), ok=True)
    finished = store.registry.dispatch('conversation.reply', {
        'mode': 'work_result', 'tool_call_ids': ['queued-retry']}, ctx)
    assert finished.ok, finished.error
    assert store.get_task(task['id'])['status'] == 'pending'


def test_user_recovery_rejects_resume_boundary_before_it_can_block_retry(store, tmp_path):
    project, task, assignment = blocked_task_with_user_grant(store, tmp_path)
    job, ctx = turn(store, project, f'Continue the failed Task {task["id"]}')
    premature = store.registry.dispatch('agent.worker.send', {
        'worker_id': assignment['agent_instance_id'], 'assignment_id': assignment['id'],
        'content': 'Inspect the prior failure.'}, ctx)
    assert not premature.ok
    assert 'RecoveryRequiresExecutionAuthorization' in str(premature.error)
    wrong_boundary = store.registry.dispatch('conversation.task.control.authorize', {
        'action': 'task.resume', 'task_id': task['id']}, ctx)
    assert not wrong_boundary.ok
    assert 'RecoveringTaskRequiresRetry' in str(wrong_boundary.error)
    assert store.intents.contract(job)['phase'] == 'unresolved'
    with pytest.raises(ValueError, match='RecoveringTaskRequiresRetry'):
        store.resume_task(task['id'])
    assert store.get_task(task['id'])['control_state'] == 'paused'
    authorized = store.registry.dispatch('conversation.execution.authorize', {
        'request': 'continue', 'requirement_id': store.get_task(task['id'])['requirement_id']}, ctx)
    assert authorized.ok, authorized.error
    retried = store.registry.dispatch('task.retry', {'task_id': task['id']}, ctx)
    assert retried.ok, retried.error
    assert store.get_task(task['id'])['status'] == 'pending'


def test_failed_task_recovery_does_not_bind_resume_control_turn(store, tmp_path):
    project, requirement, task = failed_task_with_user_grant(store, tmp_path)
    job, ctx = turn(store, project, f'继续修复 Task {task["id"]} 的交付失败')

    wrong_boundary = store.registry.dispatch('conversation.task.control.authorize', {
        'action': 'task.resume', 'task_id': task['id']}, ctx)
    assert not wrong_boundary.ok
    assert 'FailedTaskRequiresReopen' in str(wrong_boundary.error)
    assert store.intents.contract(job)['phase'] == 'unresolved'

    state = store.intents.context(job)
    direct_boundary = store.registry.dispatch('conversation.turn.resolve', {
        'source_message_id': job['user_message_id'],
        'based_on_intent_revision': state['revision'],
        'user_evidence': [{'message_id': job['user_message_id']}],
        'act': 'control', 'control_capability': 'task.resume',
        'control_task_id': task['id']}, ctx)
    assert not direct_boundary.ok
    assert 'FailedTaskRequiresReopen' in str(direct_boundary.error)
    assert store.intents.contract(job)['phase'] == 'unresolved'

    unauthorized = store.registry.dispatch('task.reopen', {'task_id': task['id']}, ctx)
    assert not unauthorized.ok
    assert 'RecoveryRequiresExecutionAuthorization' in str(unauthorized.error)
    authorized = store.registry.dispatch('conversation.execution.authorize', {
        'request': 'continue', 'requirement_id': requirement['id']}, ctx)
    assert authorized.ok, authorized.error
    reopened = store.registry.dispatch('task.reopen', {'task_id': task['id']}, ctx)
    assert reopened.ok, reopened.error
    assert store.get_task(task['id'])['status'] == 'pending'


def test_successful_continue_authorization_allows_same_retry_arguments():
    retry_args = {'task_id': 'task-1', 'column_key': 'backend'}
    denied = ledger_entry('run', 'premature', 'task.retry', 'control',
        ToolResult(ok=False, capability='task.retry',
                   error={'type': 'RecoveryRequiresExecutionAuthorization', 'message': 'authorize first'}),
        arguments=retry_args)
    inspected = ledger_entry('run', 'inspect', 'task.inspect', 'read',
        ToolResult(ok=True, capability='task.inspect', output={}), arguments={'task_id': 'task-1'})
    authorized = ledger_entry('run', 'authorize', 'conversation.execution.authorize', 'session_control',
        ToolResult(ok=True, capability='conversation.execution.authorize', output={'grant_id': 'grant-1'}),
        arguments={'request': 'continue'})
    assert repeats_failed_operation('task.retry', retry_args, [denied, inspected])
    assert not repeats_failed_operation('task.retry', retry_args, [denied, inspected, authorized])


def test_supervision_model_failure_still_notifies_original_task_failure(store, tmp_path):
    project, _, task = failed_task_with_user_grant(store, tmp_path)
    job, _ = claim_event(store, project)
    gateway = ConversationGateway(store, store.registry, agent_core=AgentCore(
        store, store.registry, lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('model failed'))))
    with pytest.raises(RuntimeError, match='model failed'):
        gateway._process(job)
    failed = store.get_conversation_job(job['id'])
    assert failed['status'] == 'failed'
    notifications = [item for item in store.messages(project['id'])
                     if item['role'] == 'assistant' and item['meta'].get('job_id') == job['id']]
    assert len(notifications) == 1
    assert 'test failure' in notifications[0]['content']
    assert '自动监督未完成' in notifications[0]['content']
    assert 'model failed' not in notifications[0]['content']


def test_supervision_without_recovery_has_smaller_run_budget_and_preserves_failure(store, tmp_path):
    project, _, task = failed_task_with_user_grant(store, tmp_path)
    job, _ = claim_event(store, project)
    calls = 0

    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id=f'inspect-{calls}', name='task.inspect', arguments={'task_id':task['id']})])

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))
    with pytest.raises(Exception, match='iteration/tool-call ceiling reached'):
        gateway._process(job)
    assert calls == 20
    assert store.get_conversation_job(job['id'])['status'] == 'failed'
    notifications = [item for item in store.messages(project['id'])
                     if item['role'] == 'assistant' and item['meta'].get('job_id') == job['id']]
    assert len(notifications) == 1
    assert 'test failure' in notifications[0]['content']


def test_supervision_stops_after_two_durable_recoveries(store, tmp_path):
    project, _, task = failed_task_with_user_grant(store, tmp_path)
    for index in range(2):
        job, mailbox = claim_event(store, project)
        assert store.bind_supervision_job(job['id'], mailbox)
        run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
        store.record_tool_invocation(agent_run_id=run['id'],
            tool_call_id=f'recovery-{index}', capability='task.reopen',
            arguments={'task_id':task['id']}, result={'ok':True,'output':{'id':task['id']}},
            ok=True)
        if index == 0:
            # Historical duplicate receipts from one notification represent
            # one recovery opportunity, not two independent failure cycles.
            store.record_tool_invocation(agent_run_id=run['id'],
                tool_call_id='historical-duplicate', capability='task.retry',
                arguments={'task_id':task['id']}, result={'ok':True,'output':{'id':task['id']}},
                ok=True)
        store.finish_conversation_job(job['id'], None, run['id'], {})
        with store.tx(immediate=True) as db:
            store._mailbox(db, project['id'], 'task.failed', task['id'], None,
                           {'error':'still failing'})
    third, mailbox = claim_event(store, project)
    assert store.bind_supervision_job(third['id'], mailbox) is None


# ---------------------------------------------------------------------------
# Failure transparency
# ---------------------------------------------------------------------------

def test_agent_execution_contract_has_no_platform_fuses():
    forbidden = {
        "max_iterations",
        "max_model_iterations",
        "max_tool_calls",
        "timeout_seconds",
        "wall_clock_timeout_seconds",
        "provider_max_attempts",
        "max_continuations",
        "direct_effect_limit",
    }

    assert forbidden.isdisjoint(AgentRunSpec.__dataclass_fields__)
    assert forbidden.isdisjoint(AgentExecutor.model_fields)
    with pytest.raises(ValidationError):
        AgentExecutor(capabilities=["system.noop"], max_iterations=16)


def test_terminal_failure_uses_declared_reason_instead_of_generic_message():
    assert _failure_message({"reason": "Task order exceeds the declared Project scope"}) == (
        "Task order exceeds the declared Project scope"
    )


def test_provider_failure_is_attempted_once_and_raised(store, tmp_path):
    project = store.create_project("provider failure", "", str(tmp_path / "project"))
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider did not answer")

    with pytest.raises(TimeoutError, match="provider did not answer"):
        AgentCore(store, build_core_registry(), model).run(AgentRunSpec(
            kind="conversation",
            project=project,
            instruction="",
            instruction_revision=1,
            context={},
            capability_ids=[],
            start_task=False,
        ))

    assert calls == 1
    run = store.agent_runs(project_id=project["id"])[0]
    assert run["status"] == "failed"
    assert "provider did not answer" in run["error"]


def test_capability_contract_error_is_not_converted_to_tool_result(store, tmp_path):
    project = store.create_project("capability failure", "", str(tmp_path / "project"))
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    with pytest.raises(ContractError, match="content"):
        build_core_registry().dispatch("project.files.write", {"path": "missing.txt"}, context)


def test_runtime_failure_is_persisted_and_re_raised(store, tmp_path):
    project = store.create_project("runtime failure", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps = [CapabilityStep(
        capability="project.command.run",
        arguments={"argv": [sys.executable, "-c", "raise SystemExit(23)"]},
    )]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "surface failure")

    with pytest.raises(RuntimeExecutionError, match="exited with code 23"):
        WorkflowRuntime(store, build_core_registry(), "worker").step(task["id"])

    persisted = store.get_task(task["id"])
    assert persisted["status"] == "recovering"
    assert persisted["control_state"] == "paused"
    assert persisted["failure_origin"] == "capability"
    assert "exited with code 23" in persisted["error"]
    attempt = store.attempts(project["id"], task["id"])[0]
    assert attempt["checkpoint"]["failed_result"]["output"]["exit_code"] == 23


def test_expired_lease_enters_explicit_recovery(store, tmp_path):
    project = store.create_project("expired lease", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "owned task")
    assert store.claim_task(task["id"], "dead-worker", lease_seconds=1)
    with store.connect() as db:
        db.execute(
            "UPDATE v1_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )

    assert task["id"] in store.runnable_task_ids()
    recovered = store.get_task(task["id"])
    assert recovered["status"] == "recovering"
    assert "WorkerLeaseExpired" in recovered["error"]


def test_command_capability_has_no_runtime_timeout_argument(store, tmp_path):
    project = store.create_project("command schema", "", str(tmp_path / "project"))
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    schema = build_core_registry().input_schema("project.command.run")

    assert "timeout_seconds" not in schema["properties"]
    with pytest.raises(ContractError, match="Additional properties"):
        build_core_registry().dispatch(
            "project.command.run",
            {"argv": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 1},
            context,
        )
