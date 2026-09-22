import json

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.domain import AgentModelResponse, AgentToolCall
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow
from tests.test_conversation_intent_contract import turn, resolve


def test_reply_tool_finishes_same_run_with_real_task_state(store, tmp_path):
    project = store.create_project('reply tool', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],sequence_workflow())
    task = create_planned_task(store,project['id'],'Actual task')
    queued = store.create_conversation_job(project['id'],'当前进度？',True)
    job = store.claim_conversation_job(queued['id'],'test')
    calls = 0
    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert kwargs['required_tool_name'] == 'conversation.turn.resolve'
            return AgentModelResponse(tool_calls=[AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
                'source_message_id':job['user_message_id'],'based_on_intent_revision':0,'act':'status'})])
        return AgentModelResponse(tool_calls=[AgentToolCall(id='reply',name='conversation.reply',arguments={
            'mode':'work_result','task_ids':[task['id']],'message':'This invented completion claim must be ignored.'})])
    result = AgentCore(store,store.registry,model).run(AgentRunSpec(kind='conversation',project=project,
        instruction='',instruction_revision=1,context={},conversation_job_id=job['id'],
        agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve'],require_conversation_report=True))
    assert 'Actual task：待执行' in result.text
    assert 'invented' not in result.text
    assert calls == 2
    assert len(store.agent_runs(project_id=project['id'])) == 1
    assert result.completion['conversation_report']['tasks'][0]['id'] == task['id']


def test_unresolved_projection_excludes_prior_tool_trace_and_restores_after_resolution(store, tmp_path):
    from app.v1.agent_provider import ProviderTurnRequester
    project = store.create_project('phase context', '', str(tmp_path/'project'))
    job, ctx = turn(store,project,'开始实施')
    payload = {'authoritative_current_request':{'message_id':job['user_message_id'],'content':job['message']},
        'authoritative_project_state':{}}
    messages = [{'role':'system','content':'same identity'},
        {'role':'tool','tool_call_id':'old','content':'OLD_TOOL_TRACE'},
        {'role':'user','content':json.dumps(payload)}]
    captured = []
    def model(messages, tools, **kwargs):
        captured.append(messages)
        return AgentModelResponse(text='{}')
    spec = AgentRunSpec(kind='conversation',project=project,instruction='',instruction_revision=1,
        context={},capability_ids=[],conversation_job_id=job['id'])
    provider = ProviderTurnRequester(store=store,model_complete=model,spec=spec,run_id=ctx.agent_run_id)
    tools = store.registry.schemas(['conversation.turn.resolve','project.files.read'],ctx)
    provider.request(messages,tools,iteration=1,require_tool=False,required_tool_name=None)
    assert 'OLD_TOOL_TRACE' not in str(captured[-1])
    assert captured[-1][0] == messages[0]
    assert resolve(ctx,job,act='execute',execution_request='begin',scope_summary='demo',
        user_evidence=[{'message_id':job['user_message_id']}]).ok
    provider.request(messages,tools,iteration=2,require_tool=False,required_tool_name=None)
    assert 'OLD_TOOL_TRACE' in str(captured[-1])


def test_reply_cannot_cite_invented_task(store, tmp_path):
    project = store.create_project('reply provenance', '', str(tmp_path/'project'))
    job, ctx = turn(store,project,'进度？')
    assert resolve(ctx,job,act='status').ok
    reply = store.registry.dispatch('conversation.reply',{'mode':'work_result','task_ids':['tsk_invented']},ctx)
    assert not reply.ok
    assert not store.list_tasks(project['id'])


def test_reply_and_mutation_batch_is_rejected_before_any_effect(store, tmp_path):
    project = store.create_project('reply batch', '', str(tmp_path/'project'))
    queued = store.create_conversation_job(project['id'], '开始写文件', True)
    job = store.claim_conversation_job(queued['id'], 'test')
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id='resolve', name='conversation.turn.resolve', arguments={
                'source_message_id':job['user_message_id'],'based_on_intent_revision':0,
                'act':'execute','execution_request':'begin','scope_summary':'write file',
                'user_evidence':[{'message_id':job['user_message_id']}]} )])
        if calls == 2:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id='reply',name='conversation.reply',arguments={'mode':'blocked','message':'Finish'}),
                AgentToolCall(id='write',name='project.files.write',arguments={'path':'bad.txt','content':'bad'})])
        return AgentModelResponse(tool_calls=[AgentToolCall(id='finish',name='conversation.reply',arguments={
            'mode':'blocked','tool_call_ids':['write']})])
    result = AgentCore(store,store.registry,model).run(AgentRunSpec(kind='conversation',project=project,
        instruction='',instruction_revision=1,context={},conversation_job_id=job['id'],
        agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve','project.files.write'],require_conversation_report=True))
    receipts = store.tool_invocations(project['id'],result.agent_run_id,hydrate_payloads=True)
    mixed = [i for i in receipts if i['tool_call_id'] in {'reply','write'}]
    assert len(mixed) == 2 and not any(i['ok'] for i in mixed)
    assert all(i['result']['error']['type'] == 'ConversationTurnProtocolError' for i in mixed)
    assert not (tmp_path/'project'/'bad.txt').exists()
    assert result.completion['conversation_report']['report']['mode'] == 'blocked'


def test_reply_receipt_recovery_renders_current_task_without_column_completion(store, tmp_path, monkeypatch):
    project = store.create_project('reply recovery', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],sequence_workflow())
    task = create_planned_task(store,project['id'],'Recoverable report')
    queued = store.create_conversation_job(project['id'],'进度？',True)
    job = store.claim_conversation_job(queued['id'],'test')
    responses = iter([
        AgentModelResponse(tool_calls=[AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
            'source_message_id':job['user_message_id'],'based_on_intent_revision':0,'act':'status'})]),
        AgentModelResponse(tool_calls=[AgentToolCall(id='reply',name='conversation.reply',arguments={
            'mode':'work_result','task_ids':[task['id']]})]),
    ])
    spec = AgentRunSpec(kind='conversation',project=project,instruction='',instruction_revision=1,context={},
        conversation_job_id=job['id'],agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve'],require_conversation_report=True)
    finish = store.finish_agent_run
    crashed = False
    def interrupt_finish(run_id, status, *args, **kwargs):
        nonlocal crashed
        if status == 'succeeded' and not crashed:
            crashed = True
            raise RuntimeError('injected interruption after reply receipt')
        return finish(run_id,status,*args,**kwargs)
    monkeypatch.setattr(store,'finish_agent_run',interrupt_finish)
    core = AgentCore(store,store.registry,lambda *a,**kw: next(responses))
    with pytest.raises(RuntimeError, match='injected interruption'):
        core.run(spec)
    store.pause_task(task['id'])
    recovered = core.run(spec)
    assert recovered.status == 'succeeded', recovered.error
    assert 'Recoverable report：已暂停' in recovered.text
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_agent_operations WHERE capability='conversation.reply'").fetchone()[0] == 1


def test_empty_discussion_response_recovers_with_reply_tool(store, tmp_path):
    project = store.create_project('empty reply', '', str(tmp_path/'project'))
    queued = store.create_conversation_job(project['id'],'只讨论',False)
    job = store.claim_conversation_job(queued['id'],'test')
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
                'source_message_id':job['user_message_id'],'based_on_intent_revision':0,'act':'discuss'})])
        if calls == 2:
            return AgentModelResponse()
        assert kwargs['required_tool_name'] == 'conversation.reply'
        return AgentModelResponse(tool_calls=[AgentToolCall(id='reply',name='conversation.reply',arguments={
            'mode':'discussion','message':'先讨论需求与验收。'})])
    spec = AgentRunSpec(kind='conversation',project=project,instruction='',instruction_revision=1,context={},
        conversation_job_id=job['id'],agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve'],require_conversation_report=True)
    result = AgentCore(store,store.registry,model).run(spec)
    assert result.status == 'succeeded', result.error
    assert result.text == '先讨论需求与验收。'
    assert calls == 3
    assert not store.agents.list_requirements(project['id'])


def test_persistent_empty_response_stops_after_three_attempts(store, tmp_path):
    project = store.create_project('bounded empty', '', str(tmp_path/'project'))
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        return AgentModelResponse()
    with pytest.raises(RuntimeError, match='neither tools nor final text'):
        AgentCore(store,store.registry,model).run(AgentRunSpec(kind='conversation',project=project,
            instruction='',instruction_revision=1,context={},capability_ids=[],require_conversation_report=True))
    assert calls == 3
