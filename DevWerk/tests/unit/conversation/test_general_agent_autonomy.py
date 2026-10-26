from __future__ import annotations

import asyncio
import json
import threading

from app.v1.agent import AgentCore
from app.v1.conversations.conversation import ConversationGateway
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.tooling.capability_registry import CapabilityEntry
from app.v1.protocols.tools.core.capability_protocol import CapabilityToolset
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow


def test_management_boundary_is_explicit_in_tool_catalog(store):
    for tool_id in ('workflow.create', 'task.create', 'agent.worker.send', 'project.scope.revise'):
        assert store.registry.is_workflow_governed(tool_id)
    for tool_id in ('project.files.write', 'project.command.run', 'project.memory.write',
                    'system.files.write', 'system.command.run'):
        assert not store.registry.is_workflow_governed(tool_id)


def test_loop_guides_are_discovered_only_when_agent_calls_tools(store, tmp_path, monkeypatch):
    project = store.create_project('loop discovery', '', str(tmp_path / 'project'))
    list_calls = []
    list_loops = store.list_loops

    def tracked_list_loops(**kwargs):
        list_calls.append(kwargs)
        return list_loops(**kwargs)

    monkeypatch.setattr(store, 'list_loops', tracked_list_loops)
    calls = 0

    def model(messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        current = next(json.loads(item['content']) for item in reversed(messages)
            if item['role'] == 'user' and 'authoritative_current_request' in item['content'])
        assert 'loops' not in current['authoritative_project_state']
        exposed = {item['function']['name'] for item in tools}
        assert {'loop.list', 'loop.inspect'} <= exposed
        if calls == 1:
            assert list_calls == []
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='find-guide', name='loop.list', arguments={'query': 'novel'},
            )])
        if calls == 2:
            assert len(list_calls) == 1
            assert list_calls[0]['query'] == 'novel'
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='read-guide', name='loop.inspect', arguments={'loop_key': 'novel.production'},
            )])
        return AgentModelResponse(text='已找到可参考的写作方法。')

    job = _run_user_turn(store, project['id'], model, '请查找适合长篇小说的工作流参考方法。')
    assert job['status'] == 'succeeded', job.get('error')
    assert len(list_calls) == 1
    invocations = store.tool_invocations(project['id'], job['agent_run_id'])
    assert [item['capability'] for item in invocations] == ['loop.list', 'loop.inspect']
    assert all(item['ok'] for item in invocations)
    assert [item['loop_key'] for item in invocations[0]['result']['output']] == ['novel.production']
    assert invocations[1]['result']['output']['loop_key'] == 'novel.production'


def test_general_agent_discovers_and_calls_external_tool_without_workflow(store, tmp_path):
    store.registry.register(CapabilityEntry(
        id='mcp__fixture__lookup', description='Look up an external fact',
        input_schema={'type': 'object', 'properties': {'query': {'type': 'string'}},
                      'required': ['query'], 'additionalProperties': False},
        output_schema={'type': 'object', 'properties': {'answer': {'type': 'string'}},
                       'required': ['answer'], 'additionalProperties': False},
        handler=lambda _args, _ctx: {'answer': 'verified'}, toolset=CapabilityToolset.MCP,
        side_effect_kind='read',
    ))
    project = store.create_project('general external lookup', '', str(tmp_path / 'project'))
    calls = 0

    def model(_messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        exposed = {tool['function']['name'] for tool in tools}
        if calls == 1:
            assert 'mcp__fixture__lookup' not in exposed
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='find-tool', name='tool.catalog.search', arguments={'query': 'external fact'},
            )])
        if calls == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='describe-tool', name='tool.catalog.describe',
                arguments={'tool_id': 'mcp__fixture__lookup'},
            )])
        if calls == 3:
            assert 'tool.catalog.call' in exposed
            assert 'mcp__fixture__lookup' not in exposed
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='lookup', name='tool.catalog.call',
                arguments={'tool_id': 'mcp__fixture__lookup',
                           'arguments': {'query': 'current fact'}},
            )])
        return AgentModelResponse(text=json.dumps({
            'mode': 'work_result', 'tool_call_ids': ['lookup'], 'message': 'Fact checked.',
        }))

    job = _run_user_turn(store, project['id'], model, 'Please look up a fact.')
    assert job['status'] == 'succeeded', job.get('error')
    assert [item['capability'] for item in store.tool_invocations(
        project['id'], job['agent_run_id'])] == [
            'tool.catalog.search', 'tool.catalog.describe', 'mcp__fixture__lookup',
        ]
    assert store.agents.list_requirements(project['id']) == []


def _run_user_turn(store, project_id: str, model, message: str):
    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))

    async def run():
        await gateway.start()
        try:
            accepted = await gateway.submit(project_id, message)
            assert await gateway.wait_for_idle(timeout=15)
            return store.get_conversation_job(accepted['job']['id'])
        finally:
            await gateway.stop()

    return asyncio.run(run())


