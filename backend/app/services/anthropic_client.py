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
import logging
from typing import Any, Dict, List

import requests as http_requests

from app.services.provider_errors import raise_for_provider_payload, raise_for_provider_response
from app.services.validation import ModelResponseValidationError, validate_model_response

_log = logging.getLogger("devwerk.llm.anthropic")


class AnthropicClient:
    def __init__(self, config: dict | None = None):
        self.last_usage: dict[str, Any] | None = None
        self.api_name: str = "anthropic"
        if config:
            self.api_name = config.get("api_name", self.api_name)
            self.base_url: str = config.get("base_url", "https://api.minimaxi.com/anthropic").rstrip("/")
            self.api_key: str | None = config.get("api_key")
            self.model: str = config.get("model", "M3")
            self.timeout: float = float(config.get("timeout", 180.0))
            self.effort_level: str | None = config.get("effort_level")
            self.thinking_mode: str | None = config.get("thinking_mode")
            self.temperature: float = float(config.get("temperature", 0.2))
            self.top_p: float | None = config.get("top_p")
            self.max_tokens: int = int(config.get("max_tokens", 4096))
            self.trust_env_proxy: bool = bool(config.get("trust_env_proxy", False))
        else:
            from app.core.config import settings
            cfg = settings().get_llm_config("coder")
            self.api_name = cfg.get("api_name", self.api_name)
            self.base_url = cfg.get("base_url", "https://api.minimaxi.com/anthropic").rstrip("/")
            self.api_key = cfg.get("api_key")
            self.model = cfg.get("model", "M3")
            self.timeout = float(cfg.get("timeout", 180.0))
            self.effort_level = cfg.get("effort_level")
            self.thinking_mode = cfg.get("thinking_mode")
            self.temperature = float(cfg.get("temperature", 0.2))
            self.top_p = cfg.get("top_p")
            self.max_tokens = int(cfg.get("max_tokens", 4096))
            self.trust_env_proxy = bool(cfg.get("trust_env_proxy", False))

        self.url = f"{self.base_url}/v1/messages" if not self.base_url.endswith("/v1") else f"{self.base_url}/messages"
        self.session = http_requests.Session()
        self.session.trust_env = self.trust_env_proxy
        _log.debug(
            "Anthropic-compatible client configured api_name=%s base_url=%s model=%s timeout=%s trust_env_proxy=%s",
            self.api_name,
            self.base_url,
            self.model,
            self.timeout,
            self.trust_env_proxy,
        )

        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        obj = self.chat_json(messages)
        if obj.get("raw_text") and not _has_structured_output(obj):
            obj = _fallback_structured_response(messages, str(obj.get("raw_text") or ""))
        try:
            validate_model_response(obj)
        except ValueError as exc:
            raise ModelResponseValidationError(str(exc), obj=obj) from exc
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
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system_text,
            "messages": user_messages,
        }
        if self.top_p is not None:
            payload["top_p"] = float(self.top_p)
        metadata = {}
        if self.effort_level:
            metadata["effort_level"] = self.effort_level
        if self.thinking_mode:
            metadata["thinking_mode"] = self.thinking_mode
        if metadata:
            payload["metadata"] = metadata

        resp = self.session.post(self.url, json=payload, headers=headers, timeout=self.timeout)
        raise_for_provider_response(resp, provider="anthropic", api_name=self.api_name)

        data = resp.json()
        raise_for_provider_payload(
            data,
            provider="anthropic",
            api_name=self.api_name,
            status_code=resp.status_code,
            request_id=resp.headers.get("request-id") or resp.headers.get("x-request-id"),
        )
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
            return _normalize_top_level_json(json.loads(cleaned), cleaned)
        except json.JSONDecodeError as first_exc:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    return _normalize_top_level_json(json.loads(cleaned[start:end + 1]), cleaned)
                except json.JSONDecodeError:
                    pass
            _log.debug(
                "Anthropic-compatible API returned non-JSON text; using raw_text fallback. error=%s snippet=%r",
                first_exc,
                cleaned[:500],
            )
            return {"raw_text": cleaned, "reply": cleaned}


def _normalize_top_level_json(value: Any, raw_text: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        return {"raw_text": raw_text, "reply": raw_text}

    if len(value) == 1 and isinstance(value[0], dict) and any(
        key in value[0] for key in ("reply", "ops", "tool_requests", "patch_ops", "done")
    ):
        _log.debug("Anthropic-compatible API returned a single-item envelope array; unwrapping it")
        return value[0]

    if value and all(isinstance(item, dict) and item.get("op") in {"create_dir", "create_file", "update_file", "delete_path"} and item.get("path") for item in value):
        _log.debug("Anthropic-compatible API returned a top-level file-op array; normalizing count=%s", len(value))
        return {
            "reply": "Generated file operations.",
            "code_tree": None,
            "ops": [
                {
                    "op": item.get("op"),
                    "path": item.get("path"),
                    "language": item.get("language"),
                    "content": item.get("content"),
                }
                for item in value
            ],
            "tool_requests": [],
            "patch_ops": [],
            "done": True,
        }

    if value and all(isinstance(item, dict) and item.get("tool") for item in value):
        _log.debug("Anthropic-compatible API returned a top-level tool-request array; normalizing count=%s", len(value))
        return {
            "reply": "",
            "code_tree": None,
            "ops": [],
            "tool_requests": value,
            "patch_ops": [],
            "done": False,
        }

    _log.warning("Anthropic-compatible API returned an unsupported top-level JSON array; using raw_text fallback count=%s", len(value))
    return {"raw_text": raw_text, "reply": raw_text}


def _has_structured_output(obj: dict[str, Any]) -> bool:
    return bool(obj.get("ops") or obj.get("tool_requests") or obj.get("patch_ops") or obj.get("done"))


def _fallback_structured_response(messages: list[dict[str, str]], raw_text: str) -> dict[str, Any]:
    _log.debug(
        "Anthropic-compatible structured fallback: non_json_text messages=%s raw_chars=%s",
        len(messages),
        len(raw_text),
    )
    return {
        "reply": raw_text[:1000],
        "code_tree": None,
        "ops": [],
        "tool_requests": [],
        "patch_ops": [],
        "done": False,
        "raw_model_text": raw_text[:1000],
    }
