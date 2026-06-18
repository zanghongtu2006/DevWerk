from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.models.ide import FileOp, IdeChatResponse
from app.models.plan import PlanFile, PlanResponse
from app.services.anthropic_client import AnthropicClient
from app.services.coder_harness import build_code_context_summary
from app.services.openai_client import OpenAIClient
from app.services.ollama_client import OllamaClient
from app.services.planner import Planner
from app.services.usage import _normalize_usage


def reset_service_dbs(kanban_service, usage_service) -> None:
    kanban_service._initialized = False
    usage_service._initialized = False


def patch_service_settings(monkeypatch, fake_settings, *modules) -> None:
    import app.services.session_store as session_store

    for module in modules:
        monkeypatch.setattr(module, "settings", lambda: fake_settings)
    monkeypatch.setattr(session_store, "settings", lambda: fake_settings)


def test_code_context_summary_uses_source_map_facts_without_framework_classification():
    workspace = {
        "root_id": "summary-smoke",
        "source_map": {
            "root": "summary-smoke",
            "generated_at": 1,
            "total_files": 2,
            "indexed_files": 2,
            "skipped_files": 0,
            "files": [
                {
                    "path": "pkg/api.py",
                    "kind": "source",
                    "language": "py",
                    "imports": ["typing"],
                    "symbols": [{"name": "create_item", "kind": "function", "line": 10}],
                    "size": 128,
                },
                {
                    "path": "README.md",
                    "kind": "doc",
                    "language": "markdown",
                    "imports": [],
                    "symbols": [],
                    "size": 32,
                },
            ],
        },
    }

    summary = build_code_context_summary(workspace)

    assert summary["available"] is True
    assert summary["source_map"]["total_files"] == 2
    assert {"name": "py", "count": 1} in summary["languages"]
    assert summary["symbol_index"][0]["name"] == "create_item"
    assert "framework" not in str(summary).lower()


def test_code_context_summary_includes_ide_syntax_diagnostics():
    workspace = {
        "root_id": "diagnostic-smoke",
        "source_map": {
            "root": "diagnostic-smoke",
            "generated_at": 1,
            "total_files": 1,
            "indexed_files": 1,
            "skipped_files": 0,
            "files": [
                {
                    "path": "src/main/java/org/example/dto/TenantCreateRequest.java",
                    "kind": "source",
                    "language": "java",
                    "symbols": [],
                    "imports": [],
                    "size": 128,
                }
            ],
        },
        "syntax_diagnostics": [
            {
                "path": "src/main/java/org/example/dto/TenantCreateRequest.java",
                "line": 28,
                "column": 34,
                "message": "Illegal escape character in string literal",
                "source": "ide_psi",
            },
            {
                "path": ".devwerk/20260618/after/src/main/java/org/example/dto/TenantCreateRequest.java",
                "line": 28,
                "column": 34,
                "message": "Snapshot copy should not be treated as source.",
                "source": "ide_psi",
            }
        ],
    }

    summary = build_code_context_summary(workspace)

    assert summary["available"] is True
    assert [item["path"] for item in summary["syntax_diagnostics"]] == [
        "src/main/java/org/example/dto/TenantCreateRequest.java"
    ]
    assert "direct file evidence" in " ".join(summary["path_policy"])


class FakeSettings:
    app_env = "test"
    llm_provider_name = "stub"
    devwerk_db_path = ""
    devwerk_usage_tracking = True
    is_production = False

    def __init__(self, db_path: Path):
        self.devwerk_db_path = str(db_path)

    def validate_provider(self, agent: str | None = None) -> None:
        return None

    def get_llm_config(self, agent: str | None = None) -> dict:
        return {
            "agent": agent or "coder",
            "protocol": "stub",
            "model": "stub-model",
        }


class FakePlannerClient:
    def chat_json(self, messages: list[dict]) -> dict:
        return {
            "plan": {
                "files": [
                    {
                        "path": "service/main.py",
                        "nature": "new",
                        "description": "Add the executable service entrypoint.",
                        "confidence": 0.9,
                    },
                    {
                        "path": "README.md",
                        "nature": "new",
                        "description": "Document the generated smoke scaffold.",
                        "confidence": 0.7,
                    },
                ],
                "summary": "Create a minimal runnable smoke scaffold.",
                "warnings": [],
            }
        }


class FakeNoPlanPlannerClient:
    def chat_json(self, messages: list[dict]) -> dict:
        return {"raw_text": "I need more concrete source evidence before producing a file-level plan."}


class FakePlannerResearchClient:
    def __init__(self, requested_path: str):
        self.requested_path = requested_path
        self.calls = 0

    def chat_json(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "raw_text": (
                    "I'll inspect the current tenant code before planning.]<]minimax[>[\n"
                    f'{{"name":"read_file","arguments":{{"file_path":"{self.requested_path}","start_line":1,"end_line":120}}}}\n'
                )
            }

        assert any("tool_results:" in message.get("content", "") for message in messages)
        return {
            "plan": {
                "files": [
                    {
                        "path": "src/domain/Tenant.py",
                        "nature": "modified",
                        "description": "Align tenant with the project structure found from source_map and file content.",
                        "confidence": 0.92,
                    }
                ],
                "summary": "Refactor tenant in the existing project path.",
                "warnings": [],
            }
        }


class FakePlannerNaturalLanguageSearchClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "tool_requests": [
                    {"id": "p1", "tool": "list_dir", "args": {"path": "src/main/java/org/example/dto", "max_depth": 2}}
                ]
            }
        if self.calls == 2:
            assert any("tool_results:" in message.get("content", "") for message in messages)
            return {
                "raw_text": (
                    'Let me search for regex annotations. Common causes include `Pattern.compile` '
                    'and `@Pattern` string literal escapes.'
                )
            }

        tool_results = "\n".join(message.get("content", "") for message in messages if "tool_results:" in message.get("content", ""))
        assert "TenantCreateRequest.java" in tool_results
        return {
            "plan": {
                "files": [
                    {
                        "path": "src/main/java/org/example/dto/TenantCreateRequest.java",
                        "nature": "modified",
                        "description": "Fix the regex/string literal syntax error found by search evidence.",
                        "confidence": 0.91,
                    }
                ],
                "summary": "Fix syntax errors in TenantCreateRequest validation pattern.",
                "warnings": [],
            }
        }


class FakeExecutorClient:
    def chat_structured(self, messages: list[dict]) -> dict:
        return {
            "reply": "Generated smoke scaffold.",
            "ops": [
                {
                    "op": "create_file",
                    "path": "service/main.py",
                    "language": "python",
                    "content": "def hello() -> str:\n    return \"Hello, DevWerk\"\n\n\nif __name__ == \"__main__\":\n    print(hello())\n",
                },
                {
                    "op": "create_file",
                    "path": "README.md",
                    "language": "markdown",
                    "content": "# DevWerk Smoke\n\nMinimal generated scaffold.\n",
                },
            ],
            "patch_ops": [],
            "tool_requests": [],
            "done": True,
        }


class FakeToolLoopExecutorClient:
    def __init__(self):
        self.calls = 0

    def chat_structured(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "reply": "Need to inspect files first.",
                "ops": [],
                "patch_ops": [],
                "tool_requests": [
                    {"id": "r1", "tool": "list_dir", "args": {"path": "", "max_depth": 4}},
                    {"id": "r2", "tool": "read_file", "args": {"path": "pom.xml", "start_line": 1, "end_line": 200}},
                    {
                        "id": "r3",
                        "tool": "read_file",
                        "args": {"path": "src/main/java/org/example/Main.java", "start_line": 1, "end_line": 200},
                    },
                ],
                "done": False,
            }

        assert any("tool_results:" in message.get("content", "") for message in messages)
        return {
            "reply": "Generated controller.",
            "ops": [
                {
                    "op": "create_file",
                    "path": "src/main/java/org/example/HelloController.java",
                    "language": "java",
                    "content": "package org.example;\n\nimport org.springframework.web.bind.annotation.GetMapping;\nimport org.springframework.web.bind.annotation.RestController;\n\n@RestController\npublic class HelloController {\n    @GetMapping(\"/hello\")\n    public String hello() {\n        return \"Hello\";\n    }\n}\n",
                }
            ],
            "patch_ops": [],
            "tool_requests": [],
            "done": True,
        }


class FakeClientToolExecutorClient:
    def chat_structured(self, messages: list[dict]) -> dict:
        return {
            "reply": "Generated code and requested a post-apply compile.",
            "ops": [
                {
                    "op": "create_file",
                    "path": "src/main/java/com/devwerk/demo/HelloController.java",
                    "language": "java",
                    "content": "package com.devwerk.demo;\n\npublic class HelloController {}\n",
                }
            ],
            "patch_ops": [],
            "tool_requests": [
                {
                    "id": "compile",
                    "tool": "run_command",
                    "args": {"command": ["./mvnw", "test"], "timeout_seconds": 120},
                }
            ],
            "done": True,
        }


class FakeProtocolRepairExecutorClient:
    def __init__(self):
        self.calls = 0

    def chat_structured(self, messages: list[dict]) -> dict:
        from app.services.validation import ModelResponseValidationError

        self.calls += 1
        if self.calls == 1:
            raise ModelResponseValidationError(
                "patch_ops[0] must be unified diff",
                obj={
                    "reply": "I tried to patch the file.",
                    "code_tree": None,
                    "ops": [],
                    "tool_requests": [],
                    "patch_ops": [{"op": "apply_patch", "content": "not a unified diff"}],
                    "done": False,
                },
            )

        assert any("protocol_error:" in message.get("content", "") for message in messages)
        return {
            "reply": "Generated repaired file op.",
            "ops": [
                {
                    "op": "update_file",
                    "path": "src/domain/a.py",
                    "language": "python",
                    "content": "print('a')\n",
                }
            ],
            "patch_ops": [],
            "tool_requests": [],
            "done": True,
        }


