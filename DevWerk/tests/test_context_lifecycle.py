import json
import sys
from dataclasses import replace

import pytest

from app.v1.agent import AgentCore
from app.v1.agent_models import AgentRunSpec
from app.v1.context_manager import ContextManager, context_input_tokens, project_result
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.policy import ContextPolicy
from app.v1.runtime import WorkflowRuntime
from app.services.provider_errors import LLMProviderError, ProviderErrorDetails
from tests.helpers import agent_workflow, publish_planned_workflow, create_planned_task


def test_large_command_loop_compacts_and_keeps_raw_evidence(store, tmp_path):
    store.policy = store.policy.model_copy(update={'context': ContextPolicy(
        fallback_window_tokens=24000, fallback_output_tokens=2000, summary_tokens=512, tool_result_tokens=1000)})
    workflow = agent_workflow()
    workflow.column('work').executor.capabilities = ['project.command.run','project.files.write']
    project = store.create_project('context', '', str(tmp_path/'project'))
    publish_planned_workflow(store, project['id'], workflow)
    task = create_planned_task(store, project['id'], 'long command loop')
    seen, count = [], 0
    def model(messages, tools, **kwargs):
        nonlocal count
        seen.append(json.dumps(messages,ensure_ascii=False))
        assert all(not any(k.startswith('_') for k in m) for m in messages)
        count += 1
        if count <= 36:
            calls = [AgentToolCall(id=f'c{count}',name='project.command.run',
                arguments={'argv':[sys.executable,'-c',f"print('diagnostic-{count} ' * 8000)"]})]
            if count <= 34:
                calls.append(AgentToolCall(id=f'w{count}',name='project.files.write',
                    arguments={'path':f'state-{count}.txt','content':'source line\n'*2400}))
            return AgentModelResponse(tool_calls=calls)
        return AgentModelResponse(tool_calls=[AgentToolCall(id='done',name='column.complete',
            arguments={'outcome':'success','output':{'delivered':True},'summary':'completed'})])
    runtime = WorkflowRuntime(store,store.registry,'owner',AgentCore(store,store.registry,model))
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    assert count == 37
    assert any('context_checkpoint' in s for s in seen)
    with store.connect() as db:
        assert db.execute('select count(*) from v1_context_checkpoints').fetchone()[0] > 0
        assert db.execute("select count(*) from v1_tool_invocations where capability='project.command.run'").fetchone()[0] == 36
        assert db.execute("select count(*) from v1_tool_invocations where capability='project.files.write'").fetchone()[0] == 34
        assert db.execute("select max(length(result_json)) from v1_tool_invocations").fetchone()[0] > 100000
    assert max(map(len, seen)) < 60000


