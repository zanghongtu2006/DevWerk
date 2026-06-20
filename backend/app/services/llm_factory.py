"""
LLM client factory.

Routes each backend agent to its configured API profile. Today the main agent
is `coder`, but planner/executor can already be bound to different profiles.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import settings
from app.services.anthropic_client import AnthropicClient
from app.services.ollama_client import OllamaClient
from app.services.openai_client import OpenAIClient
from app.services.provider_errors import LLMProviderError
from app.services.usage import record_llm_usage

_log = logging.getLogger("devwerk.llm_factory")


def get_llm_client(agent: str = "coder") -> "UsageTrackedClient":
    cfg = settings().get_llm_config(agent)
    protocol = str(cfg.get("protocol") or "").lower()

    if protocol == "openai":
        return UsageTrackedClient(OpenAIClient(config=cfg), cfg)
    if protocol == "anthropic":
        return UsageTrackedClient(AnthropicClient(config=cfg), cfg)
    if protocol == "ollama":
        return UsageTrackedClient(OllamaClient(config=cfg), cfg)

    raise ValueError(
        f"Unsupported LLM protocol {protocol!r} for agent {agent!r}. "
        "Supported protocols: openai, anthropic, ollama"
    )


class UsageTrackedClient:
    def __init__(self, client: AnthropicClient | OllamaClient | OpenAIClient, config: dict[str, Any]):
        self._client = client
        self._config = config

    def chat_structured(self, *args, **kwargs):
        return self._tracked_call("chat_structured", *args, **kwargs)

    def chat_json(self, *args, **kwargs):
        return self._tracked_call("chat_json", *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def _tracked_call(self, method_name: str, *args, **kwargs):
        started = time.monotonic()
        success = False
        error_type: str | None = None
        try:
            result = getattr(self._client, method_name)(*args, **kwargs)
            success = True
            return result
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, LLMProviderError):
                details = exc.details
                parts = [details.error_code]
                if details.status_code is not None:
                    parts.append(f"http_{details.status_code}")
                if details.provider_code is not None:
                    parts.append(f"provider_{details.provider_code}")
                error_type = ":".join(parts)
            else:
                error_type = type(exc).__name__
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            try:
                record_llm_usage(
                    agent_name=str(self._config.get("agent") or "coder"),
                    provider=str(self._config.get("protocol") or self._config.get("api_name") or "unknown"),
                    model=str(self._config.get("model") or "unknown"),
                    usage=getattr(self._client, "last_usage", None),
                    duration_ms=duration_ms,
                    success=success,
                    error_type=error_type,
                )
            except Exception as usage_error:  # noqa: BLE001
                _log.exception("usage telemetry failed and was ignored: %s", usage_error)
