from __future__ import annotations


class FakeSettings:
    def __init__(self, db_path, session_dir):
        self.devwerk_db_path = str(db_path)
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(session_dir)


def _configure(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path / "devwerk.db", tmp_path / "sessions")
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False
    return kanban_service


def _writing_workflow() -> dict:
    return {
        "name": "writing-workflow",
        "version": 1,
        "columns": [
            {"status_key": "draft", "title": "Draft", "position": 10, "transition_to": ["topic_defined", "failed"]},
            {
                "status_key": "topic_defined",
                "title": "Topic Defined",
                "position": 20,
                "transition_to": ["researched", "failed"],
                "job_template": "define_writing_topic",
                "input_artifacts": ["workflow_request"],
                "output_artifact": "topic_bundle",
                "success_action": "topic_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "researched",
                "title": "Researched",
                "position": 30,
                "transition_to": ["written", "failed"],
                "job_template": "research_writing_material",
                "input_artifacts": ["topic_bundle"],
                "output_artifact": "research_bundle",
                "success_action": "research_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "written",
                "title": "Written",
                "position": 40,
                "transition_to": ["reviewed", "topic_defined", "failed"],
                "job_template": "write_draft",
                "input_artifacts": ["topic_bundle", "research_bundle"],
                "output_artifact": "draft_bundle",
                "success_action": "draft_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "reviewed",
                "title": "Reviewed",
                "position": 50,
                "transition_to": ["revised", "written", "failed"],
                "job_template": "review_draft",
                "input_artifacts": ["draft_bundle"],
                "output_artifact": "review_bundle",
                "success_action": "review_ready",
                "failure_actions": ["request_rewrite", "fail"],
            },
            {
                "status_key": "revised",
                "title": "Revised",
                "position": 60,
                "transition_to": ["done", "reviewed", "failed"],
                "job_template": "revise_draft",
                "input_artifacts": ["draft_bundle", "review_bundle"],
                "output_artifact": "final_draft_bundle",
                "success_action": "workflow_done",
                "failure_actions": ["request_rewrite", "fail"],
            },
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["draft"]},
        ],
        "actions": {
            "topic_ready": {"to": "topic_defined"},
            "research_ready": {"to": "researched"},
            "draft_ready": {"to": "written"},
            "review_ready": {"to": "reviewed"},
            "request_rewrite": {"to": "written"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "draft"},
        },
    }


def test_workflow_designer_requires_project_llm(monkeypatch):
    from app.services import workflow_designer

    def fail_llm(*args, **kwargs):
        raise RuntimeError("no llm configured")

    monkeypatch.setattr(workflow_designer, "_ask_llm", fail_llm)

    try:
        workflow_designer.design_project_workflow(
            project_id="designer-requires-llm",
            messages=[{"role": "user", "content": "Create a flow with planning, coding, review, compile and retry."}],
            current_workflow=None,
            current_agents=None,
        )
    except ValueError as exc:
        assert "project LLM agent failed" in str(exc)
    else:
        raise AssertionError("workflow designer must fail when project LLM is unavailable")


def test_workflow_designer_can_create_non_coding_writing_workflow(monkeypatch):
    from app.services import workflow_designer
    from app.services.workflow_definition import validate_managed_workflow_definition, workflow_from_dict

    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": "Writing workflow ready.",
            "workflow": _writing_workflow(),
            "agents": {"default-agent": {"model_route": "default"}},
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="writing-flow",
        messages=[{"role": "user", "content": "Create a writing project flow with topic, research, write, review and revise."}],
        current_workflow=None,
        current_agents=None,
    )

    assert result["ok"] is True
    assert result["source"] == "llm"
    status_keys = [column["status_key"] for column in result["workflow"]["columns"]]
    assert ["topic_defined", "researched", "written", "reviewed", "revised"] == status_keys[1:6]
    assert result["agents"]["default-agent"]["model_route"] == "default"
    validate_managed_workflow_definition(workflow_from_dict(result["workflow"]))