def test_resume_ledger_is_bounded_in_actual_column_messages_but_keeps_full_receipts(store, tmp_path):
    from app.v1.agent_run_preparation import AgentRunPreparer
    from app.v1.context_manager import ContextManager
    from app.v1.runtime import _column_completion_contract
    from tests.test_agent_recovery_and_fencing import begin_agent
    from tests.test_persistent_agents import assignment

    workflow = agent_workflow()
    project = store.create_project('ledger resume', '', str(tmp_path/'project'))
    publish_planned_workflow(store, project['id'], workflow)
    task, run, assigned = assignment(store, project, workflow)
    prior = begin_agent(store, project, kind='column', task_id=task['id'],
                        column_run_id=run['id'], agent_session_id=assigned['session_id'])
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_agent_runs SET assignment_id=?,agent_instance_id=? WHERE id=?',
                   (assigned['id'], assigned['agent_instance_id'], prior['id']))
    raw = 'RAW_LOG_MARKER_' + 'x'*110000
    for number in range(4):
        result = {'ok':True,'capability':'project.command.run','status':'completed',
                  'output':{'exit_code':0,'stdout':raw,'stderr':''}}
        store.record_tool_invocation(agent_run_id=prior['id'],tool_call_id=f'cmd-{number}',
            capability='project.command.run',arguments={'argv':['offline-synthetic']},
            result=result,ok=True)
    runtime = WorkflowRuntime(store,store.registry,'test-worker')
    ledger = runtime._column_action_ledger(project['id'],run['id'])
    assert len(ledger) == 4 and all(raw in item['facts']['result']['output']['stdout'] for item in ledger)
    spec = AgentRunSpec(kind='column',project=project,instruction='Finish the current work',
        instruction_revision=1,context={'input':{'objective':'continue'}},
        capability_ids=workflow.column('work').executor.capabilities,
        runtime_ledger=ledger,task_id=task['id'],column_run_id=run['id'],
        column_attempt_id=run['attempt_id'],assignment=assigned,
        agent_session_id=assigned['session_id'],agent_instance_id=assigned['agent_instance_id'],
        requirement_id=assigned['requirement_id'],
        completion_contract=_column_completion_contract(workflow,workflow.column('work')))
    prepared = AgentRunPreparer(store=store,registry=store.registry,runtime_policy=store.policy,
                                platform_policy=store.latest_platform_policy()).prepare(spec)
    content = json.dumps(prepared.messages,ensure_ascii=False)
    assert raw not in content and content.count('execution_evidence') == 1
    assert len(content) < 16000
    assert len(prepared.logical_ledger) == 4
    assert ContextManager(store,spec,prepared.run['id'],lambda *a,**kw:None).prepare(
        prepared.messages,prepared.tools)
    directory = store.agents.evidence_page(project['id'],run['id'],worker_id=assigned['agent_instance_id'])
    assert len(directory['entries']) == 4 and not directory['has_more']
    assert raw not in json.dumps(directory,ensure_ascii=False)
    from app.v1.capabilities import CapabilityContext
    context = CapabilityContext(project['id'],project,store,agent_run_id=prepared.run['id'],
        column_run_id=run['id'],agent_instance_id=assigned['agent_instance_id'])
    listed = store.registry.dispatch('agent.evidence.list', {'limit':2}, context)
    assert listed.ok and len(listed.output['entries']) == 2 and listed.output['has_more']
    continued = store.registry.dispatch('agent.evidence.list', {
        'limit':2,'after_invocation_id':listed.output['next_cursor']}, context)
    assert continued.ok and len(continued.output['entries']) == 2 and not continued.output['has_more']
    source = directory['entries'][0]
    page = store.registry.dispatch('agent.result.read', {
        'agent_run_id':source['agent_run_id'],'tool_call_id':source['tool_call_id'],
        'field':'stdout','offset':0,'limit':4000}, context)
    assert page.ok and page.output['content'].startswith('RAW_LOG_MARKER_')
    assert page.output['next_offset'] == len(page.output['content'])
    outside = store.registry.dispatch('agent.evidence.list', {'column_run_id':'other'}, context)
    assert not outside.ok
    from app.v1.capabilities import CapabilityContext
    context = CapabilityContext(project['id'],project,store,agent_run_id=prepared.run['id'],
        column_run_id=run['id'],agent_instance_id=assigned['agent_instance_id'])
    listed = store.registry.dispatch('agent.evidence.list', {'limit':2}, context)
    assert listed.ok and len(listed.output['entries']) == 2 and listed.output['has_more']
    continued = store.registry.dispatch('agent.evidence.list', {
        'limit':2,'after_invocation_id':listed.output['next_cursor']}, context)
    assert continued.ok and len(continued.output['entries']) == 2 and not continued.output['has_more']
    source = directory['entries'][0]
    page = store.registry.dispatch('agent.result.read', {
        'agent_run_id':source['agent_run_id'],'tool_call_id':source['tool_call_id'],
        'field':'stdout','offset':0,'limit':4000}, context)
    assert page.ok and page.output['content'].startswith('RAW_LOG_MARKER_')
    assert page.output['next_offset'] == len(page.output['content'])
    outside = store.registry.dispatch('agent.evidence.list', {'column_run_id':'other'}, context)
    assert not outside.ok


