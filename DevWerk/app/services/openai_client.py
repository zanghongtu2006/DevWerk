"""OpenAI-compatible native message and tool-call adapter."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests as http_requests

from app.services.provider_errors import raise_for_provider_payload, raise_for_provider_response


_log = logging.getLogger("devwerk.llm.openai")


class OpenAIClient:
    def __init__(self, config: dict[str, Any]):
        self.last_usage: dict[str, Any] | None = None
        self.api_name = str(config.get("api_name") or "openai")
        self.base_url = str(config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = config.get("api_key")
        self.model = str(config.get("model") or "gpt-4o-mini")
        self.timeout = float(config.get("timeout") or 180)
        self.temperature = float(config.get("temperature") or 0.2)
        self.top_p = config.get("top_p")
        self.max_tokens = config.get("max_tokens")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.url = f"{self.base_url}/chat/completions"
        self.session = http_requests.Session()
        self.session.trust_env = bool(config.get("trust_env_proxy", False))
        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        if self.top_p is not None:
            payload["top_p"] = float(self.top_p)
        if self.max_tokens:
            payload["max_tokens"] = int(self.max_tokens)
        response = self.session.post(
            self.url,
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=self.timeout,
        )
        raise_for_provider_response(response, provider="openai", api_name=self.api_name)
        data = response.json()
        raise_for_provider_payload(
            data,
            provider="openai",
            api_name=self.api_name,
            status_code=response.status_code,
            request_id=response.headers.get("request-id") or response.headers.get("x-request-id"),
        )
        self.last_usage = _usage(data.get("usage"))
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("OpenAI-compatible API returned no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenAI-compatible API returned no message")
        calls: list[dict[str, Any]] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be a JSON object")
            calls.append({"id": str(raw.get("id") or ""), "name": str(function.get("name") or ""), "arguments": arguments})
        content = message.get("content")
        return {"text": content if isinstance(content, str) else "", "tool_calls": calls, "usage": self.last_usage}


def _usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    details = value.get("prompt_tokens_details") if isinstance(value.get("prompt_tokens_details"), dict) else {}
    return {
        "input_tokens": value.get("prompt_tokens") or value.get("input_tokens"),
        "output_tokens": value.get("completion_tokens") or value.get("output_tokens"),
        "total_tokens": value.get("total_tokens"),
        "cached_input_tokens": details.get("cached_tokens"),
    }
