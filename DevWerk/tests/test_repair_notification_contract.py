import json
import sys

import pytest

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityContext
from app.v1.conversation import ConversationGateway
from app.v1.domain import (AcceptanceCheck, AcceptanceObligation, AgentModelResponse,
                           AgentToolCall, Transition)
from app.v1.runtime import WorkflowRuntime
from tests.helpers import agent_workflow, sequence_workflow, publish_planned_workflow, create_planned_task, task_plan
from tests.test_conversation_intent_contract import turn


def test_host_binds_resolution_metadata_but_rejects_explicit_conflict(store, tmp_path):
    project = store.create_project('binding', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, 'Start the accepted project')
    args = {'act':'execute','execution_request':'begin','scope_summary':'accepted project'}
    wrong = store.registry.dispatch('conversation.turn.resolve', {**args,'source_message_id':-1}, ctx)
    assert not wrong.ok
    result = store.registry.dispatch('conversation.turn.resolve', args, ctx)
    assert result.ok, result.error
    assert store.registry.dispatch('conversation.turn.resolve', args, ctx).ok
    assert result.output['turn_contract']['phase'] == 'execution'


def test_invalid_command_cwd_is_rejected_before_effect_receipt(store, tmp_path):
    project = store.create_project('cwd', '', str(tmp_path/'project'))
    ctx = CapabilityContext(project['id'],project,store,agent_run_id='test-run',execution_key='test:invalid-cwd')
    result = store.registry.dispatch('project.command.run', {'argv':[sys.executable,'-c','raise Exception()'],'cwd':'../outside'},ctx)
    assert not result.ok
    assert result.checkpoint['failure_disposition'] == 'rejected_before_effect'
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM v1_execution_receipts WHERE execution_key='test:invalid-cwd'").fetchone()


def test_user_priority_and_notification_does_not_resolve_failure(store, tmp_path):
    project = store.create_project('notification', '', str(tmp_path/'project'))
    failure = store.create_conversation_job(project['id'],'a previous request',False)
    store.fail_conversation_job(failure['id'],'needs repair')
    with store.tx(immediate=True) as db:
        store._mailbox(db,project['id'],'conversation.planning_failed',None,None,
                       {'job_id':failure['id'],'error':'needs repair','report':'review remains available'})
    event_id = store.enqueue_governance_jobs()[0]
    user = store.create_conversation_job(project['id'],'What happened?',False)
    assert store.claim_conversation_job(event_id,'observer') is None
    claimed = store.claim_conversation_job(user['id'],'human')
    assert claimed
    store.finish_conversation_job(user['id'],None,None,{})
    job = store.claim_conversation_job(event_id,'observer')
    core = AgentCore(store,store.registry,lambda *a, **kw: pytest.fail('Mailbox must not call a model'))
    ConversationGateway(store,store.registry,agent_core=core)._process(job)
    assert store.get_conversation_job(failure['id'])['resolved_by_job_id'] is None
    assert store.get_conversation_job(event_id)['result']['llm_used'] is False
    assert store.mailbox(project['id'],state='acknowledged')[0]['payload']['report'] == 'review remains available'