@pytest.mark.asyncio
async def test_backend_coding_workflow_plan_then_execute_smoke(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-smoke.db"
    project_root = tmp_path / "empty-project"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.planner as planner_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": FakePlannerClient())
    monkeypatch.setattr(ide_routes, "get_llm_client", lambda agent="executor": FakeExecutorClient())

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            project_id = "backend-coding-smoke"
            plan_body = {
                "project_id": project_id,
                "mode": "agent",
                "project_root": str(project_root),
                "messages": [
                    {
                        "role": "user",
                        "content": "Create a minimal runnable smoke scaffold.",
                    }
                ],
                "workspace": {
                    "root_id": project_id,
                    "changed_files": [],
                    "open_files": [],
                    "tree_preview": "",
                    "source_map": None,
                },
                "tool_results": [],
            }

            plan_response = await client.post("/v1/plan", json=plan_body, headers={"X-DevWerk-Project-Id": project_id})
            assert plan_response.status_code == 200
            plan = plan_response.json()
            assert plan["ok"] is True
            assert plan["task_id"]
            assert plan["status_key"] == "planned"
            assert plan["session_id"]
            assert plan["next_action"] == "execute"
            assert plan["phase_output"]["phase"] == "plan"
            planned_paths = {item["path"] for item in plan["files"]}
            assert planned_paths == {"service/main.py", "README.md"}

            execute_body = {
                "project_id": project_id,
                "task_id": plan["task_id"],
                "mode": "agent",
                "project_root": str(project_root),
                "messages": plan_body["messages"],
                "approved_paths": [item["path"] for item in plan["files"]],
                "approved_ops": [],
                "workspace": plan_body["workspace"],
            }

            execute_response = await client.post(
                "/v1/execute",
                json=execute_body,
                headers={"X-DevWerk-Project-Id": project_id},
            )
            assert execute_response.status_code == 200
            executed = execute_response.json()
            assert executed["ok"] is True
            assert executed["task_id"] == plan["task_id"]
            assert executed["status_key"] == "ready_to_apply"
            assert executed["session_id"]
            assert executed["next_action"] == "apply_result"
            assert executed["phase_output"]["phase"] == "coding"
            assert executed["done"] is True
            assert len(executed["ops"]) == 2
            _apply_file_ops(project_root, executed["ops"])
            assert (project_root / "README.md").is_file()
            entrypoint = project_root / "service/main.py"
            assert entrypoint.is_file()
            assert "Hello, DevWerk" in entrypoint.read_text(encoding="utf-8")

            task_response = await client.get(f"/v1/kanban/tasks/{plan['task_id']}")
            assert task_response.status_code == 200
            task = task_response.json()["task"]
            assert task["status_key"] == "ready_to_apply"
            artifact_types = {artifact["artifact_type"] for artifact in task["artifacts"]}
            assert {"plan_request", "plan_response", "execute_response", "workflow_phase_output"}.issubset(artifact_types)

            events_response = await client.get(
                f"/v1/kanban/events?project_id={project_id}&task_id={plan['task_id']}&limit=100"
            )
            assert events_response.status_code == 200
            event_types = [event["event_type"] for event in events_response.json()["events"]]
            assert "task_moved" in event_types
            assert "plan_llm_round_started" in event_types
            assert "plan_llm_round_result" in event_types
            assert "execute_llm_round_started" in event_types
            assert "execute_llm_round_result" in event_types
            assert "execute_response_ready" in event_types


@pytest.mark.asyncio
async def test_workflow_start_poll_result_smoke(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-workflow.db"
    project_root = tmp_path / "workflow-project"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.planner as planner_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": FakePlannerClient())
    monkeypatch.setattr(ide_routes, "get_llm_client", lambda agent="executor": FakeExecutorClient())

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            project_id = "backend-workflow-smoke"
            body = {
                "project_id": project_id,
                "mode": "agent",
                "project_root": str(project_root),
                "messages": [
                    {
                        "role": "user",
                        "content": "Create a minimal runnable smoke scaffold.",
                    }
                ],
                "workspace": {
                    "root_id": project_id,
                    "changed_files": [],
                    "open_files": [],
                    "tree_preview": "",
                    "source_map": None,
                },
                "tool_results": [],
            }

            start_response = await client.post("/v1/workflows", json=body, headers={"X-DevWerk-Project-Id": project_id})
            assert start_response.status_code == 200
            started = start_response.json()
            assert started["ok"] is True
            assert started["task_id"]
            assert started["poll_url"] == f"/v1/workflows/{started['task_id']}"
            assert started["events_url"] == f"/v1/workflows/{started['task_id']}/events"

            state = {}
            for _ in range(100):
                poll_response = await client.get(started["poll_url"])
                assert poll_response.status_code == 200
                state = poll_response.json()
                if state.get("result"):
                    break
                await asyncio.sleep(0.05)

            result = state.get("result")
            assert result
            assert result["ok"] is True
            assert result["task_id"] == started["task_id"]
            assert result["status_key"] == "ready_to_apply"
            assert len(result["ops"]) == 2
            _apply_file_ops(project_root, result["ops"])
            assert (project_root / "service/main.py").is_file()

            task_response = await client.get(f"/v1/kanban/tasks/{started['task_id']}")
            task = task_response.json()["task"]
            assert task["status_key"] == "ready_to_apply"
            artifact_types = [artifact["artifact_type"] for artifact in task["artifacts"]]
            assert "workflow_request" in artifact_types
            assert "workflow_result" in artifact_types
            assert "context_bundle" in artifact_types
            assert "code_context_summary" in artifact_types
            assert "plan_bundle" in artifact_types
            assert "code_change_bundle" in artifact_types
            assert "review_bundle" in artifact_types
            phase_outputs = [
                artifact["payload"]
                for artifact in task["artifacts"]
                if artifact["artifact_type"] == "workflow_phase_output"
            ]
            phases = {item["phase"] for item in phase_outputs}
            assert {"context_indexed", "plan", "coding", "reviewed"}.issubset(phases)
            session_ids = [item["session_id"] for item in phase_outputs]
            assert len(session_ids) == len(set(session_ids))

            events_response = await client.get(started["events_url"])
            assert events_response.status_code == 200
            events_text = events_response.text
            assert "event: kanban_event" in events_text
            assert "workflow_started" in events_text
            assert "workflow_column_started" in events_text
            assert "workflow_column_completed" in events_text
            assert "workflow_transition_decided" in events_text
            assert "agent_context_built" in events_text
            assert "agent_output_recorded" in events_text
            assert "event: workflow_result" in events_text

            memory_response = await client.get(f"/v1/kanban/projects/{project_id}/memory")
            memory = memory_response.json()["memory"]
            assert "context_indexed" in {item["phase"] for item in memory["phase_summaries"]}
            assert {"service/main.py", "README.md"}.issubset(set(memory["paths"]))

            chat_response = await client.post("/v1/chat", json=body)
            assert chat_response.status_code == 404


def test_workflow_reviewer_keeps_distinct_relative_paths():
    from app.services.workflow_engine import _review_result

    plan = PlanResponse(
        files=[
            PlanFile(
                path="test/src/main/java/org/example/controller/TenantController.java",
                nature="new",
                description="Add tenant controller.",
            )
        ]
    )
    executed = IdeChatResponse(
        ops=[
            FileOp(
                op="create_file",
                path="src/main/java/org/example/controller/TenantController.java",
                content="class TenantController {}",
            )
        ],
        done=True,
    )

    review = _review_result(plan, executed)

    assert review["decision"] == "request_replan"
    assert review["normalized_plan_files"] == ["test/src/main/java/org/example/controller/TenantController.java"]
    assert review["normalized_changed_files"] == ["src/main/java/org/example/controller/TenantController.java"]
    assert review["missing_changed_files"] == ["test/src/main/java/org/example/controller/TenantController.java"]
    assert review["unplanned_changed_files"] == ["src/main/java/org/example/controller/TenantController.java"]


def test_workflow_reviewer_rejects_missing_planned_files():
    from app.services.workflow_engine import _review_result

    plan = PlanResponse(
        files=[
            PlanFile(path="src/domain/a.py", nature="modified", description="Update A."),
            PlanFile(path="src/domain/b.py", nature="modified", description="Update B."),
        ]
    )
    executed = IdeChatResponse(
        ops=[FileOp(op="update_file", path="src/domain/a.py", content="print('a')\n")],
        done=True,
    )

    review = _review_result(plan, executed)

    assert review["decision"] == "request_recoding"
    assert review["missing_changed_files"] == ["src/domain/b.py"]
    assert review["unplanned_changed_files"] == []


def test_planner_rejects_directory_level_paths_from_workspace_tree():
    plan = Planner._extract_plan(
        {
            "plan": {
                "files": [
                    {
                        "path": "src/domain",
                        "nature": "modified",
                        "description": "Update the domain package.",
                        "confidence": 0.8,
                    }
                ],
                "summary": "Update domain.",
                "warnings": [],
            }
        },
        [
            {"role": "user", "content": "Update the domain package."},
            {
                "role": "user",
                "content": "workspace_summary:\n"
                + '{"tree_preview":"./\\n  src/\\n    domain/\\n      model.py"}',
            },
        ],
    )

    assert plan.ok is False
    assert plan.error_code == "PLAN_DIRECTORY_PATHS"
    assert plan.files == []


@pytest.mark.asyncio
async def test_workflow_reviewer_rework_continues_until_approved(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-workflow-rework-loop.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    import app.services.workflow_engine as workflow_engine_service

    patch_service_settings(monkeypatch, fake_settings, kanban_service)
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="backend-workflow-rework-loop",
        title="Rework loop",
        description="Smoke",
        status_key="draft",
    )["task"]
    plan_calls = 0
    coding_calls = 0

    async def plan_runner(body: dict) -> PlanResponse:
        nonlocal plan_calls
        plan_calls += 1
        planned_path = "src/main/java/org/example/First.java" if plan_calls == 1 else "src/main/java/org/example/Second.java"
        return PlanResponse(
            ok=True,
            task_id=body["task_id"],
            files=[PlanFile(path=planned_path, nature="new", description="Generated test plan.")],
            summary="Plan",
            session_id=f"plan-{plan_calls}",
        )

    async def coding_runner(body: dict) -> IdeChatResponse:
        nonlocal coding_calls
        coding_calls += 1
        assert any("workflow_phase_context:" in message.get("content", "") for message in body["messages"])
        return IdeChatResponse(
            ok=True,
            done=True,
            task_id=body["task_id"],
            session_id=f"coding-{coding_calls}",
            ops=[
                FileOp(
                    op="create_file",
                    path="src/main/java/org/example/Second.java",
                    content="class Second {}",
                )
            ],
        )

    engine = workflow_engine_service.WorkflowEngine(plan_runner=plan_runner, coding_runner=coding_runner)
    await engine.run(
        task["id"],
        {
            "project_id": "backend-workflow-rework-loop",
            "mode": "agent",
            "messages": [{"role": "user", "content": "Add a class."}],
            "workspace": {"tree_preview": "test/\n  src/\n    main/\n      java/\n", "source_map": None},
        },
    )

    assert plan_calls == 2
    assert coding_calls == 2
    task_detail = kanban_service.get_task(task["id"])["task"]
    assert task_detail["status_key"] == "ready_to_apply"
    result = [artifact for artifact in task_detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]
    assert result["payload"]["ok"] is True
    assert result["payload"]["status_key"] == "ready_to_apply"
    event_types = [event["event_type"] for event in task_detail["events"]]
    assert "workflow_rework_loop" in event_types


@pytest.mark.asyncio
async def test_workflow_recoding_receives_review_feedback(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-workflow-recoding-feedback.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    import app.services.workflow_engine as workflow_engine_service

    patch_service_settings(monkeypatch, fake_settings, kanban_service)
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="backend-workflow-recoding-feedback",
        title="Recoding feedback",
        description="Smoke",
        status_key="draft",
    )["task"]
    coding_calls = 0

    async def plan_runner(body: dict) -> PlanResponse:
        return PlanResponse(
            ok=True,
            task_id=body["task_id"],
            files=[
                PlanFile(path="src/domain/a.py", nature="modified", description="Update A."),
                PlanFile(path="src/domain/b.py", nature="modified", description="Update B."),
            ],
            summary="Update A and B.",
            session_id="plan-1",
        )

    async def coding_runner(body: dict) -> IdeChatResponse:
        nonlocal coding_calls
        coding_calls += 1
        context_messages = [message["content"] for message in body["messages"] if "workflow_phase_context:" in message.get("content", "")]
        assert context_messages
        if coding_calls == 1:
            assert '"files":[{"path":"src/domain/a.py"' in context_messages[-1]
            return IdeChatResponse(
                ok=True,
                done=True,
                task_id=body["task_id"],
                session_id="coding-1",
                ops=[FileOp(op="update_file", path="src/domain/a.py", content="print('a')\n")],
            )

        assert '"missing_changed_files":["src/domain/b.py"]' in context_messages[-1]
        return IdeChatResponse(
            ok=True,
            done=True,
            task_id=body["task_id"],
            session_id="coding-2",
            ops=[
                FileOp(op="update_file", path="src/domain/a.py", content="print('a')\n"),
                FileOp(op="update_file", path="src/domain/b.py", content="print('b')\n"),
            ],
        )

    engine = workflow_engine_service.WorkflowEngine(plan_runner=plan_runner, coding_runner=coding_runner)
    await engine.run(
        task["id"],
        {
            "project_id": "backend-workflow-recoding-feedback",
            "mode": "agent",
            "messages": [{"role": "user", "content": "Update both files."}],
            "workspace": {"tree_preview": "project/\n  src/\n    domain/\n      a.py\n      b.py", "source_map": None},
        },
    )

    assert coding_calls == 2
    task_detail = kanban_service.get_task(task["id"])["task"]
    assert task_detail["status_key"] == "ready_to_apply"
    result = [artifact for artifact in task_detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]
    assert result["payload"]["ok"] is True
    event_types = [event["event_type"] for event in task_detail["events"]]
    assert event_types.count("coding_context_prepared") == 2
    assert "workflow_rework_loop" in event_types


@pytest.mark.asyncio
async def test_plan_does_not_infer_user_management_files_when_planner_returns_no_plan(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-user-management-plan.db"
    project_root = tmp_path / "test"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.planner as planner_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": FakeNoPlanPlannerClient())

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            project_id = "backend-user-management-plan-smoke"
            response = await client.post(
                "/v1/plan",
                json={
                    "project_id": project_id,
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [
                        {
                            "role": "user",
                            "content": "加一个用户管理功能，需要有注册，以及用户的增删改查，当前不需要考虑注册用户的权限，任何人都可以注册，但是需要考虑增删改查的权限，当前无设计，保留一个可以扩展的方式",
                        }
                    ],
                    "workspace": {
                        "root_id": project_id,
                        "changed_files": [],
                        "open_files": [],
                        "source_map": None,
                        "tree_preview": (
                            "test/\n"
                            "  src/\n"
                            "    main/\n"
                            "      java/\n"
                            "        org/\n"
                            "          example/\n"
                            "            controller/\n"
                            "              Application.java\n"
                            "      resources/\n"
                            "        application.properties\n"
                            "  pom.xml"
                        ),
                    },
                    "tool_results": [],
                },
                headers={"X-DevWerk-Project-Id": project_id},
            )

    assert response.status_code == 200
    plan = response.json()
    assert plan["ok"] is False
    assert plan["status_key"] == "failed"
    assert plan["error_code"] == "PLAN_EMPTY"
    assert plan["phase_output"]["phase"] == "plan"
    assert plan["files"] == []
    assert plan["warnings"]


@pytest.mark.asyncio
async def test_plan_does_not_infer_tenant_management_files_when_planner_returns_no_plan(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-tenant-management-plan.db"
    project_root = tmp_path / "test"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.planner as planner_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": FakeNoPlanPlannerClient())

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            project_id = "backend-tenant-management-plan-smoke"
            response = await client.post(
                "/v1/plan",
                json={
                    "project_id": project_id,
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "\u9700\u8981\u6709\u4e00\u4e2a\u79df\u6237\u7ba1\u7406\uff0c"
                                "\u6bcf\u4e2aorg\u5fc5\u987b\u6709\u4e00\u4e2atenantid\uff0c"
                                "\u7528\u6765\u7ba1\u7406\u5f52\u5c5e"
                            ),
                        }
                    ],
                    "workspace": {
                        "root_id": project_id,
                        "changed_files": [],
                        "open_files": [],
                        "source_map": None,
                        "tree_preview": (
                            "test/\n"
                            "  src/\n"
                            "    main/\n"
                            "      java/\n"
                            "        org/\n"
                            "          example/\n"
                            "            controller/\n"
                            "              Application.java\n"
                            "      resources/\n"
                            "        application.properties\n"
                            "  pom.xml"
                        ),
                    },
                    "tool_results": [],
                },
                headers={"X-DevWerk-Project-Id": project_id},
            )

    assert response.status_code == 200
    plan = response.json()
    assert plan["ok"] is False
    assert plan["status_key"] == "failed"
    assert plan["error_code"] == "PLAN_EMPTY"
    assert plan["phase_output"]["phase"] == "plan"
    assert plan["files"] == []
    assert plan["warnings"]


def test_planner_extract_plan_returns_failure_when_fallback_cannot_infer_files():
    plan = Planner._extract_plan(
        {"raw_text": "I need more information before planning files."},
        [{"role": "user", "content": "\u8fd9\u53ea\u662f\u4e00\u4e2a\u666e\u901a\u95ee\u9898\uff0c\u4e0d\u9700\u8981\u6539\u4ee3\u7801"}],
    )

    assert plan.ok is False
    assert plan.error_code == "PLAN_EMPTY"
    assert plan.files == []


def test_planner_fallback_uses_ide_diagnostic_paths_without_framework_guessing():
    plan = Planner._extract_plan(
        {"raw_text": "I need to inspect before planning."},
        [
            {"role": "user", "content": "Unclosed character class\nIllegal escape character in string literal"},
            {
                "role": "user",
                "content": "workspace_summary:\n"
                + json.dumps(
                    {
                        "source_map": {
                            "files": [
                                {
                                    "path": "src/main/java/org/example/dto/TenantCreateRequest.java",
                                    "kind": "source",
                                    "language": "java",
                                },
                                {
                                    "path": "src/main/java/org/example/service/impl/OrganizationServiceImpl.java",
                                    "kind": "source",
                                    "language": "java",
                                },
                            ]
                        },
                        "syntax_diagnostics": [
                            {
                                "path": ".devwerk/20260618/after/src/main/java/org/example/service/impl/OrganizationServiceImpl.java",
                                "line": 84,
                                "column": 60,
                                "message": "Snapshot copy should be ignored.",
                            },
                            {
                                "path": "src/main/java/org/example/dto/TenantCreateRequest.java",
                                "line": 28,
                                "column": 34,
                                "message": "Illegal escape character in string literal",
                            }
                        ],
                    }
                ),
            },
        ],
    )

    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/main/java/org/example/dto/TenantCreateRequest.java"]
    assert "diagnostic" in plan.summary.lower()


def test_planner_executes_minimax_text_tool_requests_before_planning(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "src/domain"
    source_dir.mkdir(parents=True)
    (source_dir / "Tenant.py").write_text("class Tenant:\n    pass\n", encoding="utf-8")
    fake_client = FakePlannerResearchClient("src/domain/Tenant.py")

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    events: list[tuple[str, dict]] = []
    planner = Planner(event_sink=lambda event_type, payload: events.append((event_type, payload)))
    plan = planner.plan(
        messages=[
            {"role": "user", "content": "Refactor tenant to match the project structure."},
            {
                "role": "user",
                "content": "workspace_summary:\n"
                + '{"source_map":{"files":[{"path":"src/domain/Tenant.py","kind":"source","language":"python"}]},"tree_preview":"./\\n  src/\\n    domain/\\n      Tenant.py"}',
            },
        ],
        project_root=str(project_root),
    )

    assert fake_client.calls == 2
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/domain/Tenant.py"]
    assert "plan_tool_requests" in [event_type for event_type, _ in events]
    assert "plan_tool_results" in [event_type for event_type, _ in events]


def test_planner_recovers_natural_language_search_intent(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    dto_dir = project_root / "src/main/java/org/example/dto"
    dto_dir.mkdir(parents=True)
    (dto_dir / "TenantCreateRequest.java").write_text(
        "package org.example.dto;\n\n"
        "import jakarta.validation.constraints.Pattern;\n\n"
        "public class TenantCreateRequest {\n"
        "    @Pattern(regexp = \"^$|^[0-9+\\- ]{6,32}$\")\n"
        "    private String contactPhone;\n"
        "}\n",
        encoding="utf-8",
    )
    fake_client = FakePlannerNaturalLanguageSearchClient()

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    events: list[tuple[str, dict]] = []
    planner = Planner(event_sink=lambda event_type, payload: events.append((event_type, payload)))
    plan = planner.plan(
        messages=[
            {"role": "user", "content": "Unclosed character class\nIllegal escape character in string literal"},
            {
                "role": "user",
                "content": "workspace_summary:\n"
                + json.dumps(
                    {
                        "source_map": {
                            "files": [
                                {
                                    "path": "src/main/java/org/example/dto/TenantCreateRequest.java",
                                    "kind": "source",
                                    "language": "java",
                                }
                            ]
                        },
                        "tree_preview": "./\n  src/\n    main/\n      java/\n        org/\n          example/\n            dto/\n              TenantCreateRequest.java",
                    }
                ),
            },
        ],
        project_root=str(project_root),
    )

    assert fake_client.calls == 3
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/main/java/org/example/dto/TenantCreateRequest.java"]
    tool_request_events = [payload for event_type, payload in events if event_type == "plan_tool_requests"]
    assert any(
        any(req["tool"] == "search" and req["args"]["query"] in {"@Pattern", "Pattern.compile", "Pattern"} for req in event["requests"])
        for event in tool_request_events
    )


def test_planner_normalizes_foreign_absolute_tool_paths_by_source_map_suffix(monkeypatch, tmp_path):
    project_root = tmp_path / "test"
    source_dir = project_root / "src/domain"
    source_dir.mkdir(parents=True)
    (source_dir / "Tenant.py").write_text("class Tenant:\n    tenant_id: str\n", encoding="utf-8")
    fake_client = FakePlannerResearchClient("/Users/jonathan/work/code/sandbox/ai-coding/test/src/domain/Tenant.py")

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    planner = Planner()
    plan = planner.plan(
        messages=[
            {"role": "user", "content": "Tenant structure does not match the generic structure."},
            {
                "role": "user",
                "content": "workspace_summary:\n"
                + '{"source_map":{"files":[{"path":"src/domain/Tenant.py","kind":"source","language":"python"}]},"tree_preview":"./\\n  src/\\n    domain/\\n      Tenant.py"}',
            },
        ],
        project_root=str(project_root),
    )

    assert fake_client.calls == 2
    assert plan.ok is True
    assert plan.files[0].path == "src/domain/Tenant.py"


def test_tool_protocol_accepts_search_pattern_without_path():
    from app.services.coerce import coerce_to_toolrequests
    from app.services.validation import validate_model_response

    obj = {
        "reply": "Searching first.",
        "code_tree": None,
        "ops": [],
        "tool_requests": [
            {"id": "s1", "tool": "search", "args": {"pattern": "class TenantServiceImpl"}},
        ],
        "patch_ops": [],
        "done": False,
    }

    validate_model_response(obj)
    assert obj["tool_requests"][0]["args"]["query"] == "class TenantServiceImpl"
    requests = coerce_to_toolrequests(obj["tool_requests"])
    assert requests[0].tool == "search"
    assert requests[0].args["query"] == "class TenantServiceImpl"
    assert requests[0].args["paths"] == []


@pytest.mark.asyncio
async def test_execute_resolves_tool_requests_with_project_relative_paths(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-tool-loop.db"
    project_root = tmp_path / "test"
    source_dir = project_root / "src/main/java/org/example"
    source_dir.mkdir(parents=True)
    (project_root / "pom.xml").write_text("<project></project>\n", encoding="utf-8")
    (source_dir / "Main.java").write_text("package org.example;\n\npublic class Main {}\n", encoding="utf-8")
    fake_settings = FakeSettings(db_path)
    fake_executor = FakeToolLoopExecutorClient()

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(ide_routes, "get_llm_client", lambda agent="executor": fake_executor)

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            execute_response = await client.post(
                "/v1/execute",
                json={
                    "project_id": "backend-tool-loop-smoke",
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [
                        {
                            "role": "user",
                            "content": "Add a simple Spring Boot hello controller.",
                        }
                    ],
                    "approved_paths": [
                        "pom.xml",
                        "src/main/java/org/example/Main.java",
                        "src/main/java/org/example/HelloController.java",
                    ],
                    "approved_ops": [],
                    "workspace": {
                        "root_id": "backend-tool-loop-smoke",
                        "changed_files": [],
                        "open_files": [],
                        "tree_preview": "./\n  pom.xml\n  src/\n    main/\n      java/\n        org/\n          example/\n            Main.java",
                        "source_map": None,
                    },
                },
            )

    assert execute_response.status_code == 200
    executed = execute_response.json()
    assert executed["ok"] is True
    assert executed["status_key"] == "ready_to_apply"
    assert executed["tool_requests"] == []
    assert fake_executor.calls == 2
    assert executed["ops"][0]["path"] == "src/main/java/org/example/HelloController.java"


@pytest.mark.asyncio
async def test_execute_repairs_model_protocol_errors(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-protocol-repair.db"
    project_root = tmp_path / "protocol-project"
    (project_root / "src/domain").mkdir(parents=True)
    (project_root / "src/domain/a.py").write_text("print('old')\n", encoding="utf-8")
    fake_settings = FakeSettings(db_path)
    fake_executor = FakeProtocolRepairExecutorClient()

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(ide_routes, "get_llm_client", lambda agent="executor": fake_executor)

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            execute_response = await client.post(
                "/v1/execute",
                json={
                    "project_id": "backend-protocol-repair-smoke",
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [{"role": "user", "content": "Update src/domain/a.py."}],
                    "approved_paths": ["src/domain/a.py"],
                    "approved_ops": [],
                    "workspace": {
                        "root_id": "backend-protocol-repair-smoke",
                        "changed_files": [],
                        "open_files": [],
                        "tree_preview": "./\n  src/\n    domain/\n      a.py",
                        "source_map": None,
                    },
                },
            )

    assert execute_response.status_code == 200
    executed = execute_response.json()
    assert fake_executor.calls == 2
    assert executed["ok"] is True
    assert executed["ops"][0]["path"] == "src/domain/a.py"
    assert executed["status_key"] == "ready_to_apply"


@pytest.mark.asyncio
async def test_execute_returns_client_tool_requests_and_apply_result_completes_task(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-client-tool.db"
    project_root = tmp_path / "tool-project"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(ide_routes, "get_llm_client", lambda agent="executor": FakeClientToolExecutorClient())

    task = kanban_service.create_task(
        project_id="backend-client-tool-smoke",
        title="Add controller",
        description="Smoke",
        status_key="planned",
    )["task"]

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            execute_response = await client.post(
                "/v1/execute",
                json={
                    "project_id": "backend-client-tool-smoke",
                    "task_id": task["id"],
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [
                        {"role": "user", "content": "Add a controller and verify it compiles."}
                    ],
                    "approved_paths": ["src/main/java/com/devwerk/demo/HelloController.java"],
                    "approved_ops": [],
                    "workspace": {
                        "root_id": "backend-client-tool-smoke",
                        "changed_files": [],
                        "open_files": [],
                        "tree_preview": "",
                        "source_map": None,
                    },
                },
                headers={"X-DevWerk-Project-Id": "backend-client-tool-smoke"},
            )

            assert execute_response.status_code == 200
            executed = execute_response.json()
            assert executed["ok"] is True
            assert executed["status_key"] == "ready_to_apply"
            assert len(executed["ops"]) == 1
            assert executed["tool_requests"] == [
                {
                    "id": "compile",
                    "tool": "run_command",
                    "args": {"command": ["./mvnw", "test"], "timeout_seconds": 120},
                }
            ]
            assert executed["phase_output"]["outputs"]["client_tool_requests"][0]["tool"] == "run_command"

            apply_response = await client.post(
                f"/v1/kanban/tasks/{task['id']}/actions",
                json={
                    "action": "apply_result",
                    "payload": {
                        "ok": True,
                        "snapshot_id": "20260614-0001-smoke",
                        "changed_paths": ["src/main/java/com/devwerk/demo/HelloController.java"],
                        "verification": {
                            "required": ["compile"],
                            "results": {"compile": "passed"},
                            "tool_results": [
                                {
                                    "id": "compile",
                                    "tool": "run_command",
                                    "ok": True,
                                    "content": "BUILD SUCCESS",
                                    "error": None,
                                }
                            ],
                        },
                    },
                },
            )

            assert apply_response.status_code == 200
            applied = apply_response.json()
            assert applied["task"]["status_key"] == "done"

            task_response = await client.get(f"/v1/kanban/tasks/{task['id']}")
            task_detail = task_response.json()["task"]
            artifact_types = [artifact["artifact_type"] for artifact in task_detail["artifacts"]]
            assert "apply_result" in artifact_types
            assert artifact_types.count("workflow_phase_output") >= 2


def _apply_file_ops(project_root: Path, ops: list[dict]) -> None:
    for op in ops:
        rel = Path(str(op["path"]))
        if rel.is_absolute() or ".." in rel.parts:
            raise AssertionError(f"unsafe op path: {op['path']}")
        target = project_root / rel
        if op["op"] == "create_dir":
            target.mkdir(parents=True, exist_ok=True)
        elif op["op"] in {"create_file", "update_file"}:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(op.get("content") or "", encoding="utf-8")
        elif op["op"] == "delete_path" and target.exists():
            if target.is_dir():
                raise AssertionError("smoke helper refuses to delete directories")
            target.unlink()
        else:
            raise AssertionError(f"unsupported op: {op['op']}")


def test_anthropic_non_json_text_does_not_generate_framework_ops(monkeypatch):
    client = AnthropicClient.__new__(AnthropicClient)
    monkeypatch.setattr(
        client,
        "chat_json",
        lambda messages: {
            "raw_text": "I can create a Spring Boot REST API for Java 21.",
            "reply": "I can create a Spring Boot REST API for Java 21.",
        },
    )

    response = client.chat_structured(
        [
            {
                "role": "user",
                "content": "Create a Spring Boot scaffold compatible with JDK 21 and a hello REST API.",
            }
        ]
    )

    assert response["done"] is False
    assert response["ops"] == []
    assert response["tool_requests"] == []
    assert response["patch_ops"] == []
    assert response["raw_model_text"]


def test_llm_clients_ignore_environment_proxy_by_default():
    anthropic = AnthropicClient({"api_name": "minimax", "api_key": "test", "base_url": "https://api.minimaxi.com/anthropic"})
    openai = OpenAIClient({"api_name": "openai", "api_key": "test", "base_url": "https://api.openai.com/v1"})
    ollama = OllamaClient({"base_url": "http://127.0.0.1:11434", "model": "stub"})

    assert anthropic.session.trust_env is False
    assert openai.session.trust_env is False
    assert ollama.session.trust_env is False


def test_usage_cache_hit_rate_is_clamped():
    usage = _normalize_usage({"input_tokens": 232, "cached_input_tokens": 1650, "output_tokens": 10})
    assert usage["input_cache_hit_rate"] == 1.0