def test_workflow_designer_does_not_infer_terminal_actions(monkeypatch):
    from app.services import workflow_designer

    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": "Draft only.",
            "workflow": {
                "name": "ambiguous-flow",
                "columns": [
                    {"status_key": "working", "title": "Working", "position": 10, "transition_to": ["done"]},
                    {"status_key": "done", "title": "Done", "position": 20, "transition_to": []},
                ],
                "actions": {},
            },
            "agents": {},
        },
    )

    try:
        workflow_designer.design_project_workflow(
            project_id="ambiguous-flow",
            messages=[{"role": "user", "content": "Create a tiny workflow."}],
            current_workflow=None,
            current_agents=None,
        )
    except ValueError as exc:
        assert "explicit success action" in str(exc) or "managed workflow action 'fail'" in str(exc)
    else:
        raise AssertionError("designer must not infer terminal actions from no-transition columns")


def test_workflow_designer_uses_project_agent_for_default_llm(monkeypatch):
    from app.services import workflow_designer

    captured = {}

    class FakeClient:
        def chat_json(self, messages):
            captured["messages"] = messages
            return {"reply": "ok", "workflow": {}, "agents": {}}

    def fake_get_llm_client(agent):
        captured["agent"] = agent
        return FakeClient()

    monkeypatch.setattr(workflow_designer, "get_llm_client", fake_get_llm_client)

    response = workflow_designer._ask_llm(
        project_id="project-default-agent",
        messages=[{"role": "user", "content": "Create a writing workflow."}],
        base_workflow={},
        agents={},
    )

    assert captured["agent"] == "project"
    assert response["reply"] == "ok"


def test_workflow_designer_endpoint_can_save_project_override(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": "ok",
            "workflow": _writing_workflow(),
            "agents": {"publication-agent": {"model_route": "default"}},
        },
    )
    kanban_service.upsert_project(project_id="designer-save", name="Designer Save")

    response = kanban_routes.kanban_design_project_workflow(
        "designer-save",
        kanban_routes.WorkflowDesignRequest(
            messages=[{"role": "user", "content": "Use the default flow."}],
            save=True,
        ),
    )

    assert response["saved"] is True
    assert kanban_service.get_project_workflow("designer-save")["workflow"]["name"] == "writing-workflow"
    assert (
        kanban_service.get_project_settings("designer-save")["settings"]["agents"]["publication-agent"]["model_route"]
        == "default"
    )


def test_backend_web_ui_is_split_into_template_css_and_script():
    from app.routes.web_ui import render_web_ui

    html = render_web_ui("projects")

    assert 'class="app-shell"' in html
    assert 'class="global-nav"' in html
    assert 'class="project-rail"' in html
    assert '<style' not in html
    assert '<script src="/web/static/dashboard.js" defer></script>' in html
    assert '<link rel="stylesheet" href="/web/static/dashboard.css" />' in html
    assert "renderProjectsPage" not in html


def test_backend_web_ui_static_files_own_layout_and_interactions():
    from pathlib import Path

    web_root = Path(__file__).resolve().parents[1] / "app" / "web" / "static"
    css = (web_root / "dashboard.css").read_text(encoding="utf-8")
    js = (web_root / "dashboard.js").read_text(encoding="utf-8")

    assert ".project-rail" in css
    assert "resize: horizontal" in css
    assert "overflow-y: auto" in css
    assert ".overview-grid" in css
    assert ".chat-card" in css
    assert ".chat-body" in css

    for renderer in (
        "renderOverviewPage",
        "renderProjectsPage",
        "renderKanbanPage",
        "renderTaskPage",
        "renderEventsSection",
        "renderMemorySection",
        "renderAnalyticsSection",
        "renderSettingsSection",
    ):
        assert renderer in js
    assert "data-nav" in js
    assert "data-project-tab" in js
    assert "data-chat-tab" in js
    assert "data-task-tab" in js
    assert "saveProjectDesign" not in js
    assert "startTask" not in js


