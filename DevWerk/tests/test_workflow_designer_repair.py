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
