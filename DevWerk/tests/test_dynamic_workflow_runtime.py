from __future__ import annotations

import json

import pytest


class FakeSettings:
    def __init__(self, tmp_path):
        self.devwerk_db_path = str(tmp_path / "devwerk-dynamic-workflow.db")
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(tmp_path / "sessions")


def _configure(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False
    return kanban_service


def test_new_project_has_no_default_columns_and_requires_workflow(monkeypatch, tmp_path):
    kanban = _configure(monkeypatch, tmp_path)
    project_id = "blank-project"

    kanban.upsert_project(project_id=project_id, name="Blank")
    assert kanban.list_columns(project_id) == []
    assert kanban.get_project_workflow(project_id)["workflow"]["columns"] == []

    with pytest.raises(ValueError, match="project workflow is not configured"):
        kanban.create_task(project_id=project_id, title="Should not start")

    from app.routes.workflows import start_workflow_payload

    result = start_workflow_payload(
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Run work"}],
        }
    )
    assert result["ok"] is False
    assert result["error_code"] == "PROJECT_WORKFLOW_REQUIRED"


@pytest.mark.asyncio
async def test_workflow_spawns_column_agents_without_fixed_stage_names(monkeypatch, tmp_path):
    kanban = _configure(monkeypatch, tmp_path)
    project_id = "custom-workflow"
    workflow = {
        "name": "custom-publication-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "intake",
                "title": "Intake",
                "position": 10,
                "transition_to": ["compose", "blocked"],
                "job_template": "clarify_publication_goal",
                "output_artifact": "intake_bundle",
                "success_action": "intake_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "compose",
                "title": "Compose",
                "position": 20,
                "transition_to": ["shipped", "blocked"],
                "job_template": "write_publication",
                "input_artifacts": ["intake_bundle"],
                "output_artifact": "publication_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "shipped", "title": "Shipped", "position": 90, "transition_to": []},
            {"status_key": "blocked", "title": "Blocked", "position": 99, "transition_to": ["intake"]},
        ],
        "actions": {
            "intake_ready": {"to": "intake"},
            "workflow_done": {"to": "shipped"},
            "fail": {"to": "blocked"},
            "abandon": {"to": "blocked"},
            "retry": {"to": "intake"},
        },
    }
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Publish note")["task"]

    import app.services.workflow_engine as workflow_engine_service

    calls: list[tuple[str, str]] = []

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            phase = payload["phase"]
            agent = payload["agent"]
            calls.append((phase, agent))
            return {
                "phase": phase,
                "agent": agent,
                "summary": f"{phase} completed",
                "outputs": {"ok": True},
                "warnings": [],
                "decision": "approve",
                "next_action": "intake_ready" if phase == "intake" else "workflow_done",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    async def unused_plan_runner(body: dict):
        raise AssertionError("dynamic workflow must not invoke the legacy planner")

    async def unused_coding_runner(body: dict):
        raise AssertionError("dynamic workflow must not invoke the legacy coding runner")

    engine = workflow_engine_service.WorkflowEngine(
        plan_runner=unused_plan_runner,
        coding_runner=unused_coding_runner,
    )
    await engine.run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Publish a short note"}],
            "workspace": {"root_id": project_id, "source_map": None, "tree_preview": ""},
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    assert detail["status_key"] == "shipped"
    assert calls == [("intake", "intake-agent"), ("compose", "compose-agent")]
    result = [item for item in detail["artifacts"] if item["artifact_type"] == "workflow_result"][-1]
    assert result["payload"]["ok"] is True
    assert result["payload"]["done"] is True
    assert result["payload"]["status_key"] == "shipped"
