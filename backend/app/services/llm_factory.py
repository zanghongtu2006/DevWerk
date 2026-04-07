"""
LLM client factory.

Returns the appropriate LLM adapter based on the active LLM_PROVIDER setting.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.minimax_client import MiniMaxClient
from app.services.ollama_client import OllamaClient
from app.services.openai_client import OpenAIClient


def get_llm_client() -> OllamaClient | OpenAIClient | MiniMaxClient:
    """
    Build and return the active LLM client.

    Raises:
        ValueError: if the provider is unsupported or missing credentials.
    """
    cfg = settings()
    provider = cfg.llm_provider.strip().lower()

    if provider == "ollama":
        return OllamaClient(config=cfg.get_llm_config())

    if provider == "openai":
        return OpenAIClient(config=cfg.get_llm_config())

    if provider in ("minimax", "minimax_overseas"):
        return MiniMaxClient(config=cfg.get_llm_config())

    if provider in ("xai", "gemini"):
        raise NotImplementedError(
            f"Provider '{provider}' adapter is not yet implemented. "
            f"Supported providers: ollama, openai, minimax"
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER: {provider!r}. "
        f"Supported: ollama, openai, minimax"
    )
