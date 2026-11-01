from __future__ import annotations

import json
import logging

import pytest
import requests

from app.core.config import reload_settings
from app.services.anthropic_client import AnthropicClient
from app.v1.agents.agent_prompt import assistant_message
from app.v1.domain import AgentModelResponse
from app.v1.context.session_replay import replayable_session_messages
from app.services.llm_factory import UsageTrackedClient
from app.services.openai_client import OpenAIClient
from app.services.provider_errors import LLMProviderError, raise_for_provider_payload, raise_for_provider_response
from app.v1.capabilities import (
    CapabilityContext,
    build_core_registry,
    canonicalize_workflow_capability_arguments,
)
from app.v1.contracts import (
    ContractError,
    canonicalize_contract_value,
    provider_contract_schema,
    validate_contract,
)


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class SSEMockResponse:
    def __init__(self, events, *, status_code=200):
        self.events = events
        self.status_code = status_code
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_lines(self, **_kwargs):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event.encode('utf-8')


def sse(event, payload):
    return [f'event: {event}', f'data: {json.dumps(payload)}', '']


def test_anthropic_stream_assembles_complete_text_thinking_and_tool_use():
    from app.services.visible_stream import listen_for_visible_text
    client = AnthropicClient({'api_name': 'test', 'base_url': 'https://provider.invalid',
        'api_key': 'token', 'model': 'm', 'temperature': 0.2, 'max_tokens': 100,
        'stream_response': True})
    events = [
        *sse('message_start', {'type': 'message_start', 'message': {
            'role': 'assistant', 'usage': {'input_tokens': 12, 'output_tokens': 0}}}),
        *sse('content_block_start', {'type': 'content_block_start', 'index': 0,
            'content_block': {'type': 'thinking', 'thinking': ''}}),
        *sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
            'delta': {'type': 'thinking_delta', 'thinking': 'consider'}}),
        *sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
            'delta': {'type': 'signature_delta', 'signature': 'signed'}}),
        *sse('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
        *sse('content_block_start', {'type': 'content_block_start', 'index': 1,
            'content_block': {'type': 'text', 'text': ''}}),
        *sse('content_block_delta', {'type': 'content_block_delta', 'index': 1,
            'delta': {'type': 'text_delta', 'text': 'ready'}}),
        *sse('content_block_stop', {'type': 'content_block_stop', 'index': 1}),
        *sse('content_block_start', {'type': 'content_block_start', 'index': 2,
            'content_block': {'type': 'tool_use', 'id': 'call-1', 'name': 'project.inspect', 'input': {}}}),
        *sse('content_block_delta', {'type': 'content_block_delta', 'index': 2,
            'delta': {'type': 'input_json_delta', 'partial_json': '{"limit":'}}),
        *sse('content_block_delta', {'type': 'content_block_delta', 'index': 2,
            'delta': {'type': 'input_json_delta', 'partial_json': '2}'}}),
        *sse('content_block_stop', {'type': 'content_block_stop', 'index': 2}),
        *sse('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'},
            'usage': {'output_tokens': 8}}),
        *sse('message_stop', {'type': 'message_stop'}),
    ]
    sent = []
    client.session.post = lambda _url, **kwargs: sent.append(kwargs) or SSEMockResponse(events)
    tool = {'type': 'function', 'function': {'name': 'project.inspect', 'description': '',
        'parameters': {'type': 'object', 'properties': {'limit': {'type': 'integer'}}}}}

    visible = []
    reply_messages = []
    with listen_for_visible_text(visible.append, public_field=('conversation.reply', 'message', reply_messages.append)):
        result = client.complete([{'role': 'user', 'content': 'go'}], [tool])
    assert visible == ['ready']
    assert reply_messages == []

    assert sent[0]['stream'] is True and sent[0]['json']['stream'] is True
    assert result['text'] == 'ready'
    assert result['tool_calls'][0]['arguments'] == {'limit': 2}
    assert result['usage']['input_tokens'] == 12
    assert result['usage']['output_tokens'] == 8
    assert result['provider_content']['blocks'][0]['signature'] == 'signed'


