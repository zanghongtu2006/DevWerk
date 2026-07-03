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

    engine = workflow_engine_service.WorkflowEngine()
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


@pytest.mark.asyncio
async def test_column_agent_target_status_is_mapped_to_workflow_action(monkeypatch, tmp_path):
    kanban = _configure(monkeypatch, tmp_path)
    project_id = "target-status-workflow"
    workflow = {
        "name": "target-status-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "intake",
                "title": "Intake",
                "position": 10,
                "transition_to": ["compose", "blocked"],
                "job_template": "clarify_goal",
                "output_artifact": "intake_bundle",
                "success_action": "intake_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "compose",
                "title": "Compose",
                "position": 20,
                "transition_to": ["shipped", "blocked"],
                "job_template": "write_output",
                "input_artifacts": ["intake_bundle"],
                "output_artifact": "draft_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "shipped", "title": "Shipped", "position": 90, "transition_to": []},
            {"status_key": "blocked", "title": "Blocked", "position": 99, "transition_to": ["intake"]},
        ],
        "actions": {
            "intake_ready": {"to": "compose"},
            "workflow_done": {"to": "shipped"},
            "fail": {"to": "blocked"},
            "abandon": {"to": "blocked"},
            "retry": {"to": "intake"},
        },
    }
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Publish note")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            phase = payload["phase"]
            if phase == "intake":
                return {
                    "phase": phase,
                    "summary": "Intake done.",
                    "outputs": {"ok": True},
                    "decision": "success",
                    "target": "compose",
                }
            return {
                "phase": phase,
                "summary": "Draft shipped.",
                "outputs": {"ok": True},
                "decision": "approve",
                "next_action": "shipped",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Publish a short note"}],
            "workspace": {"root_id": project_id, "source_map": None, "tree_preview": ""},
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    assert detail["status_key"] == "shipped"
    event_payloads = [
        event["payload"]
        for event in detail["events"]
        if event["event_type"] == "agent_output_normalized"
    ]
    assert any("target status 'compose' mapped to action 'intake_ready'" in payload["notes"] for payload in event_payloads)
    assert any("next_action looked like target status 'shipped'" in payload["notes"] for payload in event_payloads)


@pytest.mark.asyncio
async def test_workflow_failure_is_reported_to_project_conversation(monkeypatch, tmp_path):
    kanban = _configure(monkeypatch, tmp_path)
    project_id = "failure-conversation"
    workflow = {
        "name": "failure-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "implement",
                "title": "Implement",
                "position": 10,
                "transition_to": ["done", "failed"],
                "job_template": "implement_task",
                "output_artifact": "implementation_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implement"]},
        ],
        "actions": {
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "implement"},
        },
    }
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Build scaffold")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            return {
                "phase": payload["phase"],
                "summary": "Cannot produce scaffold from current evidence.",
                "outputs": {},
                "decision": "fail",
                "next_action": "fail",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Build the project scaffold"}],
            "workspace": {"root_id": project_id, "source_map": None, "tree_preview": ""},
        },
    )

    messages = kanban.list_project_conversation_messages(project_id, limit=10)["messages"]
    assert messages[-1]["kind"] == "workflow_failed"
    assert messages[-1]["task_id"] == task["id"]
    assert "Task failed at `implement`" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_internal_retry_reenters_retry_target_instead_of_advancing_downstream(monkeypatch, tmp_path):
    kanban = _configure(monkeypatch, tmp_path)
    project_id = "internal-retry-target"
    workflow = {
        "name": "retry-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "implement",
                "title": "Implement",
                "position": 10,
                "transition_to": ["apply", "failed"],
                "job_template": "implement_task",
                "output_artifact": "implementation_bundle",
                "success_action": "implementation_ready",
                "failure_actions": ["retry", "fail"],
            },
            {
                "status_key": "apply",
                "title": "Apply",
                "position": 20,
                "transition_to": ["done", "failed"],
                "job_template": "apply_task",
                "output_artifact": "apply_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["retry", "fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implement"]},
        ],
        "actions": {
            "implementation_ready": {"to": "apply"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "implement"},
        },
    }
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Build scaffold")["task"]

    import app.services.workflow_engine as workflow_engine_service

    calls: list[str] = []

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            phase = payload["phase"]
            calls.append(phase)
            if calls == ["implement"]:
                return {
                    "phase": phase,
                    "summary": "Need another implementation pass.",
                    "outputs": {},
                    "decision": "request_replan",
                    "next_action": "retry",
                }
            return {
                "phase": phase,
                "summary": f"{phase} complete.",
                "outputs": {"ok": True},
                "decision": "approve",
                "next_action": "implementation_ready" if phase == "implement" else "workflow_done",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Build the project scaffold"}],
            "workspace": {"root_id": project_id, "source_map": None, "tree_preview": ""},
        },
    )

    assert calls[:2] == ["implement", "implement"]
    assert calls == ["implement", "implement", "apply"]
    assert kanban.get_task(task["id"])["task"]["status_key"] == "done"


@pytest.mark.asyncio
async def test_column_agent_tool_request_aliases_are_normalized(monkeypatch, tmp_path):
    kanban = _configure(monkeypatch, tmp_path)
    project_id = "tool-alias-workflow"
    workflow = {
        "name": "tool-alias-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "inspect",
                "title": "Inspect",
                "position": 10,
                "transition_to": ["done", "blocked"],
                "job_template": "inspect_with_client_tool",
                "output_artifact": "inspection_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "blocked", "title": "Blocked", "position": 99, "transition_to": ["inspect"]},
        ],
        "actions": {
            "workflow_done": {"to": "done"},
            "fail": {"to": "blocked"},
            "abandon": {"to": "blocked"},
            "retry": {"to": "inspect"},
        },
    }
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Inspect project")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            return {
                "phase": payload["phase"],
                "summary": "Need source diagnostics before continuing.",
                "decision": "need_tool",
                "tool_requests": [
                    {
                        "name": "source.diagnostics",
                        "arguments": {"path": "src/main/java", "max_errors": 20},
                    }
                ],
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Inspect diagnostics"}],
            "client_capabilities": {"capabilities": [{"capability": "source.diagnostics"}]},
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    result = [item for item in detail["artifacts"] if item["artifact_type"] == "workflow_result"][-1]["payload"]
    assert result["waiting_for"] == "client_tool"
    assert result["tool_requests"][0]["tool"] == "source.diagnostics"
    assert result["tool_requests"][0]["args"]["paths"] == ["src/main/java"]
