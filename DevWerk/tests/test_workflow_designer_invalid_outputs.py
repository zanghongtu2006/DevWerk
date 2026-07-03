from __future__ import annotations

import pytest

from tests.workflow_test_utils import coding_workflow, configure_kanban


def _patch_designer(monkeypatch, payload: dict):
    import app.services.workflow_designer as workflow_designer

    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: payload,
    )
    return workflow_designer


@pytest.mark.parametrize(
    ("case_id", "payload", "expected"),
    [
        ("WD-001-empty-workflow", {"reply": "ok", "workflow": {}, "agents": {}}, "workflow must define project-specific columns"),
        ("WD-002-empty-columns", {"reply": "ok", "workflow": {"name": "empty", "columns": [], "actions": {}}}, "workflow must define project-specific columns"),
        ("WD-003-columns-not-list", {"reply": "ok", "workflow": {"name": "bad", "columns": {"draft": {}}, "actions": {}}}, "workflow.columns must be a list"),
        (
            "WD-004-columns-without-status-key",
            {"reply": "ok", "workflow": {"name": "bad", "columns": [{"title": "Draft"}, {"position": 20}], "actions": {}}},
            "workflow must define project-specific columns",
        ),
        (
            "WD-005-coding-missing-ready-to-apply",
            {
                "reply": "ok",
                "workflow": {
                    "name": "bad-coding",
                    "workflow_type": "coding",
                    "requires_apply": True,
                    "columns": [
                        {"status_key": "implement", "title": "Implement", "position": 10, "transition_to": ["done"], "job_template": "implement_code_change"},
                        {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                        {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implement"]},
                    ],
                    "actions": {"workflow_done": {"to": "done"}, "fail": {"to": "failed"}, "abandon": {"to": "failed"}, "retry": {"to": "implement"}},
                },
            },
            "coding workflow missing lifecycle columns",
        ),
        (
            "WD-006-code-column-uses-workflow-done",
            {
                "reply": "ok",
                "workflow": {
                    **coding_workflow("bad-code-success-action"),
                    "columns": [
                        coding_workflow()["columns"][0],
                        {
                            **coding_workflow()["columns"][1],
                            "success_action": "workflow_done",
                        },
                        *coding_workflow()["columns"][2:],
                    ],
                },
            },
            "must use success_action='code_ready'",
        ),
        (
            "BAD-006-noncoding-missing-terminal-actions",
            {"reply": "ok", "workflow": {"name": "bad-noncoding", "columns": [{"status_key": "work", "title": "Work", "position": 10, "transition_to": []}], "actions": {}}},
            "explicit success action",
        ),
    ],
)
def test_workflow_designer_invalid_outputs_record_diagnostics(monkeypatch, case_id, payload, expected):
    workflow_designer = _patch_designer(monkeypatch, payload)

    with pytest.raises(workflow_designer.WorkflowDesignError) as exc_info:
        workflow_designer.design_project_workflow(
            project_id=case_id,
            messages=[{"role": "user", "content": "Design a workflow."}],
        )

    assert expected in str(exc_info.value)
    assert exc_info.value.debug["llm_output"] == payload
    assert exc_info.value.debug.get("validation_error") or "workflow" in str(exc_info.value)


def test_workflow_designer_endpoint_returns_failed_event_for_empty_columns(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes

    project_id = "invalid-output-event"
    kanban_service.upsert_project(project_id=project_id, name="Invalid Output Event")
    _patch_designer(monkeypatch, {"reply": "ok", "workflow": {"columns": []}, "agents": {}})

    with pytest.raises(Exception) as exc_info:
        kanban_routes.kanban_design_project_workflow(
            project_id,
            kanban_routes.WorkflowDesignRequest(
                messages=[{"role": "user", "content": "Create workflow."}],
                save=True,
            ),
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    events = kanban_service.list_events(project_id=project_id, limit=20)["events"]
    failed = [event for event in events if event["event_type"] == "project_workflow_design_failed"]
    assert failed
    assert failed[0]["payload"]["debug"]["llm_output"]["workflow"]["columns"] == []