def test_partial_reply_streams_only_the_public_top_level_message():
    from app.services.visible_stream import listen_for_visible_text, publish_tool_preview
    visible = []
    fragments = ['{"nested":{"message":"internal"},"message":"已', '保存","tool_call_ids":["private"]}']
    with listen_for_visible_text(visible.append, public_field=('conversation.reply', 'message', visible.append)):
        publish_tool_preview('project.files.write', fragments, 100)
        publish_tool_preview('conversation.reply', fragments[:1], 50)
        publish_tool_preview('conversation.reply', fragments, 100)
    assert visible == ['已', '已保存']


def test_anthropic_stream_never_returns_partial_tool_call_without_message_stop():
    client = AnthropicClient({'api_name': 'test', 'base_url': 'https://provider.invalid',
        'api_key': 'token', 'model': 'm', 'temperature': 0.2, 'max_tokens': 100,
        'stream_response': True})
    events = [
        *sse('message_start', {'type': 'message_start', 'message': {'role': 'assistant'}}),
        *sse('content_block_start', {'type': 'content_block_start', 'index': 0,
            'content_block': {'type': 'tool_use', 'id': 'partial', 'name': 'project.inspect', 'input': {}}}),
        *sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
            'delta': {'type': 'input_json_delta', 'partial_json': '{"limit":2}'}}),
        *sse('content_block_stop', {'type': 'content_block_stop', 'index': 0}),
    ]
    client.session.post = lambda _url, **_kwargs: SSEMockResponse(events)
    with pytest.raises(requests.ConnectionError, match='before message_stop'):
        client.complete([{'role': 'user', 'content': 'go'}], [])


def test_anthropic_stream_provider_error_and_timeout_are_not_partial_success():
    client = AnthropicClient({'api_name': 'test', 'base_url': 'https://provider.invalid',
        'api_key': 'token', 'model': 'm', 'temperature': 0.2, 'max_tokens': 100,
        'stream_response': True, 'request_timeout_seconds': 17})
    client.session.post = lambda _url, **_kwargs: SSEMockResponse([
        *sse('message_start', {'type': 'message_start', 'message': {'role': 'assistant'}}),
        *sse('error', {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'busy'}}),
    ])
    with pytest.raises(LLMProviderError) as failed:
        client.complete([{'role': 'user', 'content': 'go'}], [])
    assert failed.value.error_code == 'LLM_OVERLOADED'

    client.session.post = lambda _url, **_kwargs: SSEMockResponse([
        *sse('message_start', {'type': 'message_start', 'message': {'role': 'assistant'}}),
        requests.Timeout('idle stream'),
    ])
    with pytest.raises(LLMProviderError) as timed_out:
        client.complete([{'role': 'user', 'content': 'go'}], [])
    assert timed_out.value.error_code == 'LLM_TIMEOUT'


def test_llm_config_is_frozen_for_running_settings_instance(tmp_path, monkeypatch):
    original = reload_settings().llm_config().model_dump(mode='json')
    original['models']['main']['stream_response'] = False
    path = tmp_path / 'llm.json'
    path.write_text(json.dumps(original), encoding='utf-8')
    monkeypatch.setenv('DEVWERK_LLM_CONFIG_PATH', str(path))
    running = reload_settings()
    assert running.llm_config().models['main'].stream_response is False

    original['models']['main']['stream_response'] = True
    path.write_text(json.dumps(original), encoding='utf-8')
    assert running.llm_config().models['main'].stream_response is False
    detached = running.llm_config()
    detached.models['main'].stream_response = True
    assert running.llm_config().models['main'].stream_response is False
    assert reload_settings().llm_config().models['main'].stream_response is True


def test_anthropic_skips_empty_assistant_records_without_losing_tool_history():
    _, messages = AnthropicClient._to_provider_messages([
        {'role':'user','content':'Discuss'},
        {'role':'assistant','content':'','tool_calls':[]},
        {'role':'user','content':'Please reply'},
        {'role':'assistant','content':'','tool_calls':[{'id':'read','function':{
            'name':'project.files.read','arguments':'{"path":"baseline.md"}'}}]},
        {'role':'tool','tool_call_id':'read','content':'baseline'},
    ])
    assert all(m['content'] for m in messages)
    assert len(messages) == 3
    assert messages[1]['content'][0]['type'] == 'tool_use'
    assert messages[2]['content'][0]['tool_use_id'] == 'read'