def test_repeated_assignment_resume_keeps_only_current_runtime_frame(store, tmp_path):
    from app.v1.agent_run_preparation import AgentRunPreparer
    from app.v1.runtime import _column_completion_contract
    from tests.test_persistent_agents import assignment

    workflow = agent_workflow()
    project = store.create_project('resume frames','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task,run,assigned = assignment(store,project,workflow)
    spec = AgentRunSpec(kind='column',project=project,instruction='Do current work',
        instruction_revision=1,context={'input':{'objective':'deliver'}},
        capability_ids=workflow.column('work').executor.capabilities,
        task_id=task['id'],column_run_id=run['id'],column_attempt_id=run['attempt_id'],
        assignment=assigned,agent_session_id=assigned['session_id'],
        agent_instance_id=assigned['agent_instance_id'],requirement_id=assigned['requirement_id'],
        completion_contract=_column_completion_contract(workflow,workflow.column('work')))
    preparer = AgentRunPreparer(store=store,registry=store.registry,runtime_policy=store.policy,
                                platform_policy=store.latest_platform_policy())
    first = preparer.prepare(spec)
    store.add_agent_message(first.run['id'],'user','WORKER_STEERING_KEEP_THIS',[])
    second = preparer.prepare(spec)
    current = [m for m in second.messages if m['role']=='user' and 'current_assignment' in m['content']]
    assert len(current)==1 and 'deliver' in current[0]['content']
    assert 'WORKER_STEERING_KEEP_THIS' in json.dumps(second.messages)
    assert any('current_assignment' in m['content'] for m in store.agent_messages(project['id'], first.run['id']))


def test_evidence_directory_matches_runtime_command_failure(store, tmp_path):
    from tests.test_agent_recovery_and_fencing import begin_agent
    from tests.test_persistent_agents import assignment

    workflow = agent_workflow()
    project = store.create_project('failed command receipt','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task,run,assigned = assignment(store,project,workflow)
    source = begin_agent(store,project,kind='column',task_id=task['id'],column_run_id=run['id'])
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_agent_runs SET agent_instance_id=? WHERE id=?',
                   (assigned['agent_instance_id'],source['id']))
    store.record_tool_invocation(agent_run_id=source['id'],tool_call_id='failed-build',
        capability='project.command.run',arguments={'argv':['offline-synthetic']},
        result={'ok':True,'status':'completed','capability':'project.command.run',
                'output':{'exit_code':1,'stdout':'expected 401 actual 429'}},ok=True)
    actual = WorkflowRuntime(store,store.registry,'test-worker')._column_action_ledger(project['id'],run['id'])
    directory = store.agents.evidence_page(project['id'],run['id'],worker_id=assigned['agent_instance_id'])
    assert actual[0]['ok'] is False and directory['entries'][0]['ok'] is False
    assert directory['entries'][0]['exit_code'] == 1


def test_projection_retains_error_and_cache_usage_semantics():
    raw = json.dumps({'ok':False,'status':'failed','output':{'exit_code':1,'stdout':'x'*110000},
                      'error':{'type':'CommandFailed','message':'expected 401 actual 429'},
                      'evidence':{'agent_run_id':'run','tool_call_id':'tool'}})
    view = json.loads(project_result(raw,8000))
    assert view['ok'] is False and view['output']['exit_code'] == 1
    assert view['error']['message'] == 'expected 401 actual 429'
    assert view['projection']['reference']['tool_call_id'] == 'tool'
    assert context_input_tokens({'input_tokens':3530,'cached_input_tokens':185019},'anthropic') == 188549
    assert context_input_tokens({'input_tokens':188549,'cached_input_tokens':185019},'openai') == 188549


def test_context_error_is_not_generic_2013_or_quota():
    def error(message,code=2013):
        return LLMProviderError(ProviderErrorDetails('anthropic','minimax',400,'LLM_BAD_REQUEST',message,code))
    assert error('invalid params, context window exceeds limit (2013)').error_code == 'LLM_CONTEXT_WINDOW_EXCEEDED'
    assert error('invalid params, missing field (2013)').error_code == 'LLM_BAD_REQUEST'
    assert error('usage limit exceeded',2056).error_code == 'LLM_BAD_REQUEST'


def test_overflow_retry_shrinks_context_without_reexecuting_effect(store, tmp_path):
    workflow = agent_workflow()
    project = store.create_project('retry','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'write once')
    calls, sizes = 0, []
    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        sizes.append(len(json.dumps(messages)))
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id='write',name='project.files.write',
                arguments={'path':'result.txt','content':'data '*5000})])
        if calls == 2:
            raise LLMProviderError(ProviderErrorDetails('anthropic','minimax',400,'LLM_BAD_REQUEST',
                'invalid params, context window exceeds limit (2013)',2013))
        return AgentModelResponse(tool_calls=[AgentToolCall(id='done',name='column.complete',
            arguments={'outcome':'success','output':{'delivered':True},'summary':'done'})])
    WorkflowRuntime(store,store.registry,'owner',AgentCore(store,store.registry,model)).step(task['id'])
    assert calls == 3 and sizes[2] < sizes[1]
    assert store.get_task(task['id'])['status'] == 'done'
    with store.connect() as db:
        assert db.execute("select count(*) from v1_tool_invocations where capability='project.files.write'").fetchone()[0] == 1


def test_batch_shim_preserves_exit_and_literal_metacharacters(tmp_path, monkeypatch):
    if sys.platform != 'win32':
        return
    from app.v1.process_runner import run_command
    from app.v1.policy import ExecutionLimits
    shim = tmp_path/'test shim.cmd'
    shim.write_text('@echo off\r\necho %1\r\nexit /b 7\r\n')
    result = run_command([str(shim),'a & b'],tmp_path,ExecutionLimits())
    assert result['exit_code'] == 7, result
    assert 'a & b' in result['stdout']