def test_general_file_work_needs_no_requirement_grant(store, tmp_path):
    project = store.create_project('general agent', '', str(tmp_path / 'project'))
    calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='write', name='project.files.write',
                arguments={'path': 'note.txt', 'content': 'direct work'},
            )])
        return AgentModelResponse(text=json.dumps({
            'mode': 'work_result', 'tool_call_ids': ['write'],
            'message': 'The requested note is saved.',
        }))

    job = _run_user_turn(store, project['id'], model, 'Save a note in this Project.')
    assert job['status'] == 'succeeded', job.get('error')
    assert (tmp_path / 'project' / 'note.txt').read_text(encoding='utf-8') == 'direct work'
    assert store.agents.list_requirements(project['id']) == []
    assert [item['capability'] for item in store.tool_invocations(
        project['id'], job['agent_run_id'])] == ['project.files.write']


def test_general_memory_work_needs_no_requirement_grant(store, tmp_path):
    project = store.create_project('general memory', '', str(tmp_path / 'project'))

    def model(messages, _tools, **_kwargs):
        if not any(item.get('role') == 'tool' for item in messages):
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='remember', name='project.memory.write',
                arguments={'kind': 'user_preference', 'scope': 'project', 'content': 'Use concise replies.'},
            )])
        return AgentModelResponse(text=json.dumps({
            'mode': 'work_result', 'tool_call_ids': ['remember'],
        }))

    job = _run_user_turn(store, project['id'], model, 'Remember my reply preference.')
    assert job['status'] == 'succeeded', job.get('error')
    assert store.agents.list_requirements(project['id']) == []
    invocations = store.tool_invocations(project['id'], job['agent_run_id'])
    assert len(invocations) == 1
    assert invocations[0]['capability'] == 'project.memory.write'
    assert invocations[0]['ok']


def test_running_workflow_does_not_take_away_general_agent_tools(store, tmp_path):
    project = store.create_project('parallel agent work', '', str(tmp_path / 'project'))
    publish_planned_workflow(store, project['id'], sequence_workflow())
    task = create_planned_task(store, project['id'], 'Managed work')
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET status='running' WHERE id=?", (task['id'],))

    def model(messages, _tools, **_kwargs):
        if not any(item.get('role') == 'tool' for item in messages):
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='write', name='project.files.write',
                arguments={'path': 'independent-note.txt', 'content': 'still available'},
            )])
        return AgentModelResponse(text=json.dumps({
            'mode': 'work_result', 'tool_call_ids': ['write'],
        }))

    job = _run_user_turn(store, project['id'], model, 'Save an independent note.')
    assert job['status'] == 'succeeded', job.get('error')
    assert (tmp_path / 'project' / 'independent-note.txt').read_text(encoding='utf-8') == 'still available'
    assert store.get_project_task(project['id'], task['id'])['status'] == 'running'


def test_new_user_message_preempts_old_model_call_without_late_effect(store, tmp_path):
    project = store.create_project('steerable agent', '', str(tmp_path / 'project'))
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            release.wait(5)
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='stale-write', name='project.files.write',
                arguments={'path': 'stale.txt', 'content': 'obsolete'},
            )])
        return AgentModelResponse(text='收到你的新消息，我会以它为准。')

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))

    async def run():
        await gateway.start()
        try:
            first = await gateway.submit(project['id'], 'Start a long check.')
            assert await asyncio.to_thread(entered.wait, 5)
            second = await gateway.submit(project['id'], 'Stop that check; discuss the next step.')
            assert await gateway.wait_for_idle(timeout=10)
            return (store.get_conversation_job(first['job']['id']),
                    store.get_conversation_job(second['job']['id']))
        finally:
            release.set()
            await gateway.stop()

    first, second = asyncio.run(run())
    assert first['status'] == 'succeeded'
    assert first['result']['business_outcome'] == 'interrupted'
    assert second['status'] == 'succeeded'
    assert '新消息' in second['result']['reply']
    assert not (tmp_path / 'project' / 'stale.txt').exists()


def test_preemption_keeps_effects_already_committed(store, tmp_path):
    project = store.create_project('durable steering', '', str(tmp_path / 'project'))
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id='saved-note', name='project.files.write',
                arguments={'path': 'saved.txt', 'content': 'committed'},
            )])
        if calls == 2:
            entered.set()
            release.wait(5)
            return AgentModelResponse(text='stale answer')
        return AgentModelResponse(text='New request received.')

    gateway = ConversationGateway(store, store.registry,
                                  agent_core=AgentCore(store, store.registry, model))

    async def run():
        await gateway.start()
        try:
            first = await gateway.submit(project['id'], 'Save a note, then continue.')
            assert await asyncio.to_thread(entered.wait, 5)
            second = await gateway.submit(project['id'], 'Stop and answer the new request.')
            assert await gateway.wait_for_idle(timeout=10)
            return (store.get_conversation_job(first['job']['id']),
                    store.get_conversation_job(second['job']['id']))
        finally:
            release.set()
            await gateway.stop()

    first, second = asyncio.run(run())
    assert first['result']['business_outcome'] == 'interrupted'
    assert second['status'] == 'succeeded'
    assert (tmp_path / 'project' / 'saved.txt').read_text(encoding='utf-8') == 'committed'
    assert [item['capability'] for item in store.tool_invocations(
        project['id'], first['agent_run_id'])] == ['project.files.write']
