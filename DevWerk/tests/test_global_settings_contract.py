from __future__ import annotations

from pathlib import Path

import pytest

from app.core.global_settings import (
    GlobalSettings,
    WorkflowGlobalSettings,
    global_settings_payload,
    load_global_settings,
    restart_required_changes,
    save_global_settings,
)
from app.core.restart import ManagedRestart
from app.v1.capabilities import CapabilityContext, _task_resume
from app.v1.domain import TaskPlan
from tests.helpers import (
    create_planned_task,
    publish_planned_workflow,
    sequence_workflow,
    task_plan,
)


def test_global_settings_default_disables_previous_task_auto_resume(tmp_path):
    path = tmp_path / "global-settings.yaml"
    path.write_text(
        "schema_version: devwerk.global-settings.v1\nworkflow: {}\n",
        encoding="utf-8",
    )

    loaded = load_global_settings(path)

    assert loaded.workflow.auto_resume_previous_tasks is False


def test_global_settings_reject_unknown_fields(tmp_path):
    path = tmp_path / "global-settings.yaml"
    path.write_text(
        "schema_version: devwerk.global-settings.v1\n"
        "workflow:\n"
        "  auto_resume_previous_tasks: false\n"
        "  unknown_setting: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match the schema"):
        load_global_settings(path)


def test_global_settings_save_round_trip_and_publish_edit_metadata(tmp_path):
    path = tmp_path / "nested" / "global-settings.yaml"
    settings = GlobalSettings(
        workflow=WorkflowGlobalSettings(auto_resume_previous_tasks=True)
    )

    save_global_settings(path, settings)

    assert load_global_settings(path) == settings
    assert not path.with_name("global-settings.yaml.tmp").exists()
    payload = global_settings_payload(settings)
    assert payload["values"]["workflow"]["auto_resume_previous_tasks"] is True
    assert payload["fields"] == [
        {
            "key": "workflow.auto_resume_previous_tasks",
            "group": "workflow",
            "label": "启动后自动继续未完成任务",
            "description": "开启后，DevWerk 启动时会自动恢复上一次未完成的 Workflow；关闭时等待用户通过 Conversation Agent 恢复。",
            "type": "boolean",
            "restart_required": True,
        }
    ]


def test_restart_is_requested_only_for_changed_startup_setting(tmp_path):
    disabled = GlobalSettings()
    enabled = GlobalSettings(
        workflow=WorkflowGlobalSettings(auto_resume_previous_tasks=True)
    )

    assert restart_required_changes(disabled, disabled) == []
    assert restart_required_changes(disabled, enabled) == [
        "workflow.auto_resume_previous_tasks"
    ]

    marker = tmp_path / "restart.request"
    restart = ManagedRestart(marker, enabled=False)
    assert restart.schedule(delay_seconds=0) is False
    assert not marker.exists()


def test_startup_script_owns_settings_restart_loop():
    startup = (Path(__file__).resolve().parents[1] / "startup.bat").read_text(
        encoding="utf-8"
    )

    assert 'set "DEVWERK_STARTUP_MANAGED=1"' in startup
    assert 'set "DEVWERK_RESTART_MARKER=%CD%\\data\\restart.request"' in startup
    assert 'if exist "%DEVWERK_RESTART_MARKER%"' in startup
    assert "goto run_service" in startup


