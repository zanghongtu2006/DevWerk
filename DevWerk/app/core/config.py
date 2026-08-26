"""Strict backend and LLM routing configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.global_settings import GlobalSettings, load_global_settings


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_files(*, override: bool = False) -> None:
    root = _project_root()
    for name in (".env", ".env.development", ".env.local"):
        path = root / name
        if path.is_file():
            load_dotenv(path, override=override)


class LLMProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["openai", "anthropic", "ollama"]
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")


class LLMModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    request_timeout_seconds: float = Field(gt=0)
    thinking_mode: str = Field(min_length=1)
    temperature: float
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int = Field(gt=0)
    effort_level: str | None = None


class LLMRoutesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: str = Field(min_length=1)
    column: str = Field(min_length=1)
    default: str = Field(min_length=1)


class LLMRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trust_env_proxy: bool = False


class LLMCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, LLMProviderConfig] = Field(min_length=1)
    models: dict[str, LLMModelConfig] = Field(min_length=1)
    routes: LLMRoutesConfig
    runtime: LLMRuntimeConfig

    @model_validator(mode="after")
    def validate_references(self) -> "LLMCatalog":
        for name, model in self.models.items():
            if model.provider not in self.providers:
                raise ValueError(
                    f"LLM model {name!r} references unknown provider {model.provider!r}"
                )
        for route, model_name in self.routes.model_dump().items():
            if model_name not in self.models:
                raise ValueError(
                    f"LLM route {route!r} references unknown model {model_name!r}"
                )
        return self


@dataclass(frozen=True)
class ApiProfile:
    name: str
    protocol: Literal["openai", "anthropic", "ollama"]
    base_url: str
    api_key: str | None
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

    def as_client_config(self) -> dict[str, Any]:
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

    workflow_supervisor_enabled: bool = Field(default=True)
    workflow_supervisor_interval_seconds: float = Field(default=5.0)
    devwerk_usage_tracking: bool = Field(default=True)
    devwerk_db_path: str = Field(default="./data/devwerk.db")
    devwerk_global_settings_path: str = Field(default="./config/global-settings.yaml")

    devwerk_llm_config_path: str = Field(default="./config/llm.json")
    devwerk_llm_config_json: str | None = Field(default=None)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local"}

    def llm_config(self) -> LLMCatalog:
        path = _resolve_config_path(self.devwerk_llm_config_path)
        source = None
        label = ""
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            label = f"LLM config file {path}"
        elif self.devwerk_llm_config_json:
            source = self.devwerk_llm_config_json
            label = "DEVWERK_LLM_CONFIG_JSON"
        if source is None:
            raise ValueError(
                f"LLM configuration is required: create {path} or set DEVWERK_LLM_CONFIG_JSON"
            )
        try:
            value = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} must contain a JSON object")
        try:
            return LLMCatalog.model_validate(value)
        except Exception as exc:  # Pydantic provides the exact unknown/missing field path.
            raise ValueError(f"{label} does not match the LLM configuration schema: {exc}") from exc

    def global_settings(self) -> GlobalSettings:
        return load_global_settings(self.global_settings_path())

    def global_settings_path(self) -> Path:
        return _resolve_config_path(self.devwerk_global_settings_path)

    def api_profiles(self) -> dict[str, ApiProfile]:
        config = self.llm_config()
        profiles: dict[str, ApiProfile] = {}
        for model_name, model in config.models.items():
            provider = config.providers[model.provider]
            profiles[model_name] = _api_profile(
                model.provider,
                provider,
                model,
                config.runtime,
            )
        return profiles

    def agent_config(self, agent: str | None = None) -> AgentModelConfig:
        agent_name = (agent or "conversation").strip().lower()
        config = self.llm_config()
        route_name = (
            config.routes.conversation
            if agent_name == "conversation"
            else config.routes.column
            if agent_name == "column"
            else config.routes.default
        )
        model = config.models[route_name]
        provider = config.providers[model.provider]
        return AgentModelConfig(
            agent=agent_name,
            api=_api_profile(model.provider, provider, model, config.runtime),
            model=model.model,
            thinking_mode=model.thinking_mode.strip().lower(),
            temperature=model.temperature,
            top_p=model.top_p,
            max_tokens=model.max_tokens,
        )

    def get_llm_config(self, agent: str | None = None) -> dict[str, Any]:
        return self.agent_config(agent).as_client_config()

    def validate_provider(self, agent: str | None = None) -> None:
        agents = [agent] if agent else ["conversation", "column", "default"]
        for route in agents:
            config = self.agent_config(route)
            if config.api.protocol in {"openai", "anthropic"} and not config.api.api_key:
                raise ValueError(
                    f"api_key is not set for route {route!r} using LLM provider "
                    f"{config.api.name!r}"
                )


def _api_profile(
    provider_name: str,
    provider: LLMProviderConfig,
    model: LLMModelConfig,
    runtime: LLMRuntimeConfig,
) -> ApiProfile:
    api_key = _none_if_empty(provider.api_key)
    if not api_key and provider.api_key_env:
        api_key = _none_if_empty(os.getenv(provider.api_key_env))
    return ApiProfile(
        name=provider_name,
        protocol=provider.protocol,
        base_url=provider.base_url.rstrip("/"),
        api_key=api_key,
        effort_level=_none_if_empty(model.effort_level),
        trust_env_proxy=runtime.trust_env_proxy,
        request_timeout_seconds=model.request_timeout_seconds,
    )


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
