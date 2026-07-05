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
        (
            "transition-to-actions",
            {
                "name": "transition-to-actions",
                "columns": [
                    {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared", "failed"]},
                    {"status_key": "prepared", "title": "Prepared", "position": 20, "transition_to": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                    {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                    {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
                ],
                "actions": {
                    "workflow_done": {"transition_to": "done"},
                    "fail": {"transition_to": "failed"},
                    "abandon": {"transition_to": "failed"},
                    "retry": {"transition_to": "intake"},
                },
            },
        ),
        (
            "to-status-actions",
            {
                "name": "to-status-actions",
                "columns": [
                    {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared", "failed"]},
                    {"status_key": "prepared", "title": "Prepared", "position": 20, "transition_to": ["done", "failed"], "job_template": "prepare", "success_action": "workflow_done"},
                    {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
                    {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
                ],
                "actions": {
                    "workflow_done": {"to_status": "done"},
                    "fail": {"to_status": "failed"},
                    "abandon": {"to_status": "failed"},
                    "retry": {"to_status": "intake"},
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


def test_workflow_designer_merges_top_level_actions(monkeypatch):
    import app.services.workflow_designer as workflow_designer

    monkeypatch.setattr(
        workflow_designer,
        "_ask_llm",
        lambda **kwargs: {
            "reply": "designed",
            "workflow": {
                "name": "top-level-actions",
                "columns": [
                    {"status_key": "intake", "title": "Intake", "position": 10, "transition_to": ["prepared"]},
                    {"status_key": "prepared", "title": "Prepared", "position": 20, "job_template": "prepare", "success_action": "workflow_done", "transition_to": ["done", "failed"]},
                    {"status_key": "done", "title": "Done", "position": 90},
                    {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["intake"]},
                ],
            },
            "actions": _actions(),
            "agents": {},
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="top-level-actions",
        messages=[{"role": "user", "content": "Create workflow."}],
    )

    assert result["workflow"]["actions"]["workflow_done"]["to"] == "done"
    assert "top-level actions merged into workflow.actions" in result["debug"]["normalization_notes"]


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


def test_workflow_designer_aligns_coding_abandon_sink_to_failed(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "coding-with-abandon-sink",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "implementing", "title": "Implementing", "position": 10, "transition_to": ["ready_to_apply", "failed"], "job_template": "implement_code", "success_action": "code_ready"},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": ["applying", "failed"]},
                {"status_key": "applying", "title": "Applying", "position": 30, "transition_to": ["done", "failed"], "success_action": "apply_succeeded"},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implementing"]},
            ],
            "actions": {
                "code_ready": {"to": "ready_to_apply"},
                "apply_succeeded": {"to": "done"},
                "verification_failed": {"to": "failed"},
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "abandon": {"to": "abandoned_sink"},
                "retry": {"to": "implementing"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="coding-with-abandon-sink",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    assert result["workflow"]["actions"]["abandon"]["to"] == "failed"
    assert "action 'abandon' target 'abandoned_sink' normalized to 'failed'" in result["debug"]["normalization_notes"]


def test_workflow_designer_aligns_column_transition_to_action_target(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "column-action-transition",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "planning", "title": "Planning", "position": 10, "transition_to": ["implementation"], "job_template": "plan", "success_action": "code_ready"},
                {"status_key": "implementation", "title": "Implementation", "position": 20, "transition_to": ["ready_to_apply"], "job_template": "implement", "success_action": "code_ready"},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 30, "transition_to": ["done", "failed"]},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implementation"]},
            ],
            "actions": {
                "code_ready": {"to": "ready_to_apply"},
                "apply_succeeded": {"to": "done"},
                "verification_failed": {"to": "failed"},
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "abandon": {"to": "failed"},
                "retry": {"to": "implementation"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="column-action-transition",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    planning = next(item for item in result["workflow"]["columns"] if item["status_key"] == "planning")
    assert planning["success_action"] == "planning_complete"
    assert result["workflow"]["actions"]["planning_complete"]["to"] == "implementation"
    assert "implementation" in planning["transition_to"]
    assert "column 'planning' reserved success_action 'code_ready' aligned to 'planning_complete'" in result["debug"]["normalization_notes"]


def test_workflow_designer_accepts_string_actions_and_infers_column_success(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "string-actions",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "design", "title": "Design", "position": 10, "transition_to": "implement", "job_template": "design"},
                {"status_key": "implement", "title": "Implement", "position": 20, "transition_to": "ready-to-apply", "job_template": "implement", "success_action": "code_ready"},
                {"status_key": "ready-to-apply", "title": "Ready To Apply", "position": 30, "transition_to": "verification"},
                {"status_key": "verification", "title": "Verification", "position": 40, "transition_to": "done", "job_template": "verify", "success_action": "workflow_done"},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": "implement"},
            ],
            "actions": {
                "code_ready": "move_to_ready_to_apply",
                "apply_succeeded": "move_to_verification",
                "verification_failed": "move_to_failed",
                "workflow_done": "move_to_done",
                "fail": "move_to_failed",
                "abandon": "move_to_failed",
                "retry": "move_to_implement",
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="string-actions",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    design = next(item for item in result["workflow"]["columns"] if item["status_key"] == "design")
    assert result["workflow"]["actions"]["code_ready"]["to"] == "ready_to_apply"
    assert result["workflow"]["actions"]["design_complete"]["to"] == "implement"
    assert design["success_action"] == "design_complete"
    assert "column 'design' success_action 'design_complete' inferred to 'implement'" in result["debug"]["normalization_notes"]


def test_workflow_designer_infers_missing_transition_and_missing_success_action(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "missing-transition",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "intake", "title": "Intake", "position": 1, "job_template": "intake", "success_action": "scaffold_ready"},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 2, "job_template": "prepare", "success_action": "code_ready"},
                {"status_key": "done", "title": "Done", "position": 3, "terminal": True},
                {"status_key": "failed", "title": "Failed", "position": 4, "terminal": True},
            ],
            "actions": {
                "code_ready": "ready_to_apply",
                "apply_succeeded": "done",
                "verification_failed": "failed",
                "workflow_done": "done",
                "fail": "failed",
                "abandon": "failed",
                "retry": "ready_to_apply",
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="missing-transition",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    intake = next(item for item in result["workflow"]["columns"] if item["status_key"] == "intake")
    assert intake["transition_to"] == ["ready_to_apply"]
    assert result["workflow"]["actions"]["scaffold_ready"]["to"] == "ready_to_apply"
    notes = result["debug"]["normalization_notes"]
    assert "column 'intake' transition_to inferred from position order: 'ready_to_apply'" in notes
    assert "action 'scaffold_ready' inferred from explicit column success_action" in notes


def test_workflow_designer_aligns_apply_succeeded_away_from_ready_gate(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "apply-self-loop",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "implement", "title": "Implement", "position": 1, "transition_to": "ready_to_apply", "job_template": "code.scaffold", "output_artifact": "code_bundle", "success_action": "code_ready"},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 2, "transition_to": "verification", "job_template": "code.package", "success_action": "apply_succeeded"},
                {"status_key": "verification", "title": "Verification", "position": 3, "transition_to": "done", "job_template": "verify", "success_action": "workflow_done"},
                {"status_key": "done", "title": "Done", "position": 4, "terminal": True},
                {"status_key": "failed", "title": "Failed", "position": 5, "terminal": True},
            ],
            "actions": {
                "code_ready": {"target_column": "ready_to_apply"},
                "apply_succeeded": {"target_column": "ready_to_apply"},
                "verification_failed": {"target_column": "failed"},
                "workflow_done": {"target_column": "done"},
                "fail": {"target_column": "failed"},
                "abandon": {"target_column": "failed"},
                "retry": {"target_column": "implement"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="apply-self-loop",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    assert result["workflow"]["actions"]["apply_succeeded"]["to"] == "verification"
    ready = next(item for item in result["workflow"]["columns"] if item["status_key"] == "ready_to_apply")
    assert "verification" in ready["transition_to"]
    assert "coding lifecycle action 'apply_succeeded' target 'ready_to_apply' aligned to 'verification'" in result["debug"]["normalization_notes"]


def test_workflow_designer_aligns_ready_gate_transition_to_apply_success_target(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "ready-gate-mismatch",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "implementation", "title": "Implementation", "position": 10, "transition_to": "ready_to_apply", "job_template": "scaffold_job", "output_artifact": "code_change_bundle", "success_action": "code_ready", "context_policy": {"output_contract": "code_change"}},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": "applying"},
                {"status_key": "applying", "title": "Applying", "position": 30, "transition_to": "verifying", "job_template": "apply_job", "success_action": "apply_succeeded"},
                {"status_key": "verifying", "title": "Verifying", "position": 40, "transition_to": "done", "job_template": "verify_job", "success_action": "workflow_done"},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": "implementation"},
            ],
            "actions": {
                "code_ready": "ready_to_apply",
                "apply_succeeded": "verifying",
                "verification_failed": "failed",
                "workflow_done": "done",
                "fail": "failed",
                "abandon": "failed",
                "retry": "implementation",
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="ready-gate-mismatch",
        messages=[{"role": "user", "content": "Create a coding workflow with apply and verify."}],
    )

    ready = next(item for item in result["workflow"]["columns"] if item["status_key"] == "ready_to_apply")
    assert "applying" in ready["transition_to"]
    assert "verifying" in ready["transition_to"]
    assert "ready gate column 'ready_to_apply' transition_to appended apply target 'verifying'" in result["debug"]["normalization_notes"]


def test_workflow_designer_removes_execution_from_terminal_columns(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "terminal-jobs",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "build", "title": "Build", "position": 10, "transition_to": "ready_to_apply", "job_template": "code_generation", "output_artifact": "code_patch", "success_action": "code_ready", "context_policy": {"output_contract": "code_change"}},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": "verification"},
                {"status_key": "verification", "title": "Verification", "position": 30, "transition_to": "done", "job_template": "verify", "success_action": "workflow_done"},
                {"status_key": "done", "title": "Done", "position": 90, "job_template": "close_out", "success_action": "workflow_done", "output_artifact": "summary"},
                {"status_key": "failed", "title": "Failed", "position": 99, "job_template": "failure_capture", "success_action": "fail", "transition_to": "build"},
            ],
            "actions": {
                "code_ready": "ready_to_apply",
                "apply_succeeded": "verification",
                "verification_failed": "failed",
                "workflow_done": "done",
                "fail": "failed",
                "abandon": "failed",
                "retry": "build",
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="terminal-jobs",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    columns = {item["status_key"]: item for item in result["workflow"]["columns"]}
    assert "job_template" not in columns["done"]
    assert "success_action" not in columns["done"]
    assert "job_template" not in columns["failed"]
    assert "success_action" not in columns["failed"]
    notes = result["debug"]["normalization_notes"]
    assert any("terminal column 'done' execution fields removed" in note for note in notes)
    assert any("terminal column 'failed' execution fields removed" in note for note in notes)


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
    assert "coding lifecycle action 'code_ready' target 'designing' aligned to 'ready_to_apply'" in notes
    assert "action 'abandon' target 'abandoned' normalized to 'failed'" in notes


def test_workflow_designer_aligns_verification_success_away_from_failure(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "verify-success-bug",
            "version": 1,
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "implementation", "title": "Implementation", "position": 10, "transition_to": ["ready_to_apply"], "job_template": "code", "output_artifact": "code_patch", "success_action": "code_ready", "context_policy": {"output_contract": "code_change"}},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": ["verifying"]},
                {
                    "status_key": "verifying",
                    "title": "Verifying",
                    "position": 30,
                    "transition_to": ["verification_failed"],
                    "job_template": "verify generated scaffold",
                    "output_artifact": "verification_report",
                    "success_action": "verifying_complete",
                },
                {"status_key": "verification_failed", "title": "Verification Failed", "position": 40, "transition_to": ["implementation"]},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99},
            ],
            "actions": {
                "code_ready": {"to": "ready_to_apply"},
                "apply_succeeded": {"to": "verifying"},
                "verifying_complete": {"to": "verification_failed"},
                "verification_failed": {"to": "verification_failed"},
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "abandon": {"to": "failed"},
                "retry": {"to": "implementation"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="verify-success-bug",
        messages=[{"role": "user", "content": "coding workflow with apply and verification."}],
    )

    columns = {column["status_key"]: column for column in result["workflow"]["columns"]}
    assert columns["verifying"]["success_action"] == "workflow_done"
    assert "done" in columns["verifying"]["transition_to"]
    assert "verification_failed" in columns["verifying"]["transition_to"]
    assert "verification column 'verifying' success_action 'verifying_complete' aligned to 'workflow_done'" in result["debug"]["normalization_notes"]


def test_workflow_designer_prevents_non_code_column_from_using_code_ready(monkeypatch):
    workflow_designer = _patch_designer(
        monkeypatch,
        {
            "name": "reserved-action-misuse",
            "version": 1,
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {
                    "status_key": "intake",
                    "title": "Requirement Intake",
                    "position": 10,
                    "transition_to": ["code_generation"],
                    "job_template": "intake_job",
                    "output_artifact": "requirement_spec",
                    "success_action": "code_ready",
                },
                {
                    "status_key": "code_generation",
                    "title": "Code Generation",
                    "position": 20,
                    "transition_to": ["ready_to_apply"],
                    "job_template": "generate scaffold code",
                    "output_artifact": "generated_scaffold_bundle",
                    "success_action": "code_ready",
                },
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 30, "transition_to": ["done"]},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99},
            ],
            "actions": {
                "code_ready": {"to": "ready_to_apply"},
                "apply_succeeded": {"to": "done"},
                "verification_failed": {"to": "failed"},
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "abandon": {"to": "failed"},
                "retry": {"to": "intake"},
            },
        },
    )

    result = workflow_designer.design_project_workflow(
        project_id="reserved-action-misuse",
        messages=[{"role": "user", "content": "coding workflow with intake and code generation."}],
    )

    columns = {column["status_key"]: column for column in result["workflow"]["columns"]}
    assert columns["intake"]["success_action"] == "intake_complete"
    assert result["workflow"]["actions"]["intake_complete"]["to"] == "code_generation"
    assert columns["code_generation"]["success_action"] == "code_ready"
    assert "column 'intake' reserved success_action 'code_ready' aligned to 'intake_complete'" in result["debug"]["normalization_notes"]


def test_workflow_designer_uses_repair_llm_after_invalid_first_output(monkeypatch):
    import app.services.workflow_designer as workflow_designer

    invalid = {
        "reply": "first draft",
        "workflow": {"name": "broken", "columns": [], "actions": {}},
        "agents": {},
    }
    repaired = {
        "reply": "repaired workflow",
        "workflow": {
            "name": "repaired-coding",
            "workflow_type": "coding",
            "requires_apply": True,
            "columns": [
                {"status_key": "implementation", "title": "Implementation", "position": 10, "transition_to": ["ready_to_apply", "failed"], "job_template": "write code", "output_artifact": "code_change_bundle", "success_action": "code_ready", "context_policy": {"output_contract": "code_change"}},
                {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": ["applied", "failed"]},
                {"status_key": "applied", "title": "Applied", "position": 30, "transition_to": ["done", "failed"]},
                {"status_key": "done", "title": "Done", "position": 90},
                {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implementation"]},
            ],
            "actions": {
                "code_ready": {"to": "ready_to_apply"},
                "apply_succeeded": {"to": "applied"},
                "verification_failed": {"to": "failed"},
                "workflow_done": {"to": "done"},
                "fail": {"to": "failed"},
                "abandon": {"to": "failed"},
                "retry": {"to": "implementation"},
            },
        },
        "agents": {"project-agent": {"enabled": True, "model_route": "default"}},
    }
    captured = {}
    monkeypatch.setattr(workflow_designer, "_ask_llm", lambda **kwargs: invalid)

    def repair(**kwargs):
        captured["validation_error"] = kwargs["validation_error"]
        captured["raw_reply"] = kwargs["raw_reply"]
        return repaired

    monkeypatch.setattr(workflow_designer, "_ask_llm_repair", repair)

    result = workflow_designer.design_project_workflow(
        project_id="repair-path",
        messages=[{"role": "user", "content": "Create a coding workflow."}],
    )

    assert result["ok"] is True
    assert result["source"] == "llm_repaired"
    assert result["reply"] == "repaired workflow"
    assert result["workflow"]["actions"]["workflow_done"]["to"] == "done"
    assert result["agents"]["project-agent"]["model_route"] == "default"
    assert result["debug"]["repair_applied"] is True
    assert captured["raw_reply"] == invalid
    assert "project-specific columns" in captured["validation_error"]