def test_startup_pause_preserves_workflow_and_requires_user_turn_to_resume(store, tmp_path):
    project = store.create_project("startup policy", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    running = create_planned_task(store, project["id"], "running task", task_ref="running")
    pending = create_planned_task(store, project["id"], "pending task", task_ref="pending")
    waiting = create_planned_task(store, project["id"], "waiting task", task_ref="waiting")
    recovering = create_planned_task(store, project["id"], "recovering task", task_ref="recovering")
    terminal = create_planned_task(store, project["id"], "done task", task_ref="done")
    claimed = store.claim_task(running["id"], "old-process")
    assert claimed is not None
    column_run = store.begin_run(claimed, {"task": claimed})
    with store.connect() as db:
        db.execute("UPDATE v1_tasks SET status='waiting' WHERE id=?", (waiting["id"],))
        db.execute("UPDATE v1_tasks SET status='recovering' WHERE id=?", (recovering["id"],))
        db.execute("UPDATE v1_tasks SET status='done' WHERE id=?", (terminal["id"],))
    running_schedule = store.task_scheduling(project["id"], running["id"])
    pending_schedule = store.task_scheduling(project["id"], pending["id"])

    result = store.prepare_workflow_startup(False)

    assert result["paused_tasks"] == 4
    for task_id in (running["id"], pending["id"], waiting["id"], recovering["id"]):
        task = store.get_task(task_id)
        assert task["status"] == "pending"
        assert task["control_state"] == "paused"
        assert task["supervision_action"] == "startup_hold"
    assert store.get_task(terminal["id"])["status"] == "done"
    assert store.runs(project["id"], running["id"])[0]["status"] == "interrupted"
    assert store.attempts(project["id"], running["id"])[0]["status"] == "interrupted"
    assert store.task_scheduling(project["id"], running["id"])["state"] == running_schedule["state"]
    assert store.task_scheduling(project["id"], pending["id"])["state"] == pending_schedule["state"]
    assert store.runnable_task_ids() == []
    assert any(
        event["type"] == "task.startup_paused"
        and event["task_id"] == running["id"]
        for event in store.events(project_id=project["id"])
    )

    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    with pytest.raises(ValueError, match="user-initiated Conversation turn"):
        _task_resume({"task_id": running["id"]}, context)

    resumed = _task_resume(
        {"task_id": running["id"]},
        CapabilityContext(
            project_id=project["id"],
            project=project,
            store=store,
            user_initiated=True,
        ),
    )
    assert resumed["control_state"] == "active"
    assert resumed["supervision_action"] is None
    assert running["id"] in store.runnable_task_ids()
    assert column_run["id"] == store.runs(project["id"], running["id"])[0]["id"]


def test_enabled_startup_policy_leaves_task_eligible(store, tmp_path):
    project = store.create_project("auto resume", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "pending task")

    result = store.prepare_workflow_startup(True)

    assert result["paused_tasks"] == 0
    assert store.get_task(task["id"])["control_state"] == "active"
    assert task["id"] in store.runnable_task_ids()


def test_startup_hold_is_a_task_plan_gate_not_a_downstream_task_pause(store, tmp_path):
    project = store.create_project("startup dependency graph", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    _, revision = publish_planned_workflow(store, project["id"], workflow)
    template = task_plan(revision["id"], workflow)
    first = template.tasks[0].model_copy(update={
        "proposed_task_ref": "first",
        "title": "first root",
    })
    parallel = template.tasks[0].model_copy(update={
        "proposed_task_ref": "parallel",
        "title": "parallel root",
    })
    downstream = template.tasks[0].model_copy(update={
        "proposed_task_ref": "downstream",
        "title": "downstream",
        "dependencies": ["first"],
    })
    plan = TaskPlan(
        objective="Exercise startup recovery across a dependency graph",
        workflow_revision_id=revision["id"],
        tasks=[first, parallel, downstream],
    )
    plan_id = store.create_task_plan(project["id"], plan)["id"]
    first_task = store.create_task(
        project["id"], task_plan_id=plan_id, proposed_task_ref="first"
    )
    parallel_task = store.create_task(
        project["id"], task_plan_id=plan_id, proposed_task_ref="parallel"
    )
    downstream_task = store.create_task(
        project["id"], task_plan_id=plan_id, proposed_task_ref="downstream"
    )

    result = store.prepare_workflow_startup(False)

    assert result["paused_tasks"] == 2
    assert store.get_task(first_task["id"])["supervision_action"] == "startup_hold"
    assert store.get_task(parallel_task["id"])["supervision_action"] == "startup_hold"
    waiting = store.get_task(downstream_task["id"])
    assert waiting["status"] == "pending"
    assert waiting["control_state"] == "active"
    assert waiting["supervision_action"] is None
    assert store.task_scheduling(project["id"], downstream_task["id"])["pending_reason"] == "waiting_dependency"
    assert store.task_scheduling(project["id"], first_task["id"])["pending_reason"] == "startup_hold"

    store.resume_task(first_task["id"])

    assert store.get_task(first_task["id"])["control_state"] == "active"
    assert store.get_task(parallel_task["id"])["control_state"] == "active"
    released = [
        event
        for event in store.events(project_id=project["id"])
        if event["type"] == "task.startup_hold_released"
    ]
    assert {event["task_id"] for event in released} == {
        first_task["id"],
        parallel_task["id"],
    }

    with store.connect() as db:
        db.execute(
            "UPDATE v1_tasks SET status='done',finished_at=updated_at WHERE id=?",
            (first_task["id"],),
        )
    store._resolve_planned_dependencies()

    scheduling = store.task_scheduling(project["id"], downstream_task["id"])
    assert scheduling["state"] == "admitted"
    assert scheduling["pending_reason"] in {"ready", "waiting_wip_or_resource"}
    assert store.get_task(downstream_task["id"])["control_state"] == "active"


def test_startup_releases_legacy_hold_from_dependency_queued_task(store, tmp_path):
    project = store.create_project("legacy startup hold", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    _, revision = publish_planned_workflow(store, project["id"], workflow)
    template = task_plan(revision["id"], workflow)
    first = template.tasks[0].model_copy(update={"proposed_task_ref": "first"})
    downstream = template.tasks[0].model_copy(update={
        "proposed_task_ref": "downstream",
        "dependencies": ["first"],
    })
    plan_id = store.create_task_plan(
        project["id"],
        TaskPlan(
            objective="Migrate an old dependency hold",
            workflow_revision_id=revision["id"],
            tasks=[first, downstream],
        ),
    )["id"]
    store.create_task(project["id"], task_plan_id=plan_id, proposed_task_ref="first")
    downstream_task = store.create_task(
        project["id"], task_plan_id=plan_id, proposed_task_ref="downstream"
    )
    with store.connect() as db:
        db.execute(
            "UPDATE v1_tasks SET control_state='paused',supervision_action='startup_hold' WHERE id=?",
            (downstream_task["id"],),
        )

    result = store.prepare_workflow_startup(False)

    assert result["dependency_tasks_released"] == 1
    migrated = store.get_task(downstream_task["id"])
    assert migrated["control_state"] == "active"
    assert migrated["supervision_action"] is None