def test_two_cross_column_repair_cycles_use_real_files_checks_and_same_workers(store, tmp_path):
    workflow = agent_workflow()
    work = workflow.column('work')
    work.executor.worker_key = 'implementer'
    work.transitions = [Transition(outcome='success',target='review'),Transition(outcome='failure',target='failed')]
    review = work.model_copy(deep=True)
    review.key, review.name = 'review','Review'
    review.executor.worker_key = 'reviewer'
    review.executor.capabilities = ['project.command.run','task.feedback.record']
    review.transitions = [Transition(outcome='success',target='done'),
                          Transition(outcome='rework',target='work',allows_unresolved_failures=True)]
    check = {'argv':[sys.executable,'-c',"from pathlib import Path; assert Path('level.txt').read_text() == '2'"], 'cwd':'.'}
    review.acceptance_checks = [AcceptanceCheck(key='actual_behavior',capability='project.command.run',arguments=check)]
    workflow.columns.append(review)
    workflow.acceptance_obligations = [AcceptanceObligation(column='review',check_key='actual_behavior')]
    project = store.create_project('repair', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'repair twice')
    responses = []
    def tool(name,args):
        responses.append(AgentModelResponse(tool_calls=[AgentToolCall(id=f'call-{len(responses)}',name=name,arguments=args)]))
    for level in range(3):
        tool('project.files.write',{'path':'level.txt','content':str(level)})
        tool('column.complete',{'outcome':'success','output':{'delivered':True},'summary':f'implementation {level}'})
        if level < 2:
            tool('project.command.run',check)
            tool('task.feedback.record',{'task_id':task['id'],'dedupe_key':f'defect-{level}',
                'responsible_column':'work','description':f'Actual behavior still fails after implementation {level}',
                'checks':[{'column':'review','check_key':'actual_behavior'}]})
            tool('column.complete',{'outcome':'rework','output':{'delivered':False},'summary':'failed behavior needs implementation repair'})
        else:
            tool('column.complete',{'outcome':'success','output':{'delivered':True},'summary':'actual behavior passed'})
    replies = iter(responses)
    seen = []
    def model(messages,*a,**kw):
        seen.append(messages)
        return next(replies)
    runtime = WorkflowRuntime(store,store.registry,'test-worker',AgentCore(store,store.registry,model))
    for _ in range(6):
        runtime.step(task['id'])
    current = store.get_task(task['id'])
    assert current['status'] == 'done', current.get('error')
    assert (tmp_path/'project'/'level.txt').read_text() == '2'
    feedback = store.feedback.list(project['id'],task['id'])
    assert len(feedback) == 2 and all(f['state'] == 'resolved' and f['verification'] for f in feedback)
    workers = [w for w in store.agents.list_workers(project['id']) if w['role']=='leaf']
    assert len(workers) == 2
    for worker in workers:
        assignments = store.agents.inspect_worker(project['id'],worker['id'])['assignments']
        assert len(assignments) == 3 and len({a['session_id'] for a in assignments}) == 1
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v1_acceptance_results WHERE ok=1').fetchone()[0] >= 1
    gateway = ConversationGateway(store,store.registry,agent_core=AgentCore(store,store.registry,lambda *a,**kw:pytest.fail('notification invoked model')))
    for job_id in store.enqueue_governance_jobs():
        gateway._process(store.claim_conversation_job(job_id,'observer'))
    assert len(store.list_tasks(project['id'])) == 1


def test_successor_keeps_old_revision_and_uses_explicit_new_plan(store, tmp_path):
    workflow = agent_workflow()
    project = store.create_project('successor','',str(tmp_path/'project'))
    _, first_revision = publish_planned_workflow(store,project['id'],workflow)
    first = create_planned_task(store,project['id'],'original')
    store.route_task_to_failed(first['id'],'bad frozen command')
    workflow.name = 'corrected'
    _, revision = publish_planned_workflow(store,project['id'],workflow)
    plan = store.create_task_plan(project['id'],task_plan(revision['id'],workflow,task_ref=first['proposed_task_ref']))
    # The same atomic materialization path used by task.successor after scope checks.
    successor = store.materialize_task_plan(project['id'],task_plan_id=plan['id'],proposed_task_ref=first['proposed_task_ref'],rerun_of_task_id=first['id'])
    assert successor['workflow_revision_id'] == revision['id']
    assert successor['rerun_of_task_id'] == first['id']
    assert store.get_task(first['id'])['workflow_revision_id'] == first_revision['id']
    replay = store.materialize_task_plan(project['id'],task_plan_id=plan['id'],proposed_task_ref=first['proposed_task_ref'],rerun_of_task_id=first['id'])
    assert replay['id'] == successor['id'] and replay['materialization']['created_task_ids'] == []
    with pytest.raises(ValueError,match='already has'):
        store.create_task(project['id'],task_plan_id=plan['id'],proposed_task_ref=first['proposed_task_ref'],rerun_of_task_id=first['id'])


