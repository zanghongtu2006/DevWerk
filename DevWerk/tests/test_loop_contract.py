from __future__ import annotations

import copy

import pytest

from app.v1.capabilities import build_core_registry
from app.v1.contracts import ContractError
from app.v1.domain import TaskPlan, WorkflowDefinition
from app.v1.runtime import WorkflowRuntime
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow, task_plan, workflow_plan


NOVEL_BINDINGS = {
    "project_title": "问道残卷",
    "premise": "一个被宗门放弃的少年从旧书阁开始修行。",
    "chapter_count": 10,
    "chapter_target_characters": 2000,
    "chapter_max_characters": 20000,
}


def novel_task_input(chapter_number: int = 1) -> dict:
    return {
        "chapter_number": chapter_number,
        "summary_path": f"summaries/{chapter_number:02d}.md",
        "body_path": f"chapters/{chapter_number:02d}.md",
        "review_path": f"reviews/{chapter_number:02d}.md",
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
    project_fields = set(novel["parameter_schema"]["properties"])
    task_fields = set(novel["bundle"]["workflow_plan"]["task_contract"]["input_schema"]["properties"])
    dependency_contract = novel["bundle"]["workflow_plan"]["task_contract"]["dependency_contract"]
    assert dependency_contract == {
        "kind": "linear_by_integer_input",
        "order_pointer": "/chapter_number",
        "first_value": 1,
    }
    assert project_fields.isdisjoint(task_fields)
    assert "chapter_count" in project_fields
    assert "is_final_chapter" not in task_fields
    assert len(novel["assets"]) == 10
    assert {asset["path"] for asset in novel["assets"]} == {
        "guides/anti_ai_style.md",
        "guides/body.md",
        "guides/dialogue.md",
        "guides/emotion.md",
        "guides/era.md",
        "guides/outline.md",
        "guides/pacing.md",
        "guides/scene.md",
        "guides/supporting_characters.md",
        "guides/symbols.md",
    }
    serialized = str(novel["bundle"])
    assert "十章" not in serialized
    assert "第十章" not in serialized
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


def test_novel_task_plan_requires_and_materializes_strict_chapter_dependencies(store, tmp_path):
    project = store.create_project("strict novel", "", str(tmp_path / "strict-novel"))
    store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    payload = task_plan(
        active["id"],
        workflow,
        task_ref="chapter_01",
        title="chapter 1",
        input_data=novel_task_input(1),
    ).model_dump(mode="json")
    second = copy.deepcopy(payload["tasks"][0])
    second.update({
        "proposed_task_ref": "chapter_02",
        "title": "chapter 2",
        "input": novel_task_input(2),
        "readiness": {**second["readiness"], "decision": "queue"},
    })
    payload["tasks"].append(second)
    with pytest.raises(ValueError, match="must depend exactly on its linear predecessor"):
        store.create_task_plan(project["id"], TaskPlan.model_validate(payload))

    payload["tasks"][1]["dependencies"] = ["chapter_01"]
    planned = store.create_task_plan(project["id"], TaskPlan.model_validate(payload))
    store.materialize_task_plan(
        project["id"],
        task_plan_id=planned["id"],
        proposed_task_ref="chapter_01",
    )
    tasks = {item["proposed_task_ref"]: item for item in store.list_tasks(project["id"])}
    with store.connect() as db:
        dependency_tokens = db.execute(
            "SELECT dependencies_json FROM v1_scheduling_entries WHERE task_id=?",
            (tasks["chapter_02"]["id"],),
        ).fetchone()[0]
    assert dependency_tokens == f'["task-plan:{planned["id"]}:chapter_01"]'
    assert tasks["chapter_02"]["status"] == "pending"


def test_loop_rejects_duplicate_project_and_task_parameter_ownership(store, monkeypatch):
    invalid = copy.deepcopy(store.loops.get("novel.production"))
    task_schema = invalid["bundle"]["workflow_plan"]["task_contract"]["input_schema"]
    task_schema["properties"]["project_title"] = {"type": "string"}
    monkeypatch.setattr(store.loops, "get", lambda _loop_key: invalid)

    with pytest.raises(ValueError, match="both Project bindings and Task input.*project_title"):
        store.get_loop("novel.production")


def test_runtime_separates_project_loop_bindings_from_task_input(store, tmp_path):
    project = store.create_project("context ownership", "", str(tmp_path / "context"))
    store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    planned = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"],
            workflow,
            task_ref="chapter_01",
            title="第一章",
            input_data=novel_task_input(1),
        ),
    )
    task = store.create_task(project["id"], task_plan_id=planned["id"], proposed_task_ref="chapter_01")

    context = WorkflowRuntime(store, build_core_registry(), "context-test")._input_for(
        task,
        workflow,
        workflow.column("foundation"),
    )

    assert context["project"]["loop"]["key"] == "novel.production"
    assert context["project"]["loop"]["bindings"] == NOVEL_BINDINGS
    assert len(context["project"]["loop"]["assets"]) == 10
    assert all(asset["content"].strip() for asset in context["project"]["loop"]["assets"])
    assert context["task"]["input"] == novel_task_input(1)
    assert set(context["project"]["loop"]["bindings"]).isdisjoint(context["task"]["input"])

    invalid = task_plan(
        active["id"],
        workflow,
        task_ref="chapter_02",
        title="第二章",
        input_data={**novel_task_input(2), "project_title": "冲突小说"},
    )
    with pytest.raises(ContractError, match="project_title"):
        store.create_task_plan(project["id"], invalid)


def test_loop_digest_includes_method_assets(tmp_path):
    from app.v1.loops import LoopCatalog

    root = tmp_path / "loops"
    source = LoopCatalog().root / "novel-production"
    import shutil
    shutil.copytree(source, root / "novel-production")
    catalog = LoopCatalog(root)
    before = catalog.get("novel.production")["digest"]
    asset = root / "novel-production" / "assets" / "guides" / "pacing.md"
    asset.write_text(asset.read_text(encoding="utf-8") + "\n补充规则。\n", encoding="utf-8")
    assert catalog.get("novel.production")["digest"] != before



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