def test_anthropic_replays_signed_thinking_in_original_block_order():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid",
        "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 1024})
    response_blocks = [
        {"type": "thinking", "thinking": "check the result", "signature": "signed"},
        {"type": "text", "text": "working"},
        {"type": "redacted_thinking", "data": "redacted"},
        {"type": "tool_use", "id": "call-1", "name": "system.noop", "input": {}},
    ]
    client.session.post = lambda _url, **_kwargs: Response(
        {"content": response_blocks, "usage": {}})
    tool = {"type": "function", "function": {"name": "system.noop", "parameters": {"type": "object"}}}
    completion = AgentModelResponse.model_validate(client.complete(
        [{"role": "user", "content": "go"}], [tool]))
    assistant = assistant_message(completion)
    replay = replayable_session_messages([
        assistant,
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
    ])
    _, provider_messages = client._to_provider_messages(replay)

    assert provider_messages[0]["content"] == response_blocks
    assert provider_messages[1]["content"] == [
        {"type": "tool_result", "tool_use_id": "call-1", "content": '{"ok":true}'}]
    assert completion.text == "working"
    assert completion.tool_calls[0].name == "system.noop"


def test_anthropic_rejects_unsigned_thinking_instead_of_silently_dropping_it():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid",
        "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 1024})
    client.session.post = lambda _url, **_kwargs: Response(
        {"content": [{"type": "thinking", "thinking": "unsigned"}], "usage": {}})
    with pytest.raises(ValueError):
        client.complete([{"role": "user", "content": "go"}])


def test_anthropic_replay_uses_executed_tool_id_after_runner_normalization():
    completion = AgentModelResponse.model_validate({
        "tool_calls": [{"id": "original", "name": "system.noop", "arguments": {}}],
        "provider_content": {"protocol": "anthropic", "blocks": [
            {"type": "thinking", "thinking": "private", "signature": "signed"},
            {"type": "tool_use", "id": "original", "name": "system.noop", "input": {}},
        ]},
    })
    completion.tool_calls[0].id = "normalized"
    assistant = assistant_message(completion)
    _, messages = AnthropicClient._to_provider_messages([assistant,
        {"role": "tool", "tool_call_id": "normalized", "content": "done"}])
    assert messages[0]["content"][0]["signature"] == "signed"
    assert messages[0]["content"][1]["id"] == "normalized"
    assert messages[1]["content"][0]["tool_use_id"] == "normalized"


def test_signed_thinking_is_durable_but_absent_from_public_agent_messages(store, tmp_path):
    project = store.create_project("thinking replay", "", str(tmp_path / "project"))
    run = store.begin_agent_run(project_id=project["id"], kind="column",
        instruction_revision=1, instruction_snapshot="", context_snapshot={},
        capabilities=[], platform_policy=store.latest_platform_policy(),
        runtime_policy=store.policy)
    completion = AgentModelResponse.model_validate({
        "tool_calls": [{"id": "call-1", "name": "system.noop", "arguments": {}}],
        "provider_content": {"protocol": "anthropic", "blocks": [
            {"type": "thinking", "thinking": "private", "signature": "signed"},
            {"type": "tool_use", "id": "call-1", "name": "system.noop", "input": {}},
        ]},
    })
    assistant = assistant_message(completion)
    saved = store.add_agent_message(run["id"], "assistant", "",
        assistant["tool_calls"], provider_content=assistant["provider_content"])
    store.add_agent_message(run["id"], "tool", '{"ok":true}', [], "call-1")
    with store.connect() as db:
        rows = db.execute("SELECT role,content,tool_calls_json,tool_call_id,provider_content_json "
            "FROM v1_agent_messages WHERE agent_run_id=? ORDER BY sequence", (run["id"],)).fetchall()
    history = [{"role": row["role"], "content": row["content"],
        "tool_calls": json.loads(row["tool_calls_json"]), "tool_call_id": row["tool_call_id"],
        "provider_content": json.loads(row["provider_content_json"]) if row["provider_content_json"] else None}
        for row in rows]
    replay = replayable_session_messages(history)

    assert replay[0]["provider_content"]["blocks"][0]["signature"] == "signed"
    assert saved["provider_content"]["blocks"][0]["thinking"] == "private"
    assert "provider_content_json" not in store.agent_messages(project["id"], run["id"])[0]


