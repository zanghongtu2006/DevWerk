"""
Ollama local LLM client.

Handles the /api/chat endpoint. Works with any Ollama-compatible server.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests as http_requests

from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.services.validation import validate_model_response


class OllamaClient:
    def __init__(self, config: dict | None = None):
        self.last_usage: dict[str, Any] | None = None
        """
        Args:
            config: Plain dict with keys: base_url, model, timeout, enable_schema.
                    If None, reads from app settings (legacy behaviour).
        """
        if config:
            self.base_url: str = config.get("base_url", "http://127.0.0.1:11434").rstrip("/")
            self.model: str = config.get("model", "deepseek-r1:32b")
            self.timeout: float = float(config.get("timeout", 180.0))
            self.enable_schema: bool = bool(config.get("enable_schema", True))
            self.temperature: float = float(config.get("temperature", 0.4))
            self.top_p: float | None = config.get("top_p")
        else:
            from app.core.config import settings
            cfg = settings().get_llm_config("coder")
            self.base_url = cfg.get("base_url", "http://127.0.0.1:11434").rstrip("/")
            self.model = cfg.get("model", "deepseek-r1:32b")
            self.timeout = float(cfg.get("timeout", 180.0))
            self.enable_schema = bool(cfg.get("enable_schema", True))
            self.temperature = float(cfg.get("temperature", 0.4))
            self.top_p = cfg.get("top_p")

        self.url = f"{self.base_url}/api/chat"

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        obj = self.chat_json(messages, schema=MODEL_RESPONSE_SCHEMA if self.enable_schema else None)
        validate_model_response(obj)
        return obj

    def chat_json(self, messages: List[Dict[str, str]], schema: dict | None = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": self.temperature},
        }
        if self.top_p is not None:
            payload["options"]["top_p"] = float(self.top_p)

        # Only send schema if the server/model is known to support it.
        # Older models (e.g. llama3 without --format flag) may reject it.
        payload["format"] = schema if schema is not None else "json"

        resp = http_requests.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self.last_usage = self._extract_usage(data)

        content = (data.get("message") or {}).get("content")
        if isinstance(content, dict):
            obj = content
        elif isinstance(content, str):
            try:
                obj = json.loads(content)
            except json.JSONDecodeError:
                raise ValueError(
                    f"Ollama returned non-JSON content: {content[:200]!r}"
                )
        else:
            raise ValueError(
                f"Ollama returned unexpected content type: {type(content).__name__}"
            )

        return obj

    @staticmethod
    def _extract_usage(data: Dict[str, Any]) -> Dict[str, Any]:
        input_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        total_tokens = None
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens = input_tokens + output_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
