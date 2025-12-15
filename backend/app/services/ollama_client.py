# app/services/ollama_client.py
from __future__ import annotations

import json
from typing import Any, Dict, List

import requests as http_requests

from app.core.config import Settings
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.services.validation import validate_model_response

class OllamaClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.url = f"{self.base_url}/api/chat"

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.settings.ollama_model,
            "stream": False,
            "messages": messages,
            "format": MODEL_RESPONSE_SCHEMA,
            "options": {"temperature": 0.2},
        }
        resp = http_requests.post(self.url, json=payload, timeout=self.settings.ollama_timeout)
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("message") or {}).get("content")

        if isinstance(content, dict):
            obj = content
        elif isinstance(content, str):
            obj = json.loads(content)
        else:
            raise ValueError("Ollama returned invalid content type")

        validate_model_response(obj)
        return obj