def test_contract_error_explains_nested_discriminator_branch_failure():
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "agent"},
                    "max_iterations": {"type": "integer"},
                },
                "required": ["kind", "max_iterations"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "capability_sequence"},
                    "steps": {"type": "array"},
                },
                "required": ["kind", "steps"],
                "additionalProperties": False,
            },
        ]
    }

    with pytest.raises(ContractError) as raised:
        validate_contract(
            {
                "kind": "capability_sequence",
                "steps": [],
                "max_iterations": 6,
            },
            schema,
            label="workflow.publish input",
        )

    message = str(raised.value)
    assert "branch details" in message
    assert "max_iterations" in message
    assert "Additional properties" in message
    assert "'agent' was expected" not in message


def test_openai_adapter_returns_canonical_native_tool_call(caplog):
    caplog.set_level(logging.DEBUG, logger="devwerk.llm.provider.openai")
    client = OpenAIClient({"api_name": "test", "base_url": "https://provider.invalid/v1", "api_key": "token", "model": "m", "temperature": 0.2})
    sent = []
    client.session.post = lambda _url, **kwargs: sent.append(kwargs) or Response(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "system.noop", "arguments": "{}"}}
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
    )
    tool = {"type": "function", "function": {"name": "system.noop", "description": "", "parameters": {"type": "object"}}}
    result = client.complete([{"role": "user", "content": "go"}], [tool], trace_id="trace-provider", require_tool=True)

    assert result["tool_calls"] == [{"id": "c1", "name": "system.noop", "arguments": {}}]
    assert sent[0]["json"]["tools"] == [tool]
    assert sent[0]["json"]["tool_choice"] == "required"
    assert result["usage"]["total_tokens"] == 5
    assert "llm.provider_request" in caplog.text
    assert '"trace_id":"trace-provider"' in caplog.text
    assert '"content":"go"' in caplog.text
    assert "llm.provider_response" in caplog.text
    assert "total_tokens" in caplog.text


def test_openai_adapter_can_force_one_selected_tool():
    client = OpenAIClient({"api_name": "test", "base_url": "https://provider.invalid/v1", "api_key": "token", "model": "m", "temperature": 0.2})
    sent = []
    client.session.post = lambda _url, **kwargs: sent.append(kwargs) or Response(
        {"choices": [{"message": {"content": "", "tool_calls": []}}], "usage": {}}
    )
    tools = [
        {"type": "function", "function": {"name": name, "description": "", "parameters": {"type": "object"}}}
        for name in ("project.inspect", "task.create")
    ]

    client.complete(
        [{"role": "user", "content": "go"}],
        tools,
        require_tool=True,
        required_tool_name="task.create",
    )

    assert sent[0]["json"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "task.create"},
    }


def test_provider_request_uses_transport_timeout_and_reports_timeout_error():
    client = OpenAIClient({
        "api_name": "test",
        "base_url": "https://provider.invalid/v1",
        "api_key": "token",
        "model": "m",
        "temperature": 0.2,
        "request_timeout_seconds": 17,
    })
    sent = []

    def timeout(_url, **kwargs):
        sent.append(kwargs)
        raise requests.Timeout("provider did not respond")

    client.session.post = timeout

    with pytest.raises(LLMProviderError) as captured:
        client.complete([{"role": "user", "content": "go"}], [])

    assert sent[0]["timeout"] == 17
    assert captured.value.error_code == "LLM_TIMEOUT"
    assert "17 seconds" in str(captured.value)


def test_anthropic_adapter_translates_tool_calls_and_results_without_prompt_protocol():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid", "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 65535})
    system, messages = client._to_provider_messages(
        [
            {"role": "system", "content": '{"protocol_version":"devwerk.agent.v1"}'},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "system.noop", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "system.noop", "content": '{"ok":true}'},
        ]
    )
    assert system == '{"protocol_version":"devwerk.agent.v1"}'
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"


def test_anthropic_transport_retries_connection_establishment_only():
    client = AnthropicClient({
        "api_name": "test",
        "base_url": "https://provider.invalid",
        "api_key": "token",
        "model": "m",
        "temperature": 0.2,
        "max_tokens": 65535,
        "connect_max_attempts": 4,
    })

    retry = client.session.get_adapter(client.url).max_retries
    assert retry.connect == 3
    assert retry.total == 3
    assert retry.read == 0
    assert retry.status == 0


