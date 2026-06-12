"""
Anthropic-compatible Messages API client.

Designed to work with Claude Code style environment variables and MiniMax's
Anthropic-compatible endpoint, for example:
  ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
  ANTHROPIC_AUTH_TOKEN=...
  ANTHROPIC_MODEL=M3
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests as http_requests

from app.services.validation import validate_model_response


class AnthropicClient:
    def __init__(self, config: dict | None = None):
        self.last_usage: dict[str, Any] | None = None
        if config:
            self.base_url: str = config.get("base_url", "https://api.minimaxi.com/anthropic").rstrip("/")
            self.api_key: str | None = config.get("api_key")
            self.model: str = config.get("model", "M3")
            self.timeout: float = float(config.get("timeout", 180.0))
            self.effort_level: str | None = config.get("effort_level")
        else:
            from app.core.config import settings
            cfg = settings().get_llm_config("coder")
            self.base_url = cfg.get("base_url", "https://api.minimaxi.com/anthropic").rstrip("/")
            self.api_key = cfg.get("api_key")
            self.model = cfg.get("model", "M3")
            self.timeout = float(cfg.get("timeout", 180.0))
            self.effort_level = cfg.get("effort_level")

        self.url = f"{self.base_url}/v1/messages" if not self.base_url.endswith("/v1") else f"{self.base_url}/messages"

        if not self.api_key:
            raise ValueError("ANTHROPIC_AUTH_TOKEN is not set.")

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        obj = self.chat_json(messages)
        validate_model_response(obj)
        return obj

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        system_text, user_messages = self._split_system(messages)
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0.2,
            "system": system_text,
            "messages": user_messages,
        }
        if self.effort_level:
            payload["metadata"] = {"effort_level": self.effort_level}

        resp = http_requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        resp.raise_for_status()

        data = resp.json()
        self.last_usage = self._extract_usage(data)
        content = self._extract_text(data)
        obj = self._parse_json_object(content)
        return obj

    @staticmethod
    def _extract_usage(data: Dict[str, Any]) -> Dict[str, Any]:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {}
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        }

    @staticmethod
    def _split_system(messages: List[Dict[str, str]]) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        for message in messages:
            role = (message.get("role") or "user").strip().lower()
            content = message.get("content") or ""
            if role == "system":
                system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            out.append({"role": role, "content": content})

        if not out:
            out.append({"role": "user", "content": "Return a valid DevWerk JSON response."})
        return "\n\n".join(p for p in system_parts if p).strip(), out

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        content = data.get("content")
        if isinstance(content, str):
            return content.strip()
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError("Anthropic-compatible API returned empty text content")
        return text

    @staticmethod
    def _parse_json_object(text: str) -> Dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            return json.loads(cleaned)
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                return json.loads(cleaned[start:end + 1])
            raise
