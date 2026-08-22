from __future__ import annotations

import pytest

from app.v1.domain import WorkflowDefinition
from tests.helpers import create_planned_task, orchestration_plan, publish_planned_workflow, sequence_workflow


def test_filesystem_loops_are_discoverable_and_novel_is_a_directed_graph(store):
    loops = store.list_loops()
    assert {item["loop_key"] for item in loops} >= {
        "novel.production",
        "software.gitlab_devops",
    }

    novel = store.get_loop("novel.production")
    assert novel["directory"] == "novel-production"
    assert novel["version"] == "1.0.0"
    assert "independent-review" in novel["tags"]
    assert "# Long-form Novel Production" in novel["meta"]
    workflow = WorkflowDefinition.model_validate(novel["bundle"]["workflow"])
    transitions = {
        (column.key, transition.outcome, transition.target)
        for column in workflow.columns
        for transition in column.transitions
    }
    assert ("review", "chapter_rejected", "write") in transitions
    assert ("review", "recap_rejected", "recap") in transitions
    assert ("review", "foundation_invalid", "foundation") in transitions
    assert workflow.column("write").metadata["agent_session_key"] == "chapter_writer"
    assert workflow.column("recap").metadata["writable_paths"] == [
        {"$ref": "/input/task/input/summary_path"}
    ]
    assert workflow.column("write").metadata["writable_paths"] == [
        {"$ref": "/input/task/input/body_path"}
    ]


def test_preset_loop_definitions_are_not_stored_in_sqlite(store):
    with store.connect() as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "v1_workflow_templates" not in tables
    assert "v1_project_template_applications" not in tables
    assert "v1_loop_applications" in tables


def test_workflow_revision_requires_loop_provenance(store, tmp_path):
    project = store.create_project("legacy workflow", "", str(tmp_path / "legacy"))
    workflow = sequence_workflow()
    plan = store.create_orchestration_plan(project["id"], orchestration_plan(workflow))
    now = "2026-08-21T00:00:00+00:00"
    with store.tx(immediate=True) as db:
        db.execute(
            "INSERT INTO v1_workflows(id,project_id,name,active_revision_id,state_version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("workflow_legacy", project["id"], workflow.name, "wfrev_legacy", 1, now, now),
        )
        db.execute(
            "INSERT INTO v1_workflow_revisions(id,project_id,revision,definition_json,active,created_at,"
            "workflow_id,revision_no,schema_version,definition_hash,orchestration_plan_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("wfrev_legacy", project["id"], 1, workflow.model_dump_json(), 1, now,
             "workflow_legacy", 1, workflow.schema_version, "legacy", plan["id"]),
        )

    with pytest.raises(ValueError, match="filesystem Loop application"):
        store.publish_workflow(project["id"], workflow, plan["id"])


def test_novel_loop_application_materializes_serial_chapter_tasks(store, tmp_path):
    project = store.create_project("novel", "", str(tmp_path / "novel"))
    application = store.apply_loop(
        project["id"],
        "novel.production",
        {
            "project_title": "问道残卷",
            "premise": "一个被宗门放弃的少年从旧书阁开始修行。",
            "chapter_target_characters": 2000,
            "chapter_max_characters": 20000,
        },
    )

    assert len(application["tasks"]) == 10
    tasks = {task["proposed_task_ref"]: task for task in application["tasks"]}
    assert tasks["chapter_01"]["status"] == "pending"
    assert store.task_scheduling(project["id"], tasks["chapter_01"]["id"])["state"] == "admitted"
    chapter_two = store.task_scheduling(project["id"], tasks["chapter_02"]["id"])
    assert chapter_two["state"] == "queued"
    assert chapter_two["pending_reason"] == "waiting_dependency"
    store.schedule_task(
        project["id"], tasks["chapter_02"]["id"], "queued", 90,
        "novel_chapter_serial", 1, None, None,
    )
    assert store.task_scheduling(project["id"], tasks["chapter_02"]["id"])["auto_admit"] is True
    assert tasks["chapter_10"]["input"]["is_final_chapter"] is True
    active = store.get_workflow(project["id"])
    assert active["source_loop_key"] == "novel.production"
    assert active["source_loop_version"] == "1.0.0"
    assert active["source_loop_digest"] == application["loop"]["digest"]


def test_loop_application_canonicalizes_provider_numeric_strings(store, tmp_path):
    project = store.create_project("provider-shaped bindings", "", str(tmp_path / "provider-shaped"))

    application = store.apply_loop(
        project["id"],
        "novel.production",
        {
            "project_title": "Provider shaped novel",
            "premise": "A domain-neutral continuous story.",
            "chapter_target_characters": "2000",
            "chapter_max_characters": "20000",
        },
    )

    assert application["bindings"]["chapter_target_characters"] == 2000
    assert application["bindings"]["chapter_max_characters"] == 20000
    assert application["tasks"][0]["input"]["chapter_target_characters"] == 2000
    assert application["tasks"][0]["input"]["chapter_max_characters"] == 20000


def test_failed_task_reopen_reuses_identity_and_returns_to_graph_entry(store, tmp_path):
    project = store.create_project("reopen", "", str(tmp_path / "reopen"))
    workflow = sequence_workflow()
    plan, _revision = publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "deliver", plan_id=plan["id"])
    failed = store.route_task_to_failed(task["id"], "temporary provider failure")
    assert failed["status"] == "failed"

    reopened = store.reopen_task(task["id"])
    assert reopened["id"] == task["id"]
    assert reopened["status"] == "pending"
    assert reopened["current_column"] == workflow.entry
    assert reopened["error"] is None
    assert len(store.runs(project["id"], task["id"])) == 2
    assert any(event["type"] == "task.reopened" for event in store.events(task_id=task["id"]))


def test_writer_rework_reuses_one_logical_agent_session(store, tmp_path):
    project = store.create_project("writer-session", "", str(tmp_path / "writer-session"))
    application = store.apply_loop(
        project["id"],
        "novel.production",
        {
            "project_title": "问道残卷",
            "premise": "一个被宗门放弃的少年从旧书阁开始修行。",
            "chapter_target_characters": 2000,
            "chapter_max_characters": 20000,
        },
    )
    task = application["tasks"][0]
    first = store.get_or_create_agent_session(project["id"], task["id"], "chapter_writer")
    store.suspend_agent_session(project["id"], first["id"], task["id"])
    resumed = store.get_or_create_agent_session(project["id"], task["id"], "chapter_writer")
    independent_reviewer = store.get_or_create_agent_session(project["id"], task["id"], "chapter_reviewer")

    assert resumed["id"] == first["id"]
    assert independent_reviewer["id"] != first["id"]
    session_events = [event["type"] for event in store.events(task_id=task["id"])]
    assert "agent.session.created" in session_events
    assert "agent.session.suspended" in session_events
    assert "agent.session.resumed" in session_events