def test_anthropic_adapter_canonicalizes_schema_shaped_array_and_boolean_wrappers():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid", "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 65535})
    client.session.post = lambda _url, **_kwargs: Response(
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "plan.save",
                    "input": {
                        "plan": {
                            "evidence": {"item": ["first", "second"]},
                            "self_check": "true",
                        }
                    },
                }
            ],
            "usage": {},
        }
    )
    tool = {
        "type": "function",
        "function": {
            "name": "plan.save",
            "description": "",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"$ref": "#/$defs/Plan"},
                },
                "$defs": {
                    "Plan": {
                        "type": "object",
                        "properties": {
                            "evidence": {"type": "array", "items": {"type": "string"}},
                            "self_check": {"type": "boolean"},
                        },
                    }
                },
            },
        },
    }

    result = client.complete([{"role": "user", "content": "go"}], [tool])

    assert result["tool_calls"][0]["arguments"] == {
        "plan": {"evidence": {"item": ["first", "second"]}, "self_check": "true"}
    }
    assert canonicalize_contract_value(result["tool_calls"][0]["arguments"],
                                       tool["function"]["parameters"]) == {
        "plan": {"evidence": ["first", "second"], "self_check": True}
    }


def test_anthropic_adapter_can_require_a_native_tool_call():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid", "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 65535})
    sent = []
    client.session.post = lambda _url, **kwargs: sent.append(kwargs) or Response(
        {"content": [{"type": "tool_use", "id": "c1", "name": "system.noop", "input": {}}], "usage": {}}
    )
    tool = {"type": "function", "function": {"name": "system.noop", "description": "", "parameters": {"type": "object"}}}

    client.complete([{"role": "user", "content": "go"}], [tool], require_tool=True)

    assert sent[0]["json"]["tool_choice"] == {"type": "any"}


def test_anthropic_adapter_can_force_one_selected_tool():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid", "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 65535})
    sent = []
    client.session.post = lambda _url, **kwargs: sent.append(kwargs) or Response(
        {"content": [], "usage": {}}
    )
    tools = [
        {"type": "function", "function": {"name": name, "description": "", "parameters": {"type": "object"}}}
        for name in ("project.inspect", "task.create")
    ]

    client.complete(
        [{"role": "user", "content": "go"}],
        tools,
        require_tool=True,
        required_tool_name="task.create",
    )

    assert sent[0]["json"]["tool_choice"] == {
        "type": "tool",
        "name": "task.create",
    }


def test_anthropic_adapter_materializes_provider_schema_and_scalar_wrappers():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid", "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 65535})
    sent = []
    client.session.post = lambda _url, **kwargs: sent.append(kwargs) or Response(
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "publish",
                    "input": {
                        "items": "one",
                        "empty_items": "",
                        "confirmed": "true",
                        "executor": {"kind": "agent", "capabilities": {"item": "project.files.write"}},
                        "wait_policy": "",
                    },
                }
            ],
            "usage": {},
        }
    )
    tool = {
        "type": "function",
        "function": {
            "name": "publish",
            "description": "",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "string"}},
                    "empty_items": {"type": "array", "items": {"type": "string"}},
                    "confirmed": {"const": True},
                    "executor": {
                        "anyOf": [
                            {
                                "discriminator": {"propertyName": "kind"},
                                "oneOf": [
                                    {"$ref": "#/$defs/Agent"},
                                    {"$ref": "#/$defs/Sequence"},
                                ],
                            },
                            {"type": "null"},
                        ]
                    },
                    "wait_policy": {"anyOf": [{"$ref": "#/$defs/Wait"}, {"type": "null"}]},
                },
                "$defs": {
                    "Agent": {
                        "type": "object",
                        "properties": {
                            "kind": {"const": "agent"},
                            "capabilities": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["capabilities"],
                    },
                    "Sequence": {
                        "type": "object",
                        "properties": {
                            "kind": {"const": "sequence"},
                            "steps": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["steps"],
                    },
                    "Wait": {
                        "type": "object",
                        "properties": {"kind": {"const": "timer"}},
                    },
                },
            },
        },
    }

    result = client.complete([{"role": "user", "content": "go"}], [tool])

    provider_schema = sent[0]["json"]["tools"][0]["input_schema"]
    provider_text = json.dumps(provider_schema)
    assert "$ref" not in provider_text
    assert "$defs" not in provider_text
    assert '"discriminator":' not in provider_text
    executor_schema = provider_schema["properties"]["executor"]
    assert executor_schema["type"] == "object"
    assert executor_schema["required"] == ["kind"]
    assert set(executor_schema["properties"]["kind"]["enum"]) == {"agent", "sequence"}
    assert {"capabilities", "steps"} <= set(executor_schema["properties"])
    assert "When kind='agent'" in executor_schema["description"]
    assert "When kind='sequence'" in executor_schema["description"]
    assert result["tool_calls"][0]["arguments"] == {
        "items": "one", "empty_items": "", "confirmed": "true",
        "executor": {"kind": "agent", "capabilities": {"item": "project.files.write"}},
        "wait_policy": "",
    }
    assert canonicalize_contract_value(result["tool_calls"][0]["arguments"],
                                       tool["function"]["parameters"]) == {
        "items": ["one"],
        "empty_items": [],
        "confirmed": True,
        "executor": {"kind": "agent", "capabilities": ["project.files.write"]},
        "wait_policy": None,
    }


