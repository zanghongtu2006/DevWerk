"""Ollama native message and tool-call adapter."""

from __future__ import annotations

import json
from typing import Any

import requests as http_requests


class OllamaClient:
    def __init__(self, config: dict[str, Any]):
        self.last_usage: dict[str, Any] | None = None
        self.base_url = str(config.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
        self.model = str(config.get("model") or "deepseek-r1:32b")
        self.timeout = float(config.get("timeout") or 180)
        self.temperature = float(config.get("temperature") or 0.4)
        self.top_p = config.get("top_p")
        self.url = f"{self.base_url}/api/chat"
        self.session = http_requests.Session()
        self.session.trust_env = bool(config.get("trust_env_proxy", False))

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        response = self.session.post(self.url, json=payload, timeout=self.timeout)
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
