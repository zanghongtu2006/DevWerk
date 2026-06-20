from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import requests

from app.models.ide import FileOp, IdeChatResponse, ToolRequest
from app.models.plan import PlanFile, PlanResponse
from app.services.anthropic_client import AnthropicClient
from app.services.coder_harness import build_code_context_summary
from app.services.openai_client import OpenAIClient
from app.services.ollama_client import OllamaClient
from app.services.planner import Planner
from app.services.provider_errors import LLMProviderError, ProviderErrorDetails, classify_provider_response
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


class FakePlannerDirectToolClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"id": "l1", "tool": "list_dir", "args": {"path": "project/src", "max_depth": 3}}
        if self.calls == 2:
            assert any("tool_results:" in message.get("content", "") for message in messages)
            return {"id": "r1", "tool": "read_file", "args": {"path": "project/src/main.py"}}
        assert sum("tool_results:" in message.get("content", "") for message in messages) >= 2
        return {
            "plan": {
                "files": [
                    {
                        "path": "src/main.py",
                        "nature": "modified",
                        "description": "Fix the compile error found through project inspection.",
                        "confidence": 0.95,
                    }
                ],
                "summary": "Fix the inspected source file.",
                "warnings": [],
            }
        }


class FakePlannerResearchBudgetClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls <= 4:
            return {
                "tool_requests": [
                    {
                        "id": f"r{self.calls}",
                        "tool": "read_file",
                        "args": {"path": f"src/file{self.calls}.py"},
                    }
                ]
            }
        assert "research budget is complete" in messages[-1]["content"]
        return {
            "plan": {
                "files": [
                    {
                        "path": "src/file1.py",
                        "nature": "modified",
                        "description": "Fix the compile error identified during research.",
                        "confidence": 0.9,
                    }
                ],
                "summary": "Fix the researched compile error.",
                "warnings": [],
            }
        }


class FakePlannerProtocolRecoveryClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"tool_requests": [{"id": "r1", "tool": "read_file", "args": {"path": "src/main.py"}}]}
        if self.calls == 2:
            return {"raw_text": "I found one issue. Let me check related code before producing the plan."}
        assert "neither a valid tool request nor a file-level plan" in messages[-1]["content"]
        return {
            "plan": {
                "files": [
                    {
                        "path": "src/main.py",
                        "nature": "modified",
                        "description": "Fix the issue found during research.",
                        "confidence": 0.9,
                    }
                ],
                "summary": "Fix the researched issue.",
                "warnings": [],
            }
        }


class FakePlannerFormatRepairClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls <= 4:
            return {"tool_requests": [{"id": f"r{self.calls}", "tool": "read_file", "args": {"path": "src/main.py"}}]}
        if self.calls == 5:
            return {"raw_text": "## Summary\n\n`src/main.py` contains the compile defect and must be modified."}
        assert "Convert the supplied planner analysis" in messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        assert payload["allowed_paths"] == ["src/main.py"]
        return {
            "plan": {
                "files": [
                    {
                        "path": "src/main.py",
                        "nature": "modified",
                        "description": "Fix the compile defect from the planner analysis.",
                        "confidence": 0.9,
                    }
                ],
                "summary": "Fix the compile defect.",
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


class FakeRetryableProviderErrorExecutorClient:
    def __init__(self):
        self.calls = 0

    def chat_structured(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise LLMProviderError(
                ProviderErrorDetails(
                    provider="anthropic",
                    api_name="minimax",
                    status_code=529,
                    error_code="LLM_OVERLOADED",
                    message="The API is temporarily overloaded.",
                    retryable=True,
                    provider_error_type="overloaded_error",
                    body_snippet='{"type":"error","error":{"type":"overloaded_error"}}',
                )
            )
        return {
            "reply": "Recovered after provider retry.",
            "ops": [
                {
                    "op": "create_file",
                    "path": "service/retry.py",
                    "language": "python",
                    "content": "print('retry ok')\n",
                }
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


class FakeIncrementalExecutorClient:
    def __init__(self):
        self.calls = 0

    def chat_structured(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "reply": "Fixed the first file; inspecting the dependency next.",
                "ops": [{"op": "update_file", "path": "src/a.py", "content": "A = 2\n"}],
                "patch_ops": [],
                "tool_requests": [{"id": "read-b", "tool": "read_file", "args": {"path": "src/b.py"}}],
                "done": False,
            }

        assert any("tool_results:" in message.get("content", "") for message in messages)
        assert any("candidate_revision_state:" in message.get("content", "") for message in messages)
        return {
            "reply": "Completed both related fixes.",
            "ops": [{"op": "update_file", "path": "src/b.py", "content": "B = 2\n"}],
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
        if self.calls <= 2:
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

        assert any("Do not use patch_ops again" in message.get("content", "") for message in messages)
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

    async def approve_review(body: dict) -> dict:
        return {"decision": "approve", "summary": "Candidate satisfies the plan.", "findings": [], "warnings": []}

    monkeypatch.setattr(ide_routes, "_run_review_phase", approve_review)

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
async def test_execute_retries_retryable_provider_error(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-provider-retry.db"
    project_root = tmp_path / "retry-project"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)
    fake_executor = FakeRetryableProviderErrorExecutorClient()

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.usage as usage_service

    patch_service_settings(monkeypatch, fake_settings, main_module, ide_routes, kanban_service, usage_service)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(ide_routes, "get_llm_client", lambda agent="executor": fake_executor)
    task = kanban_service.create_task(
        project_id="backend-provider-retry",
        title="Retry provider error",
        description="Smoke",
        status_key="planned",
    )["task"]

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/execute",
                json={
                    "project_id": "backend-provider-retry",
                    "task_id": task["id"],
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [{"role": "user", "content": "Create retry file."}],
                    "approved_paths": ["service/retry.py"],
                    "approved_ops": [],
                    "workspace": {"root_id": "backend-provider-retry", "source_map": None},
                },
                headers={"X-DevWerk-Project-Id": "backend-provider-retry"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ops"][0]["path"] == "service/retry.py"
    assert fake_executor.calls == 2
    events = kanban_service.list_events(project_id="backend-provider-retry", task_id=task["id"], limit=100)["events"]
    retry_events = [event for event in events if event["event_type"] == "execute_llm_round_retry"]
    assert retry_events
    assert retry_events[0]["payload"]["error_code"] == "LLM_OVERLOADED"
    assert retry_events[0]["payload"]["provider_error"]["status_code"] == 529


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

    async def approve_review(body: dict) -> dict:
        return {"decision": "approve", "summary": "Candidate satisfies the plan.", "findings": [], "warnings": []}

    monkeypatch.setattr(ide_routes, "_run_review_phase", approve_review)

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
            for _ in range(300):
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


@pytest.mark.asyncio
async def test_workflow_message_api_confirms_plan_without_starting_new_task(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-workflow-conversation-api.db"
    project_root = tmp_path / "conversation-project"
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

    async def approve_review(body: dict) -> dict:
        return {"decision": "approve", "summary": "Approved.", "findings": [], "warnings": []}

    monkeypatch.setattr(ide_routes, "_run_review_phase", approve_review)
    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = (await client.post(
                "/v1/workflows",
                json={
                    "project_id": "conversation-api",
                    "mode": "agent",
                    "interaction_mode": "confirm_plan",
                    "project_root": str(project_root),
                    "messages": [{"role": "user", "content": "Create a minimal runnable smoke scaffold."}],
                    "workspace": {"root_id": "conversation-api", "tree_preview": "", "source_map": None},
                },
            )).json()
            task_id = start["task_id"]

            waiting = {}
            for _ in range(120):
                waiting = (await client.get(start["poll_url"])).json()
                if waiting.get("result"):
                    break
                await asyncio.sleep(0.05)
            assert waiting["result"]["waiting_for"] == "plan_confirmation"
            assert waiting["status_key"] == "planned"

            resumed = (await client.post(
                f"/v1/workflows/{task_id}/messages",
                json={"action": "confirm_plan", "message": "Confirmed."},
            )).json()
            assert resumed["task_id"] == task_id
            assert "result_after=" in resumed["poll_url"]

            completed = {}
            for _ in range(120):
                completed = (await client.get(resumed["poll_url"])).json()
                if completed.get("result"):
                    break
                await asyncio.sleep(0.05)
            assert completed["result"]["status_key"] == "ready_to_apply"
            assert completed["result"]["waiting_for"] is None
            conversation = kanban_service.get_conversation(task_id)
            assert [message["message_type"] for message in conversation["messages"]].count("plan_confirmation") == 1

            failed_task = kanban_service.create_task(
                project_id="conversation-api",
                title="Terminal task",
                description="Must not be resumed implicitly.",
                status_key="failed",
            )["task"]
            rejected = (await client.post(
                f"/v1/workflows/{failed_task['id']}/messages",
                json={"action": "confirm_plan", "message": "Do not replay this."},
            )).json()
            assert rejected["ok"] is False
            assert rejected["error_code"] == "WORKFLOW_TERMINAL"
            assert rejected["retryable"] is False


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


def test_workflow_reviewer_accepts_unchanged_candidate_paths():
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

    assert review["decision"] == "approve"
    assert review["missing_changed_files"] == ["src/domain/b.py"]
    assert review["required_missing_files"] == []
    assert review["unplanned_changed_files"] == []


def test_workflow_reviewer_treats_required_file_as_semantic_review_evidence():
    from app.services.workflow_engine import _review_result

    plan = PlanResponse(
        files=[
            PlanFile(path="src/domain/a.py", nature="modified", description="Update A."),
            PlanFile(path="src/domain/b.py", nature="modified", description="Update B.", required=True),
        ]
    )
    executed = IdeChatResponse(
        ops=[FileOp(op="update_file", path="src/domain/a.py", content="print('a')\n")],
        done=True,
    )

    review = _review_result(plan, executed)

    assert review["decision"] == "approve"
    assert review["required_missing_files"] == ["src/domain/b.py"]


def test_workflow_reviewer_includes_paths_applied_before_verification_rework():
    from app.services.workflow_engine import _review_result

    plan = PlanResponse(
        files=[
            PlanFile(path="src/domain/a.py", nature="modified", description="Update A.", required=True),
            PlanFile(path="src/domain/b.py", nature="modified", description="Update B.", required=True),
        ]
    )
    executed = IdeChatResponse(
        ops=[FileOp(op="update_file", path="src/domain/b.py", content="print('b')\n")],
        done=True,
    )

    review = _review_result(plan, executed, prior_changed_paths=["src/domain/a.py"])

    assert review["decision"] == "approve"
    assert review["missing_changed_files"] == []
    assert review["required_missing_files"] == []


def test_workflow_reviewer_does_not_treat_prior_revision_as_current_unplanned_change():
    from app.services.workflow_engine import _review_result

    plan = PlanResponse(
        files=[PlanFile(path="src/domain/b.py", nature="modified", description="Fix the remaining error.")]
    )
    executed = IdeChatResponse(
        ops=[FileOp(op="update_file", path="src/domain/b.py", content="print('fixed')\n")],
        done=True,
    )

    review = _review_result(plan, executed, prior_changed_paths=["src/domain/a.py"])

    assert review["decision"] == "approve"
    assert review["normalized_changed_files"] == ["src/domain/b.py"]
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


def test_planner_fallback_does_not_treat_workflow_feedback_as_user_paths():
    from app.services.planner import _fallback_plan

    plan = _fallback_plan(
        {"raw_text": "I need more evidence."},
        [
            {"role": "user", "content": "Fix all compilation errors."},
            {
                "role": "user",
                "content": (
                    "workflow_replan_feedback:\n"
                    '{"verification":{"tool_results":[{"content":"C:/workspace/project/src/App.java '
                    "http://example.invalid/build/help" + '"}]}}'
                ),
            },
        ],
    )

    assert plan.ok is False
    assert plan.error_code == "PLAN_EMPTY"
    assert plan.files == []


@pytest.mark.asyncio
async def test_workflow_reviewer_rework_continues_until_approved(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-workflow-rework-loop.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    import app.services.workflow as workflow_service
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
        phase_contexts = [
            message.get("content", "")
            for message in body["messages"]
            if "workflow_phase_context:" in message.get("content", "")
        ]
        assert phase_contexts
        if coding_calls == 2:
            assert '"unplanned_changed_files":["src/main/java/org/example/Second.java"]' in phase_contexts[-1]
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
    import app.services.workflow as workflow_service
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
    review_calls = 0

    async def plan_runner(body: dict) -> PlanResponse:
        return PlanResponse(
            ok=True,
            task_id=body["task_id"],
            files=[
                PlanFile(path="src/domain/a.py", nature="modified", description="Update A."),
                PlanFile(path="src/domain/b.py", nature="modified", description="Update B.", required=True),
            ],
            summary="Update A and B.",
            session_id="plan-1",
        )

    async def coding_runner(body: dict) -> IdeChatResponse:
        nonlocal coding_calls
        coding_calls += 1
        assert body["client_capabilities"] == {"tools": ["run_command"]}
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
            tool_requests=[
                ToolRequest(id="unsupported", tool="ide_syntax_check", args={"paths": ["src/domain/a.py"]}),
                ToolRequest(id="verify", tool="run_command", args={"command": ["project-check"]}),
            ],
        )

    async def review_runner(body: dict) -> dict:
        nonlocal review_calls
        review_calls += 1
        if review_calls == 1:
            return {"decision": "fail", "summary": "The requested second module is still incomplete."}
        return {"decision": "approve", "summary": "Both requested modules are complete."}

    engine = workflow_engine_service.WorkflowEngine(
        plan_runner=plan_runner,
        coding_runner=coding_runner,
        review_runner=review_runner,
    )
    await engine.run(
        task["id"],
        {
            "project_id": "backend-workflow-recoding-feedback",
            "mode": "agent",
            "messages": [{"role": "user", "content": "Update both files."}],
            "workspace": {"tree_preview": "project/\n  src/\n    domain/\n      a.py\n      b.py", "source_map": None},
            "client_capabilities": {"tools": ["run_command"]},
        },
    )

    assert coding_calls == 2
    task_detail = kanban_service.get_task(task["id"])["task"]
    assert task_detail["status_key"] == "ready_to_apply"
    result = [artifact for artifact in task_detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]
    assert result["payload"]["ok"] is True
    assert [request["id"] for request in result["payload"]["tool_requests"]] == ["verify"]
    event_types = [event["event_type"] for event in task_detail["events"]]
    assert event_types.count("coding_context_prepared") == 2
    assert "workflow_rework_loop" in event_types


@pytest.mark.asyncio
async def test_workflow_rework_budget_emits_explicit_resumable_pause(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-workflow-rework-pause.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    import app.services.workflow_engine as workflow_engine_service

    patch_service_settings(monkeypatch, fake_settings, kanban_service)
    kanban_service._initialized = False
    project_id = "backend-workflow-rework-pause"
    kanban_service.update_project_settings(project_id, parameters={"workflow_max_rework_runs": 1})
    task = kanban_service.create_task(
        project_id=project_id,
        title="Pause after repeated review",
        description="Smoke",
        status_key="draft",
    )["task"]

    async def plan_runner(body: dict) -> PlanResponse:
        return PlanResponse(
            ok=True,
            task_id=body["task_id"],
            files=[PlanFile(path="src/module.py", nature="modified", description="Repair the module.")],
            summary="Repair the module.",
            session_id="pause-plan",
        )

    async def coding_runner(body: dict) -> IdeChatResponse:
        return IdeChatResponse(
            ok=True,
            done=True,
            task_id=body["task_id"],
            ops=[FileOp(op="update_file", path="src/module.py", content="VALUE = 1\n")],
        )

    async def review_runner(body: dict) -> dict:
        return {"decision": "fail", "summary": "The repair still needs user-provided diagnostics."}

    engine = workflow_engine_service.WorkflowEngine(
        plan_runner=plan_runner,
        coding_runner=coding_runner,
        review_runner=review_runner,
    )
    await engine.run(
        task["id"],
        {
            "project_id": project_id,
            "mode": "agent",
            "messages": [{"role": "user", "content": "Fix the compilation errors."}],
            "workspace": {"tree_preview": "project/\n  src/\n    module.py", "source_map": None},
        },
    )

    task_detail = kanban_service.get_task(task["id"])["task"]
    conversation = kanban_service.get_conversation(task["id"])
    result = [artifact for artifact in task_detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]["payload"]
    pause_events = [event for event in task_detail["events"] if event["event_type"] == "workflow_run_paused"]

    assert result["waiting_for"] == "user_guidance"
    assert result["interaction"]["reason"] == "rework_budget"
    assert result["interaction"]["actions"] == ["message", "cancel"]
    assert conversation["state"] == "waiting_user"
    assert conversation["waiting_for"] == "user_guidance"
    assert pause_events[-1]["payload"]["terminal"] is False
    assert pause_events[-1]["payload"]["max_rework_rounds"] == 1
    assert not [event for event in task_detail["events"] if event["event_type"] == "workflow_finished"]


@pytest.mark.asyncio
async def test_interactive_workflow_pauses_for_plan_confirmation_and_resumes(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-interactive-workflow.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    import app.services.workflow_engine as workflow_engine_service

    patch_service_settings(monkeypatch, fake_settings, kanban_service)
    kanban_service._initialized = False
    task = kanban_service.create_task(
        project_id="interactive-project",
        title="Interactive plan",
        description="Create a module",
        status_key="draft",
    )["task"]
    kanban_service.ensure_conversation(task["id"])
    kanban_service.append_conversation_message(task["id"], role="user", content="Create a module.")
    coding_calls = 0

    async def plan_runner(body: dict) -> PlanResponse:
        return PlanResponse(
            task_id=body["task_id"],
            files=[PlanFile(path="src/module.py", nature="new", intent="create", description="Create module.")],
            summary="Create src/module.py.",
        )

    async def coding_runner(body: dict) -> IdeChatResponse:
        nonlocal coding_calls
        coding_calls += 1
        return IdeChatResponse(
            task_id=body["task_id"],
            done=True,
            reply="Created module.",
            ops=[FileOp(op="create_file", path="src/module.py", content="VALUE = 1\n")],
        )

    async def review_runner(body: dict) -> dict:
        return {
            "decision": "approve",
            "summary": "Apply, then run the project check.",
            "verification_tool_requests": [
                {"id": "project-check", "tool": "run_command", "args": {"command": ["./project-check"]}}
            ],
        }

    engine = workflow_engine_service.WorkflowEngine(
        plan_runner=plan_runner,
        coding_runner=coding_runner,
        review_runner=review_runner,
    )
    body = {
        "project_id": "interactive-project",
        "mode": "agent",
        "interaction_mode": "confirm_plan",
        "messages": [{"role": "user", "content": "Create a module."}],
        "workspace": {"tree_preview": "project/", "source_map": None},
        "client_capabilities": {"tools": ["run_command", "ide_syntax_check"]},
    }
    await engine.run(task["id"], body)

    waiting_task = kanban_service.get_task(task["id"])["task"]
    waiting_result = [item for item in waiting_task["artifacts"] if item["artifact_type"] == "workflow_result"][-1]["payload"]
    assert waiting_result["waiting_for"] == "plan_confirmation"
    assert "Waiting for plan confirmation" in waiting_result["reply"]
    assert waiting_task["status_key"] == "planned"
    assert coding_calls == 0
    assert kanban_service.get_conversation(task["id"])["state"] == "waiting_user"
    waiting_events = kanban_service.get_task(task["id"])["task"]["events"]
    pause_events = [event for event in waiting_events if event["event_type"] == "workflow_run_paused"]
    assert pause_events[-1]["payload"]["waiting_for"] == "plan_confirmation"
    assert pause_events[-1]["payload"]["terminal"] is False

    kanban_service.append_conversation_message(
        task["id"], role="user", content="Confirmed.", message_type="plan_confirmation"
    )
    await engine.run(task["id"], {**body, "resume_action": "confirm_plan"})

    completed_task = kanban_service.get_task(task["id"])["task"]
    assert completed_task["status_key"] == "ready_to_apply"
    assert coding_calls == 1
    completed_result = [
        item for item in completed_task["artifacts"] if item["artifact_type"] == "workflow_result"
    ][-1]["payload"]
    assert completed_result["tool_requests"] == [
        {"id": "project-check", "tool": "run_command", "args": {"command": ["./project-check"]}}
    ]
    revision_events = [event for event in completed_task["events"] if event["event_type"] == "revision_created"]
    assert len(revision_events) == 1


def test_conversation_context_compresses_old_messages_and_keeps_recent_turns(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-context-compression.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    from app.services.conversation_context import prepare_conversation_context

    patch_service_settings(monkeypatch, fake_settings, kanban_service)
    kanban_service._initialized = False
    project_id = "compression-project"
    task = kanban_service.create_task(project_id=project_id, title="Compress", description="Compress", status_key="draft")["task"]
    kanban_service.update_project_settings(
        project_id,
        parameters={"context_budget_tokens": 120, "context_recent_messages": 3},
    )
    kanban_service.ensure_conversation(task["id"])
    for index in range(9):
        kanban_service.append_conversation_message(
            task["id"], role="user" if index % 2 == 0 else "assistant", content=f"turn-{index} " + ("context " * 80)
        )

    context = prepare_conversation_context(task["id"])
    conversation = kanban_service.get_conversation(task["id"])

    assert context["compressed"] is True
    assert conversation["summary_version"] == 1
    assert len([item for item in conversation["messages"] if not item["compressed"]]) == 3
    assert "turn-8" in context["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_workflow_engine_runs_project_defined_custom_columns(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-custom-workflow.db"
    fake_settings = FakeSettings(db_path)

    import app.services.kanban as kanban_service
    import app.services.workflow as workflow_service
    import app.services.workflow_engine as workflow_engine_service

    patch_service_settings(monkeypatch, fake_settings, kanban_service)
    kanban_service._initialized = False

    project_id = "backend-custom-workflow"
    custom_workflow = {
        "name": "custom",
        "version": 7,
        "columns": [
            {"status_key": "draft", "title": "Draft", "position": 10, "transition_to": ["indexed", "failed"]},
            {
                "status_key": "indexed",
                "title": "Indexed",
                "position": 20,
                "transition_to": ["design", "failed"],
                "agent": "context",
                "input_artifacts": ["workflow_request"],
                "output_artifact": "context_bundle",
                "success_action": "context_done",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "design",
                "title": "Design",
                "position": 30,
                "transition_to": ["build", "failed"],
                "agent": "planner",
                "input_artifacts": ["context_bundle"],
                "output_artifact": "plan_bundle",
                "success_action": "design_done",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "build",
                "title": "Build",
                "position": 40,
                "transition_to": ["quality", "design", "failed"],
                "agent": "coder",
                "input_artifacts": ["plan_bundle"],
                "output_artifact": "code_change_bundle",
                "success_action": "build_done",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "quality",
                "title": "Quality",
                "position": 50,
                "transition_to": ["ready_to_apply", "build", "design", "failed"],
                "agent": "reviewer",
                "input_artifacts": ["plan_bundle", "code_change_bundle"],
                "output_artifact": "review_bundle",
                "success_action": "quality_passed",
                "failure_actions": ["request_recoding", "request_replan", "fail"],
            },
            {"status_key": "ready_to_apply", "title": "Ready", "position": 60, "transition_to": ["done", "failed"]},
            {"status_key": "done", "title": "Done", "position": 70, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 80, "transition_to": ["draft"]},
        ],
        "actions": {
            "context_done": {"to": "indexed"},
            "design_done": {"to": "design"},
            "build_started": {"to": "build"},
            "build_done": {"to": "quality"},
            "quality_passed": {"to": "ready_to_apply"},
            "apply_succeeded": {"to": "done"},
            "request_recoding": {"to": "build"},
            "request_replan": {"to": "design"},
            "fail": {"to": "failed"},
            "retry": {"to": "draft"},
            "abandon": {"to": "failed"},
        },
    }
    kanban_service.update_project_workflow(project_id, custom_workflow)
    task = kanban_service.create_task(
        project_id=project_id,
        title="Custom workflow",
        description="Smoke",
        status_key="draft",
    )["task"]

    async def plan_runner(body: dict) -> PlanResponse:
        return PlanResponse(
            ok=True,
            task_id=body["task_id"],
            files=[PlanFile(path="src/custom.py", nature="new", description="Create custom file.")],
            summary="Custom plan.",
            session_id="custom-plan",
        )

    async def coding_runner(body: dict) -> IdeChatResponse:
        phase_contexts = [message["content"] for message in body["messages"] if "workflow_phase_context:" in message.get("content", "")]
        assert phase_contexts and '"phase":"build"' in phase_contexts[-1]
        assert body["approved_paths"] == ["src/custom.py"]
        return IdeChatResponse(
            ok=True,
            done=True,
            task_id=body["task_id"],
            session_id="custom-code",
            ops=[FileOp(op="create_file", path="src/custom.py", content="print('custom')\n")],
        )

    engine = workflow_engine_service.WorkflowEngine(plan_runner=plan_runner, coding_runner=coding_runner)
    await engine.run(
        task["id"],
        {
            "project_id": project_id,
            "mode": "agent",
            "messages": [{"role": "user", "content": "Create custom file."}],
            "workspace": {"tree_preview": "project/\n  src/\n", "source_map": None},
        },
    )

    task_detail = kanban_service.get_task(task["id"])["task"]
    assert task_detail["status_key"] == "ready_to_apply"
    moved_to = [event["to_status"] for event in task_detail["events"] if event["event_type"] == "task_moved"]
    assert ["indexed", "design", "build", "quality", "ready_to_apply"] == moved_to[-5:]
    assert "planned" not in moved_to
    assert "coding" not in moved_to
    assert "reviewed" not in moved_to
    completed_columns = [
        event["payload"]["status_key"]
        for event in task_detail["events"]
        if event["event_type"] == "workflow_column_completed"
    ]
    assert completed_columns == ["indexed", "design", "build", "quality"]
    artifact_types = [artifact["artifact_type"] for artifact in task_detail["artifacts"]]
    assert {"context_bundle", "plan_bundle", "code_change_bundle", "review_bundle"}.issubset(artifact_types)
    result = [artifact for artifact in task_detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]
    assert result["payload"]["ok"] is True
    assert result["payload"]["status_key"] == "ready_to_apply"
    state = workflow_service.current_workflow_state(task["id"])
    assert state["actions"] == ["apply_result", "abandon"]
    with pytest.raises(ValueError, match="cannot move"):
        workflow_service.apply_workflow_action(task["id"], "design_done", {"reason": "invalid jump"})


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


def test_planner_executes_direct_top_level_tool_calls_across_research_rounds(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("broken = True\n", encoding="utf-8")
    fake_client = FakePlannerDirectToolClient()

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    events: list[tuple[str, dict]] = []
    plan = Planner(event_sink=lambda event_type, payload: events.append((event_type, payload))).plan(
        messages=[
            {"role": "user", "content": "Find and fix the compile errors."},
            {
                "role": "user",
                "content": "workspace_summary:\n"
                + '{"source_map":{"root":"project","files":[{"path":"src/main.py","kind":"source","language":"python"}]}}',
            },
        ],
        project_root=str(project_root),
    )

    assert fake_client.calls == 3
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/main.py"]
    requests = [payload for event_type, payload in events if event_type == "plan_tool_requests"]
    assert requests[0]["requests"][0]["args"]["path"] == "src"
    assert requests[1]["requests"][0]["args"]["path"] == "src/main.py"


def test_planner_reserves_a_final_synthesis_round_after_tool_budget(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "src"
    source_dir.mkdir(parents=True)
    for index in range(1, 5):
        (source_dir / f"file{index}.py").write_text(f"value = {index}\n", encoding="utf-8")
    fake_client = FakePlannerResearchBudgetClient()

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    events: list[tuple[str, dict]] = []
    plan = Planner(event_sink=lambda event_type, payload: events.append((event_type, payload)), max_rounds=4).plan(
        messages=[{"role": "user", "content": "Find and fix all compile errors."}],
        project_root=str(project_root),
    )

    assert fake_client.calls == 5
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/file1.py"]
    final_events = [payload for event_type, payload in events if event_type == "plan_llm_round_started"]
    assert final_events[-1]["final_synthesis"] is True


def test_planner_recovers_when_model_narrates_instead_of_using_protocol(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("broken = True\n", encoding="utf-8")
    fake_client = FakePlannerProtocolRecoveryClient()

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    plan = Planner().plan(
        messages=[{"role": "user", "content": "Find and fix the errors."}],
        project_root=str(project_root),
    )

    assert fake_client.calls == 3
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/main.py"]


def test_planner_normalizes_top_level_tool_request_array(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("broken = True\n", encoding="utf-8")

    class ArrayPlannerClient:
        def __init__(self):
            self.calls = 0

        def chat_json(self, messages):
            self.calls += 1
            if self.calls == 1:
                return [{"id": "read-main", "tool": "read_file", "args": {"path": "src/main.py"}}]
            return {
                "plan": {
                    "files": [
                        {
                            "path": "src/main.py",
                            "nature": "modified",
                            "description": "Fix the compile error.",
                            "confidence": 0.9,
                        }
                    ],
                    "summary": "Fix the inspected file.",
                    "warnings": [],
                }
            }

    client = ArrayPlannerClient()
    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": client)
    plan = Planner().plan(
        messages=[{"role": "user", "content": "Fix the compile errors."}],
        project_root=str(project_root),
    )

    assert client.calls == 2
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/main.py"]


def test_planner_repairs_markdown_final_analysis_into_plan_contract(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    source_dir = project_root / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("broken = True\n", encoding="utf-8")
    fake_client = FakePlannerFormatRepairClient()

    import app.services.planner as planner_service

    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": fake_client)
    events: list[tuple[str, dict]] = []
    plan = Planner(event_sink=lambda event_type, payload: events.append((event_type, payload)), max_rounds=4).plan(
        messages=[
            {"role": "user", "content": "Find and fix the errors."},
            {
                "role": "user",
                "content": 'workspace_summary:\n{"source_map":{"files":[{"path":"src/main.py"}]}}',
            },
        ],
        project_root=str(project_root),
    )

    assert fake_client.calls == 6
    assert plan.ok is True
    assert [item.path for item in plan.files] == ["src/main.py"]
    assert "plan_format_repair_completed" in [event_type for event_type, _ in events]


def test_planner_default_round_budget_is_a_high_safety_ceiling():
    assert Planner().max_rounds == 128


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


def test_execute_filters_canonicalize_project_root_prefixed_output_paths():
    from app.routes.ide import _filter_ops, _filter_patch_ops

    project_root = "C:/workspace/sample"
    approved = {"src/main.py"}
    ops = _filter_ops(
        [{"op": "update_file", "path": "sample/src/main.py", "content": "fixed\n"}],
        approved,
        project_root,
    )
    patches = _filter_patch_ops(
        [
            {
                "op": "apply_patch",
                "content": (
                    "--- a/sample/src/main.py\n"
                    "+++ b/sample/src/main.py\n"
                    "@@ -1 +1 @@\n"
                    "-broken\n"
                    "+fixed\n"
                ),
            }
        ],
        approved,
        project_root,
    )

    assert ops[0]["path"] == "src/main.py"
    assert "--- a/src/main.py" in patches[0]["content"]
    assert "+++ b/src/main.py" in patches[0]["content"]
    assert "sample/src/main.py" not in patches[0]["content"]


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
    task = kanban_service.create_task(
        project_id="backend-tool-loop-smoke",
        title="Tool evidence",
        description="Retain research evidence for review.",
        status_key="planned",
    )["task"]

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            execute_response = await client.post(
                "/v1/execute",
                json={
                    "project_id": "backend-tool-loop-smoke",
                    "task_id": task["id"],
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
    evidence = executed["phase_output"]["outputs"]["research_evidence"]
    assert {item["id"] for item in evidence} == {"r1", "r2", "r3"}
    assert all(item["ok"] for item in evidence)


@pytest.mark.asyncio
async def test_execute_preserves_candidate_changes_across_research_rounds(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-incremental-tool-loop.db"
    project_root = tmp_path / "incremental"
    (project_root / "src").mkdir(parents=True)
    (project_root / "src/a.py").write_text("A = 1\n", encoding="utf-8")
    (project_root / "src/b.py").write_text("B = 1\n", encoding="utf-8")
    fake_settings = FakeSettings(db_path)
    fake_executor = FakeIncrementalExecutorClient()

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
            response = await client.post(
                "/v1/execute",
                json={
                    "project_id": "incremental-project",
                    "mode": "agent",
                    "project_root": str(project_root),
                    "messages": [{"role": "user", "content": "Fix both modules."}],
                    "approved_paths": ["src/a.py", "src/b.py"],
                    "workspace": {"tree_preview": "./\n  src/\n    a.py\n    b.py"},
                },
            )

    payload = response.json()
    assert payload["ok"] is True
    assert payload["done"] is True
    assert fake_executor.calls == 2
    assert [op["path"] for op in payload["ops"]] == ["src/a.py", "src/b.py"]


def test_coding_rework_context_includes_previous_revision():
    from app.services.workflow_engine import _coding_phase_messages

    plan = PlanResponse(files=[PlanFile(path="src/a.py", nature="modified", description="Fix A")])
    previous = IdeChatResponse(
        reply="First attempt",
        done=True,
        ops=[FileOp(op="update_file", path="src/a.py", content="A = 1\n")],
    )

    messages = _coding_phase_messages([], plan, {"decision": "request_recoding"}, previous_revision=previous)

    assert '"previous_revision"' in messages[-1]["content"]
    assert '"content":"A = 1\\n"' in messages[-1]["content"]


def test_coding_apply_failure_context_requires_whole_file_operations():
    from app.services.workflow_engine import _coding_phase_messages

    plan = PlanResponse(files=[PlanFile(path="src/a.py", nature="modified", description="Fix A")])
    messages = _coding_phase_messages(
        [],
        plan,
        {
            "decision": "request_recoding",
            "client_feedback": {"kind": "apply_failed", "summary": "Patch context mismatch"},
        },
    )

    context = messages[-1]["content"]
    assert '"kind":"apply_failed"' in context
    assert "do not return patch_ops again" in context
    assert "complete update_file/create_file ops" in context


def test_reviewer_verification_requests_are_capability_bounded():
    from app.services.reviewer import _normalize_review

    review = _normalize_review(
        {
            "decision": "approve",
            "summary": "Apply under snapshot protection, then verify.",
            "verification_tool_requests": [
                {"id": "compile", "tool": "run_command", "args": {"command": ["./project-check"]}},
                {"id": "write", "tool": "apply_ops", "args": {}},
            ],
        },
        {"run_command"},
    )

    assert review["decision"] == "approve"
    assert review["verification_tool_requests"] == [
        {
            "id": "compile",
            "tool": "run_command",
            "args": {"command": ["./project-check"], "timeout_seconds": 120},
        }
    ]


def test_reviewer_prompt_requires_authoritative_project_verification(monkeypatch):
    import app.services.reviewer as reviewer_service

    captured: list[dict] = []

    class FakeReviewerClient:
        def chat_json(self, messages):
            captured.extend(messages)
            return {
                "decision": "approve",
                "summary": "Ready to apply.",
                "verification_tool_requests": [],
            }

    monkeypatch.setattr(reviewer_service, "get_llm_client", lambda agent: FakeReviewerClient())
    reviewer_service.Reviewer().review({"client_capabilities": {"tools": ["run_command"]}})

    system_prompt = captured[0]["content"]
    assert "authoritative project-native" in system_prompt
    assert "Never use source-printing or text-matching commands" in system_prompt


def test_reviewer_repairs_missing_mandatory_verification_request(monkeypatch):
    import app.services.reviewer as reviewer_service

    class FakeReviewerClient:
        def __init__(self):
            self.calls = 0

        def chat_json(self, messages):
            self.calls += 1
            if self.calls == 1:
                return {
                    "decision": "approve",
                    "summary": "The source change looks correct.",
                    "verification_tool_requests": [],
                }
            assert "requires executable verification" in messages[-1]["content"]
            return {
                "decision": "approve",
                "summary": "Apply and run the project-native verifier.",
                "verification_tool_requests": [
                    {
                        "id": "project-verification",
                        "tool": "run_command",
                        "args": {"command": ["project-check"], "cwd": ""},
                    }
                ],
            }

    client = FakeReviewerClient()
    monkeypatch.setattr(reviewer_service, "get_llm_client", lambda agent: client)

    result = reviewer_service.Reviewer().review(
        {
            "verification_required": True,
            "client_capabilities": {"tools": ["run_command", "ide_syntax_check"]},
        }
    )

    assert client.calls == 2
    assert result["decision"] == "approve"
    assert result["verification_tool_requests"][0]["id"] == "project-verification"


def test_reviewer_repairs_malformed_mandatory_verification_request(monkeypatch):
    import app.services.reviewer as reviewer_service

    class FakeReviewerClient:
        def __init__(self):
            self.calls = 0

        def chat_json(self, messages):
            self.calls += 1
            request = (
                {"id": "broken", "tool": "run_command", "args": {"cmd": "project-check"}}
                if self.calls == 1
                else {"id": "fixed", "tool": "run_command", "args": {"command": ["project-check"]}}
            )
            return {
                "decision": "approve",
                "summary": "Verify the revision.",
                "verification_tool_requests": [request],
            }

    client = FakeReviewerClient()
    monkeypatch.setattr(reviewer_service, "get_llm_client", lambda agent: client)
    result = reviewer_service.Reviewer().review(
        {"verification_required": True, "client_capabilities": {"tools": ["run_command"]}}
    )

    assert client.calls == 2
    assert [item["id"] for item in result["verification_tool_requests"]] == ["fixed"]


def test_compile_repair_task_requires_executable_verification():
    from app.services.workflow_engine import _task_requires_executable_verification

    assert _task_requires_executable_verification(
        {"title": "代码里有很多编译错误", "description": "尤其是 Tenant 相关"}
    ) is True
    assert _task_requires_executable_verification(
        {"title": "Fix all compilation errors", "description": "Inspect the whole project"}
    ) is True
    assert _task_requires_executable_verification(
        {"title": "Rename the tenant label", "description": "Text-only change"}
    ) is False


def test_workflow_filters_reviewer_tools_against_client_capabilities():
    from app.services.workflow_engine import _allowed_client_tool_requests

    requests = _allowed_client_tool_requests(
        [
            {"id": "syntax", "tool": "ide_syntax_check", "args": {"paths": ["src/a.py"]}},
            {"id": "unknown", "tool": "remote_exec", "args": {}},
        ],
        {"tools": ["ide_syntax_check"]},
    )

    assert [request.id for request in requests] == ["syntax"]


def test_workflow_normalizes_client_command_cwd_to_project_relative_path():
    from app.services.workflow_engine import _allowed_client_tool_requests

    requests = _allowed_client_tool_requests(
        [
            {"id": "root-name", "tool": "run_command", "args": {"command": ["build"], "cwd": "sample"}},
            {
                "id": "absolute-root",
                "tool": "run_command",
                "args": {"command": ["build"], "cwd": "C:/workspace/sample"},
            },
            {
                "id": "nested",
                "tool": "run_command",
                "args": {"command": ["build"], "cwd": "sample/backend"},
            },
            {
                "id": "escape",
                "tool": "run_command",
                "args": {"command": ["build"], "cwd": "../outside"},
            },
        ],
        {"tools": ["run_command"]},
        project_root="C:/workspace/sample",
    )

    assert [(request.id, request.args["cwd"]) for request in requests] == [
        ("root-name", ""),
        ("absolute-root", ""),
        ("nested", "backend"),
    ]


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
    assert fake_executor.calls == 3
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


def test_minimax_top_level_file_op_array_is_normalized():
    response = AnthropicClient._parse_json_object(
        '[{"op":"create_file","path":"src/main.py","language":"python","content":"print(1)\\n"}]'
    )

    assert response["done"] is True
    assert response["ops"] == [
        {"op": "create_file", "path": "src/main.py", "language": "python", "content": "print(1)\n"}
    ]


def test_minimax_top_level_tool_array_is_normalized():
    response = AnthropicClient._parse_json_object(
        '[{"id":"read-1","tool":"read_file","args":{"path":"src/main.py"}}]'
    )

    assert response["done"] is False
    assert response["tool_requests"][0]["tool"] == "read_file"


def test_usage_telemetry_failure_does_not_hide_llm_result(monkeypatch):
    import app.services.llm_factory as llm_factory

    class SuccessfulClient:
        last_usage = {"input_tokens": 1, "output_tokens": 1}

        def chat_json(self, messages):
            return {"reply": "ok"}

    monkeypatch.setattr(llm_factory, "record_llm_usage", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    client = llm_factory.UsageTrackedClient(
        SuccessfulClient(),
        {"agent": "planner", "protocol": "anthropic", "model": "M3"},
    )

    assert client.chat_json([]) == {"reply": "ok"}


def test_minimax_anthropic_529_is_retryable_overloaded_error():
    response = requests.Response()
    response.status_code = 529
    response.reason = "Unknown Status Code"
    response.url = "https://api.minimaxi.com/anthropic/v1/messages"
    response._content = b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
    response.headers["request-id"] = "mini-req-1"

    details = classify_provider_response(response, provider="anthropic", api_name="minimax")

    assert details.error_code == "LLM_OVERLOADED"
    assert details.retryable is True
    assert details.status_code == 529
    assert details.provider_error_type == "overloaded_error"
    assert details.request_id == "mini-req-1"


def test_minimax_business_error_code_is_classified():
    response = requests.Response()
    response.status_code = 200
    response.reason = "OK"
    response.url = "https://api.minimaxi.com/v1/text/chatcompletion_v2"
    response._content = b'{"base_resp":{"status_code":1002,"status_msg":"rate limit"}}'

    details = classify_provider_response(response, provider="openai", api_name="minimax")

    assert details.error_code == "LLM_RATE_LIMITED"
    assert details.provider_code == 1002
    assert details.retryable is True


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