def test_anthropic_adapter_round_trips_the_second_discriminated_object_variant():
    client = AnthropicClient({"api_name": "test", "base_url": "https://provider.invalid", "api_key": "token", "model": "m", "temperature": 0.2, "max_tokens": 65535})
    client.session.post = lambda _url, **_kwargs: Response(
        {
            "content": [{
                "type": "tool_use",
                "id": "c1",
                "name": "publish",
                "input": {
                    "executor": {
                        "kind": "sequence",
                        "steps": {"item": {"capability": "system.noop"}},
                    }
                },
            }],
            "usage": {},
        }
    )
    tool = {
        "type": "function",
        "function": {
            "name": "publish",
            "description": "",
            "parameters": {
                "type": "object",
                "required": ["executor"],
                "properties": {
                    "executor": {
                        "discriminator": {"propertyName": "kind"},
                        "oneOf": [
                            {
                                "type": "object",
                                "required": ["kind", "capabilities"],
                                "properties": {
                                    "kind": {"const": "agent"},
                                    "capabilities": {"type": "array", "items": {"type": "string"}},
                                },
                                "additionalProperties": False,
                            },
                            {
                                "type": "object",
                                "required": ["kind", "steps"],
                                "properties": {
                                    "kind": {"const": "sequence"},
                                    "steps": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["capability"],
                                            "properties": {"capability": {"type": "string"}},
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "additionalProperties": False,
                            },
                        ],
                    }
                },
                "additionalProperties": False,
            },
        },
    }

    result = client.complete([{"role": "user", "content": "go"}], [tool])

    assert result["tool_calls"][0]["arguments"] == {
        "executor": {"kind": "sequence", "steps": {"item": {"capability": "system.noop"}}}
    }
    assert canonicalize_contract_value(result["tool_calls"][0]["arguments"],
                                       tool["function"]["parameters"]) == {
        "executor": {
            "kind": "sequence",
            "steps": [{"capability": "system.noop"}],
        }
    }


def test_workflow_publish_canonicalizes_dynamic_nested_capability_arguments():
    registry = build_core_registry()
    workflow = {
        "columns": [
            {
                "executor": {
                    "kind": "capability_sequence",
                    "steps": [
                        {
                            "capability": "project.command.run",
                                "arguments": {
                                    "argv": {"item": ["python", "-c", "print(1)"]},
                            },
                        },
                        {
                            "capability": "project.files.write",
                            "arguments": {
                                "$text": '{"path":"probe.txt","content":"ready"}'
                            },
                        }
                    ],
                },
                "wait_policy": {
                    "poll_capability": "project.command.run",
                    "poll_arguments": {"argv": {"item": "python"}},
                },
            }
        ]
    }

    normalized = canonicalize_workflow_capability_arguments(workflow, registry)

    assert normalized["columns"][0]["executor"]["steps"][0]["arguments"] == {
        "argv": ["python", "-c", "print(1)"],
    }
    assert normalized["columns"][0]["executor"]["steps"][1]["arguments"] == {
        "path": "probe.txt",
        "content": "ready",
    }
    assert normalized["columns"][0]["wait_policy"]["poll_arguments"] == {
        "argv": ["python"],
    }
    assert workflow["columns"][0]["executor"]["steps"][0]["arguments"]["argv"] == {
        "item": ["python", "-c", "print(1)"]
    }


def test_real_workflow_publish_provider_schema_keeps_executor_fields_visible(store, tmp_path):
    project = store.create_project("provider workflow schema", "", str(tmp_path / "project"))
    registry = build_core_registry(store.policy)
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        start_task=True,
    )
    strict_schema = registry.schemas(["workflow.publish"], context)[0]["function"]["parameters"]
    provider_schema = provider_contract_schema(strict_schema)
    executor = (
        provider_schema["properties"]["workflow"]["properties"]["columns"]["items"]
        ["properties"]["executor"]
    )

    assert executor["type"] == "object"
    assert executor["required"] == ["kind"]
    assert set(executor["properties"]["kind"]["enum"]) == {
        "agent",
        "capability_sequence",
    }
    assert {"capabilities", "steps", "completed_outcome", "outcome_from"} <= set(
        executor["properties"]
    )
    assert "omit this field when outcome_from" in executor["properties"]["completed_outcome"]["description"].lower()
    assert "without completed_outcome" in executor["properties"]["outcome_from"]["description"]


