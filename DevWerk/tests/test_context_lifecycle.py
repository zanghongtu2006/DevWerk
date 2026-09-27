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
