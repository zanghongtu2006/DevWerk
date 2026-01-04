# app/services/openai_client.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests as http_requests

from app.core.config import Settings
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.services.validation import validate_model_response


class OpenAIClient:
    """
    OpenAI Responses API client.

    - Prefer Structured Outputs via json_schema(strict)
    - Fallback to json_object if server/model doesn't support json_schema
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

        base = (self.settings.openai_base_url or "").strip().rstrip("/")
        if not base:
            base = "https://api.openai.com/v1"
        if not base.endswith("/v1"):
            base = base + "/v1"

        self.base_url = base
        self.url = f"{self.base_url}/responses"

        self.api_key = self.settings.openai_api_key
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")

        self.model = (self.settings.openai_model or "").strip() or "gpt-4o-mini"
        self.timeout = float(self.settings.openai_timeout or 180.0)

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "temperature": 0.2,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ide_chat_response",
                    "strict": True,
                    "schema": MODEL_RESPONSE_SCHEMA,
                }
            },
        }

        resp = http_requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)

        # If json_schema is rejected, fallback to json_object
        if resp.status_code == 400:
            txt = (resp.text or "").lower()
            if ("json_schema" in txt) or ("text.format" in txt) or ("schema" in txt):
                payload_fallback: Dict[str, Any] = {
                    "model": self.model,
                    "input": messages,
                    "temperature": 0.2,
                    "text": {"format": {"type": "json_object"}},
                }
                resp = http_requests.post(
                    self.url, json=payload_fallback, headers=headers, timeout=self.timeout
                )

        resp.raise_for_status()
        data = resp.json()

        content = self._extract_output_text(data)
        if not content:
            raise ValueError("OpenAI returned empty output text")

        try:
            obj = json.loads(content)
        except Exception as e:
            raise ValueError(f"OpenAI output is not valid JSON: {e}. Raw: {content[:200]}")

        validate_model_response(obj)
        return obj

    @staticmethod
    def _extract_output_text(data: Dict[str, Any]) -> Optional[str]:
        out = data.get("output")
        if not isinstance(out, list):
            return None

        parts: List[str] = []
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                if content.strip():
                    parts.append(content)
                continue
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t in ("output_text", "text"):
                    text = c.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text)

        joined = "\n".join(parts).strip()
        return joined or None
