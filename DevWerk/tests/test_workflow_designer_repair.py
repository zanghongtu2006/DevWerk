from __future__ import annotations

import pytest


def _patch_designer(monkeypatch, workflow: dict):
    import app.services.workflow_designer as workflow_designer

    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {"reply": "repaired", "workflow": workflow, "agents": {}},
    )
    return workflow_designer


def _actions() -> dict:
    return {
        "prepared": {"to": "prepared"},
        "workflow_done": {"to": "done"},
        "fail": {"to": "failed"},
        "abandon": {"to": "failed"},
        "retry": {"to": "intake"},
    }


@pytest.mark.parametrize(
    ("case_id", "workflow"),
    [
        (
            "nested-workflow",
            {
                "workflow": {
                    "name": "nested",
                    "columns": [
                        {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared", "failed"]},
                        {"status_key": "prepared", "title": "Prepared", "position": 20, "transition_to": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                        {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                        {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
                    ],
                    "actions": _actions(),
                }
            },
        ),
        (
            "kanban-columns",
            {
                "name": "kanban",
                "kanban": {
                    "columns": [
                        {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared", "failed"]},
                        {"status_key": "prepared", "title": "Prepared", "position": 20, "transition_to": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                        {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                        {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
                    ]
                },
                "actions": _actions(),
            },
        ),
        (
            "states-list",
            {
                "name": "states",
                "states": [
                    {"id": "intake", "title": "Intake", "next": ["prepared", "failed"]},
                    {"id": "prepared", "title": "Prepared", "next": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                    {"id": "done", "title": "Done"},
                    {"id": "failed", "title": "Failed", "next": ["intake"]},
                ],
                "actions": _actions(),
            },
        ),
        (
            "states-map",
            {
                "name": "states-map",
                "states": {
                    "intake": {"title": "Intake", "transitions": ["prepared", "failed"]},
                    "prepared": {"title": "Prepared", "transitions": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                    "done": {"title": "Done"},
                    "failed": {"title": "Failed", "transitions": ["intake"]},
                },
                "actions": _actions(),
            },
        ),
        (
            "target-column-actions",
            {
                "name": "target-column-actions",
                "columns": [
                    {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared", "failed"]},
                    {"status_key": "prepared", "title": "Prepared", "position": 20, "transition_to": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                    {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                    {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
                ],
                "actions": {
                    "workflow_done": {"target_column": "done"},
                    "fail": {"target_column": "failed"},
                    "abandon": {"target_column": "failed"},
                    "retry": {"target_column": "intake"},
                },
            },
        ),
    ],
)
def test_workflow_designer_repairs_common_llm_workflow_shapes(monkeypatch, case_id, workflow):
    workflow_designer = _patch_designer(monkeypatch, workflow)

    result = workflow_designer.design_project_workflow(
        project_id=case_id,
        messages=[{"role": "user", "content": "Create workflow."}],
    )

    assert result["workflow"]["columns"]
    assert result["workflow"]["actions"]["workflow_done"]["to"] == "done"
    assert result["debug"]["normalization_notes"]


def test_workflow_designer_aligns_coding_abandon_to_failed(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "coding-with-abandoned",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "implementation", "title": "Implementation", "position": 10, "transition_to": ["ready_to_apply", "failed"], "job_template": "implement_code", "output_artifact": "code_change_bundle", "success_action": "code_ready", "context_policy": {"output_contract": "code_change"}},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": ["applied", "failed"]},
                {"status_key": "applied", "title": "Applied", "position": 30, "transition_to": ["done", "failed"]},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 98, "transition_to": ["implementation"]},
                {"status_key": "abandoned", "title": "Abandoned", "position": 99},
            ],
            "actions": {
                "code_ready": {"target_column": "ready_to_apply"},
                "apply_succeeded": {"target_column": "applied"},
                "verification_failed": {"target_column": "failed"},
                "workflow_done": {"target_column": "done"},
                "fail": {"target_column": "failed"},
                "abandon": {"target_column": "abandoned"},
                "retry": {"target_column": "implementation"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="coding-with-abandoned",
        messages=[{"role": "user", "content": "Create a coding workflow with review."}],
    )

    assert result["workflow"]["actions"]["abandon"]["to"] == "failed"
    assert "action 'abandon' target 'abandoned' normalized to 'failed'" in result["debug"]["normalization_notes"]


def test_workflow_designer_repairs_real_minimax_coding_lifecycle_shape(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "offline-points-full-lifecycle",
            "version": 2,
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "backlog", "title": "Backlog", "position": 1, "job_template": "captured_requirement", "output_artifact": "requirement_card", "success_action": "code_ready"},
                {"status_key": "designing", "title": "Designing", "position": 2, "job_template": "design_task", "output_artifact": "design_doc", "success_action": "code_ready"},
                {"status_key": "developing", "title": "Developing", "position": 3, "job_template": "code_task", "output_artifact": "code_changes", "success_action": "code_ready"},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 4, "job_template": "apply_ready_task", "output_artifact": "apply_package", "success_action": "code_ready"},
                {"status_key": "applying", "title": "Applying", "position": 5, "job_template": "apply_task", "output_artifact": "apply_result", "success_action": "apply_succeeded"},
                {"status_key": "apply_succeeded", "title": "Apply Succeeded", "position": 6, "job_template": "verify_task", "output_artifact": "verification_report", "success_action": "code_ready"},
                {"status_key": "done", "title": "Done", "position": 90, "job_template": "delivery_task", "output_artifact": "delivery_report", "success_action": "workflow_done"},
                {"status_key": "failed", "title": "Failed", "position": 98, "job_template": "fail_terminal", "output_artifact": "failure_report", "success_action": "fail"},
                {"status_key": "abandoned", "title": "Abandoned", "position": 99, "job_template": "abandon_terminal", "output_artifact": "abandon_record", "success_action": "abandon"},
                {"status_key": "retry_pending", "title": "Retry Pending", "position": 100, "job_template": "retry_task", "output_artifact": "retry_intent", "success_action": "retry"},
            ],
            "actions": {
                "workflow_done": {"target": "done"},
                "fail": {"target": "failed"},
                "abandon": {"target": "abandoned"},
                "retry": {"target": "retry_pending"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="offline-points",
        messages=[{"role": "user", "content": "coding+review, start the project."}],
    )

    actions = result["workflow"]["actions"]
    notes = result["debug"]["normalization_notes"]
    assert actions["code_ready"]["to"] == "ready_to_apply"
    assert actions["apply_succeeded"]["to"] == "apply_succeeded"
    assert actions["verification_failed"]["to"] == "failed"
    assert actions["workflow_done"]["to"] == "done"
    assert actions["fail"]["to"] == "failed"
    assert actions["abandon"]["to"] == "failed"
    assert actions["retry"]["to"] == "retry_pending"
    assert "coding lifecycle action 'code_ready' inferred from explicit ready_to_apply column" in notes
    assert "action 'abandon' target 'abandoned' normalized to 'failed'" in notes
