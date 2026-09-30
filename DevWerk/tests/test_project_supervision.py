import pytest

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityContext
from app.v1.conversation import ConversationGateway
from app.v1.domain import AgentModelResponse, AgentToolCall
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow
from tests.test_agent_recovery_and_fencing import begin_agent
from tests.test_conversation_intent_contract import turn


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
    assert calls == 3
    assert store.get_task(task['id'])['status'] == 'pending'
    assert '待执行' in result['result']['reply']
    assert [item['capability'] for item in store.tool_invocations(project['id'], result['agent_run_id'])] == [
        'task.inspect','task.reopen','conversation.reply']


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
