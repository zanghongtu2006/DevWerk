from __future__ import annotations

import json
import logging

import pytest
import requests

from app.core.config import reload_settings
from app.services.anthropic_client import AnthropicClient
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


def test_real_orchestration_provider_schema_exposes_flat_exact_string_transport(store, tmp_path):
    project = store.create_project("provider exact strings", "", str(tmp_path / "project"))
    registry = build_core_registry(store.policy)
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        start_task=True,
    )
    strict_schema = registry.schemas(["orchestration.plan.save"], context)[0]["function"]["parameters"]
    exact_strings = strict_schema["$defs"]["OrchestrationTaskPlan"]["properties"]["exact_input_strings"]
    assert "generated helper-program bodies" in exact_strings["description"]
    assert "must not be inlined" in exact_strings["description"]
    provider_schema = provider_contract_schema(strict_schema)
    task_plan = (
        provider_schema["properties"]["plan"]["properties"]["task_portfolio"]["items"]
    )
    exact_strings = task_plan["properties"]["exact_input_strings"]
    escaped_value = exact_strings["items"]["properties"]["escaped_value"]

    assert escaped_value["type"] == "string"
    assert "\\n" in escaped_value["description"]
    assert "numbers or booleans" in exact_strings["description"]


def test_real_orchestration_provider_schema_requires_task_agent_execution_policy(store, tmp_path):
    project = store.create_project("provider agent policy", "", str(tmp_path / "project"))
    registry = build_core_registry(store.policy)
    context = CapabilityContext(
        project_id=project["id"],
        project=project,
        store=store,
        start_task=True,
    )
    strict_schema = registry.schemas(["orchestration.plan.save"], context)[0]["function"]["parameters"]
    provider_schema = provider_contract_schema(strict_schema)
    task_plan = provider_schema["properties"]["plan"]["properties"]["task_portfolio"]["items"]

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
        "routing": {"default": "provider/model-a", "workflow": "provider/model-b"},
        "llms": {
            "provider": {
                "api": "openai",
                "base_url": "https://provider.invalid/v1",
                "api_key": "secret",
                "models": {
                    "model-a": {"temperature": 0.2, "thinking_mode": "balanced", "max_tokens": 65535},
                    "model-b": {"model": "provider-model-b", "temperature": 0.2, "thinking_mode": "balanced", "max_tokens": 65535},
                },
            }
        },
    }
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_JSON", json.dumps(config))
    settings = reload_settings()
    assert settings.get_llm_config("project")["model"] == "model-a"
    assert settings.get_llm_config("workflow")["model"] == "provider-model-b"


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
