from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest


def _real_smoke_enabled() -> bool:
    return os.environ.get("DEVWERK_RUN_REAL_LLM_SMOKE") == "1"


def _local_llm_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "llm.json"


def _writing_workflow() -> dict:
    return {
        "name": "real-llm-writing-smoke",
        "version": 1,
        "columns": [
            {"status_key": "draft", "title": "Draft", "position": 10, "transition_to": ["topic_defined", "failed"]},
            {
                "status_key": "topic_defined",
                "title": "Topic Defined",
                "position": 20,
                "transition_to": ["written", "failed"],
                "job_template": "define_writing_topic",
                "input_artifacts": ["workflow_request"],
                "output_artifact": "topic_bundle",
                "success_action": "topic_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "written",
                "title": "Written",
                "position": 30,
                "transition_to": ["done", "failed"],
                "job_template": "write_short_note",
                "input_artifacts": ["topic_bundle"],
                "output_artifact": "note_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["draft"]},
        ],
        "actions": {
            "topic_ready": {"to": "topic_defined"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "draft"},
        },
    }


def _wait_for_workflow(workflow_routes, task_id: str, *, timeout_seconds: int = 260) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict = {}
    terminal_seen_at: float | None = None
    while time.monotonic() < deadline:
        last_state = workflow_routes.workflow_state_payload(task_id, include_result=True)
        result = last_state.get("result")
        if isinstance(result, dict):
            return last_state
        if last_state.get("status_key") in {"done", "failed"}:
            terminal_seen_at = terminal_seen_at or time.monotonic()
            if time.monotonic() - terminal_seen_at > 20:
                return last_state
        time.sleep(1.5)
    raise AssertionError(f"workflow did not finish before timeout; last_state={last_state}")


@pytest.mark.skipif(not _real_smoke_enabled(), reason="set DEVWERK_RUN_REAL_LLM_SMOKE=1 to run real LLM smoke")
def test_real_minimax_project_conversation_and_dynamic_workflow_smoke(monkeypatch, tmp_path):
    config_path = _local_llm_config_path()
    if not config_path.is_file():
        pytest.skip(f"local LLM config is missing: {config_path}")

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEVWERK_LLM_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("DEVWERK_DB_PATH", str(tmp_path / "real-llm-smoke.db"))
    monkeypatch.setenv("DEVWERK_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("DEVWERK_USAGE_TRACKING", "true")

    from app.core.config import reload_settings
    import app.services.kanban as kanban_service
    import app.services.memory_system as memory_system
    import app.services.usage as usage_service
    from app.routes import kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    reload_settings()
    kanban_service._initialized = False
    memory_system._initialized = False
    usage_service._initialized = False
    workflow_routes._active_workflows.clear()
    workflow_routes._pending_workflows.clear()

    project_id = f"real-llm-smoke-{uuid.uuid4().hex[:10]}"
    kanban_routes.kanban_upsert_project(
        kanban_routes.ProjectUpsertRequest(project_id=project_id, name="Real LLM Smoke")
    )
    memory_system.upsert_memory_item(
        project_id=project_id,
        scope="project",
        memory_type="project_rules",
        key="real-llm-memory-rule",
        content={"rule": "Real smoke agents must receive project memory through context packs."},
        source_type="test",
    )

    design_response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="save_design",
            save=True,
            message=(
                "Create and save this non-coding writing workflow. Keep it concise and do not turn it "
                "into a code workflow. The task should define a topic, write a short note, then finish."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Use the provided workflow JSON as the project workflow. Preserve the dynamic "
                        "job templates so the generic column agent can execute them."
                    ),
                }
            ],
            current_workflow=_writing_workflow(),
            current_agents={"project-agent": {"enabled": True, "model_route": "default"}},
        ),
    )
    assert design_response["ok"] is True
    assert design_response["saved"] is True
    saved_workflow = kanban_service.get_project_workflow(project_id)["workflow"]
    assert saved_workflow["actions"]["fail"]["to"] == "failed"
    assert any(column.get("job_template") for column in saved_workflow["columns"])

    start_response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="start_task",
            message="Write a two sentence release note for DevWerk's Kanban-driven project workflow.",
        ),
    )
    assert start_response["ok"] is True
    task_id = start_response["task_id"]
    final_state = _wait_for_workflow(workflow_routes, task_id)

    assert final_state["status_key"] == "done"
    result = final_state["result"]
    assert result["ok"] is True
    assert result["done"] is True
    assert result["status_key"] == "done"

    task_detail = kanban_service.get_task(task_id)["task"]
    event_types = [event["event_type"] for event in task_detail["events"]]
    assert "workflow_started" in event_types
    assert "workflow_finished" in event_types
    assert event_types.count("job_scheduled") >= 1
    artifact_types = [artifact["artifact_type"] for artifact in task_detail["artifacts"]]
    assert "workflow_phase_output" in artifact_types
    assert "workflow_result" in artifact_types
    context_packs = [
        artifact["payload"]
        for artifact in task_detail["artifacts"]
        if artifact["artifact_type"] == "context_pack" and isinstance(artifact.get("payload"), dict)
    ]
    assert context_packs
    assert any(
        (pack.get("project") or {}).get("rules", {}).get("real-llm-memory-rule", {}).get("rule", "").startswith("Real smoke")
        for pack in context_packs
    )
    assert all("messages" not in (pack.get("session") or {}) for pack in context_packs)