def test_successor_materializes_dependencies_atomically(store, tmp_path):
    workflow = agent_workflow()
    project = store.create_project('repair dependencies','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    first = create_planned_task(store,project['id'],'original')
    store.route_task_to_failed(first['id'],'old contract failed')
    workflow.name = 'corrected dependency contract'
    _, revision = publish_planned_workflow(store,project['id'],workflow)
    plan = task_plan(revision['id'],workflow,task_ref=first['proposed_task_ref'])
    dependency = plan.tasks[0].model_copy(deep=True,update={'proposed_task_ref':'prerequisite','title':'prerequisite'})
    plan.tasks[0].dependencies = ['prerequisite']
    plan.tasks.append(dependency)
    plan.repair_of_task_id = first['id']
    saved = store.create_task_plan(project['id'],plan)
    successor = store.materialize_task_plan(project['id'],task_plan_id=saved['id'],proposed_task_ref=first['proposed_task_ref'],rerun_of_task_id=first['id'])
    assert len(successor['materialization']['created_task_ids']) == 2
    assert successor['rerun_of_task_id'] == first['id']
    assert {t['proposed_task_ref'] for t in store.list_tasks(project['id']) if t['task_plan_id']==saved['id']} == {first['proposed_task_ref'],'prerequisite'}


def test_terminal_transaction_rejects_claim_without_runtime_acceptance_receipt(store, tmp_path):
    workflow = agent_workflow()
    workflow.column('work').acceptance_checks = [AcceptanceCheck(key='required',capability='project.files.read',arguments={'path':'proof.txt'})]
    workflow.acceptance_obligations = [AcceptanceObligation(column='work',check_key='required')]
    project = store.create_project('no forged proof','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'must verify')
    owned = store.claim_task(task['id'],'owner')
    run = store.begin_run(owned,{})
    with pytest.raises(ValueError,match='Required acceptance evidence missing'):
        store.finish_run(owned,run['id'],{'report':'All checks passed'},'success','done',terminal='done')
    assert store.get_task(task['id'])['status'] == 'running'
    assert not any(e['type']=='task.done' for e in store.events(project['id']))


def test_sequence_checks_generate_runtime_evidence_without_agent(store, tmp_path):
    workflow = sequence_workflow()
    column = workflow.column(workflow.entry)
    column.acceptance_checks = [AcceptanceCheck(key='actual',capability='project.command.run',
        arguments={'argv':[sys.executable,'-c','assert 2 + 2 == 4']})]
    workflow.acceptance_obligations = [AcceptanceObligation(column=column.key,check_key='actual')]
    project = store.create_project('sequence proof','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'sequence')
    WorkflowRuntime(store,store.registry,'owner').step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    with store.connect() as db:
        proof = db.execute('SELECT ok,agent_run_id FROM v1_acceptance_results WHERE task_id=?',(task['id'],)).fetchone()
        assert proof['ok'] == 1 and proof['agent_run_id'] is None


def test_software_template_cannot_admit_task_without_instantiated_checks(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    project, plan, ctx = software_plan(store,tmp_path)
    current = store.get_workflow(project['id'])
    from app.v1.domain import WorkflowDefinition
    definition = WorkflowDefinition.model_validate(current['definition'])
    definition.acceptance_obligations = []
    for column in definition.columns:
        column.acceptance_checks = []
    revision = store.publish_workflow(project['id'],definition,current['workflow_plan_id'])
    plan.workflow_revision_id = revision['id']
    with pytest.raises(ValueError,match='AcceptanceContractMissing'):
        store.create_task_plan(project['id'],plan)
