import json

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.conversation_failure import failure_summary
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.runtime import WorkflowRuntime
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow
from tests.test_conversation_intent_contract import turn, resolve


def test_mixed_resolution_and_mutation_batch_has_no_business_effects(store, tmp_path):
    project = store.create_project('mixed batch', '', str(tmp_path/'project'))
    queued = store.create_conversation_job(project['id'], '开始写文件', True)
    job = store.claim_conversation_job(queued['id'], 'test')
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
                    'source_message_id':job['user_message_id'],'based_on_intent_revision':0,
                    'act':'execute','execution_request':'begin','scope_summary':'write file',
                    'user_evidence':[{'message_id':job['user_message_id'],'quote':job['message']}]}),
                AgentToolCall(id='write',name='project.files.write',arguments={'path':'bad.txt','content':'bad'}),
            ])
        return AgentModelResponse(text=json.dumps({'mode':'discussion','message':'需要单独提交回合边界。'}))
    result = AgentCore(store, store.registry, model).run(AgentRunSpec(kind='conversation',project=project,
        instruction='',instruction_revision=1,context={},conversation_job_id=job['id'],
        agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve','project.files.write'],require_conversation_report=True))
    invocations = store.tool_invocations(project['id'],result.agent_run_id,hydrate_payloads=True)
    assert len(invocations) == 2 and not any(i['ok'] for i in invocations)
    assert all(i['result']['error']['type'] == 'ConversationTurnProtocolError' for i in invocations)
    assert not (tmp_path/'project'/'bad.txt').exists()
    assert not store.agents.list_requirements(project['id'])


def test_plain_discussion_cannot_hide_successful_effect_receipt(store, tmp_path):
    from app.v1.conversation_report import render_report
    project = store.create_project('legacy recovery receipt', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '讨论')
    assert resolve(ctx, job).ok
    store.record_tool_invocation(agent_run_id=ctx.agent_run_id,tool_call_id='legacy-write',
        capability='project.files.write',arguments={'path':'legacy.txt','content':'x'},
        result={'ok':True,'capability':'project.files.write','output':{}},ok=True)
    with pytest.raises(ValueError):
        render_report(store,project['id'],ctx.agent_run_id,'只是讨论，不需要检查执行结果。')


def test_partial_failure_reports_saved_work_and_actual_started_count(store, tmp_path):
    project = store.create_project('partial effects', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],sequence_workflow())
    task = create_planned_task(store,project['id'],'first')
    WorkflowRuntime(store,store.registry,'test-worker').step(task['id'])
    def entry(capability,ok=True,output=None,error=None):
        return {'capability':capability,'ok':ok,'facts':{'result':{'output':output,'error':{'message':error}}}}
    ledger = [entry('loop.apply'),entry('task.plan.save'),
        entry('task.create',output={'materialization':{'created_task_ids':[task['id']],'reused_task_ids':[]}}),
        entry('task.create',output={'materialization':{'created_task_ids':[],'reused_task_ids':[task['id']]}}),
        entry('task.plan.save',False,error='ExactInputTargetType: /requirements_confirmed is boolean')]
    reply = failure_summary(store,project['id'],ledger,'ConversationProtocolStalled')
    assert '新建 1 个任务，其中 1 个已启动' in reply
    assert '工作流已保存' in reply and '任务计划已保存' in reply
    assert 'ExactInputTargetType' in reply and '重复失败调用已停止' in reply
    assert '复用 1' not in reply
