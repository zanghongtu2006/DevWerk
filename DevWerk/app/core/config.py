"""
DevWerk backend configuration.

The backend owns model/provider selection. Capability providers only send context and execute granted operations.

Configuration is split into:
  - API profiles: protocol + base URL + auth + default model
  - Agent bindings: which API profile/model each backend agent should use

This keeps today's single coder agent simple while leaving room for planner,
reviewer, framework-memory, and other future agents to use different APIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_files(*, override: bool = False) -> None:
    root = _project_root()
    for name in (".env", ".env.development", ".env.local"):
        path = root / name
        if path.is_file():
            load_dotenv(path, override=override)


@dataclass(frozen=True)
class ApiProfile:
    name: str
    protocol: Literal["openai", "anthropic", "ollama"]
    base_url: str
    api_key: str | None
    model: str
    timeout: float
    effort_level: str | None = None
    enable_schema: bool = True
    trust_env_proxy: bool = False


@dataclass(frozen=True)
class AgentModelConfig:
    agent: str
    api: ApiProfile
    model: str
    thinking_mode: str
    temperature: float
    top_p: float | None
    max_tokens: int

    def as_client_config(self) -> dict:
        return {
            "agent": self.agent,
            "api_name": self.api.name,
            "protocol": self.api.protocol,
            "base_url": self.api.base_url,
            "api_key": self.api.api_key,
            "model": self.model,
            "timeout": self.api.timeout,
            "effort_level": self.api.effort_level,
            "enable_schema": self.api.enable_schema,
            "trust_env_proxy": self.api.trust_env_proxy,
            "thinking_mode": self.thinking_mode,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_case=True,
        extra="ignore",
    )

    # Server
    app_env: str = Field(default="development")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    reload: bool = Field(default=False)
    log_level: str = Field(default="debug")
    log_format: str = Field(default="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    log_file_enabled: bool = Field(default=True)
    log_dir: str = Field(default="./data/logs")
    log_file_name: str = Field(default="devwerk.log")
    log_retention_days: int = Field(default=30)
    uvicorn_access_log: bool = Field(default=True)

    # Persistent workflow supervision. Non-terminal Kanban tasks are recovered
    # after worker loss and are failed explicitly when an external boundary or
    # execution lease expires.
    workflow_supervisor_enabled: bool = Field(default=True)
    workflow_supervisor_interval_seconds: float = Field(default=5.0)
    workflow_queued_recovery_seconds: int = Field(default=15)
    workflow_execution_timeout_seconds: int = Field(default=1800)
    workflow_client_timeout_seconds: int = Field(default=1800)
    workflow_user_timeout_seconds: int = Field(default=86400)

    # Local usage accounting.
    devwerk_usage_tracking: bool = Field(default=True)
    devwerk_db_path: str = Field(default="./data/devwerk.db")

    # JSON LLM catalog and routing map. This is the preferred configuration
    # surface; legacy provider env vars below are used only as a fallback.
    devwerk_llm_config_path: str = Field(default="./config/llm.json")
    devwerk_llm_config_json: str | None = Field(default=None)

    # Default agent runtime parameters. Project settings may reference these
    # profiles but should not expose tokens or provider credentials.
    devwerk_thinking_mode: str = Field(default="balanced")
    devwerk_temperature: float = Field(default=0.2)
    devwerk_top_p: float | None = Field(default=None)
    devwerk_max_tokens: int = Field(default=4096)
    devwerk_trust_env_proxy: bool = Field(default=False)

    # Agent routing. Values are API profile names: openai, anthropic, ollama.
    devwerk_default_api: str = Field(default="anthropic")
    devwerk_coder_api: str | None = Field(default=None)
    devwerk_planner_api: str | None = Field(default=None)
    devwerk_executor_api: str | None = Field(default=None)
    devwerk_default_agent: str = Field(default="coder")

    # OpenAI-compatible API profile.
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_api_key: str | None = Field(default=None)
    openai_model: str = Field(default="gpt-4o-mini")
    openai_timeout: float = Field(default=180.0)

    # Anthropic-compatible API profile. Defaults match Claude Code + MiniMax.
    anthropic_auth_token: str | None = Field(default=None)
    anthropic_base_url: str = Field(default="https://api.minimaxi.com/anthropic")
    anthropic_model: str = Field(default="M3")
    anthropic_default_sonnet_model: str | None = Field(default=None)
    anthropic_default_haiku_model: str | None = Field(default=None)
    anthropic_default_opus_model: str | None = Field(default=None)
    claude_code_subagent_model: str | None = Field(default=None)
    claude_code_effort_level: str = Field(default="max")
    disable_autoupdater: str | None = Field(default="1")
    anthropic_timeout: float = Field(default=180.0)

    # Ollama remains useful for local development.
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    ollama_model: str = Field(default="deepseek-r1:32b")
    ollama_timeout: float = Field(default=180.0)
    ollama_enable_schema: bool = Field(default=True)

    # Backward-compatible selector. If set, it becomes the default API profile.
    llm_provider: str | None = Field(default=None)

    @field_validator(
        "openai_api_key",
        "anthropic_auth_token",
        "devwerk_coder_api",
        "devwerk_planner_api",
        "devwerk_executor_api",
        "anthropic_default_sonnet_model",
        "anthropic_default_haiku_model",
        "anthropic_default_opus_model",
        "claude_code_subagent_model",
        "disable_autoupdater",
        "llm_provider",
        "devwerk_top_p",
        "devwerk_llm_config_path",
        "devwerk_llm_config_json",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("openai_base_url", "anthropic_base_url", "ollama_base_url", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local"}

    @property
    def active_provider(self) -> str:
        return (self.llm_provider or self.devwerk_default_api or "anthropic").strip().lower()

    @property
    def llm_provider_name(self) -> str:
        return self.active_provider

    def llm_config(self) -> dict[str, Any]:
        path = _resolve_config_path(self.devwerk_llm_config_path)
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return _normalize_llm_config(value, self._legacy_llm_config())
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"LLM config file {path} is not valid JSON: {exc}") from exc
        if self.devwerk_llm_config_json:
            try:
                value = json.loads(self.devwerk_llm_config_json)
                if isinstance(value, dict):
                    return _normalize_llm_config(value, self._legacy_llm_config())
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"DEVWERK_LLM_CONFIG_JSON is not valid JSON: {exc}") from exc
        return self._legacy_llm_config()

    def _legacy_llm_config(self) -> dict[str, Any]:
        anthropic_model = (
            self.claude_code_subagent_model
            or self.anthropic_model
            or self.anthropic_default_sonnet_model
            or "M3"
        )
        return {
            "routing": {
                "default": "minimax/m3",
                "architecture": "minimax/m3",
                "product": "deepseek/deepseek-chat",
                "design": "deepseek/deepseek-chat",
                "compression": "ollama/deepseek-r1:32b",
            },
            "llms": {
                "minimax": {
                    "api": "anthropic",
                    "base_url": self.anthropic_base_url,
                    "api_key": self.anthropic_auth_token or "",
                    "timeout": self.anthropic_timeout,
                    "trust_env_proxy": self.devwerk_trust_env_proxy,
                    "models": {
                        "m3": {
                            "model": anthropic_model,
                            "temperature": self.devwerk_temperature,
                            "top_p": self.devwerk_top_p,
                            "max_tokens": self.devwerk_max_tokens,
                            "thinking_mode": self.devwerk_thinking_mode,
                            "effort_level": self.claude_code_effort_level,
                        }
                    },
                },
                "deepseek": {
                    "api": "openai",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "",
                    "timeout": self.openai_timeout,
                    "trust_env_proxy": self.devwerk_trust_env_proxy,
                    "models": {
                        "deepseek-chat": {
                            "temperature": 0.2,
                            "max_tokens": self.devwerk_max_tokens,
                            "thinking_mode": "balanced",
                        }
                    },
                },
                "openai": {
                    "api": "openai",
                    "base_url": self.openai_base_url,
                    "api_key": self.openai_api_key or "",
                    "timeout": self.openai_timeout,
                    "trust_env_proxy": self.devwerk_trust_env_proxy,
                    "models": {
                        self.openai_model: {
                            "temperature": self.devwerk_temperature,
                            "top_p": self.devwerk_top_p,
                            "max_tokens": self.devwerk_max_tokens,
                            "thinking_mode": self.devwerk_thinking_mode,
                        }
                    },
                },
                "ollama": {
                    "api": "ollama",
                    "base_url": self.ollama_base_url,
                    "api_key": "",
                    "timeout": self.ollama_timeout,
                    "enable_schema": self.ollama_enable_schema,
                    "trust_env_proxy": self.devwerk_trust_env_proxy,
                    "models": {
                        self.ollama_model: {
                            "temperature": 0.4,
                            "max_tokens": self.devwerk_max_tokens,
                            "thinking_mode": "local",
                        }
                    },
                },
            },
        }

    def api_profiles(self) -> dict[str, ApiProfile]:
        llm_config = self.llm_config()
        profiles: dict[str, ApiProfile] = {}
        for name, provider in (llm_config.get("llms") or {}).items():
            if not isinstance(provider, dict):
                continue
            models = provider.get("models") or {}
            if not isinstance(models, dict) or not models:
                continue
            first_model_id = next(iter(models))
            model_settings = models.get(first_model_id) or {}
            protocol = str(provider.get("api") or provider.get("protocol") or "openai").lower()
            if protocol not in {"openai", "anthropic", "ollama"}:
                protocol = "openai"
            profiles[str(name).lower()] = ApiProfile(
                name=str(name).lower(),
                protocol=protocol,  # type: ignore[arg-type]
                base_url=str(provider.get("base_url") or provider.get("url") or "").rstrip("/"),
                api_key=_none_if_empty(provider.get("api_key") or provider.get("key")),
                model=str(model_settings.get("model") or first_model_id),
                timeout=float(provider.get("timeout") or 180.0),
                effort_level=_none_if_empty(model_settings.get("effort_level") or provider.get("effort_level")),
                enable_schema=bool(provider.get("enable_schema", True)),
                trust_env_proxy=bool(provider.get("trust_env_proxy", self.devwerk_trust_env_proxy)),
            )
        if profiles:
            return profiles

        anthropic_model = (
            self.claude_code_subagent_model
            or self.anthropic_model
            or self.anthropic_default_sonnet_model
            or "M3"
        )
        return {
            "openai": ApiProfile(
                name="openai",
                protocol="openai",
                base_url=self.openai_base_url,
                api_key=self.openai_api_key,
                model=self.openai_model,
                timeout=self.openai_timeout,
                trust_env_proxy=self.devwerk_trust_env_proxy,
            ),
            "anthropic": ApiProfile(
                name="anthropic",
                protocol="anthropic",
                base_url=self.anthropic_base_url,
                api_key=self.anthropic_auth_token,
                model=anthropic_model,
                timeout=self.anthropic_timeout,
                effort_level=self.claude_code_effort_level,
                trust_env_proxy=self.devwerk_trust_env_proxy,
            ),
            "ollama": ApiProfile(
                name="ollama",
                protocol="ollama",
                base_url=self.ollama_base_url,
                api_key=None,
                model=self.ollama_model,
                timeout=self.ollama_timeout,
                trust_env_proxy=self.devwerk_trust_env_proxy,
            ),
        }

    def agent_config(self, agent: str | None = None) -> AgentModelConfig:
        agent_name = (agent or self.devwerk_default_agent or "coder").strip().lower()
        llm_config = self.llm_config()
        profile, model_key, model_settings = self._resolve_llm_ref(agent_name, llm_config)
        return AgentModelConfig(
            agent=agent_name,
            api=profile,
            model=str(model_settings.get("model") or model_key),
            thinking_mode=str(model_settings.get("thinking_mode") or self.devwerk_thinking_mode or "balanced").strip().lower(),
            temperature=float(model_settings.get("temperature", self.devwerk_temperature)),
            top_p=model_settings.get("top_p", self.devwerk_top_p),
            max_tokens=max(1, int(model_settings.get("max_tokens", self.devwerk_max_tokens))),
        )

    def _resolve_llm_ref(self, agent: str, llm_config: dict[str, Any]) -> tuple[ApiProfile, str, dict[str, Any]]:
        routing = llm_config.get("routing") or {}
        model_ref = None
        if isinstance(routing, dict):
            for key in _routing_keys(agent):
                if routing.get(key):
                    model_ref = routing[key]
                    break
            model_ref = model_ref or routing.get("default")
        if isinstance(model_ref, dict):
            model_ref = model_ref.get("primary") or model_ref.get("model")
        if not model_ref:
            profile_name = self._agent_profile_name(agent)
            profiles = self.api_profiles()
            profile = profiles.get(profile_name)
            if profile is None:
                raise ValueError(f"Unknown API profile {profile_name!r} for agent {agent!r}.")
            return profile, profile.model, {}

        provider_name, model_key = _split_model_ref(str(model_ref))
        providers = llm_config.get("llms") or {}
        provider = providers.get(provider_name) if isinstance(providers, dict) else None
        if not isinstance(provider, dict):
            raise ValueError(f"Unknown LLM provider {provider_name!r} for model ref {model_ref!r}.")
        models = provider.get("models") or {}
        if not isinstance(models, dict):
            models = {}
        model_settings = models.get(model_key) or {"model": model_key}
        if not isinstance(model_settings, dict):
            model_settings = {"model": model_key}
        protocol = str(provider.get("api") or provider.get("protocol") or "openai").lower()
        if protocol not in {"openai", "anthropic", "ollama"}:
            protocol = "openai"
        profile = ApiProfile(
            name=provider_name,
            protocol=protocol,  # type: ignore[arg-type]
            base_url=str(provider.get("base_url") or provider.get("url") or "").rstrip("/"),
            api_key=_none_if_empty(provider.get("api_key") or provider.get("key")),
            model=str(model_settings.get("model") or model_key),
            timeout=float(provider.get("timeout") or 180.0),
            effort_level=_none_if_empty(model_settings.get("effort_level") or provider.get("effort_level")),
            enable_schema=bool(provider.get("enable_schema", True)),
            trust_env_proxy=bool(provider.get("trust_env_proxy", self.devwerk_trust_env_proxy)),
        )
        return profile, model_key, model_settings

    def _agent_profile_name(self, agent: str) -> str:
        if agent == "coder" and self.devwerk_coder_api:
            return self.devwerk_coder_api.strip().lower()
        if agent == "planner" and self.devwerk_planner_api:
            return self.devwerk_planner_api.strip().lower()
        if agent in {"executor", "execute"} and self.devwerk_executor_api:
            return self.devwerk_executor_api.strip().lower()
        return self.active_provider

    def get_llm_config(self, agent: str | None = None) -> dict:
        return self.agent_config(agent).as_client_config()

    def validate_provider(self, agent: str | None = None) -> None:
        config = self.agent_config(agent)
        if config.api.protocol in {"openai", "anthropic"} and not config.api.api_key:
            raise ValueError(
                f"api_key is not set for agent {config.agent!r} using LLM provider {config.api.name!r}."
            )


def _normalize_llm_config(value: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    out = {
        "routing": value.get("routing") if isinstance(value.get("routing"), dict) else {},
        "llms": {},
    }
    providers = value.get("llms") or value.get("providers") or value.get("models")
    if isinstance(providers, dict):
        for name, raw in providers.items():
            if not isinstance(raw, dict):
                continue
            provider = dict(raw)
            provider["api"] = str(provider.get("api") or provider.get("protocol") or "openai").lower()
            provider["base_url"] = str(provider.get("base_url") or provider.get("url") or "").rstrip("/")
            if "api_key" not in provider and "key" in provider:
                provider["api_key"] = provider.get("key")
            models = provider.get("models") or {}
            if isinstance(models, list):
                provider["models"] = {str(item): {} for item in models}
            elif isinstance(models, dict):
                provider["models"] = models
            else:
                provider["models"] = {}
            out["llms"][str(name).lower()] = provider
    if not out["llms"]:
        out["llms"] = fallback.get("llms", {})
    _validate_llm_default_route(out)
    return out


def _validate_llm_default_route(config: dict[str, Any]) -> None:
    routing = config.get("routing")
    if not isinstance(routing, dict):
        raise ValueError("llm.json must define routing.default")
    default_ref = routing.get("default")
    if isinstance(default_ref, dict):
        default_ref = default_ref.get("primary") or default_ref.get("model")
    if not str(default_ref or "").strip():
        raise ValueError("llm.json must define routing.default")

    provider_name, model_key = _split_model_ref(str(default_ref))
    providers = config.get("llms")
    if not isinstance(providers, dict):
        raise ValueError("llm.json must define llms")
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        raise ValueError(f"llm.json routing.default references unknown provider {provider_name!r}")

    models = provider.get("models")
    if isinstance(models, dict) and models:
        if model_key not in models:
            raise ValueError(f"llm.json routing.default references unknown model {model_key!r}")
    elif not str(provider.get("model") or model_key).strip():
        raise ValueError(f"llm.json provider {provider_name!r} must define models or model")


def _split_model_ref(model_ref: str) -> tuple[str, str]:
    if "/" not in model_ref:
        return "default", model_ref.strip().lower()
    provider, model = model_ref.split("/", 1)
    return provider.strip().lower(), model.strip()


def _routing_keys(agent: str) -> list[str]:
    aliases = {
        "coder": ["coder", "coding", "default"],
        "planner": ["planner", "product", "design", "default"],
        "architect": ["architect", "architecture"],
        "architecture": ["architecture", "architect"],
        "executor": ["executor", "coding", "default"],
        "execute": ["executor", "coding", "default"],
        "reviewer": ["reviewer", "review", "default"],
        "review": ["reviewer", "review", "default"],
        "verifier": ["verifier", "verify", "default"],
        "verify": ["verifier", "verify", "default"],
    }
    return aliases.get(agent, [agent])


def _none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_config_path(value: str | None) -> Path:
    configured = Path(value or "./config/llm.json")
    if configured.is_absolute():
        return configured
    return _project_root() / configured


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _load_env_files(override=False)
        _settings = Settings()
        _settings.validate_provider()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _load_env_files(override=True)
    _settings = Settings()
    _settings.validate_provider()
    return _settings


class _CompatSettings(Settings):
    @classmethod
    def from_env(cls) -> "_CompatSettings":
        _load_env_files(override=False)
        return cls()


class _FrozenSettings(_CompatSettings):
    pass
