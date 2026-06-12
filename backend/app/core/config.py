"""
DevWerk backend configuration.

The backend owns model/provider selection. The IDE plugin only sends context.

Configuration is split into:
  - API profiles: protocol + base URL + auth + default model
  - Agent bindings: which API profile/model each backend agent should use

This keeps today's single coder agent simple while leaving room for planner,
reviewer, framework-memory, and other future agents to use different APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_files() -> None:
    root = _project_root()
    for name in (".env", ".env.development", ".env.local"):
        path = root / name
        if path.is_file():
            load_dotenv(path, override=False)


@dataclass(frozen=True)
class ApiProfile:
    name: str
    protocol: Literal["openai", "anthropic", "ollama"]
    base_url: str
    api_key: str | None
    model: str
    timeout: float
    effort_level: str | None = None


@dataclass(frozen=True)
class AgentModelConfig:
    agent: str
    api: ApiProfile
    model: str

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

    # Local usage accounting.
    devwerk_usage_tracking: bool = Field(default=True)
    devwerk_db_path: str = Field(default="./data/devwerk.db")

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
        "llm_provider",
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

    def api_profiles(self) -> dict[str, ApiProfile]:
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
            ),
            "anthropic": ApiProfile(
                name="anthropic",
                protocol="anthropic",
                base_url=self.anthropic_base_url,
                api_key=self.anthropic_auth_token,
                model=anthropic_model,
                timeout=self.anthropic_timeout,
                effort_level=self.claude_code_effort_level,
            ),
            "ollama": ApiProfile(
                name="ollama",
                protocol="ollama",
                base_url=self.ollama_base_url,
                api_key=None,
                model=self.ollama_model,
                timeout=self.ollama_timeout,
            ),
        }

    def agent_config(self, agent: str | None = None) -> AgentModelConfig:
        agent_name = (agent or self.devwerk_default_agent or "coder").strip().lower()
        profile_name = self._agent_profile_name(agent_name)
        profiles = self.api_profiles()
        profile = profiles.get(profile_name)
        if profile is None:
            raise ValueError(
                f"Unknown API profile {profile_name!r} for agent {agent_name!r}. "
                f"Supported profiles: {', '.join(sorted(profiles))}"
            )
        return AgentModelConfig(agent=agent_name, api=profile, model=profile.model)

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
            env_name = "OPENAI_API_KEY" if config.api.protocol == "openai" else "ANTHROPIC_AUTH_TOKEN"
            raise ValueError(
                f"{env_name} is not set for agent {config.agent!r} using API profile {config.api.name!r}."
            )


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _load_env_files()
        _settings = Settings()
        _settings.validate_provider()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _load_env_files()
    _settings = Settings()
    _settings.validate_provider()
    return _settings


class _CompatSettings(Settings):
    @classmethod
    def from_env(cls) -> "_CompatSettings":
        _load_env_files()
        return cls()


class _FrozenSettings(_CompatSettings):
    pass
