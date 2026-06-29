from __future__ import annotations

import json

import pytest


class FakeSettings:
    def __init__(self, tmp_path):
        self.devwerk_db_path = str(tmp_path / "devwerk-workflow.db")
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(tmp_path / "sessions")


def configure_kanban(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False
    return kanban_service


def repair_workflow() -> dict:
    return {
        "name": "compile-repair-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "inspect",
                "title": "Inspect",
                "position": 10,
                "transition_to": ["repair", "blocked"],
                "job_template": "inspect_compile_failure",
                "output_artifact": "inspection_bundle",
                "success_action": "inspection_done",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "repair",
                "title": "Repair",
                "position": 20,
                "transition_to": ["verify", "blocked"],
                "job_template": "repair_code",
                "input_artifacts": ["inspection_bundle"],
                "output_artifact": "repair_bundle",
                "success_action": "repair_done",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "verify",
                "title": "Verify",
                "position": 30,
                "transition_to": ["complete", "blocked"],
                "job_template": "verify_repair",
                "input_artifacts": ["repair_bundle"],
                "output_artifact": "verification_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "complete", "title": "Complete", "position": 90, "transition_to": []},
            {"status_key": "blocked", "title": "Blocked", "position": 99, "transition_to": ["inspect"]},
        ],
        "actions": {
            "inspection_done": {"to": "repair"},
            "repair_done": {"to": "verify"},
            "workflow_done": {"to": "complete"},
            "fail": {"to": "blocked"},
            "abandon": {"to": "blocked"},
            "retry": {"to": "inspect"},
        },
    }


@pytest.mark.asyncio
async def test_dynamic_code_repair_workflow_returns_file_ops(monkeypatch, tmp_path):
    kanban = configure_kanban(monkeypatch, tmp_path)
    project_id = "compile-repair-success"
    kanban.update_project_workflow(project_id, repair_workflow())
    task = kanban.create_task(
        project_id=project_id,
        title="Fix compile errors",
        description="The demo project has compile errors, especially TenantServiceImpl.",
    )["task"]

    import app.services.workflow_engine as workflow_engine_service

    calls: list[str] = []

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            phase = payload["phase"]
            calls.append(phase)
            if phase == "inspect":
                return {
                    "phase": phase,
                    "summary": "Found a TenantServiceImpl compile mismatch.",
                    "outputs": {"diagnostics": ["TenantServiceImpl.java type mismatch"]},
                    "decision": "approve",
                    "next_action": "inspection_done",
                }
            if phase == "repair":
                return {
                    "phase": phase,
                    "summary": "Updated TenantServiceImpl to compile.",
                    "outputs": {"changed_paths": ["src/main/java/org/example/service/impl/TenantServiceImpl.java"]},
                    "ops": [
                        {
                            "op": "update_file",
                            "path": "src/main/java/org/example/service/impl/TenantServiceImpl.java",
                            "language": "java",
                            "content": "package org.example.service.impl;\n\nclass TenantServiceImpl {}\n",
                        }
                    ],
                    "decision": "approve",
                    "next_action": "repair_done",
                    "done": True,
                }
            return {
                "phase": phase,
                "summary": "Verification plan passed for the generated repair.",
                "outputs": {"verification": "passed"},
                "decision": "approve",
                "next_action": "workflow_done",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Find and fix compile errors"}],
            "workspace": {
                "root_id": project_id,
                "tree_preview": "src/main/java/org/example/service/impl/TenantServiceImpl.java",
                "source_map": None,
            },
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    assert detail["status_key"] == "complete"
    assert calls == ["inspect", "repair", "verify"]
    result = [item for item in detail["artifacts"] if item["artifact_type"] == "workflow_result"][-1]["payload"]
    assert result["ok"] is True
    assert result["done"] is True
    assert result["status_key"] == "complete"
    assert result["ops"][0]["path"].endswith("TenantServiceImpl.java")
    assert any(item["artifact_type"] == "repair_bundle" for item in detail["artifacts"])


@pytest.mark.asyncio
async def test_dynamic_workflow_failure_uses_project_failure_column(monkeypatch, tmp_path):
    kanban = configure_kanban(monkeypatch, tmp_path)
    project_id = "compile-repair-failure"
    kanban.update_project_workflow(project_id, repair_workflow())
    task = kanban.create_task(project_id=project_id, title="Repair impossible")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeColumnClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            phase = payload["phase"]
            if phase == "inspect":
                return {"phase": phase, "summary": "Evidence collected.", "outputs": {}, "decision": "approve", "next_action": "inspection_done"}
            return {"phase": phase, "summary": "Cannot repair without source files.", "outputs": {}, "decision": "fail", "next_action": "fail"}

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeColumnClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {"project_id": project_id, "messages": [{"role": "user", "content": "Fix compile errors"}]},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert detail["status_key"] == "blocked"
    result = [item for item in detail["artifacts"] if item["artifact_type"] == "workflow_result"][-1]["payload"]
    assert result["ok"] is False
    assert result["status_key"] == "blocked"