def test_provider_view_recurses_through_dynamic_schema_keywords_and_boolean_children():
    schema = {
        "type": "object",
        "properties": {
            "named": {"patternProperties": {"^x": {"$ref": "#/$defs/Label"}}},
            "values": {"type": "array", "prefixItems": [True, {"$ref": "#/$defs/Label"}], "items": False},
        },
        "$defs": {"Label": {"type": "string", "minLength": 2}},
    }
    view = provider_contract_schema(schema)
    assert "$ref" not in json.dumps(view)
    assert "$defs" not in json.dumps(view)
    assert view["properties"]["named"]["patternProperties"]["^x"]["minLength"] == 2
    assert view["properties"]["values"]["prefixItems"][0] is True
    assert view["properties"]["values"]["prefixItems"][1]["type"] == "string"
    assert view["properties"]["values"]["items"] is False


def test_real_task_plan_provider_schema_exposes_flat_exact_string_transport(store, tmp_path):
    project = store.create_project("provider exact strings", "", str(tmp_path / "project"))
    registry = build_core_registry(store.policy)
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        start_task=True,
    )
    strict_schema = registry.schemas(["task.plan.save"], context)[0]["function"]["parameters"]
    exact_strings = strict_schema["$defs"]["TaskPlanItem"]["properties"]["exact_input_strings"]
    assert "generated helper-program bodies" in exact_strings["description"]
    assert "must not be inlined" in exact_strings["description"]
    provider_schema = provider_contract_schema(strict_schema)
    task_item = provider_schema["properties"]["plan"]["properties"]["tasks"]["items"]
    exact_strings = task_item["properties"]["exact_input_strings"]
    escaped_value = exact_strings["items"]["properties"]["escaped_value"]

    assert escaped_value["type"] == "string"
    assert "\\n" in escaped_value["description"]
    assert "numbers or booleans" in exact_strings["description"]


def test_real_task_plan_provider_schema_requires_task_agent_execution_policy(store, tmp_path):
    project = store.create_project("provider agent policy", "", str(tmp_path / "project"))
    registry = build_core_registry(store.policy)
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        start_task=True,
    )
    strict_schema = registry.schemas(["task.plan.save"], context)[0]["function"]["parameters"]
    provider_schema = provider_contract_schema(strict_schema)
    task_plan = provider_schema["properties"]["plan"]["properties"]["tasks"]["items"]

    assert "agent_execution" in task_plan["required"]
    assert set(task_plan["properties"]["agent_execution"]["enum"]) == {
        "forbidden",
        "required",
        "allowed",
    }


def test_provider_reference_wrapper_is_normalized_only_as_an_exact_reserved_value():
    schema = {"type": "object", "additionalProperties": True}

    assert canonicalize_contract_value(
        {
            "path": "<$ref>/input/task/input/contract/path</$ref>",
            "content": "prefix <$ref>/input/task/input/contract/content</$ref>",
            "invalid": "<$ref>/input/task/~2invalid</$ref>",
        },
        schema,
    ) == {
        "path": {"$ref": "/input/task/input/contract/path"},
        "content": "prefix <$ref>/input/task/input/contract/content</$ref>",
        "invalid": "<$ref>/input/task/~2invalid</$ref>",
    }