def test_backend_web_ui_navigation_has_distinct_routes_and_sections():
    from pathlib import Path

    template = (Path(__file__).resolve().parents[1] / "app" / "web" / "templates" / "dashboard.html").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")

    expected_renderers = (
        "renderOverviewPage",
        "renderProjectsPage",
        "renderKanbanPage",
        "renderTaskPage",
        "renderEventsSection",
        "renderMemorySection",
        "renderAnalyticsSection",
        "renderSettingsSection",
    )
    for renderer in expected_renderers:
        assert renderer in js

    for nav, href in {
        "overview": "/workbench",
        "projects": "/dashboard",
        "kanban": "/kanban",
        "tasks": "/tasks",
        "events": "/dashboard#events",
        "memory": "/dashboard#memory",
        "analytics": "/dashboard#analytics",
        "settings": "/dashboard#settings",
    }.items():
        assert f'data-nav="{nav}" href="{href}"' in template

    assert "window.addEventListener(\"hashchange\"" in js
    assert 'function activeSection()' in js
    assert "events: renderEventsSection" in js
    assert "memory: renderMemorySection" in js
    assert "analytics: renderAnalyticsSection" in js
    assert "settings: renderSettingsSection" in js
    assert "renderProjectsPage();" not in js.split("function renderAnalyticsSection", 1)[-1].split("function ", 1)[0]


def test_backend_web_ui_uses_backend_data_not_demo_metrics():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "dashboard.js").read_text(encoding="utf-8")

    forbidden = (
        "DEMO_TASKS",
        "128,540",
        "1,000,000",
        "4h 32m",
        "up 18%",
        "up 12%",
        "Math.random",
        "T-1042",
        "Implement NextAuth setup",
        "Standard Dev Flow",
        "Bug Triage Flow",
        "anthropic/claude-3-5-sonnet",
    )
    for text in forbidden:
        assert text not in js

    assert "`${API}/usage/summary`" in js
    assert "`${API}/kanban/projects`" in js
    assert "`${API}/kanban/tasks`" in js
    assert "No usage rows returned by backend" in js


def test_project_stats_include_status_breakdown(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)

    kanban_service.update_project_workflow("stats-project", _writing_workflow())
    kanban_service.create_task(project_id="stats-project", title="Active", status_key="written")
    kanban_service.create_task(project_id="stats-project", title="Done", status_key="done")
    kanban_service.create_task(project_id="stats-project", title="Failed", status_key="failed")

    project = next(item for item in kanban_service.list_projects() if item["id"] == "stats-project")
    stats = project["stats"]

    assert stats["tasks"] == 3
    assert stats["active_tasks"] == 1
    assert stats["done_tasks"] == 1
    assert stats["failed_tasks"] == 1


def test_project_conversation_can_save_workflow_design(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    def fake_llm(**kwargs):
        return {
            "reply": "Writing workflow ready.",
            "workflow": _writing_workflow(),
            "agents": {"default-agent": {"model_route": "default"}},
        }

    monkeypatch.setattr(workflow_designer, "_ask_llm", fake_llm)
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            action="save_design",
            message="Create a writing workflow with topic, research, draft, review, revise, done.",
            save=True,
        ),
    )

    assert response["ok"] is True
    assert response["kind"] == "workflow_design"
    assert response["saved"] is True
    assert kanban_service.get_project_settings("writing-project")["settings"]["agents"]["default-agent"]["model_route"] == "default"
    conversation = kanban_routes.kanban_project_conversation("writing-project")["messages"]
    assert [message["role"] for message in conversation] == ["user", "assistant"]
    assert conversation[0]["kind"] == "save_design"


