from __future__ import annotations

import pytest

from app.v1.domain import WorkflowDefinition
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow, task_plan, workflow_plan


NOVEL_BINDINGS = {
    "project_title": "问道残卷",
    "premise": "一个被宗门放弃的少年从旧书阁开始修行。",
    "chapter_target_characters": 2000,
    "chapter_max_characters": 20000,
}


def novel_task_input(chapter_number: int = 1) -> dict:
    return {
        **NOVEL_BINDINGS,
        "chapter_number": chapter_number,
        "summary_path": f"summaries/{chapter_number:02d}.md",
        "body_path": f"chapters/{chapter_number:02d}.md",
        "review_path": f"reviews/{chapter_number:02d}.md",
        "is_final_chapter": chapter_number == 10,
    }


def test_filesystem_loops_are_discoverable_and_novel_is_a_directed_graph(store):
    loops = store.list_loops()
    assert {item["loop_key"] for item in loops} >= {"novel.production", "software.gitlab_devops"}
    novel = store.get_loop("novel.production")
    assert novel["directory"] == "novel-production"
    assert "independent-review" in novel["tags"]
    assert set(novel["bundle"]) == {"defaults", "workflow_plan", "workflow"}
    assert "tasks" not in novel["bundle"]
    assert "task_portfolio" not in novel["bundle"]["workflow_plan"]
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


def test_preset_loop_definitions_are_not_stored_in_sqlite(store):
    with store.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "v1_workflow_templates" not in tables
    assert "v1_loop_applications" not in tables
    assert "v1_orchestration_plans" not in tables
    assert "v1_project_loop_bindings" in tables
    assert "v1_workflow_plans" in tables
    assert "v1_task_plans" in tables


def test_workflow_revision_requires_loop_provenance(store, tmp_path):
    project = store.create_project("legacy workflow", "", str(tmp_path / "legacy"))
    workflow = sequence_workflow()
    plan = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    now = "2026-08-21T00:00:00+00:00"
    with store.tx(immediate=True) as db:
        db.execute(
            "INSERT INTO v1_workflows(id,project_id,name,active_revision_id,state_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("workflow_legacy", project["id"], workflow.name, "wfrev_legacy", 1, now, now),
        )
        db.execute(
            "INSERT INTO v1_workflow_revisions(id,project_id,revision,definition_json,active,created_at,workflow_id,revision_no,schema_version,definition_hash,workflow_plan_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("wfrev_legacy", project["id"], 1, workflow.model_dump_json(), 1, now, "workflow_legacy", 1, workflow.schema_version, "legacy", plan["id"]),
        )
    with pytest.raises(ValueError, match="filesystem Loop application"):
        store.publish_workflow(project["id"], workflow, plan["id"])


def test_loop_application_creates_reusable_workflow_without_tasks(store, tmp_path):
    project = store.create_project("novel", "", str(tmp_path / "novel"))
    application = store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    assert application["workflow_plan"]["plan"]["schema_version"] == "devwerk.workflow-plan.v1"
    assert store.list_tasks(project["id"]) == []
    assert store.list_task_plans(project["id"]) == []
    active = store.get_workflow(project["id"])
    assert active["source_loop_key"] == "novel.production"
    assert active["source_loop_digest"] == application["loop"]["digest"]
    assert active["loop_bindings"] == NOVEL_BINDINGS


def test_multiple_task_plans_can_share_one_workflow_revision(store, tmp_path):
    project = store.create_project("repeatable", "", str(tmp_path / "repeatable"))
    store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    first = store.create_task_plan(project["id"], task_plan(active["id"], workflow, task_ref="chapter_01", title="第一章", input_data=novel_task_input(1)))
    second = store.create_task_plan(project["id"], task_plan(active["id"], workflow, task_ref="chapter_02", title="第二章", input_data=novel_task_input(2)))
    assert first["workflow_revision_id"] == second["workflow_revision_id"] == active["id"]


def test_loop_application_canonicalizes_provider_numeric_strings(store, tmp_path):
    project = store.create_project("provider-shaped bindings", "", str(tmp_path / "provider-shaped"))
    application = store.apply_loop(project["id"], "novel.production", {**NOVEL_BINDINGS, "chapter_target_characters": "2000", "chapter_max_characters": "20000"})
    assert application["bindings"]["chapter_target_characters"] == 2000
    assert application["bindings"]["chapter_max_characters"] == 20000
    assert store.get_workflow(project["id"])["loop_bindings"]["chapter_target_characters"] == 2000


def test_failed_task_reopen_reuses_identity_and_returns_to_graph_entry(store, tmp_path):
    project = store.create_project("reopen", "", str(tmp_path / "reopen"))
    workflow = sequence_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "deliver")
    failed = store.route_task_to_failed(task["id"], "temporary provider failure")
    assert failed["status"] == "failed"
    reopened = store.reopen_task(task["id"])
    assert reopened["id"] == task["id"]
    assert reopened["status"] == "pending"
    assert reopened["current_column"] == workflow.entry
    assert len(store.runs(project["id"], task["id"])) == 2


def test_writer_rework_reuses_one_logical_agent_session(store, tmp_path):
    project = store.create_project("writer-session", "", str(tmp_path / "writer-session"))
    store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    concrete = store.create_task_plan(project["id"], task_plan(active["id"], workflow, task_ref="chapter_01", title="第一章", input_data=novel_task_input(1)))
    task = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="chapter_01")
    first = store.get_or_create_agent_session(project["id"], task["id"], "chapter_writer")
    store.suspend_agent_session(project["id"], first["id"], task["id"])
    resumed = store.get_or_create_agent_session(project["id"], task["id"], "chapter_writer")
    independent_reviewer = store.get_or_create_agent_session(project["id"], task["id"], "chapter_reviewer")
    assert resumed["id"] == first["id"]
    assert independent_reviewer["id"] != first["id"]
