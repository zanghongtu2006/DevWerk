from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.v1.domain import AgentModelResponse
from tests.helpers import publish_initial_workflow, sequence_workflow, task_plan, workflow_plan


def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


def test_web_routes_and_modular_assets_are_served():
    with client() as web:
        assert web.get("/v1/health").json()["status"] == "ok"
        statuses = web.get("/v1/runtime-statuses").json()
        assert "recovering" in statuses["task"]["values"]
        assert set(statuses) == {
            "task", "column_run", "attempt", "agent_run", "tool_invocation"
        }
        settings = web.get("/v1/settings")
        assert settings.status_code == 200
        assert settings.json()["values"]["workflow"]["auto_resume_previous_tasks"] is False
        assert settings.json()["fields"][0]["restart_required"] is True
        for route in ("/", "/workbench", "/dashboard", "/kanban", "/tasks", "/events", "/settings"):
            response = web.get(route)
            assert response.status_code == 200
            assert 'type="module"' in response.text
            assert "no-store" in response.headers["cache-control"]
        for asset in (
            "/web/static/dashboard.js",
            "/web/static/core/api.js",
            "/web/static/pages/overview.js",
            "/web/static/pages/projects.js",
            "/web/static/pages/kanban.js",
            "/web/static/pages/tasks.js",
            "/web/static/pages/events.js",
            "/web/static/pages/settings.js",
        ):
            response = web.get(asset)
            assert response.status_code == 200
            assert "no-store" in response.headers["cache-control"]
        dashboard = web.get("/web/static/dashboard.js").text
        assert "mergeConversationMessages" in dashboard
        assert "after_id=" in dashboard
        assert "before_id=" in dashboard
        assert "refreshConversationStatus" in dashboard
        assert "queueProjectEvent" in dashboard
        assert "flushProjectEvents" in dashboard
        assert 'event.type !== "conversation.progress"' in dashboard
        assert 'state.route === "projects"' in dashboard
        assert "currentPageView" in dashboard
        assert "scroll.scrollLeft = view.left" in dashboard
        assert "scroll.scrollTop = view.top" in dashboard
        assert "saveGlobalSettings" in dashboard
        assert "waitForRuntimeRestart" in dashboard
        assert "tasks/${event.task_id}" not in dashboard
        assert "conversationProgressFromEvents" not in dashboard
        assert "appendConversationProgress" not in dashboard
        assert 'projects.js?v=20260804-debug1' in dashboard
        projects = web.get("/web/static/pages/projects.js").text
        assert 'components.js?v=20260804-debug1' in projects
        assert "visibleConversationMessages" in projects
        assert 'message.meta?.status === "failed"' not in projects
        assert "conversationStatusStrip" in projects
        assert "data-message-id" in projects
        assert "加载更早消息" in projects
        assert "工具结果" not in projects
        assert "模型输出" not in projects
        assert "Conversation 已失败" not in projects
        assert "conversation-trace" not in projects
        core_api = web.get("/web/static/core/api.js").text
        assert "events?limit=500" in core_api
        assert "conversation-state" in core_api
        assert "new EventSource" in core_api
        components = web.get("/web/static/ui/components.js").text
        assert "data.content" in components
        kanban = web.get("/web/static/pages/kanban.js").text
        assert "Column 目的" in kanban
        assert "Task 输入" in kanban
        assert "上游结果" in kanban
        assert "派生临时 Agent" in kanban
        assert "查看原始 Runtime JSON" in kanban
        assert "column.instruction" in kanban
        pages_css = web.get("/web/static/styles/pages.css").text
        assert ".conversation-head { grid-row: 1;" in pages_css
        assert ".conversation-status-strip { grid-row: 2;" in pages_css
        assert ".conversation-messages { grid-row: 3;" in pages_css
        assert ".conversation-composer { grid-row: 4; min-height: max-content;" in pages_css
        assert ".kanban-page" in pages_css
        assert "grid-template-rows: auto minmax(0,1fr)" in pages_css
        assert ".kanban-scroll" in pages_css
        assert "overflow: auto" in pages_css
        tasks = web.get("/web/static/pages/tasks.js").text
        assert "FAILURE REASON" in tasks
        assert "失败阶段" in tasks
        assert "查看原始 Runtime 错误" in tasks
        assert "task.error" in tasks
        settings_page = web.get("/web/static/pages/settings.js").text
        assert "global-settings-form" in settings_page
        assert "data-setting-key" in settings_page
        assert "保存后自动重启" in settings_page


