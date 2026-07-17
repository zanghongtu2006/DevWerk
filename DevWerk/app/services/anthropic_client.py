"""Anthropic-compatible native message and tool-call adapter."""

from __future__ import annotations

import json
import time
from typing import Any

import requests as http_requests

from app.services.provider_errors import raise_for_provider_payload, raise_for_provider_response


class AnthropicClient:
    def __init__(self, config: dict[str, Any]):
        self.last_usage: dict[str, Any] | None = None
        self.api_name = str(config.get("api_name") or "anthropic")
        self.base_url = str(config.get("base_url") or "https://api.anthropic.com").rstrip("/")
        self.api_key = config.get("api_key")
        self.model = str(config.get("model") or "claude-sonnet-4-5")
        self.timeout = float(config.get("timeout") or 180)
        self.temperature = float(config.get("temperature") or 0.2)
        self.top_p = config.get("top_p")
        self.max_tokens = int(config.get("max_tokens") or 4096)
        self.max_retries = max(0, int(config.get("max_retries") or 0))
        self.url = f"{self.base_url}/messages" if self.base_url.endswith("/v1") else f"{self.base_url}/v1/messages"
        self.session = http_requests.Session()
        self.session.trust_env = bool(config.get("trust_env_proxy", False))
        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
                    "input_schema": item["function"].get("parameters") or {"type": "object"},
                }
                for item in tools
            ]
        headers = {
            "x-api-key": str(self.api_key),
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
                break
            except http_requests.exceptions.ReadTimeout:
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(2 ** (attempt + 1), 8))
        assert response is not None
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
        for item in data.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                text.append(item["text"])
            elif item.get("type") == "tool_use":
                arguments = item.get("input") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("tool_use input must be a JSON object")
                calls.append({"id": str(item.get("id") or ""), "name": str(item.get("name") or ""), "arguments": arguments})
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
