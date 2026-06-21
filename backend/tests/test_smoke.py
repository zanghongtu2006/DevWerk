import json

from fastapi.testclient import TestClient

from app.models.protocol import IdeChatResponse
from app.services.kanban import DEFAULT_COLUMNS


def test_ide_error_response_can_omit_reply():
    response = IdeChatResponse(ok=False, done=True, error_code="BAD_REQUEST")

    assert response.reply == ""
    assert response.ok is False


def test_default_kanban_flow_contains_required_control_points():
    statuses = [column["status_key"] for column in DEFAULT_COLUMNS]

    for required in (
        "draft",
        "context_indexed",
        "planned",
        "coding",
        "reviewed",
        "ready_to_apply",
        "applied",
        "verified",
        "done",
        "failed",
    ):
        assert required in statuses


def test_dashboard_contains_task_detail_surface():
    from app.routes.kanban import DASHBOARD_HTML

    assert 'data-view="details"' in DASHBOARD_HTML
    assert 'id="view-details"' in DASHBOARD_HTML
    assert "loadTaskDetail" in DASHBOARD_HTML
    assert "workflow_phase_output" in DASHBOARD_HTML
    assert "code_context_summary" in DASHBOARD_HTML
    assert "review_bundle" in DASHBOARD_HTML


def test_workflow_action_protocol_drives_kanban_state(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.workflow as workflow_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "workflow.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="workflow-smoke",
        title="Implement feature",
        description="Smoke",
        status_key="ready_to_apply",
    )["task"]

    applied = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {
            "ok": True,
            "snapshot_id": "20260614-0001-test",
            "changed_paths": ["src/main/java/App.java"],
            "verification": {},
        },
    )

    assert applied["task"]["status_key"] == "done"
    state = workflow_service.current_workflow_state(task["id"])
    assert state["status_key"] == "done"
    assert state["actions"] == []
    events = kanban_service.list_events(project_id="workflow-smoke", task_id=task["id"], limit=50)["events"]
    event_types = [event["event_type"] for event in events]
    assert "task_moved" in event_types
    assert "apply_result_received" in event_types
    assert events[0]["task_title"] == "Implement feature"
    audit_log = tmp_path / "sessions" / "workflow-smoke" / "audit_events.jsonl"
    legacy_task_dir = tmp_path / "sessions" / "workflow-smoke" / task["id"]
    project_memory_path = tmp_path / "sessions" / "workflow-smoke" / "project_memory.json"
    project_memory_log = tmp_path / "sessions" / "workflow-smoke" / "project_memory.jsonl"
    assert audit_log.is_file()
    assert not legacy_task_dir.exists()
    assert project_memory_path.is_file()
    assert project_memory_log.is_file()
    task_events = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert any(event["event_type"] == "task_moved" for event in task_events)
    project_memory = session_store.read_project_memory("workflow-smoke")
    assert project_memory["project_id"] == "workflow-smoke"
    assert task["id"] in project_memory["tasks_seen"]
    assert "src/main/java/App.java" in project_memory["paths"]
    assert any(item["phase"] == "apply" and item["status_key"] == "done" for item in project_memory["phase_summaries"])

    failed_task = kanban_service.create_task(
        project_id="workflow-smoke",
        title="Failed task",
        description="Smoke",
        status_key="failed",
    )["task"]
    abandoned = workflow_service.apply_workflow_action(failed_task["id"], "abandon", {"reason": "test"})
    assert abandoned["task"]["status_key"] == "failed"

    retried = workflow_service.apply_workflow_action(failed_task["id"], "retry", {"reason": "test"})
    assert retried["task"]["status_key"] == "draft"


def test_project_memory_does_not_treat_source_map_symbol_kinds_as_frameworks():
    from app.services.session_store import _normalize_project_memory

    memory = _normalize_project_memory(
        "memory-smoke",
        {"frameworks": ["class", "method", "source", "Spring Boot"]},
    )

    assert memory["frameworks"] == ["Spring Boot"]


