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

