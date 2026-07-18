from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.v1.domain import AgentModelResponse
from tests.helpers import sequence_workflow, readiness


def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


def test_web_routes_and_modular_assets_are_served():
    with client() as web:
        assert web.get("/v1/health").json()["status"] == "ok"
        for route in ("/", "/workbench", "/dashboard", "/kanban", "/tasks", "/events"):
            response = web.get(route)
            assert response.status_code == 200
            assert 'type="module"' in response.text
        for asset in (
            "/web/static/dashboard.js",
            "/web/static/core/api.js",
            "/web/static/pages/overview.js",
            "/web/static/pages/projects.js",
            "/web/static/pages/kanban.js",
            "/web/static/pages/tasks.js",
            "/web/static/pages/events.js",
        ):
            assert web.get(asset).status_code == 200


def test_declarative_api_workflow_reaches_done_without_llm(tmp_path):
    with client() as web:
        project = web.post(
            "/v1/projects",
            json={"name": "api", "description": "", "base_dir": str(tmp_path / "project")},
        ).json()
        workflow = sequence_workflow(content="api done").model_dump(mode="json")
        published = web.post(f"/v1/projects/{project['id']}/automation/workflow", json={"workflow": workflow})
        assert published.status_code == 201
        task = web.post(
            f"/v1/projects/{project['id']}/automation/tasks",
            json={"title": "deterministic", "brief": "", "input": {}, "readiness": readiness()},
        ).json()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = web.get(f"/v1/projects/{project['id']}/tasks/{task['id']}").json()
            if task["status"] in {"done", "failed"}:
                break
            time.sleep(0.03)

        assert task["status"] == "done"
        assert (tmp_path / "project" / "result.txt").read_text(encoding="utf-8") == "api done"
        assert [item["column_key"] for item in task["runs"]] == ["execute"]
        assert web.get(f"/v1/projects/{project['id']}/board").json()["tasks"][0]["id"] == task["id"]


def test_api_rejects_unknown_declared_capability(tmp_path):
    with client() as web:
        project = web.post(
            "/v1/projects",
            json={"name": "invalid", "description": "", "base_dir": str(tmp_path / "project")},
        ).json()
        workflow = sequence_workflow().model_dump(mode="json")
        workflow["columns"][0]["executor"]["steps"][0]["capability"] = "not.registered"
        response = web.post(f"/v1/projects/{project['id']}/automation/workflow", json={"workflow": workflow})
        assert response.status_code == 422
        assert "unknown or non-delegable capabilities" in response.json()["detail"]


def test_conversation_and_agent_audit_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr("app.v1.agent.provider_complete", lambda *_args, **_kwargs: AgentModelResponse(text="I inspected the Project."))
    with client() as web:
        project = web.post(
            "/v1/projects",
            json={"name": "conversation", "description": "", "base_dir": str(tmp_path / "project")},
        ).json()
        accepted = web.post(
            f"/v1/projects/{project['id']}/conversation",
            json={"message": "Inspect only.", "start_task": False},
        ).json()
        deadline = time.monotonic() + 3
        job = {}
        while time.monotonic() < deadline:
            job = web.get(f"/v1/projects/{project['id']}/conversation-jobs/{accepted['job']['id']}").json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.02)
        assert job["status"] == "succeeded"
        assert job["result"]["reply"] == "I inspected the Project."
        assert job["result"]["task_ids"] == []
        runs = web.get(f"/v1/projects/{project['id']}/agent-runs").json()
        assert len(runs) == 1
        detail = web.get(f"/v1/projects/{project['id']}/agent-runs/{runs[0]['id']}").json()
        assert detail["kind"] == "conversation"
        assert detail["tool_invocations"] == []
