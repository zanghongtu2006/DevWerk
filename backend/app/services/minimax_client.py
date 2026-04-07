"""
MiniMax Responses API client.

MiniMax provides an OpenAI-compatible /v1/responses endpoint.
Two regional entry points are supported:
  - China mainland : https://api.minimax.chat/v1
  - Overseas        : https://api.minimaxi.chat/v1

The region is selected via the MINIMAX_REGION config field;
the matching API key and base URL are resolved in config.get_llm_config().
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests as http_requests

from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.services.validation import validate_model_response


class MiniMaxClient:
    """
    MiniMax Responses API client.

    Inherits the same request shape as OpenAIClient but targets the
    MiniMax regional endpoint selected at startup.
    """

    def __init__(self, config: dict | None = None):
        """
        Args:
            config: Plain dict with keys: base_url, api_key, model, timeout.
                    If None, reads from app settings (legacy behaviour).
        """
        if config:
            self.base_url: str = config.get("base_url", "https://api.minimax.chat/v1").rstrip("/")
            self.api_key: str | None = config.get("api_key")
            self.model: str = config.get("model", "MiniMax-Text-01")
            self.timeout: float = float(config.get("timeout", 180.0))
        else:
            from app.core.config import settings
            cfg = settings()
            if cfg.minimax_region == "cn":
                self.base_url = cfg.minimax_cn_base_url.rstrip("/")
                self.api_key = cfg.minimax_cn_api_key
            else:
                self.base_url = cfg.minimax_overseas_base_url.rstrip("/")
                self.api_key = cfg.minimax_overseas_api_key
            self.model = cfg.minimax_model
            self.timeout = float(cfg.minimax_timeout)

        if not self.base_url.endswith("/v1"):
            self.base_url = f"{self.base_url}/v1"
        self.url = f"{self.base_url}/responses"

        if not self.api_key:
            raise ValueError(
                "MiniMax API key is not set for the selected region. "
                "Set MINIMAX_CN_API_KEY or MINIMAX_OVERSEAS_API_KEY as a real "
                "environment variable (never in a committed .env file), "
                "and confirm MINIMAX_REGION is correct."
            )

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

        # Fallback: if json_schema is rejected, try json_object.
        if resp.status_code == 400:
            txt = (resp.text or "").lower()
            if any(kw in txt for kw in ("json_schema", "text.format", "schema")):
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
            raise ValueError("MiniMax returned empty output text")

        try:
            obj = json.loads(content)
        except Exception as e:
            raise ValueError(
                f"MiniMax output is not valid JSON: {e}. Raw: {content[:200]}"
            )

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
