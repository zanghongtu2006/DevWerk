"""
DevWerk backend configuration.

The backend owns model/provider selection. Capability providers only send context and execute granted operations.

Configuration is split into:
  - API profiles: protocol + base URL + auth + default model
  - Route keys: which provider/model each project or dynamically spawned
    workflow node agent should use
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
    effort_level: str | None = None
    trust_env_proxy: bool = False
    request_timeout_seconds: float = 600.0


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
            "effort_level": self.api.effort_level,
            "trust_env_proxy": self.api.trust_env_proxy,
            "request_timeout_seconds": self.api.request_timeout_seconds,
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

    # Persistent workflow supervision controls only the durable dispatcher.
    workflow_supervisor_enabled: bool = Field(default=True)
    workflow_supervisor_interval_seconds: float = Field(default=5.0)

    # Local usage accounting.
    devwerk_usage_tracking: bool = Field(default=True)
    devwerk_db_path: str = Field(default="./data/devwerk.db")

    # JSON LLM catalog and routing map. It is required and has no built-in fallback.
    devwerk_llm_config_path: str = Field(default="./config/llm.json")
    devwerk_llm_config_json: str | None = Field(default=None)

    # Transport setting; it does not cap the Agent loop. Anthropic requires a numeric value.
    devwerk_trust_env_proxy: bool = Field(default=False)

    @field_validator(
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

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local"}

    def llm_config(self) -> dict[str, Any]:
        path = _resolve_config_path(self.devwerk_llm_config_path)
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return _normalize_llm_config(value)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"LLM config file {path} is not valid JSON: {exc}") from exc
        if self.devwerk_llm_config_json:
            try:
                value = json.loads(self.devwerk_llm_config_json)
                if isinstance(value, dict):
                    return _normalize_llm_config(value)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"DEVWERK_LLM_CONFIG_JSON is not valid JSON: {exc}") from exc
        raise ValueError(
            f"LLM configuration is required: create {path} or set DEVWERK_LLM_CONFIG_JSON"
        )

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
            protocol = str(provider.get("api") or provider.get("protocol") or "").lower()
            if protocol not in {"openai", "anthropic", "ollama"}:
                raise ValueError(f"LLM provider {name!r} must declare api as openai, anthropic, or ollama")
            profiles[str(name).lower()] = ApiProfile(
                name=str(name).lower(),
                protocol=protocol,  # type: ignore[arg-type]
                base_url=str(provider.get("base_url") or provider.get("url") or "").rstrip("/"),
                api_key=_none_if_empty(provider.get("api_key") or provider.get("key")),
                model=str(model_settings.get("model") or first_model_id),
                effort_level=_none_if_empty(model_settings.get("effort_level") or provider.get("effort_level")),
                trust_env_proxy=bool(provider.get("trust_env_proxy", self.devwerk_trust_env_proxy)),
                request_timeout_seconds=float(provider.get("request_timeout_seconds", 600.0)),
            )
        return profiles

    def agent_config(self, agent: str | None = None) -> AgentModelConfig:
        agent_name = (agent or "conversation").strip().lower()
        llm_config = self.llm_config()
        profile, model_key, model_settings = self._resolve_llm_ref(agent_name, llm_config)
        return AgentModelConfig(
            agent=agent_name,
            api=profile,
            model=str(model_settings.get("model") or model_key),
            thinking_mode=str(model_settings["thinking_mode"]).strip().lower(),
            temperature=float(model_settings["temperature"]),
            top_p=model_settings.get("top_p"),
            max_tokens=int(model_settings["max_tokens"]),
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
            raise ValueError(f"No LLM route configured for agent {agent!r}")

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
        missing_settings = sorted(
            key for key in ("temperature", "thinking_mode", "max_tokens")
            if key not in model_settings
        )
        if missing_settings:
            raise ValueError(
                f"LLM model {model_ref!r} must explicitly configure: {', '.join(missing_settings)}"
            )
        protocol = str(provider.get("api") or provider.get("protocol") or "").lower()
        if protocol not in {"openai", "anthropic", "ollama"}:
            raise ValueError(f"LLM provider {provider_name!r} must declare api as openai, anthropic, or ollama")
        profile = ApiProfile(
            name=provider_name,
            protocol=protocol,  # type: ignore[arg-type]
            base_url=str(provider.get("base_url") or provider.get("url") or "").rstrip("/"),
            api_key=_none_if_empty(provider.get("api_key") or provider.get("key")),
            model=str(model_settings.get("model") or model_key),
            effort_level=_none_if_empty(model_settings.get("effort_level") or provider.get("effort_level")),
            trust_env_proxy=bool(provider.get("trust_env_proxy", self.devwerk_trust_env_proxy)),
            request_timeout_seconds=float(provider.get("request_timeout_seconds", 600.0)),
        )
        return profile, model_key, model_settings

    def get_llm_config(self, agent: str | None = None) -> dict:
        return self.agent_config(agent).as_client_config()

    def validate_provider(self, agent: str | None = None) -> None:
        config = self.agent_config(agent)
        if config.api.protocol in {"openai", "anthropic"} and not config.api.api_key:
            raise ValueError(
                f"api_key is not set for agent {config.agent!r} using LLM provider {config.api.name!r}."
            )


def _normalize_llm_config(value: dict[str, Any]) -> dict[str, Any]:
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
            provider["api"] = str(provider.get("api") or provider.get("protocol") or "").lower()
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
        raise ValueError("llm.json must define at least one provider in llms")
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
        "conversation": ["conversation", "default"],
        "column": ["column", "default"],
    }
    return aliases.get(agent, [agent, "default"])


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
