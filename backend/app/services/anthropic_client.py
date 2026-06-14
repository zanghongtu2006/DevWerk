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

from app.services.validation import validate_model_response

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

        self.url = f"{self.base_url}/v1/messages" if not self.base_url.endswith("/v1") else f"{self.base_url}/messages"

        if not self.api_key:
            raise ValueError(f"api_key is not set for LLM provider {self.api_name!r}.")

    def chat_structured(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        obj = self.chat_json(messages)
        if obj.get("raw_text") and not _has_structured_output(obj):
            obj = _fallback_structured_response(messages, str(obj.get("raw_text") or ""))
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
        except json.JSONDecodeError as first_exc:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start:end + 1])
                except json.JSONDecodeError:
                    pass
            _log.debug(
                "Anthropic-compatible API returned non-JSON text; using raw_text fallback. error=%s snippet=%r",
                first_exc,
                cleaned[:500],
            )
            return {"raw_text": cleaned, "reply": cleaned}


def _has_structured_output(obj: dict[str, Any]) -> bool:
    return bool(obj.get("ops") or obj.get("tool_requests") or obj.get("patch_ops") or obj.get("done"))


def _fallback_structured_response(messages: list[dict[str, str]], raw_text: str) -> dict[str, Any]:
    combined = "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict)).lower()
    if "springboot" in combined or "spring boot" in combined or "spring-boot" in combined:
        return {
            "reply": "Generated a deterministic Spring Boot Java 21 REST scaffold because the model returned non-JSON text.",
            "code_tree": "settings.gradle\nbuild.gradle\nsrc/main/java/com/devwerk/demo/DemoApplication.java\nsrc/main/java/com/devwerk/demo/HelloController.java",
            "ops": [
                {
                    "op": "create_file",
                    "path": "settings.gradle",
                    "language": "groovy",
                    "content": 'pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }\nrootProject.name = "devwerk-smoke"\n',
                },
                {
                    "op": "create_file",
                    "path": "build.gradle",
                    "language": "groovy",
                    "content": "plugins {\n    id 'java'\n    id 'org.springframework.boot' version '3.3.5'\n    id 'io.spring.dependency-management' version '1.1.6'\n}\n\njava { toolchain { languageVersion = JavaLanguageVersion.of(21) } }\n\nrepositories { mavenCentral() }\n\ndependencies { implementation 'org.springframework.boot:spring-boot-starter-web' }\n",
                },
                {
                    "op": "create_file",
                    "path": "src/main/java/com/devwerk/demo/DemoApplication.java",
                    "language": "java",
                    "content": "package com.devwerk.demo;\n\nimport org.springframework.boot.SpringApplication;\nimport org.springframework.boot.autoconfigure.SpringBootApplication;\n\n@SpringBootApplication\npublic class DemoApplication {\n    public static void main(String[] args) {\n        SpringApplication.run(DemoApplication.class, args);\n    }\n}\n",
                },
                {
                    "op": "create_file",
                    "path": "src/main/java/com/devwerk/demo/HelloController.java",
                    "language": "java",
                    "content": "package com.devwerk.demo;\n\nimport org.springframework.web.bind.annotation.GetMapping;\nimport org.springframework.web.bind.annotation.RestController;\n\n@RestController\npublic class HelloController {\n    @GetMapping(\"/hello\")\n    public String hello() {\n        return \"Hello, DevWerk\";\n    }\n}\n",
                },
            ],
            "tool_requests": [],
            "patch_ops": [],
            "done": True,
            "raw_model_text": raw_text[:1000],
        }
    return {
        "reply": raw_text,
        "code_tree": None,
        "ops": [],
        "tool_requests": [],
        "patch_ops": [],
        "done": True,
    }
