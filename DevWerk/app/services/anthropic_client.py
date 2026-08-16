"""Anthropic-compatible native message and tool-call adapter."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests as http_requests

from app.core.debug_trace import trace_json
from app.services.provider_errors import provider_timeout_error, raise_for_provider_payload, raise_for_provider_response
from app.v1.contracts import canonicalize_contract_value, provider_contract_schema


_trace_log = logging.getLogger("devwerk.llm.provider.anthropic")


class AnthropicClient:
    def __init__(self, config: dict[str, Any]):
        self.last_usage: dict[str, Any] | None = None
        self.api_name = str(config["api_name"])
        self.base_url = str(config["base_url"]).rstrip("/")
        self.api_key = config.get("api_key")
        self.model = str(config["model"])
        self.temperature = float(config["temperature"])
        self.top_p = config.get("top_p")
        self.max_tokens = int(config["max_tokens"])
        self.request_timeout_seconds = float(config.get("request_timeout_seconds", 600.0))
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_tokens < 65_535:
            raise ValueError("Anthropic max_tokens must be explicitly configured to at least 65535")
        self.url = f"{self.base_url}/messages" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/messages"
        self.session = http_requests.Session()
        self.session.trust_env = bool(config.get("trust_env_proxy", False))
        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *, trace_id: str | None = None, require_tool: bool = False) -> dict[str, Any]:
        system, provider_messages = self._to_provider_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": provider_messages,
        }
        if self.top_p is not None:
            payload["top_p"] = float(self.top_p)
        if tools:
            payload["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "input_schema": provider_contract_schema(
                        item["function"].get("parameters") or {"type": "object"}
                    ),
                }
                for item in tools
            ]
            if require_tool:
                payload["tool_choice"] = {"type": "any"}
        headers = {
            "x-api-key": str(self.api_key),
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        trace_json(
            _trace_log,
            "llm.provider_request",
            trace_id=trace_id,
            provider="anthropic",
            api_name=self.api_name,
            model=self.model,
            url=self.url,
            payload=payload,
        )
        try:
            response = self.session.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=self.request_timeout_seconds,
            )
        except http_requests.Timeout as exc:
            raise provider_timeout_error(
                exc,
                provider="anthropic",
                api_name=self.api_name,
                timeout_seconds=self.request_timeout_seconds,
            ) from exc
        trace_json(
            _trace_log,
            "llm.provider_response",
            trace_id=trace_id,
            provider="anthropic",
            api_name=self.api_name,
            model=self.model,
            status_code=response.status_code,
            response_headers=dict(response.headers),
            body=response.text,
        )
        raise_for_provider_response(response, provider="anthropic", api_name=self.api_name)
        data = response.json()
        raise_for_provider_payload(
            data,
            provider="anthropic",
            api_name=self.api_name,
            status_code=response.status_code,
            request_id=response.headers.get("request-id") or response.headers.get("x-request-id"),
        )
        self.last_usage = _usage(data.get("usage"))
        text: list[str] = []
        calls: list[dict[str, Any]] = []
        tool_schemas = {
            str(item.get("function", {}).get("name") or ""): item.get("function", {}).get("parameters") or {}
            for item in tools or []
        }
        for item in data.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                text.append(item["text"])
            elif item.get("type") == "tool_use":
                arguments = item.get("input") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("tool_use input must be a JSON object")
                name = str(item.get("name") or "")
                arguments = canonicalize_contract_value(arguments, tool_schemas.get(name, {}))
                calls.append({"id": str(item.get("id") or ""), "name": name, "arguments": arguments})
        return {"text": "\n".join(text).strip(), "tool_calls": calls, "usage": self.last_usage}

    @staticmethod
    def _to_provider_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        systems: list[str] = []
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "system":
                systems.append(str(message.get("content") or ""))
                continue
            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": str(message["content"])})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    arguments = function.get("arguments") or {}
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call.get("id") or ""),
                            "name": str(function.get("name") or ""),
                            "input": arguments,
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
                continue
            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": str(message.get("content") or ""),
                }
                if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], list):
                    converted[-1]["content"].append(block)
                else:
                    converted.append({"role": "user", "content": [block]})
                continue
            content = str(message.get("content") or "")
            if converted and converted[-1]["role"] == "user" and isinstance(converted[-1]["content"], str):
                converted[-1]["content"] += "\n\n" + content
            else:
                converted.append({"role": "user", "content": content})
        if not converted:
            converted.append({"role": "user", "content": ""})
        return "\n\n".join(item for item in systems if item), converted


def _usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    total = input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else value.get("total_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cached_input_tokens": value.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": value.get("cache_creation_input_tokens"),
    }
