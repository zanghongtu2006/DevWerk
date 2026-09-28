import hashlib
import json

from app.v1.agent import AgentCore
from app.v1.agent_models import AgentRunSpec
from app.v1.agent_run_preparation import AgentRunPreparer
from app.v1.capabilities import CapabilityContext
from app.v1.runtime import WorkflowRuntime, _column_completion_contract
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow
from tests.test_persistent_agents import assignment
from tests.test_scope_extension import invoke, turn


def prepared_assignment(store, project, workflow, task, run, assigned):
    spec = AgentRunSpec(kind='column',project=project,instruction='Continue current work',
        instruction_revision=1,context={'input':{'objective':'continue'}},
        capability_ids=workflow.column('work').executor.capabilities,
        task_id=task['id'],column_run_id=run['id'],column_attempt_id=run['attempt_id'],
        assignment=assigned,agent_session_id=assigned['session_id'],
        agent_instance_id=assigned['agent_instance_id'],requirement_id=assigned['requirement_id'],
        completion_contract=_column_completion_contract(workflow,workflow.column('work')))
    return AgentRunPreparer(store=store,registry=store.registry,runtime_policy=store.policy,
        platform_policy=store.latest_platform_policy()).prepare(spec)


def replaced_worker(store, tmp_path, summary):
    project = store.create_project('handoff','',str(tmp_path/'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store,project['id'],workflow)
    task,run,prior = assignment(store,project,workflow)
    requirement = store.agents.get_requirement(project['id'],prior['requirement_id'])
    evidence = store.prepare_terminal_evidence(task,run['id'],'done',{},None)
    store.finish_run(task,run['id'],{},'success','done',terminal='done',terminal_artifact=evidence)
    store.agents.set_lifecycle(project['id'],prior['agent_instance_id'],'retired')
    ctx,_ = turn(store,project,requirement,request='continue')
    successor = invoke(ctx,'agent.worker.replace',{'worker_id':prior['agent_instance_id'],'summary':summary})
    store.finish_conversation_job(store.agents.conversation_job(ctx)['id'],None,ctx.agent_run_id,{})
    next_task,next_run,assigned = assignment(store,project,workflow,requirement=requirement)
    return project,workflow,prior,successor,next_task,next_run,assigned


def test_successor_gets_handoff_in_real_assignment_input(store,tmp_path):
    marker = 'UNOPENED_LETTER_MUST_REMAIN_UNOPENED'
    project,workflow,prior,successor,task,run,assigned = replaced_worker(store,tmp_path,marker)
    prepared = prepared_assignment(store,project,workflow,task,run,assigned)
    messages = json.dumps(prepared.messages,ensure_ascii=False)
    assert assigned['agent_instance_id']==successor['worker']['id']
    assert marker in messages
    assert messages.count('session_handoff')==1
    assert 'current_assignment' in messages
    assert store.agents.get_worker(project['id'],prior['agent_instance_id'])['lifecycle']=='retired'
    assert successor['snapshot_id'] in messages


def test_long_handoff_reassembles_through_worker_scoped_pages(store,tmp_path):
    summary = '开头重要。' + ('中段细节·'*1800) + '结尾重要。'
    project,workflow,prior,successor,task,run,assigned = replaced_worker(store,tmp_path,summary)
    prepared = prepared_assignment(store,project,workflow,task,run,assigned)
    messages = json.dumps(prepared.messages,ensure_ascii=False)
    assert '结尾重要' not in messages and successor['snapshot_id'] in messages
    ctx = CapabilityContext(project['id'],project,store,agent_run_id=prepared.run['id'],
        column_run_id=run['id'],agent_instance_id=assigned['agent_instance_id'])
    chunks,offset,expected = [],0,None
    while True:
        args = {'snapshot_id':successor['snapshot_id'],'offset':offset,
                'max_characters':4000}
        if expected:
            args['expected_sha256']=expected
        reply = store.registry.dispatch('agent.context.read',args,ctx)
        assert reply.ok,reply.error
        page = reply.output[0]
        expected = page['sha256']
        assert page['offset']==offset
        chunks.append(page['content'])
        if page['next_offset'] is None:
            break
        assert page['next_offset']==offset+len(page['content'])
        offset=page['next_offset']
    restored = ''.join(chunks)
    assert hashlib.sha256(restored.encode('utf8')).hexdigest()==expected
    assert json.loads(restored)['summary']==summary
    old_ctx = CapabilityContext(project['id'],project,store,agent_run_id=prepared.run['id'],
        column_run_id=run['id'],agent_instance_id=prior['agent_instance_id'])
    denied = store.registry.dispatch('agent.context.read',
        {'snapshot_id':successor['snapshot_id']},old_ctx)
    assert not denied.ok


def test_idle_worker_snapshot_reaches_next_assignment(store,tmp_path):
    from app.v1.domain import AgentModelResponse, AgentToolCall
    project = store.create_project('idle handoff','',str(tmp_path/'project'))
    workflow = agent_workflow()
    workflow.column('work').executor.worker_key='developer'
    publish_planned_workflow(store,project['id'],workflow)
    first = create_planned_task(store,project['id'],'first delivery')
    def model(*_args,**_kwargs):
        return AgentModelResponse(tool_calls=[AgentToolCall(id='done',name='column.complete',
            arguments={'outcome':'success','output':{'delivered':True},'summary':'first done'})])
    WorkflowRuntime(store,store.registry,'worker',AgentCore(store,store.registry,model)).step(first['id'])
    prior = next(worker for worker in store.agents.list_workers(project['id']) if worker['role']=='leaf')
    source = store.agents.context_page(project['id'],prior['id'])[-1]['id']
    store.agents.save_context_snapshot(project['id'],prior['id'],source,
        {'decision':'KEEP_THE_PUBLIC_API_STABLE'})
    task,run,assigned = assignment(store,project,workflow)
    prepared = prepared_assignment(store,project,workflow,task,run,assigned)
    assert assigned['agent_instance_id']==prior['id']
    assert 'KEEP_THE_PUBLIC_API_STABLE' in json.dumps(prepared.messages)
    assert 'current_assignment' in json.dumps(prepared.messages)
    ctx = CapabilityContext(project['id'],project,store,agent_run_id=prepared.run['id'],
        column_run_id=run['id'],agent_instance_id=assigned['agent_instance_id'])
    exact = store.registry.dispatch('agent.context.read',
        {'source_message_id':source,'offset':0,'max_characters':100},ctx)
    assert exact.ok and exact.output[0]['source_kind']=='message'
    assert exact.output[0]['source_id']==source
