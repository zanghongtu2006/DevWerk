from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from tests.workflow_test_utils import configure_kanban


def _enabled() -> bool:
    return os.environ.get("DEVWERK_REAL_LLM_SMOKE") == "1"


def _local_llm_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "llm.json"


def _smoke_llm_config_path(tmp_path: Path) -> Path:
    source = _local_llm_config_path()
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    for provider in (config.get("llms") or {}).values():
        if not isinstance(provider, dict):
            continue
        provider["timeout"] = max(int(provider.get("timeout") or 180), 240)
        for model_settings in (provider.get("models") or {}).values():
            if not isinstance(model_settings, dict):
                continue
            model_settings["max_tokens"] = min(int(model_settings.get("max_tokens") or 2048), 2048)
            if model_settings.get("thinking_mode") == "max":
                model_settings["thinking_mode"] = "balanced"
            if model_settings.get("effort_level") == "max":
                model_settings["effort_level"] = "medium"
    target = tmp_path / "llm-project-conversation-smoke.json"
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


LIVE_CASES = [
    {
        "id": "LIVE-ZH-CODING",
        "message": "我准备做一个线下活动小程序，前端用 uniapp，后端用 Java。请先把项目 workflow 搭起来，后面我会一项一项让 agent 去做。",
        "expect_design": True,
    },
    {
        "id": "LIVE-EN-WRITING",
        "message": "I’m planning a technical blog series about backend engineering and AI coding tools. Please create a workflow for topic planning, research, drafting, technical review, editing, and final publishing.",
        "expect_design": True,
    },
    {
        "id": "LIVE-ZH-REPLY",
        "message": "我准备做一个 AI Agent 相关的技术内容项目，主要面向程序员。",
        "expect_design": False,
    },
]


@pytest.mark.skipif(not _enabled(), reason="set DEVWERK_REAL_LLM_SMOKE=1 to run live project conversation smoke")
@pytest.mark.parametrize("case", LIVE_CASES, ids=lambda item: item["id"])
def test_project_conversation_real_llm_smoke(monkeypatch, tmp_path, case):
    if not _local_llm_config_path().is_file():
        pytest.skip("local LLM config is missing")

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(_smoke_llm_config_path(tmp_path)))
    monkeypatch.setenv("DEVWERK_DB_PATH", str(tmp_path / "project-conversation-real-smoke.db"))
    monkeypatch.setenv("DEVWERK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DEVWERK_USAGE_TRACKING", "true")

    from app.core.config import reload_settings
    import app.kanban.store as kanban_service
    import app.services.memory_system as memory_system
    import app.services.usage as usage_service
    import app.routes.kanban as kanban_routes

    reload_settings()
    kanban_service._initialized = False
    memory_system._initialized = False
    usage_service._initialized = False

    project_id = f"live-{case['id'].lower()}-{uuid.uuid4().hex[:8]}"
    kanban_service.upsert_project(project_id=project_id, name=case["id"])

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(action="message", message=case["message"]),
    )

    assert response["ok"] in {True, False}
    assert response.get("kind") != "workflow_design_failed" or response.get("debug_event_recorded") is True
    assert "workflow must define project-specific columns" not in str(response)
    if response.get("kind") == "workflow_design":
        workflow = kanban_service.get_project_workflow(project_id)["workflow"]
        known = {column["status_key"] for column in workflow.get("columns") or []}
        assert known
        for action in ("workflow_done", "fail", "abandon", "retry"):
            assert workflow["actions"][action]["to"] in known
        if workflow.get("workflow_type") == "coding" or workflow.get("requires_apply"):
            assert {"ready_to_apply", "done", "failed"}.issubset(known)
            assert workflow["actions"]["code_ready"]["to"] == "ready_to_apply"
    elif case["expect_design"]:
        assert response.get("kind") in {"reply", "workflow_design_failed"}
