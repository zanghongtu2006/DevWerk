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
        else:
            from app.core.config import settings
            cfg = settings()
            self.base_url = cfg.ollama_base_url.rstrip("/")
            self.model = cfg.ollama_model
            self.timeout = float(cfg.ollama_timeout)
            self.enable_schema = cfg.ollama_enable_schema

        self.url = f"{self.base_url}/api/chat"

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": 0.4},
        }

        # Only send schema if the server/model is known to support it.
        # Older models (e.g. llama3 without --format flag) may reject it.
        if self.enable_schema:
            payload["format"] = MODEL_RESPONSE_SCHEMA

        resp = http_requests.post(self.url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

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

        validate_model_response(obj)
        return obj
