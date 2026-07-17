from __future__ import annotations

import json

import pytest

from app.core.config import reload_settings
from app.services.anthropic_client import AnthropicClient
from app.services.llm_factory import UsageTrackedClient
from app.services.openai_client import OpenAIClient
from app.services.provider_errors import LLMProviderError, is_retryable_llm_error, raise_for_provider_payload


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


def test_openai_adapter_returns_canonical_native_tool_call():
    client = OpenAIClient({"base_url": "https://provider.invalid/v1", "api_key": "token", "model": "m"})
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
    result = client.complete([{"role": "user", "content": "go"}], [tool])

    assert result["tool_calls"] == [{"id": "c1", "name": "system.noop", "arguments": {}}]
    assert sent[0]["json"]["tools"] == [tool]
    assert result["usage"]["total_tokens"] == 5


def test_anthropic_adapter_translates_tool_calls_and_results_without_prompt_protocol():
    client = AnthropicClient({"base_url": "https://provider.invalid", "api_key": "token", "model": "m"})
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


def test_settings_resolve_explicit_json_routing(monkeypatch, tmp_path):
    config = {
        "routing": {"default": "provider/model-a", "workflow": "provider/model-b"},
        "llms": {
            "provider": {
                "api": "openai",
                "base_url": "https://provider.invalid/v1",
                "api_key": "secret",
                "models": {"model-a": {}, "model-b": {"model": "provider-model-b"}},
            }
        },
    }
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_JSON", json.dumps(config))
    settings = reload_settings()
    assert settings.get_llm_config("project")["model"] == "model-a"
    assert settings.get_llm_config("workflow")["model"] == "provider-model-b"


def test_usage_wrapper_tracks_complete_without_leaking_telemetry_arguments(monkeypatch):
    calls = []
    telemetry = []

    class FakeClient:
        last_usage = {"input_tokens": 2, "output_tokens": 3}

        def complete(self, messages, tools):
            calls.append((messages, tools))
            return {"text": "ok", "tool_calls": []}

    monkeypatch.setattr("app.services.llm_factory.record_llm_usage", lambda **kwargs: telemetry.append(kwargs))
    wrapper = UsageTrackedClient(FakeClient(), {"agent": "workflow", "protocol": "openai", "model": "test"})
    result = wrapper.complete([{"role": "user", "content": "hello"}], [], project_id="p", task_id="t")

    assert result["text"] == "ok"
    assert len(calls) == 1
    assert telemetry[0]["project_id"] == "p"
    assert telemetry[0]["task_id"] == "t"


def test_provider_error_retry_classification_is_explicit():
    with pytest.raises(LLMProviderError) as captured:
        raise_for_provider_payload(
            {"error": {"message": "overloaded", "type": "overloaded_error"}},
            provider="anthropic",
            api_name="test",
            status_code=503,
        )
    assert is_retryable_llm_error(captured.value)

    with pytest.raises(LLMProviderError) as captured_auth:
        raise_for_provider_payload(
            {"error": {"message": "unauthorized", "type": "authentication_error"}},
            provider="anthropic",
            api_name="test",
            status_code=401,
        )
    assert not is_retryable_llm_error(captured_auth.value)
