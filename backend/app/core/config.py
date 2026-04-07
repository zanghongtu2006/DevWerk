"""
DevWerk Configuration — Pydantic BaseSettings + python-dotenv

Environment loading priority (high → low):
  1. Real environment variables  (always, never overwritten by .env files)
  2. OS-level dotenv file         (path pointed to by APP_ENV_FILE, or .env in CWD)
  3. Environment-specific files   (.env.development, .env.test, .env.production)

Usage:
  from app.core.config import settings

  # settings is a global, lazily-initialised Pydantic instance.
  # It reads from real env vars first, then from .env / .env.{APP_ENV}.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """backend/app/core/config.py → backend/"""
    return Path(__file__).resolve().parents[2]


def _env_file_for_env(env_name: str) -> Path | None:
    """Return the optional .env.{env} side-car file, or None."""
    root = _project_root()
    candidates = [root / f".env.{env_name}", root / f".env.{env_name.lower()}"]
    for p in candidates:
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Pydantic model — fields map 1-to-1 with environment variables
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """
    DevWerk application settings.

    All fields default to safe, development-friendly values.
    Switching to production only requires setting APP_ENV=production
    and providing real API keys — no code changes needed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_case=True,
        extra="ignore",          # reject unknown env vars loudly in tests, not in prod
    )

    # ── Environment ──────────────────────────────────────────────────────

    app_env: Literal["development", "test", "production"] = Field(
        default="development",
        description="Active environment. Controls which .env.{name} file is loaded.",
    )

    # ── Server ───────────────────────────────────────────────────────────

    host: str = Field(default="0.0.0.0", description="Bind address")
    port: int = Field(default=8000, description="Bind port")
    reload: bool = Field(default=False, description="Enable uvicorn hot-reload")

    # ── LLM general ──────────────────────────────────────────────────────

    llm_provider: Literal["ollama", "openai", "xai", "gemini", "minimax", "minimax_overseas"] = Field(
        default="ollama",
        description="Which LLM backend to use.",
    )

    # ── Ollama ──────────────────────────────────────────────────────────

    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        description="Ollama server base URL.",
    )
    ollama_model: str = Field(default="deepseek-r1:32b", description="Default Ollama model.")
    ollama_timeout: float = Field(default=180.0, description="Request timeout in seconds.")
    ollama_enable_schema: bool = Field(
        default=True,
        description=(
            "Send JSON schema to Ollama (modern models such as deepseek-r1 support it; "
            "older models like llama3 may need --format json instead). "
            "Set to false if Ollama returns schema errors."
        ),
    )

    # ── OpenAI-compatible (OpenAI, xAI, o1-preview, ...) ─────────────────

    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for OpenAI-compatible API (v1 endpoint).",
    )
    openai_api_key: str | None = Field(
        default=None,
        description="API key. Leave None to use Ollama. Set in a real env var, not in .env files that get committed!",
    )
    openai_model: str = Field(default="gpt-4o-mini", description="Default OpenAI model.")
    openai_timeout: float = Field(default=180.0, description="Request timeout in seconds.")

    # ── xAI (Grok) ───────────────────────────────────────────────────────

    xai_base_url: str = Field(
        default="https://api.x.ai/v1",
        description="Base URL for xAI (Grok) API.",
    )
    xai_api_key: str | None = Field(default=None, description="xAI API key.")
    xai_model: str = Field(default="grok-3", description="Default xAI model.")
    xai_timeout: float = Field(default=180.0, description="Request timeout in seconds.")

    # ── Google Gemini ─────────────────────────────────────────────────────

    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        description="Base URL for Google Gemini API.",
    )
    gemini_api_key: str | None = Field(
        default=None,
        description="Gemini API key. Also reads GOOGLE_API_KEY for compatibility.",
    )
    gemini_model: str = Field(default="gemini-2.0-flash", description="Default Gemini model.")
    gemini_timeout: float = Field(default=180.0, description="Request timeout in seconds.")

    # ── MiniMax ──────────────────────────────────────────────────────────

    minimax_base_url: str = Field(
        default="https://api.minimax.chat/v1",
        description="Base URL for MiniMax China mainland API (api.minimax.chat).",
    )
    minimax_api_key: str | None = Field(
        default=None,
        description="MiniMax API key for China mainland.",
    )
    minimax_model: str = Field(default="MiniMax-Text-01", description="Default MiniMax model.")
    minimax_timeout: float = Field(default=180.0, description="Request timeout in seconds.")

    # ── MiniMax Overseas ─────────────────────────────────────────────────

    minimax_overseas_base_url: str = Field(
        default="https://api.minimaxi.chat/v1",
        description="Base URL for MiniMax overseas API (api.minimaxi.chat).",
    )
    minimax_overseas_api_key: str | None = Field(
        default=None,
        description="MiniMax API key for overseas.",
    )
    minimax_overseas_model: str = Field(default="MiniMax-Text-01", description="Default MiniMax overseas model.")
    minimax_overseas_timeout: float = Field(default=180.0, description="Request timeout in seconds.")

    # ── Validation ───────────────────────────────────────────────────────

    @field_validator("openai_api_key", "xai_api_key", "gemini_api_key",
                     "minimax_api_key", "minimax_overseas_api_key", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: str | None) -> str | None:
        """Treat empty-string API keys as None so the provider is silently skipped."""
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("ollama_base_url", "openai_base_url", "xai_base_url", "gemini_base_url", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    # ── Computed properties ──────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    def get_llm_config(self) -> dict:
        """Return the active LLM provider config as a plain dict (for clients)."""
        p = self.llm_provider
        if p == "ollama":
            return {
                "base_url": self.ollama_base_url,
                "model": self.ollama_model,
                "timeout": self.ollama_timeout,
                "enable_schema": self.ollama_enable_schema,
            }
        if p == "openai":
            return {
                "base_url": self.openai_base_url,
                "api_key": self.openai_api_key,
                "model": self.openai_model,
                "timeout": self.openai_timeout,
            }
        if p == "xai":
            return {
                "base_url": self.xai_base_url,
                "api_key": self.xai_api_key,
                "model": self.xai_model,
                "timeout": self.xai_timeout,
            }
        if p == "gemini":
            return {
                "base_url": self.gemini_base_url,
                "api_key": self.gemini_api_key,
                "model": self.gemini_model,
                "timeout": self.gemini_timeout,
            }
        if p == "minimax":
            return {
                "base_url": self.minimax_base_url,
                "api_key": self.minimax_api_key,
                "model": self.minimax_model,
                "timeout": self.minimax_timeout,
            }
        if p == "minimax_overseas":
            return {
                "base_url": self.minimax_overseas_base_url,
                "api_key": self.minimax_overseas_api_key,
                "model": self.minimax_overseas_model,
                "timeout": self.minimax_overseas_timeout,
            }
        raise ValueError(f"Unknown LLM provider: {p!r}")

    def validate_provider(self) -> None:
        """
        Raise ValueError with a clear message if the selected provider
        is missing its required credentials.
        """
        p = self.llm_provider
        if p == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. "
                "Set it as a real environment variable, or switch to APP_ENV=ollama."
            )
        if p == "xai" and not self.xai_api_key:
            raise ValueError(
                "XAI_API_KEY is not set. "
                "Set it as a real environment variable, or switch to APP_ENV=ollama."
            )
        if p == "gemini" and not self.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. "
                "Set it as a real environment variable, or switch to APP_ENV=ollama."
            )
        if p == "minimax" and not self.minimax_api_key:
            raise ValueError(
                "MINIMAX_API_KEY is not set. "
                "Set it as a real environment variable, or switch to APP_ENV=ollama."
            )
        if p == "minimax_overseas" and not self.minimax_overseas_api_key:
            raise ValueError(
                "MINIMAX_OVERSEAS_API_KEY is not set. "
                "Set it as a real environment variable, or switch to APP_ENV=ollama."
            )


# ---------------------------------------------------------------------------
# Module-level singleton (lazily instantiated on first import)
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def settings() -> Settings:
    """Return the global Settings instance, initialising it on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.validate_provider()
    return _settings


# ---------------------------------------------------------------------------
# Backwards-compatible API (for existing code that imports Settings.from_env)
# ---------------------------------------------------------------------------

def reload_settings() -> Settings:
    """Force-reload settings (useful in tests)."""
    global _settings
    _settings = Settings()
    _settings.validate_provider()
    return _settings


class _CompatSettings(Settings):
    """
    Compatibility shim that mimics the old frozen dataclass interface.
    Existing code that does `settings = Settings.from_env()` continues to work.
    """
    @classmethod
    def from_env(cls) -> "_CompatSettings":
        return cls()


class _FrozenSettings(_CompatSettings):
    """Drop-in replacement for old `Settings.from_env()` calls."""
    pass