def test_settings_resolve_explicit_json_routing(monkeypatch, tmp_path):
    config = {
        "providers": {
            "provider": {
                "protocol": "openai",
                "base_url": "https://provider.invalid/v1",
                "api_key": "secret",
            }
        },
        "models": {
            "model_a": {"provider": "provider", "model": "model-a", "request_timeout_seconds": 180, "temperature": 0.2, "thinking_mode": "balanced", "max_tokens": 65535},
            "model_b": {"provider": "provider", "model": "provider-model-b", "request_timeout_seconds": 240, "temperature": 0.2, "thinking_mode": "balanced", "max_tokens": 65535},
        },
        "routes": {"conversation": "model_a", "column": "model_b", "default": "model_a"},
        "runtime": {"trust_env_proxy": False},
    }
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_JSON", json.dumps(config))
    settings = reload_settings()
    assert settings.get_llm_config("conversation")["model"] == "model-a"
    assert settings.get_llm_config("conversation")["request_timeout_seconds"] == 180
    assert settings.get_llm_config("column")["model"] == "provider-model-b"
    assert settings.get_llm_config("column")["request_timeout_seconds"] == 240


def test_settings_reject_legacy_or_unknown_llm_fields(monkeypatch, tmp_path):
    config = {
        "routing": {"default": "provider/model-a"},
        "llms": {},
        "timeout": 180,
    }
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_JSON", json.dumps(config))

    with pytest.raises(ValueError, match="configuration schema") as captured:
        reload_settings()

    assert "routing" in str(captured.value)
    assert "llms" in str(captured.value)
    assert "timeout" in str(captured.value)


def test_usage_wrapper_tracks_complete_and_logs_full_io(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="devwerk.llm.trace")
    calls = []
    telemetry = []

    class FakeClient:
        last_usage = {"input_tokens": 2, "output_tokens": 3}

        def complete(self, messages, tools, *, trace_id=None):
            calls.append((messages, tools))
            return {"text": "full model output", "tool_calls": []}

    monkeypatch.setattr("app.services.llm_factory.record_llm_usage", lambda **kwargs: telemetry.append(kwargs))
    wrapper = UsageTrackedClient(FakeClient(), {"agent": "workflow", "protocol": "openai", "model": "test"})
    result = wrapper.complete([{"role": "user", "content": "hello"}], [], project_id="p", task_id="t")

    assert result["text"] == "full model output"
    assert len(calls) == 1
    assert telemetry[0]["project_id"] == "p"
    assert telemetry[0]["task_id"] == "t"
    assert telemetry[0]["trace_id"].startswith("llm_")
    assert "llm.agent_input" in caplog.text
    assert '"content":"hello"' in caplog.text
    assert "llm.agent_output" in caplog.text
    assert '"text":"full model output"' in caplog.text


def test_provider_errors_preserve_provider_details_without_retry_policy():
    with pytest.raises(LLMProviderError) as captured:
        raise_for_provider_payload(
            {"error": {"message": "overloaded", "type": "overloaded_error"}},
            provider="anthropic",
            api_name="test",
            status_code=503,
        )
    assert captured.value.error_code == "LLM_OVERLOADED"
    assert captured.value.status_code == 503

    with pytest.raises(LLMProviderError) as captured_auth:
        raise_for_provider_payload(
            {"error": {"message": "unauthorized", "type": "authentication_error"}},
            provider="anthropic",
            api_name="test",
            status_code=401,
        )
    assert captured_auth.value.error_code == "LLM_AUTHENTICATION_ERROR"
    assert captured_auth.value.status_code == 401


def test_non_json_provider_503_is_still_a_structured_provider_error():
    response = Response(None, status_code=503)
    response.text = "service temporarily unavailable"
    response.reason = "Service Unavailable"
    response.url = "https://provider.invalid/v1/messages"

    def invalid_json():
        raise ValueError("not json")

    response.json = invalid_json

    with pytest.raises(LLMProviderError) as captured:
        raise_for_provider_response(response, provider="anthropic", api_name="test")

    assert captured.value.error_code == "LLM_PROVIDER_ERROR"
    assert captured.value.status_code == 503


def test_minimax_usage_limit_embedded_in_message_is_not_classified_as_rate_limit():
    response = Response(
        {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": "Token Plan usage limit reached (2056)",
            },
        },
        status_code=429,
    )
    response.url = "https://api.minimaxi.com/anthropic/v1/messages"

    with pytest.raises(LLMProviderError) as captured:
        raise_for_provider_response(response, provider="anthropic", api_name="minimax")

    assert captured.value.error_code == "LLM_USAGE_LIMIT"
    assert captured.value.details.provider_code == 2056
