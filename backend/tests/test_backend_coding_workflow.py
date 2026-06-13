from __future__ import annotations

from pathlib import Path

import httpx
import pytest


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
                "summary": "Update the smoke target file.",
                "files": [
                    {
                        "path": "src/main/kotlin/App.kt",
                        "nature": "modified",
                        "description": "Return the smoke implementation.",
                        "confidence": 0.97,
                    }
                ],
                "warnings": [],
            }
        }


class FakeExecutorClient:
    def chat_structured(self, messages: list[dict]) -> dict:
        return {
            "reply": "Generated smoke implementation.",
            "ops": [
                {
                    "op": "write_file",
                    "path": "src/main/kotlin/App.kt",
                    "language": "kotlin",
                    "content": "fun main() = println(\"DevWerk smoke\")\n",
                }
            ],
            "patch_ops": [],
            "tool_requests": [],
            "done": True,
        }


@pytest.mark.asyncio
async def test_backend_coding_workflow_plan_then_execute_smoke(monkeypatch, tmp_path):
    db_path = tmp_path / "devwerk-smoke.db"
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
                "project_root": str(tmp_path),
                "messages": [
                    {"role": "user", "content": "Update App.kt to print the DevWerk smoke message."}
                ],
                "workspace": {
                    "root_id": project_id,
                    "changed_files": [],
                    "open_files": ["src/main/kotlin/App.kt"],
                    "tree_preview": "src/main/kotlin/App.kt",
                    "source_map": {
                        "root": str(tmp_path),
                        "generated_at": 1,
                        "total_files": 1,
                        "indexed_files": 1,
                        "skipped_files": 0,
                        "files": [
                            {
                                "path": "src/main/kotlin/App.kt",
                                "kind": "source",
                                "language": "kotlin",
                                "package": None,
                                "imports": [],
                                "symbols": [],
                                "size": 42,
                            }
                        ],
                    },
                },
                "tool_results": [],
            }

            plan_response = await client.post("/v1/plan", json=plan_body, headers={"X-DevWerk-Project-Id": project_id})
            assert plan_response.status_code == 200
            plan = plan_response.json()
            assert plan["ok"] is True
            assert plan["task_id"]
            assert plan["status_key"] == "planned"
            assert plan["files"][0]["path"] == "src/main/kotlin/App.kt"

            execute_body = {
                "project_id": project_id,
                "task_id": plan["task_id"],
                "mode": "agent",
                "project_root": str(tmp_path),
                "messages": plan_body["messages"],
                "approved_paths": ["src/main/kotlin/App.kt"],
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
            assert executed["done"] is True
            assert executed["ops"] == [
                {
                    "op": "write_file",
                    "path": "src/main/kotlin/App.kt",
                    "language": "kotlin",
                    "content": "fun main() = println(\"DevWerk smoke\")\n",
                }
            ]

            task_response = await client.get(f"/v1/kanban/tasks/{plan['task_id']}")
            assert task_response.status_code == 200
            task = task_response.json()["task"]
            assert task["status_key"] == "ready_to_apply"
            artifact_types = {artifact["artifact_type"] for artifact in task["artifacts"]}
            assert {"plan_request", "plan_response", "execute_response"}.issubset(artifact_types)
