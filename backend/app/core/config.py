# app/core/config.py
from __future__ import annotations

import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    ollama_model: str
    ollama_timeout: float

    @staticmethod
    def from_env() -> "Settings":
        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "deepseek-r1:32b")
        timeout = float(os.getenv("OLLAMA_TIMEOUT", "180"))
        return Settings(
            ollama_base_url=base,
            ollama_model=model,
            ollama_timeout=timeout,
        )
