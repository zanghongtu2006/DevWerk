# app/services/llm_factory.py
from __future__ import annotations

from app.core.config import Settings
from app.services.ollama_client import OllamaClient
from app.services.openai_client import OpenAIClient


def get_llm_client(settings: Settings):
    provider = (settings.llm_provider or "ollama").strip().lower()

    if provider == "ollama":
        return OllamaClient(settings=settings)

    if provider == "openai":
        return OpenAIClient(settings=settings)

    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")
