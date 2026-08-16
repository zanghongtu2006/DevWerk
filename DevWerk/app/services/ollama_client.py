"""Ollama native message and tool-call adapter."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests as http_requests

from app.core.debug_trace import trace_json
from app.services.provider_errors import provider_timeout_error


_trace_log = logging.getLogger("devwerk.llm.provider.ollama")


class OllamaClient:
    def __init__(self, config: dict[str, Any]):
        self.last_usage: dict[str, Any] | None = None
        self.base_url = str(config["base_url"]).rstrip("/")
        self.model = str(config["model"])
        self.temperature = float(config["temperature"])
        self.top_p = config.get("top_p")
        self.request_timeout_seconds = float(config.get("request_timeout_seconds", 600.0))
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.url = f"{self.base_url}/api/chat"
        self.session = http_requests.Session()
        self.session.trust_env = bool(config.get("trust_env_proxy", False))

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, *, trace_id: str | None = None, require_tool: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": self.temperature},
        }
        if self.top_p is not None:
            payload["options"]["top_p"] = float(self.top_p)
        if tools:
            payload["tools"] = tools
        trace_json(
            _trace_log,
            "llm.provider_request",
            trace_id=trace_id,
            provider="ollama",
            model=self.model,
            url=self.url,
            payload=payload,
        )
        try:
            response = self.session.post(
                self.url,
                json=payload,
                timeout=self.request_timeout_seconds,
            )
        except http_requests.Timeout as exc:
            raise provider_timeout_error(
                exc,
                provider="ollama",
                api_name="ollama",
                timeout_seconds=self.request_timeout_seconds,
            ) from exc
        trace_json(
            _trace_log,
            "llm.provider_response",
            trace_id=trace_id,
            provider="ollama",
            model=self.model,
            status_code=response.status_code,
            response_headers=dict(response.headers),
            body=response.text,
        )
        response.raise_for_status()
        data = response.json()
        self.last_usage = _usage(data)
        message = data.get("message")
        if not isinstance(message, dict):
            raise ValueError("Ollama returned no message")
        calls: list[dict[str, Any]] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be a JSON object")
            calls.append({"id": str(raw.get("id") or f"ollama-{index}"), "name": str(function.get("name") or ""), "arguments": arguments})
        content = message.get("content")
        return {"text": content if isinstance(content, str) else "", "tool_calls": calls, "usage": self.last_usage}


def _usage(data: dict[str, Any]) -> dict[str, Any]:
    input_tokens = data.get("prompt_eval_count")
    output_tokens = data.get("eval_count")
    total = input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total}
