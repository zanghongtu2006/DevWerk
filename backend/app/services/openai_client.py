"""
OpenAI-compatible Chat Completions client.

Targets the widely implemented /v1/chat/completions API rather than a
provider-specific endpoint, so it can work with OpenAI and compatible gateways.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests as http_requests

from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.services.validation import validate_model_response


class OpenAIClient:
    def __init__(self, config: dict | None = None):
        self.last_usage: dict[str, Any] | None = None
        self.api_name: str = "openai"
        if config:
            self.api_name = config.get("api_name", self.api_name)
            self.base_url: str = config.get("base_url", "https://api.openai.com/v1").rstrip("/")
            self.api_key: str | None = config.get("api_key")
            self.model: str = config.get("model", "gpt-4o-mini")
            self.timeout: float = float(config.get("timeout", 180.0))
            self.temperature: float = float(config.get("temperature", 0.2))
            self.top_p: float | None = config.get("top_p")
            self.max_tokens: int | None = config.get("max_tokens")
        else:
            from app.core.config import settings
            cfg = settings().get_llm_config("coder")
            self.api_name = cfg.get("api_name", self.api_name)
            self.base_url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
            self.api_key = cfg.get("api_key")
            self.model = cfg.get("model", "gpt-4o-mini")
            self.timeout = float(cfg.get("timeout", 180.0))
            self.temperature = float(cfg.get("temperature", 0.2))
            self.top_p = cfg.get("top_p")
            self.max_tokens = cfg.get("max_tokens")

        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"
        self.url = f"{self.base_url}/chat/completions"

        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        obj = self.chat_json(messages, schema=MODEL_RESPONSE_SCHEMA)
        validate_model_response(obj)
        return obj

    def chat_json(self, messages: List[Dict[str, str]], schema: dict | None = None) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        if self.top_p is not None:
            payload["top_p"] = float(self.top_p)
        if self.max_tokens:
            payload["max_tokens"] = int(self.max_tokens)
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "devwerk_json_response",
                    "strict": True,
                    "schema": schema,
                },
            }

        resp = http_requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code == 400:
            text = (resp.text or "").lower()
            if any(key in text for key in ("json_schema", "response_format", "schema")):
                fallback = dict(payload)
                fallback["response_format"] = {"type": "json_object"}
                resp = http_requests.post(self.url, json=fallback, headers=headers, timeout=self.timeout)

        resp.raise_for_status()
        data = resp.json()
        self.last_usage = self._extract_usage(data)
        content = self._extract_content(data)
        obj = json.loads(content)
        return obj

    @staticmethod
    def _extract_usage(data: Dict[str, Any]) -> Dict[str, Any]:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {}
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, dict):
            details = {}
        return {
            "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
            "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_input_tokens": details.get("cached_tokens"),
        }

    @staticmethod
    def _extract_content(data: Dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenAI-compatible API returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ValueError("OpenAI-compatible API returned no message")
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        raise ValueError("OpenAI-compatible API returned empty message content")