def test_global_settings_api_saves_and_requests_managed_restart(tmp_path):
    class RestartProbe:
        def __init__(self):
            self.calls = 0

        def schedule(self):
            self.calls += 1
            return True

    with client() as web:
        path = tmp_path / "saved-global-settings.yaml"
        restart = RestartProbe()
        web.app.state.v1_global_settings_path = path
        web.app.state.v1_restart = restart

        response = web.post(
            "/v1/settings",
            json={
                "schema_version": "devwerk.global-settings.v1",
                "workflow": {"auto_resume_previous_tasks": True},
            },
        )

        assert response.status_code == 200
        assert response.json()["saved"] is True
        assert response.json()["restart_required"] is True
        assert response.json()["restart_scheduled"] is True
        assert response.json()["restart_changes"] == [
            "workflow.auto_resume_previous_tasks"
        ]
        assert restart.calls == 1
        assert "auto_resume_previous_tasks: true" in path.read_text(encoding="utf-8")
        assert web.app.state.v1_conversation.global_settings["workflow"][
            "auto_resume_previous_tasks"
        ] is True

        unchanged = web.post(
            "/v1/settings",
            json={
                "schema_version": "devwerk.global-settings.v1",
                "workflow": {"auto_resume_previous_tasks": True},
            },
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["restart_required"] is False
        assert unchanged.json()["restart_scheduled"] is False
        assert restart.calls == 1


def test_loop_api_is_the_only_initial_workflow_creation_path(tmp_path):
    with client() as web:
        loops = web.get("/v1/loops")
        assert loops.status_code == 200
        assert {item["loop_key"] for item in loops.json()} >= {
            "novel.production",
            "software.gitlab_devops",
        }
        assert web.get("/v1/workflow-templates").status_code == 404

        project = web.post(
            "/v1/projects",
            json={"name": "loop-api", "description": "", "base_dir": str(tmp_path / "loop-api")},
        ).json()
        applied = web.post(
            f"/v1/projects/{project['id']}/automation/loop",
            json={
                "loop_key": "software.gitlab_devops",
                "bindings": {
                    "product_name": "API product",
                    "gitlab_repository": "group/project",
                },
            },
        )
        assert applied.status_code == 201
        assert applied.json()["loop"]["loop_key"] == "software.gitlab_devops"

        second = web.post(
            f"/v1/projects/{project['id']}/automation/loop",
            json={"loop_key": "software.gitlab_devops", "bindings": {}},
        )
        assert second.status_code == 422
        assert "only before the Project has a Workflow" in second.json()["detail"]

        fresh = web.post(
            "/v1/projects",
            json={"name": "no-loop", "description": "", "base_dir": str(tmp_path / "no-loop")},
        ).json()
        definition = sequence_workflow()
        plan = web.post(
            f"/v1/projects/{fresh['id']}/automation/workflow-plans",
            json={"plan": workflow_plan(definition).model_dump(mode="json")},
        ).json()
        rejected = web.post(
            f"/v1/projects/{fresh['id']}/automation/workflow-revisions",
            json={"workflow_plan_id": plan["id"], "workflow": definition.model_dump(mode="json")},
        )
        assert rejected.status_code == 422
        assert "initial Workflow creation requires loop.apply" in rejected.json()["detail"]


def test_declarative_api_workflow_reaches_done_without_llm(tmp_path, monkeypatch):
    model_turn = 0

    def conversation_model(_messages, _tools, **_kwargs):
        nonlocal model_turn
        model_turn += 1
        if model_turn == 1:
            return AgentModelResponse(tool_calls=[
                {"id": "inspect", "name": "project.inspect", "arguments": {}}
            ])
        return AgentModelResponse(text="The deterministic Task reached its terminal state.")

    monkeypatch.setattr("app.v1.agent.provider_complete", conversation_model)
    with client() as web:
        project = web.post(
            "/v1/projects",
            json={"name": "api", "description": "", "base_dir": str(tmp_path / "project")},
        ).json()
        catalog_response = web.get(f"/v1/projects/{project['id']}/capabilities")
        assert catalog_response.status_code == 200
        catalog = {item["id"]: item for item in catalog_response.json()}
        assert "project.files.write" in catalog
        assert "task.create" not in catalog
        definition = sequence_workflow(content="api done")
        plan = web.post(f"/v1/projects/{project['id']}/automation/workflow-plans", json={"plan": workflow_plan(definition).model_dump(mode="json")}).json()
        workflow = definition.model_dump(mode="json")
        publish_initial_workflow(web.app.state.v1_store, project["id"], definition, plan["id"])
        published = web.post(f"/v1/projects/{project['id']}/automation/workflow-revisions", json={"workflow_plan_id": plan["id"], "workflow": workflow})
        assert published.status_code == 201
        tasks = web.post(
            f"/v1/projects/{project['id']}/automation/task-plans",
            json={"plan": task_plan(published.json()["id"], definition, title="deterministic").model_dump(mode="json")},
        ).json()
        task = web.post(
            f"/v1/projects/{project['id']}/automation/tasks",
            json={"task_plan_id": tasks["id"], "proposed_task_ref": "primary"},
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
        definition = sequence_workflow()
        plan = web.post(f"/v1/projects/{project['id']}/automation/workflow-plans", json={"plan": workflow_plan(definition).model_dump(mode="json")}).json()
        publish_initial_workflow(web.app.state.v1_store, project["id"], definition, plan["id"])
        workflow = definition.model_dump(mode="json")
        workflow["columns"][0]["executor"]["steps"][0]["capability"] = "not.registered"
        response = web.post(f"/v1/projects/{project['id']}/automation/workflow-revisions", json={"workflow_plan_id": plan["id"], "workflow": workflow})
        assert response.status_code == 422
        assert "unknown or non-delegable capabilities" in response.json()["detail"]


def test_conversation_and_agent_audit_endpoints(tmp_path, monkeypatch):
    traces = []
    monkeypatch.setattr("app.v1.api.trace_json", lambda _logger, event, **payload: traces.append((event, payload)))
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
        events = web.get(f"/v1/projects/{project['id']}/events?limit=150").json()
        progress_kinds = [item["data"].get("kind") for item in events if item["type"] == "conversation.progress"]
        assert progress_kinds == ["provider_wait", "model_output"]
        messages = web.get(f"/v1/projects/{project['id']}/conversation?limit=10").json()
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert [item["meta"]["kind"] for item in messages] == ["message", "reply"]
        after = web.get(
            f"/v1/projects/{project['id']}/conversation?limit=10&after_id={messages[0]['id']}"
        ).json()
        before = web.get(
            f"/v1/projects/{project['id']}/conversation?limit=10&before_id={messages[1]['id']}"
        ).json()
        assert [item["id"] for item in after] == [messages[1]["id"]]
        assert [item["id"] for item in before] == [messages[0]["id"]]
        invalid = web.get(
            f"/v1/projects/{project['id']}/conversation?after_id={messages[0]['id']}&before_id={messages[1]['id']}"
        )
        assert invalid.status_code == 422
        status = web.get(f"/v1/projects/{project['id']}/conversation-state").json()
        assert status["job"]["id"] == accepted["job"]["id"]
        assert status["job"]["status"] == "succeeded"
        assert status["job"]["has_error"] is False
        assert ("web.conversation_input", {"project_id": project["id"], "message": "Inspect only.", "start_task": False}) in traces
        output_trace = next(payload for event, payload in traces if event == "web.conversation_output")
        assert output_trace["output"]["job"]["id"] == accepted["job"]["id"]
