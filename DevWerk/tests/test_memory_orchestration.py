from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


class FakeSettings:
    def __init__(self, tmp_path):
        self.devwerk_db_path = str(tmp_path / "devwerk-memory.db")
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(tmp_path / "sessions")


def _configure(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.memory_system as memory_system
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    monkeypatch.setattr(memory_system, "settings", lambda: fake)
    kanban_service._initialized = False
    memory_system._initialized = False
    return kanban_service, memory_system


def _memory_workflow() -> dict:
    return {
        "name": "memory-flow",
        "version": 1,
        "columns": [
            {
                "status_key": "analyze",
                "title": "Analyze",
                "position": 10,
                "transition_to": ["implement", "failed"],
                "job_template": "analyze_code_context",
                "output_artifact": "analysis_bundle",
                "success_action": "analysis_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "implement",
                "title": "Implement",
                "position": 20,
                "transition_to": ["done", "failed"],
                "job_template": "implement_change",
                "input_artifacts": ["analysis_bundle"],
                "output_artifact": "implementation_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["analyze"]},
        ],
        "actions": {
            "analysis_ready": {"to": "implement"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "retry": {"to": "analyze"},
            "abandon": {"to": "failed"},
        },
    }


def test_memory_repository_context_writeback_and_promotion(monkeypatch, tmp_path):
    kanban, memory = _configure(monkeypatch, tmp_path)
    kanban.update_project_workflow("memory-project", _memory_workflow())
    task = kanban.create_task(
        project_id="memory-project",
        title="Fix tenant compile errors",
        description="Repair TenantServiceImpl without changing public API.",
    )["task"]

    memory.upsert_memory_item(
        project_id="memory-project",
        task_id=task["id"],
        scope="project",
        memory_type="project_rules",
        key="public-api",
        content={"rule": "Do not change public REST API paths without approval."},
        source_type="user",
    )
    memory.upsert_memory_item(
        project_id="memory-project",
        task_id=task["id"],
        scope="task",
        memory_type="task_brief",
        key="latest",
        content={"goal": "Fix tenant compile errors"},
        source_type="task_created",
    )
    run = memory.create_agent_run(
        project_id="memory-project",
        task_id=task["id"],
        workflow_id="memory-flow",
        agent_role="analyzer",
        stage="analyze",
        token_budget=900,
    )

    context = memory.build_context_pack(
        project_id="memory-project",
        task_id=task["id"],
        workflow_id="memory-flow",
        agent_role="analyzer",
        stage="analyze",
        token_budget=900,
        run_id=run["run_id"],
        workspace={
            "source_map": {
                "files": [
                    {"path": "src/main/java/org/example/service/impl/TenantServiceImpl.java", "symbols": ["TenantServiceImpl"]},
                    {"path": "src/main/java/org/example/dto/TenantUpdateRequest.java", "symbols": ["TenantUpdateRequest"]},
                ]
            }
        },
    )

    assert context["context_pack"]["context_pack_id"]
    assert context["context_pack"]["task"]["brief"]["goal"] == "Fix tenant compile errors"
    assert context["context_pack"]["project"]["rules"]["public-api"]["rule"].startswith("Do not change")
    assert context["context_pack"]["task"]["code_context"]["related_files"]
    assert context["context_pack"]["debug"]["included_memory_ids"]
    assert "full_session" in context["context_pack"]["on_demand"]

    writeback = memory.handle_agent_writeback(
        run["run_id"],
        {
            "task_updates": {
                "analysis_summary": {"summary": "Tenant implementation and DTO are the likely compile-error surface."},
                "code_context": {
                    "related_files": [
                        "src/main/java/org/example/service/impl/TenantServiceImpl.java",
                        "src/main/java/org/example/dto/TenantUpdateRequest.java",
                    ],
                    "files_to_change": ["src/main/java/org/example/service/impl/TenantServiceImpl.java"],
                    "files_to_avoid": ["src/main/java/org/example/controller/TenantController.java"],
                    "risk_notes": ["Keep REST API paths stable."],
                },
                "decisions": [{"decision": "Preserve controller contract."}],
                "handoff_summary": {"to": "implementer", "summary": "Use task_code_context; do not rescan whole repo."},
                "final_summary": {"summary": "Analysis complete."},
            },
            "workflow_updates": {"current_stage": "implement"},
            "run_updates": {"observations": ["source_map narrowed likely files"]},
            "project_memory_candidates": [
                {
                    "target_memory_type": "project_rule",
                    "content": {"rule": "Tenant API paths are stable unless explicitly changed."},
                    "reason": "Stable project contract discovered during repair.",
                    "confidence": 0.88,
                }
            ],
        },
    )

    task_memory = memory.read_task_memory(task["id"])
    assert writeback["ok"] is True
    assert task_memory["task_analysis_summary"]["latest"]["summary"].startswith("Tenant implementation")
    assert task_memory["task_code_context"]["latest"]["files_to_change"] == [
        "src/main/java/org/example/service/impl/TenantServiceImpl.java"
    ]
    assert task_memory["task_handoff_summary"]["items"][-1]["summary"].startswith("Use task_code_context")
    assert len(memory.list_promotion_candidates(project_id="memory-project", task_id=task["id"])["candidates"]) == 1
    assert memory.read_project_memory_items("memory-project", memory_type="project_rule")["items"][0]["key"] == "public-api"

    candidate_id = memory.list_promotion_candidates(project_id="memory-project", task_id=task["id"])["candidates"][0]["candidate_id"]
    approved = memory.approve_promotion_candidate("memory-project", candidate_id)
    assert approved["candidate"]["status"] == "approved"
    project_rules = memory.read_project_memory_items("memory-project", memory_type="project_rule")["items"]
    assert any(item["content"].get("rule", "").startswith("Tenant API paths") for item in project_rules)


def test_memory_writeback_accepts_text_confidence(monkeypatch, tmp_path):
    kanban, memory = _configure(monkeypatch, tmp_path)
    kanban.update_project_workflow("memory-confidence", _memory_workflow())
    task = kanban.create_task(project_id="memory-confidence", title="Distill note")["task"]
    run = memory.create_agent_run(
        project_id="memory-confidence",
        task_id=task["id"],
        workflow_id="memory-flow",
        agent_role="writer",
        stage="write",
        token_budget=900,
    )

    writeback = memory.handle_agent_writeback(
        run["run_id"],
        {
            "project_memory_candidates": [
                {
                    "target_memory_type": "known_issue",
                    "content": {"issue": "Provider may describe confidence as natural language."},
                    "reason": "Observed in real LLM smoke.",
                    "confidence": "medium",
                }
            ],
        },
    )

    candidates = memory.list_promotion_candidates(project_id="memory-confidence", task_id=task["id"])["candidates"]
    assert writeback["ok"] is True
    assert candidates[0]["confidence"] == 0.5
    assert writeback["promotion_candidates"][0]["confidence"] == 0.5


def test_memory_api_surface_and_task_memory_view(monkeypatch, tmp_path):
    kanban, _memory = _configure(monkeypatch, tmp_path)
    import app.main as main_module

    kanban.update_project_workflow("memory-api", _memory_workflow())
    task = kanban.create_task(project_id="memory-api", title="Compile repair", description="Fix compile errors")["task"]
    app = main_module.create_app()
    with TestClient(app) as client:
        created = client.post(
            f"/v1/memory/projects/memory-api/tasks/{task['id']}/items",
            json={
                "scope": "task",
                "memory_type": "task_constraints",
                "key": "api-stability",
                "content": {"constraint": "Do not rename controller routes."},
                "source_type": "user",
            },
        )
        run = client.post(
            f"/v1/memory/projects/memory-api/tasks/{task['id']}/runs",
            json={"workflow_id": "memory-flow", "agent_role": "analyzer", "stage": "analyze", "token_budget": 1000},
        )
        context = client.post(
            f"/v1/memory/projects/memory-api/tasks/{task['id']}/context",
            json={"workflow_id": "memory-flow", "agent_role": "analyzer", "stage": "analyze", "token_budget": 1000},
        )
        writeback = client.post(
            f"/v1/memory/runs/{run.json()['run_id']}/writeback",
            json={
                "task_updates": {
                    "progress": "Compiler repair analysis is underway.",
                    "code_context": {"related_files": ["src/TenantServiceImpl.java"], "risk_notes": ["Compile-only fix"]},
                    "decisions": ["Keep endpoint names stable."],
                    "handoff_summary": "TenantServiceImpl is the next focus.",
                },
                "project_memory_candidates": [
                    {
                        "target_memory_type": "known_issue",
                        "content": {"issue": "Tenant compile repair needs source-map evidence."},
                        "reason": "Repeated compile failures.",
                        "confidence": 0.7,
                    }
                ],
            },
        )
        task_memory = client.get(f"/v1/kanban/tasks/{task['id']}/memory")
        candidates = client.get(f"/v1/memory/projects/memory-api/candidates?task_id={task['id']}")

    assert created.status_code == 200
    assert run.status_code == 200
    assert context.status_code == 200
    assert writeback.status_code == 200
    assert task_memory.status_code == 200
    assert task_memory.json()["memory"]["structured"]["task_constraints"]["api-stability"]["constraint"].startswith("Do not rename")
    assert task_memory.json()["memory"]["structured"]["task_progress"]["latest"]["value"].startswith("Compiler repair")
    assert task_memory.json()["memory"]["structured"]["task_code_context"]["latest"]["related_files"] == ["src/TenantServiceImpl.java"]
    assert task_memory.json()["memory"]["structured"]["task_decisions"]["items"][-1]["decision"].startswith("Keep endpoint")
    assert task_memory.json()["memory"]["structured"]["task_handoff_summary"]["items"][-1]["summary"].startswith("TenantServiceImpl")
    assert candidates.json()["candidates"][0]["target_memory_type"] == "known_issue"


@pytest.mark.asyncio
async def test_workflow_engine_uses_context_compiler_and_writeback(monkeypatch, tmp_path):
    kanban, memory = _configure(monkeypatch, tmp_path)
    project_id = "workflow-memory"
    kanban.update_project_workflow(project_id, _memory_workflow())
    task = kanban.create_task(project_id=project_id, title="Fix tenant compile errors")["task"]
    memory.upsert_memory_item(
        project_id=project_id,
        task_id=task["id"],
        scope="project",
        memory_type="project_rules",
        key="compile-first",
        content={"rule": "Compiler diagnostics are authoritative."},
        source_type="user",
    )

    import app.services.workflow_engine as workflow_engine_service

    observed_contexts: list[dict] = []

    class FakeMemoryClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            context = payload["agent_context"]
            observed_contexts.append(context)
            phase = payload["phase"]
            if phase == "analyze":
                return {
                    "summary": "Analysis captured compile repair context.",
                    "outputs": {"ok": True},
                    "decision": "approve",
                    "next_action": "analysis_ready",
                    "writeback": {
                        "task_updates": {
                            "analysis_summary": {"summary": "Compiler diagnostics point to tenant files."},
                            "code_context": {
                                "related_files": ["src/main/java/org/example/service/impl/TenantServiceImpl.java"],
                                "files_to_change": ["src/main/java/org/example/service/impl/TenantServiceImpl.java"],
                                "risk_notes": ["Preserve public API."],
                            },
                            "handoff_summary": {"summary": "Implementer should use task_code_context."},
                        },
                    },
                }
            return {
                "summary": "Implementation completed.",
                "outputs": {"ok": True},
                "decision": "approve",
                "next_action": "workflow_done",
                "writeback": {
                    "task_updates": {
                        "final_summary": {"summary": "Tenant compile repair is complete."},
                    },
                    "project_memory_candidates": [
                        {
                            "target_memory_type": "test_strategy",
                            "content": {"strategy": "Run compile diagnostics after tenant service changes."},
                            "reason": "Compile repair task produced a reusable verification strategy.",
                            "confidence": 0.8,
                        }
                    ],
                },
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeMemoryClient())

    engine = workflow_engine_service.WorkflowEngine()
    await engine.run(
        task["id"],
        {
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Fix tenant compile errors"}],
            "workspace": {
                "root_id": project_id,
                "tree_preview": "src/main/java/org/example/service/impl/TenantServiceImpl.java",
                "source_map": {
                    "files": [
                        {"path": "src/main/java/org/example/service/impl/TenantServiceImpl.java", "symbols": ["TenantServiceImpl"]}
                    ]
                },
            },
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    task_memory = memory.read_task_memory(task["id"])
    candidates = memory.list_promotion_candidates(project_id=project_id, task_id=task["id"])["candidates"]
    context_artifacts = [item for item in detail["artifacts"] if item["artifact_type"] == "context_pack"]

    assert detail["status_key"] == "done"
    assert len(observed_contexts) == 2
    assert observed_contexts[0]["context_pack"]["project"]["rules"]["compile-first"]["rule"].startswith("Compiler")
    assert observed_contexts[1]["context_pack"]["task"]["code_context"]["related_files"]
    assert "messages" not in observed_contexts[1]["context_pack"]["session"]
    assert task_memory["task_analysis_summary"]["latest"]["summary"].startswith("Compiler diagnostics")
    assert task_memory["task_final_summary"]["latest"]["summary"] == "Tenant compile repair is complete."
    assert candidates[0]["target_memory_type"] == "test_strategy"
    assert context_artifacts