def test_failed_verification_returns_to_coding(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.workflow as workflow_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "verification.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="verification-smoke",
        title="Compile checked change",
        description="Smoke",
        status_key="ready_to_apply",
    )["task"]

    result = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {
            "ok": True,
            "snapshot_id": "20260617-compile-fail",
            "changed_paths": ["src/main/java/org/example/dto/TenantCreateRequest.java"],
            "verification": {
                "required": ["compile"],
                "results": {"compile": "failed"},
                "tool_results": [
                    {
                        "id": "compile",
                        "tool": "process.run",
                        "ok": False,
                        "content": "java: illegal escape character",
                        "error": "java: illegal escape character",
                    }
                ],
            },
        },
    )

    assert result["task"]["status_key"] == "planned"
    task_detail = kanban_service.get_task(task["id"])["task"]
    event_types = [event["event_type"] for event in task_detail["events"]]
    assert "verification_failed" in event_types
    phase_outputs = [artifact["payload"] for artifact in task_detail["artifacts"] if artifact["artifact_type"] == "workflow_phase_output"]
    assert phase_outputs[-1]["next_action"] == "request_recoding"


def test_kanban_apply_result_queues_resume_after_failed_verification(monkeypatch, tmp_path):
    import app.main as main_module
    import app.routes.workflows as ide_routes
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.usage as usage_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "resume.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    monkeypatch.setattr(usage_service, "settings", lambda: FakeSettings())
    kanban_service._initialized = False
    usage_service._initialized = False

    started = []
    monkeypatch.setattr(ide_routes, "_start_workflow_thread", lambda task_id, body: started.append((task_id, body)))

    task = kanban_service.create_task(
        project_id="resume-smoke",
        title="Compile checked change",
        description="Smoke",
        status_key="ready_to_apply",
    )["task"]
    kanban_service.add_artifact(
        task["id"],
        artifact_type="workflow_request_body",
        payload={
            "project_id": "resume-smoke",
            "task_id": task["id"],
            "mode": "agent",
            "messages": [{"role": "user", "content": "Fix compile error"}],
            "workspace": {"tree_preview": "pom.xml\nsrc/main/java/App.java"},
        },
    )
    kanban_service.add_artifact(
        task["id"],
        artifact_type="workflow_result",
        payload={"ok": True, "task_id": task["id"], "status_key": "ready_to_apply"},
    )

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.post(
            f"/v1/kanban/tasks/{task['id']}/actions",
            json={
                "action": "apply_result",
                "payload": {
                    "ok": True,
                    "snapshot_id": "20260617-compile-fail",
                    "changed_paths": ["src/main/java/App.java"],
                    "verification": {
                        "required": ["compile"],
                        "results": {"compile": "failed"},
                        "tool_results": [{"id": "compile", "tool": "process.run", "ok": False, "error": "compile failed"}],
                    },
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["status_key"] == "planned"
    assert body["workflow_resume"]["poll_url"].startswith(f"/v1/workflows/{task['id']}?result_after=")
    assert started and started[0][0] == task["id"]
    assert started[0][1]["resume_status"] == "planned"
    assert started[0][1]["verification_feedback"]["results"]["compile"] == "failed"
    assert started[0][1]["verification_feedback"]["applied_changed_paths"] == [
        "src/main/java/App.java"
    ]


def test_kanban_apply_failure_requests_recoding_and_queues_resume(monkeypatch, tmp_path):
    import app.main as main_module
    import app.routes.workflows as ide_routes
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.usage as usage_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "apply-resume.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    monkeypatch.setattr(usage_service, "settings", lambda: FakeSettings())
    kanban_service._initialized = False
    usage_service._initialized = False
    started = []
    monkeypatch.setattr(ide_routes, "_start_workflow_thread", lambda task_id, body: started.append((task_id, body)))

    task = kanban_service.create_task(
        project_id="apply-resume-smoke",
        title="Apply checked change",
        description="Smoke",
        status_key="ready_to_apply",
    )["task"]
    kanban_service.add_artifact(
        task["id"],
        artifact_type="workflow_request_body",
        payload={
            "project_id": "apply-resume-smoke",
            "task_id": task["id"],
            "mode": "agent",
            "messages": [{"role": "user", "content": "Fix compile error"}],
            "workspace": {"tree_preview": "src/main.py"},
        },
    )
    kanban_service.add_artifact(
        task["id"],
        artifact_type="workflow_result",
        payload={"ok": True, "task_id": task["id"], "status_key": "ready_to_apply"},
    )

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.post(
            f"/v1/kanban/tasks/{task['id']}/actions",
            json={
                "action": "apply_result",
                "payload": {
                    "ok": False,
                    "snapshot_id": "apply-failed",
                    "changed_paths": [],
                    "error_message": "Patch context does not match the current file",
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["status_key"] == "coding"
    assert body["workflow_resume"]["reason"] == "apply_failed"
    assert started[0][1]["client_feedback"]["kind"] == "apply_failed"
    assert "Patch context" in started[0][1]["client_feedback"]["summary"]


def test_stale_apply_result_is_idempotently_ignored(monkeypatch, tmp_path):
    import app.main as main_module
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.usage as usage_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "stale-apply.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    monkeypatch.setattr(usage_service, "settings", lambda: FakeSettings())
    kanban_service._initialized = False
    usage_service._initialized = False

    task = kanban_service.create_task(
        project_id="stale-apply-smoke",
        title="Continue coding",
        description="The reviewer already returned this task to coding.",
        status_key="coding",
    )["task"]

    app = main_module.create_app()
    with TestClient(app) as client:
        response = client.post(
            f"/v1/kanban/tasks/{task['id']}/actions",
            json={
                "action": "apply_result",
                "payload": {
                    "ok": True,
                    "snapshot_id": "late-client-result",
                    "changed_paths": [],
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["action_ignored"] is True
    assert body["task"]["status_key"] == "coding"
    task_detail = kanban_service.get_task(task["id"])["task"]
    assert "stale_apply_result_ignored" in [event["event_type"] for event in task_detail["events"]]


def test_verification_policy_uses_project_configured_tools_only():
    from app.services.verification_policy import configured_post_apply_tool_requests

    assert configured_post_apply_tool_requests({"parameters": {}}) == []

    requests = configured_post_apply_tool_requests(
        {
            "parameters": {
                "verification": {
                    "tool_requests": [
                        {
                            "id": "syntax",
                            "tool": "source.diagnostics",
                            "args": {"paths": ["src/main/java/org/example/Application.java"]},
                        }
                    ]
                }
            }
        }
    )

    assert len(requests) == 1
    assert requests[0].tool == "source.diagnostics"
    assert requests[0].args["paths"] == ["src/main/java/org/example/Application.java"]


def test_workflow_semantic_rework_actions(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.session_store as session_store
    import app.services.workflow as workflow_service

    class FakeSettings:
        devwerk_db_path = str(tmp_path / "workflow-rework.db")

    monkeypatch.setattr(kanban_service, "settings", lambda: FakeSettings())
    monkeypatch.setattr(session_store, "settings", lambda: FakeSettings())
    kanban_service._initialized = False

    task = kanban_service.create_task(
        project_id="workflow-rework",
        title="Review coding output",
        description="Smoke",
        status_key="reviewed",
    )["task"]

    recoding = workflow_service.apply_workflow_action(
        task["id"],
        "request_recoding",
        {"phase": "reviewed", "reason": "Generated change missed an approved file."},
    )
    assert recoding["task"]["status_key"] == "coding"

    reviewed = kanban_service.move_task(task["id"], "reviewed", force=True, payload={"reason": "retry review"})
    assert reviewed["task"]["status_key"] == "reviewed"

    approved = workflow_service.apply_workflow_action(
        task["id"],
        "approve",
        {"phase": "reviewed", "reason": "Review passed."},
    )
    assert approved["task"]["status_key"] == "ready_to_apply"

    events = kanban_service.list_events(project_id="workflow-rework", task_id=task["id"], limit=50)["events"]
    event_types = [event["event_type"] for event in events]
    assert "workflow_transition_decided" in event_types
    assert "workflow_rework_requested" in event_types
