from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.services.anthropic_client import AnthropicClient


def reset_service_dbs(kanban_service, usage_service) -> None:
    kanban_service._initialized = False
    usage_service._initialized = False


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
        return {"raw_text": "Create a minimal Spring Boot Java 21 REST API."}


class FakeToolRequestPlannerClient:
    def chat_json(self, messages: list[dict]) -> dict:
        return {
            "tool_requests": [
                {"id": "p1", "tool": "read_file", "args": {"path": "pom.xml", "start_line": 1, "end_line": 200}}
            ]
        }


class FakeExecutorClient:
    def chat_structured(self, messages: list[dict]) -> dict:
        return {
            "reply": "Generated Spring Boot smoke scaffold.",
            "ops": [
                {
                    "op": "create_file",
                    "path": "settings.gradle",
                    "language": "groovy",
                    "content": 'pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }\nrootProject.name = "devwerk-smoke"\n',
                },
                {
                    "op": "create_file",
                    "path": "build.gradle",
                    "language": "groovy",
                    "content": "plugins {\n    id 'java'\n    id 'org.springframework.boot' version '3.3.5'\n    id 'io.spring.dependency-management' version '1.1.6'\n}\n\njava { toolchain { languageVersion = JavaLanguageVersion.of(21) } }\n\nrepositories { mavenCentral() }\n\ndependencies { implementation 'org.springframework.boot:spring-boot-starter-web' }\n",
                },
                {
                    "op": "create_file",
                    "path": "src/main/java/com/devwerk/demo/DemoApplication.java",
                    "language": "java",
                    "content": "package com.devwerk.demo;\n\nimport org.springframework.boot.SpringApplication;\nimport org.springframework.boot.autoconfigure.SpringBootApplication;\n\n@SpringBootApplication\npublic class DemoApplication {\n    public static void main(String[] args) {\n        SpringApplication.run(DemoApplication.class, args);\n    }\n}\n",
                },
                {
                    "op": "create_file",
                    "path": "src/main/java/com/devwerk/demo/HelloController.java",
                    "language": "java",
                    "content": "package com.devwerk.demo;\n\nimport org.springframework.web.bind.annotation.GetMapping;\nimport org.springframework.web.bind.annotation.RestController;\n\n@RestController\npublic class HelloController {\n    @GetMapping(\"/hello\")\n    public String hello() {\n        return \"Hello, DevWerk\";\n    }\n}\n",
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
                    {"id": "r1", "tool": "list_dir", "args": {"path": "test", "max_depth": 4}},
                    {"id": "r2", "tool": "read_file", "args": {"path": "test/pom.xml", "start_line": 1, "end_line": 200}},
                    {
                        "id": "r3",
                        "tool": "read_file",
                        "args": {"path": "test/src/main/java/org/example/Main.java", "start_line": 1, "end_line": 200},
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
                    "path": "test/src/main/java/org/example/HelloController.java",
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

    monkeypatch.setattr(main_module, "settings", lambda: fake_settings)
    monkeypatch.setattr(ide_routes, "settings", lambda: fake_settings)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake_settings)
    monkeypatch.setattr(usage_service, "settings", lambda: fake_settings)
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
                        "content": "Create a Spring Boot scaffold compatible with JDK 21 and a minimal REST hello world API.",
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
            assert "build.gradle" in planned_paths
            assert "src/main/java/com/devwerk/demo/HelloController.java" in planned_paths

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
            assert len(executed["ops"]) == 4
            _apply_file_ops(project_root, executed["ops"])
            assert (project_root / "build.gradle").is_file()
            controller = project_root / "src/main/java/com/devwerk/demo/HelloController.java"
            assert controller.is_file()
            assert "@GetMapping(\"/hello\")" in controller.read_text(encoding="utf-8")

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
async def test_plan_falls_back_to_user_management_files_when_planner_returns_tool_requests(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-user-management-plan.db"
    project_root = tmp_path / "test"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.planner as planner_service
    import app.services.usage as usage_service

    monkeypatch.setattr(main_module, "settings", lambda: fake_settings)
    monkeypatch.setattr(ide_routes, "settings", lambda: fake_settings)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake_settings)
    monkeypatch.setattr(usage_service, "settings", lambda: fake_settings)
    reset_service_dbs(kanban_service, usage_service)
    monkeypatch.setattr(planner_service, "get_llm_client", lambda agent="planner": FakeToolRequestPlannerClient())

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
    assert plan["ok"] is True
    assert plan["status_key"] == "planned"
    assert plan["next_action"] == "execute"
    assert plan["phase_output"]["phase"] == "plan"
    planned_paths = {item["path"] for item in plan["files"]}
    assert "src/main/java/org/example/user/UserController.java" in planned_paths
    assert "src/main/java/org/example/user/UserService.java" in planned_paths
    assert "src/main/java/org/example/user/UserPermissionPolicy.java" in planned_paths
    assert "pom.xml" in planned_paths
    assert plan["warnings"]


@pytest.mark.asyncio
async def test_execute_resolves_tool_requests_and_strips_project_root_prefix(monkeypatch, tmp_path):
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

    monkeypatch.setattr(main_module, "settings", lambda: fake_settings)
    monkeypatch.setattr(ide_routes, "settings", lambda: fake_settings)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake_settings)
    monkeypatch.setattr(usage_service, "settings", lambda: fake_settings)
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
                        "test/pom.xml",
                        "test/src/main/java/org/example/Main.java",
                        "test/src/main/java/org/example/HelloController.java",
                    ],
                    "approved_ops": [],
                    "workspace": {
                        "root_id": "backend-tool-loop-smoke",
                        "changed_files": [],
                        "open_files": [],
                        "tree_preview": "test/\n  pom.xml\n  src/\n    main/\n      java/\n        org/\n          example/\n            Main.java",
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
async def test_execute_returns_client_tool_requests_and_apply_result_completes_task(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-client-tool.db"
    project_root = tmp_path / "tool-project"
    project_root.mkdir()
    fake_settings = FakeSettings(db_path)

    import app.main as main_module
    import app.routes.ide as ide_routes
    import app.services.kanban as kanban_service
    import app.services.usage as usage_service

    monkeypatch.setattr(main_module, "settings", lambda: fake_settings)
    monkeypatch.setattr(ide_routes, "settings", lambda: fake_settings)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake_settings)
    monkeypatch.setattr(usage_service, "settings", lambda: fake_settings)
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


def test_anthropic_non_json_text_falls_back_to_spring_boot_ops(monkeypatch):
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

    assert response["done"] is True
    assert {op["path"] for op in response["ops"]} >= {
        "build.gradle",
        "src/main/java/com/devwerk/demo/HelloController.java",
    }