def test_project_conversation_default_message_uses_project_agent(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes

    captured = {}

    class FakeProjectAgent:
        def chat_json(self, messages):
            captured["messages"] = messages
            return {"action": "reply", "reply": "Tell me the writing audience and tone."}

    def fake_get_llm_client(agent):
        captured["agent"] = agent
        return FakeProjectAgent()

    monkeypatch.setattr(kanban_routes, "get_llm_client", fake_get_llm_client)
    kanban_service.upsert_project(project_id="conversation-project", name="Conversation Project")

    response = kanban_routes.kanban_project_conversation_message(
        "conversation-project",
        kanban_routes.ProjectConversationRequest(
            message="I want to create a writing project.",
        ),
    )

    assert captured["agent"] == "project"
    prompt_payload = captured["messages"][-1]["content"]
    assert "active_task" in prompt_payload
    assert "current_workflow" in prompt_payload
    assert response["kind"] == "reply"
    assert response["reply"] == "Tell me the writing audience and tone."
    conversation = kanban_routes.kanban_project_conversation("conversation-project")["messages"]
    assert [message["role"] for message in conversation] == ["user", "assistant"]


def test_project_conversation_agent_can_save_non_coding_workflow(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.services.workflow_designer as workflow_designer

    class FakeProjectAgent:
        def chat_json(self, messages):
            return {"action": "save_design", "reply": "Writing workflow should be saved.", "save": True}

    monkeypatch.setattr(kanban_routes, "get_llm_client", lambda agent: FakeProjectAgent())
    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": "Writing workflow saved.",
            "workflow": _writing_workflow(),
            "agents": {"default-agent": {"model_route": "default"}},
        },
    )
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            message="Create a writing project with topic, research, draft, review, revision, and done.",
            current_workflow={},
            current_agents={},
        ),
    )

    assert response["kind"] == "workflow_design"
    assert response["saved"] is True
    workflow = kanban_service.get_project_workflow("writing-project")["workflow"]
    assert [column["status_key"] for column in workflow["columns"]][1:6] == [
        "topic_defined",
        "researched",
        "written",
        "reviewed",
        "revised",
    ]
    board_columns = [column["status_key"] for column in kanban_service.list_columns("writing-project")]
    assert "topic_defined" in board_columns
    assert "written" in board_columns


def test_project_conversation_start_task_uses_workflow_entrypoint(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.routes.workflows as workflow_routes

    captured = {}

    def fake_start_workflow_payload(body):
        captured.update(body)
        return {
            "ok": True,
            "task_id": "task-123",
            "poll_url": "/v1/workflows/task-123",
            "events_url": "/v1/workflows/task-123/events",
        }

    monkeypatch.setattr(workflow_routes, "start_workflow_payload", fake_start_workflow_payload)
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            action="start_task",
            message="Write a short release note using the project writing flow.",
        ),
    )

    assert response["ok"] is True
    assert response["kind"] == "task_started"
    assert response["task_id"] == "task-123"
    assert captured["project_id"] == "writing-project"
    assert captured["interaction_mode"] == "auto"
    assert captured["messages"] == [{"role": "user", "content": "Write a short release note using the project writing flow."}]
    assert captured["workspace"]["root_id"] == "writing-project"
    conversation = kanban_routes.kanban_project_conversation("writing-project")["messages"]
    assert conversation[-1]["task_id"] == "task-123"


def test_project_conversation_agent_can_dispatch_task_to_workflow_engine(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.routes.workflows as workflow_routes

    captured = {}

    class FakeProjectAgent:
        def chat_json(self, messages):
            return {
                "action": "start_task",
                "reply": "Starting the writing task.",
                "task_request": "Write the release note using the writing workflow.",
            }

    def fake_start_workflow_payload(body):
        captured.update(body)
        return {
            "ok": True,
            "task_id": "task-agent-456",
            "poll_url": "/v1/workflows/task-agent-456",
            "events_url": "/v1/workflows/task-agent-456/events",
        }

    monkeypatch.setattr(kanban_routes, "get_llm_client", lambda agent: FakeProjectAgent())
    monkeypatch.setattr(workflow_routes, "start_workflow_payload", fake_start_workflow_payload)
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            message="Please write a release note now.",
        ),
    )

    assert response["ok"] is True
    assert response["kind"] == "task_started"
    assert response["task_id"] == "task-agent-456"
    assert captured["messages"] == [{"role": "user", "content": "Write the release note using the writing workflow."}]
    assert captured["metadata"]["source"] == "project_conversation"
    assert captured["metadata"]["project_agent_decision"]["action"] == "start_task"


