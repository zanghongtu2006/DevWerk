from __future__ import annotations

import json
import os

import pytest

from app.core.config import reload_settings
from app.v1.store import V1Store
from app.v1.capabilities import build_core_registry
from app.v1.policy import PlatformPolicyLoader
from pathlib import Path


os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    config = {
        "routing": {"default": "test/model"},
        "llms": {
            "test": {
                "api": "anthropic",
                "base_url": "https://provider.invalid/anthropic",
                "api_key": "test-token",
                "models": {"model": {"model": "test-model", "temperature": 0.2, "thinking_mode": "balanced", "max_tokens": 65535}},
            }
        },
    }
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEVWERK_DB_PATH", str(tmp_path / "devwerk.db"))
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(tmp_path / "missing-llm.json"))
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_JSON", json.dumps(config))
    monkeypatch.setenv("WORKFLOW_SUPERVISOR_INTERVAL_SECONDS", "0.02")
    reload_settings()


@pytest.fixture
def store(tmp_path) -> V1Store:
    value = V1Store(str(tmp_path / "store.db"), registry=build_core_registry())
    value.register_platform_policy(PlatformPolicyLoader(Path(__file__).resolve().parents[1] / "DEVWERK.md").load())
    return value
