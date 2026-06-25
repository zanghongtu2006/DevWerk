from __future__ import annotations

import pytest

from app.core.config import _normalize_llm_config


def _fallback() -> dict:
    return {
        "routing": {"default": "minimax/m3"},
        "llms": {
            "minimax": {
                "api": "anthropic",
                "base_url": "https://api.minimaxi.com/anthropic",
                "models": {"m3": {"model": "M3"}},
            }
        },
    }


def test_llm_config_requires_default_route():
    with pytest.raises(ValueError, match="routing.default"):
        _normalize_llm_config(
            {
                "routing": {"coder": "minimax/m3"},
                "llms": _fallback()["llms"],
            },
            _fallback(),
        )


def test_llm_config_default_route_must_reference_existing_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        _normalize_llm_config(
            {
                "routing": {"default": "missing/m3"},
                "llms": _fallback()["llms"],
            },
            _fallback(),
        )


def test_llm_config_default_route_must_reference_existing_model():
    with pytest.raises(ValueError, match="unknown model"):
        _normalize_llm_config(
            {
                "routing": {"default": "minimax/missing"},
                "llms": _fallback()["llms"],
            },
            _fallback(),
        )


def test_llm_config_accepts_valid_default_route():
    value = _normalize_llm_config(
        {
            "routing": {"default": "minimax/m3"},
            "llms": _fallback()["llms"],
        },
        _fallback(),
    )

    assert value["routing"]["default"] == "minimax/m3"
    assert value["llms"]["minimax"]["models"]["m3"]["model"] == "M3"