def test_project_conversation_agent_normalizes_raw_json_reply(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.routes.workflows as workflow_routes

    raw_decision = (
        '{"action":"start_task","reply":"Start the writing intake.",'
        '"task_request":"Run the writing intake workflow."}'
    )
    captured = {}

    class FakeProjectAgent:
        def chat_json(self, messages):
            return {"raw_text": raw_decision, "reply": raw_decision}

    def fake_start_workflow_payload(body):
        captured["messages"] = body["messages"]
        captured["metadata"] = body["metadata"]
        return {
            "ok": True,
            "task_id": "task-json-123",
            "status_key": "queued",
            "poll_url": "/v1/workflows/task-json-123",
            "events_url": "/v1/workflows/task-json-123/events",
        }

    monkeypatch.setattr(kanban_routes, "get_llm_client", lambda agent: FakeProjectAgent())
    monkeypatch.setattr(workflow_routes, "start_workflow_payload", fake_start_workflow_payload)
    kanban_service.upsert_project(project_id="json-project", name="JSON Project")

    response = kanban_routes.kanban_project_conversation_message(
        "json-project",
        kanban_routes.ProjectConversationRequest(message="开始第一个任务"),
    )

    assert response["ok"] is True
    assert response["kind"] == "task_started"
    assert captured["messages"] == [{"role": "user", "content": "Run the writing intake workflow."}]
    assert captured["metadata"]["project_agent_decision"]["action"] == "start_task"
    conversation = kanban_routes.kanban_project_conversation("json-project")["messages"]
    assistant_messages = [message for message in conversation if message["role"] == "assistant"]
    assert assistant_messages[-1]["content"] == "Task started: task-json-123"
    assert '"action"' not in assistant_messages[-1]["content"]


def test_project_conversation_agent_can_continue_active_task(monkeypatch, tmp_path):
    kanban_service = _configure(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    import app.routes.workflows as workflow_routes

    kanban_service.update_project_workflow("writing-project", _writing_workflow())
    task = kanban_service.create_task(
        project_id="writing-project",
        title="Release note",
        description="Write the release note.",
        status_key="written",
    )["task"]
    kanban_service.add_project_event(
        "writing-project",
        "project_conversation_message",
        {"role": "assistant", "content": "Task started.", "kind": "start_task", "task_id": task["id"]},
    )
    captured = {}

    class FakeProjectAgent:
        def chat_json(self, messages):
            captured["prompt"] = messages[-1]["content"]
            return {
                "action": "continue_task",
                "task_id": task["id"],
                "task_request": "Use the new tone guidance in the active release-note task.",
            }

    def fake_continue_workflow_payload(task_id, incoming):
        captured["task_id"] = task_id
        captured["incoming"] = incoming
        return {
            "ok": True,
            "task_id": task_id,
            "poll_url": f"/v1/workflows/{task_id}",
            "events_url": f"/v1/workflows/{task_id}/events",
        }

    monkeypatch.setattr(kanban_routes, "get_llm_client", lambda agent: FakeProjectAgent())
    monkeypatch.setattr(workflow_routes, "continue_workflow_payload", fake_continue_workflow_payload)
    kanban_service.upsert_project(project_id="writing-project", name="Writing Project")

    response = kanban_routes.kanban_project_conversation_message(
        "writing-project",
        kanban_routes.ProjectConversationRequest(
            message="Keep the same release-note task, but make the tone more formal.",
        ),
    )

    assert response["ok"] is True
    assert response["kind"] == "task_continued"
    assert response["task_id"] == task["id"]
    assert captured["task_id"] == task["id"]
    assert captured["incoming"]["action"] == "message"
    assert captured["incoming"]["message"] == "Use the new tone guidance in the active release-note task."
    assert task["id"] in captured["prompt"]
