"""LLM client factory for project and dynamically spawned workflow agents."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.debug_trace import trace_json
from app.services.anthropic_client import AnthropicClient
from app.services.ollama_client import OllamaClient
from app.services.openai_client import OpenAIClient
from app.services.provider_errors import LLMProviderError
from app.services.usage import record_llm_usage

_log = logging.getLogger("devwerk.llm_factory")
_trace_log = logging.getLogger("devwerk.llm.trace")


def get_llm_client(agent: str = "project") -> "UsageTrackedClient":
    cfg = dict(settings().get_llm_config(agent))
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

    def complete(self, *args, project_id=None, task_id=None, **kwargs):
        return self._tracked_call("complete", *args, project_id=project_id, task_id=task_id, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def _tracked_call(self, method_name: str, *args, project_id=None, task_id=None, **kwargs):
        trace_id = f"llm_{uuid.uuid4().hex}"
        started = time.monotonic()
        trace_json(
            _trace_log,
            "llm.agent_input",
            trace_id=trace_id,
            project_id=project_id,
            task_id=task_id,
            agent=str(self._config.get("agent") or "project"),
            provider=str(self._config.get("protocol") or self._config.get("api_name") or "unknown"),
            model=str(self._config.get("model") or "unknown"),
            method=method_name,
            args=args,
            kwargs=kwargs,
        )
        try:
            result = getattr(self._client, method_name)(*args, trace_id=trace_id, **kwargs)
            trace_json(
                _trace_log,
                "llm.agent_output",
                trace_id=trace_id,
                project_id=project_id,
                task_id=task_id,
                agent=str(self._config.get("agent") or "project"),
                provider=str(self._config.get("protocol") or self._config.get("api_name") or "unknown"),
                model=str(self._config.get("model") or "unknown"),
                output=result,
            )
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
            trace_json(
                _trace_log,
                "llm.agent_error",
                trace_id=trace_id,
                project_id=project_id,
                task_id=task_id,
                agent=str(self._config.get("agent") or "project"),
                provider=str(self._config.get("protocol") or self._config.get("api_name") or "unknown"),
                model=str(self._config.get("model") or "unknown"),
                error_type=error_type,
                error=str(exc),
            )
            record_llm_usage(
                agent_name=str(self._config.get("agent") or "project"),
                provider=str(self._config.get("protocol") or self._config.get("api_name") or "unknown"),
                model=str(self._config.get("model") or "unknown"),
                usage=getattr(self._client, "last_usage", None),
                duration_ms=int((time.monotonic() - started) * 1000),
                success=False,
                error_type=error_type,
                project_id=project_id,
                task_id=task_id,
                trace_id=trace_id,
            )
            raise
        record_llm_usage(
            agent_name=str(self._config.get("agent") or "project"),
            provider=str(self._config.get("protocol") or self._config.get("api_name") or "unknown"),
            model=str(self._config.get("model") or "unknown"),
            usage=getattr(self._client, "last_usage", None),
            duration_ms=int((time.monotonic() - started) * 1000),
            success=True,
            error_type=None,
            project_id=project_id,
            task_id=task_id,
            trace_id=trace_id,
        )
        return result
