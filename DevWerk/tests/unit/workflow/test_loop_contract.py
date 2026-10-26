from __future__ import annotations

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from app.v1.capabilities import build_core_registry
from app.v1.contracts import ContractError, validate_contract, validate_optional_defaults
from app.v1.domain import TaskPlan, Transition, WorkflowDefinition
from app.v1.execution.runtime import WorkflowRuntime
from app.v1.workflows.loops import LoopCatalog
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow, task_plan, workflow_plan
from tests.support.paths import REPO_ROOT
from tests.support.legacy_loop_fixture import apply_legacy_fixture, legacy_package


NOVEL_BINDINGS = {
    "project_title": "问道残卷",
    "premise": "一个被宗门放弃的少年从旧书阁开始修行。",
    "chapter_count": 10,
    "chapter_target_characters": 2000,
    "chapter_max_characters": 20000,
}


def test_default_loop_catalog_uses_repository_loop_directory():
    catalog = LoopCatalog()
    assert catalog.root == REPO_ROOT / "loops"
    assert catalog.list()


def test_loop_defaults_defer_only_missing_top_level_bindings():
    schema = {
        "type": "object",
        "required": ["user_value"],
        "properties": {
            "user_value": {"type": "string"},
            "settings": {
                "type": "object", "required": ["mode"],
                "properties": {"mode": {"type": "string"}},
            },
        },
        "additionalProperties": False,
    }
    validate_optional_defaults({"settings": {"mode": "ready"}}, schema, label="Loop defaults")
    with pytest.raises(ContractError, match="mode"):
        validate_optional_defaults({"settings": {}}, schema, label="Loop defaults")
    with pytest.raises(ContractError, match="unknown"):
        validate_optional_defaults({"unknown": True}, schema, label="Loop defaults")


def novel_task_input(chapter_number: int = 1) -> dict:
    return {
        "chapter_number": chapter_number,
        "summary_path": f"summaries/{chapter_number:02d}.md",
        "body_path": f"chapters/{chapter_number:02d}.md",
        "review_path": f"reviews/{chapter_number:02d}.md",
    }


def test_filesystem_loops_are_descriptive_guides_without_executable_bundles(store):
    loops = store.list_loops()
    assert {item["loop_key"] for item in loops} >= {
        "novel.production", "software.gitlab_devops", "software.ddd_delivery"
    }
    for item in loops:
        guide = store.get_loop(item["loop_key"])
        assert guide["guide"].startswith("# ")
        assert "bundle" not in guide
        assert "parameter_schema" not in guide
        assert not (REPO_ROOT / "loops" / guide["directory"] / "loop.json").exists()
    novel = store.get_loop("novel.production")
    assert "前情" in novel["guide"]
    assert len(novel["assets"]) == 10
    assert "summaries/*.md" not in novel["guide"]
    assert "chapters/*.md" not in novel["guide"]


def test_workflow_column_rejects_nested_multi_agent_executor():
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate({
            "name": "invalid nested agent graph",
            "entry": "build",
            "terminals": {"success": "done", "failure": "failed"},
            "columns": [{
                "key": "build",
                "name": "Build",
                "executor": {
                    "kind": "workcell",
                    "entry": "write",
                    "participants": [
                        {"key": "first", "kind": "agent", "capabilities": ["system.noop"]},
                        {"key": "second", "kind": "agent", "capabilities": ["system.noop"]},
                    ],
                    "states": [],
                    "terminals": [],
                },
                "transitions": [{"outcome": "success", "target": "done"}],
            }],
        })


def test_ddd_software_delivery_loop_is_guidance_not_a_frozen_graph(store):
    guide = store.get_loop("software.ddd_delivery")
    assert guide["version"] == "2.0.0"
    assert guide["category"] == "software_delivery"
    assert "前后端" in guide["guide"]
    assert "workflow" not in guide
    assert "bundle" not in guide
    assert "scripts/" not in guide["guide"]


def test_preset_loop_definitions_are_not_stored_in_sqlite(store):
    with store.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "v1_workflow_templates" not in tables
    assert "v1_loop_applications" not in tables
    assert "v1_orchestration_plans" not in tables
    assert "v1_project_loop_bindings" in tables
    assert "v1_workflow_plans" in tables
    assert "v1_task_plans" in tables
    assert "v1_workcells" not in tables
    assert "v1_workcell_participants" not in tables
    assert "v1_workcell_handoffs" not in tables


def test_workflow_revision_preserves_absent_loop_provenance(store, tmp_path):
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
    revised = store.publish_workflow(project["id"], workflow, plan["id"])
    assert revised["source_loop_key"] is None
    assert revised["workflow_plan_id"] == plan["id"]


def test_loop_application_creates_reusable_workflow_without_tasks(store, tmp_path):
    project = store.create_project("novel", "", str(tmp_path / "novel"))
    application = apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    assert application["workflow_plan"]["plan"]["schema_version"] == "devwerk.workflow-plan.v1"
    assert store.list_tasks(project["id"]) == []
    assert store.list_task_plans(project["id"]) == []
    active = store.get_workflow(project["id"])
    assert active["source_loop_key"] == "novel.production"
    assert active["source_loop_digest"] == application["loop"]["digest"]
    assert active["loop_bindings"] == {**NOVEL_BINDINGS, 'ending_policy': 'conclude'}


def test_novel_task_plan_requires_and_materializes_strict_chapter_dependencies(store, tmp_path):
    project = store.create_project("strict novel", "", str(tmp_path / "strict-novel"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
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
    with pytest.raises(ValueError, match="must depend exactly on its ordered predecessor"):
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


def test_task_plan_preflight_uses_canonical_chapter_number_for_graph_admission(store, tmp_path):
    from app.v1.capabilities import CapabilityContext, build_core_registry
    from app.v1.tooling.capability_handlers import _task_plan_validate

    project = store.create_project("novel preflight", "", str(tmp_path / "novel-preflight"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    payload = task_plan(active["id"], workflow, task_ref="chapter_01",
        title="chapter 1", input_data=novel_task_input(1)).model_dump(mode="json")
    payload["tasks"][0]["input"]["chapter_number"] = "1"
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    result = _task_plan_validate({"plan": payload}, context, build_core_registry())
    assert result["valid"], result["diagnostics"]
    saved = store.create_task_plan(project["id"], TaskPlan.model_validate(payload))
    assert saved["plan"]["tasks"][0]["input"]["chapter_number"] == 1


def test_linear_task_plan_extends_project_graph_without_recreating_predecessor(store, tmp_path):
    project = store.create_project("incremental novel", "", str(tmp_path / "incremental-novel"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])

    first_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"],
            workflow,
            task_ref="chapter_01",
            title="chapter 1",
            input_data=novel_task_input(1),
        ),
    )
    first = store.materialize_task_plan(
        project["id"],
        task_plan_id=first_plan["id"],
        proposed_task_ref="chapter_01",
    )
    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_tasks SET status='done',current_column='done',finished_at=?,updated_at=? "
            "WHERE id=?",
            ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", first["id"]),
        )

    second_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"],
            workflow,
            task_ref="chapter_02",
            title="chapter 2",
            input_data=novel_task_input(2),
        ),
    )
    second = store.materialize_task_plan(
        project["id"],
        task_plan_id=second_plan["id"],
        proposed_task_ref="chapter_02",
    )

    tasks = store.list_tasks(project["id"])
    assert len(tasks) == 2
    assert {item["logical_task_key"] for item in tasks} == {
        "/chapter_number=1",
        "/chapter_number=2",
    }
    scheduling = store.task_scheduling(project["id"], second["id"])
    assert scheduling["state"] == "admitted"
    assert scheduling["dependencies"] == [{
        "reference": first["id"],
        "task_ref": "chapter_01",
        "resolved_task_id": first["id"],
        "required_terminal": "done",
        "status": "done",
        "satisfied": True,
    }]


def test_novel_recap_activation_excludes_future_plans_and_unaccepted_files(store, tmp_path):
    project_dir = tmp_path / "recap-boundary"
    project = store.create_project(
        "recap boundary", "future chapter events in project description", str(project_dir)
    )
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])

    first_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"], workflow, task_ref="chapter_01", title="chapter 1",
            brief="future plan must not become history", input_data=novel_task_input(1),
        ),
    )
    first = store.materialize_task_plan(
        project["id"], task_plan_id=first_plan["id"], proposed_task_ref="chapter_01"
    )
    chapter_path = project_dir / "chapters" / "01.md"
    chapter_path.parent.mkdir(parents=True)
    chapter_path.write_text("Only this event has happened.", encoding="utf-8")
    chapter_bytes = chapter_path.read_bytes()
    store.register_artifact(
        project["id"], first["id"], None, "chapter", "chapters/01.md",
        hashlib.sha256(chapter_bytes).hexdigest(), len(chapter_bytes),
    )
    prior_summary_path = project_dir / "summaries" / "01.md"
    prior_summary_path.parent.mkdir(parents=True)
    prior_summary_path.write_text("Accepted chapter one recap.", encoding="utf-8")
    prior_summary_bytes = prior_summary_path.read_bytes()
    store.register_artifact(
        project["id"], first["id"], None, "summary", "summaries/01.md",
        hashlib.sha256(prior_summary_bytes).hexdigest(), len(prior_summary_bytes),
    )
    baseline_path = project_dir / "baseline" / "outline.md"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text("A future event planned for chapter nine.", encoding="utf-8")
    baseline_bytes = baseline_path.read_bytes()
    store.register_artifact(
        project["id"], first["id"], None, "baseline", "baseline/outline.md",
        hashlib.sha256(baseline_bytes).hexdigest(), len(baseline_bytes),
    )
    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_tasks SET status='done',current_column='done',finished_at=?,updated_at=? "
            "WHERE id=?",
            ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", first["id"]),
        )

    second_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"], workflow, task_ref="chapter_02", title="future chapter title",
            brief="future chapter objective", input_data=novel_task_input(2),
        ),
    )
    second = store.materialize_task_plan(
        project["id"], task_plan_id=second_plan["id"], proposed_task_ref="chapter_02"
    )
    revised_baseline = project_dir / "baseline" / "characters.md"
    revised_baseline.write_text("A partial current-Task baseline revision.", encoding="utf-8")
    revised_bytes = revised_baseline.read_bytes()
    store.register_artifact(
        project["id"], second["id"], None, "baseline", "baseline/characters.md",
        hashlib.sha256(revised_bytes).hexdigest(), len(revised_bytes),
    )
    stale_path = project_dir / "chapters" / "09.md"
    stale_path.write_text("Unaccepted future workspace draft.", encoding="utf-8")

    recap = workflow.column("recap")
    context = WorkflowRuntime(store, build_core_registry(), "recap-context")._input_for(
        store.get_task(second["id"]), workflow, recap
    )

    assert "current_goal" not in context
    assert set(context["task"]) == {"id", "input"}
    assert set(context["project"]) == {"loop"}
    assert "bindings" not in context["project"]["loop"]
    assert context["project"]["loop"]["assets"]
    assert {item["path"] for item in context["accepted_artifacts"]} == {
        "summaries/01.md", "chapters/01.md"
    }
    assert "working_artifacts" not in context
    validate_contract(context, recap.input_contract, label="recap successor input")
    assert "reference_artifacts" not in context
    assert context["dependencies"][0]["task_id"] == first["id"]
    serialized = str(context)
    assert "Only this event has happened" in serialized
    assert "Accepted chapter one recap" in serialized
    assert "future chapter events in project description" not in serialized
    assert "future chapter objective" not in serialized
    assert "future plan must not become history" not in serialized
    assert "chapter nine" not in serialized
    assert "Unaccepted future workspace draft" not in serialized

    current_summary_path = project_dir / "summaries" / "02.md"
    current_summary_path.write_text("Compressed history for chapter two.", encoding="utf-8")
    current_summary_bytes = current_summary_path.read_bytes()
    store.register_artifact(
        project["id"], second["id"], None, "summary", "summaries/02.md",
        hashlib.sha256(current_summary_bytes).hexdigest(), len(current_summary_bytes),
    )
    authoring_context = WorkflowRuntime(
        store, build_core_registry(), "authoring-context"
    )._input_for(store.get_task(second["id"]), workflow, workflow.column("authoring"))
    assert "accepted_artifacts" not in authoring_context
    assert "upstream_outputs" not in authoring_context
    assert [item["path"] for item in authoring_context["working_artifacts"]] == [
        "summaries/02.md"
    ]
    assert "Only this event has happened" not in str(authoring_context)
    assert "Accepted chapter one recap" not in str(authoring_context)
    assert "A future event planned for chapter nine" not in str(authoring_context)

    second_body_path = project_dir / "chapters" / "02.md"
    second_body_path.write_text("Only chapter two's new event.", encoding="utf-8")
    second_body_bytes = second_body_path.read_bytes()
    store.register_artifact(
        project["id"], second["id"], None, "chapter", "chapters/02.md",
        hashlib.sha256(second_body_bytes).hexdigest(), len(second_body_bytes),
    )
    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_tasks SET status='done',current_column='done',finished_at=?,updated_at=? "
            "WHERE id=?",
            ("2026-09-02T00:00:00+00:00", "2026-09-02T00:00:00+00:00", second["id"]),
        )
    third_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"], workflow, task_ref="chapter_03", title="chapter three",
            input_data=novel_task_input(3),
        ),
    )
    third = store.materialize_task_plan(
        project["id"], task_plan_id=third_plan["id"], proposed_task_ref="chapter_03"
    )
    third_recap_context = WorkflowRuntime(
        store, build_core_registry(), "third-recap-context"
    )._input_for(store.get_task(third["id"]), workflow, recap)
    assert {item["path"] for item in third_recap_context["accepted_artifacts"]} == {
        "summaries/02.md", "chapters/02.md"
    }
    assert "Only this event has happened" not in str(third_recap_context)
    assert "Accepted chapter one recap" not in str(third_recap_context)
    validate_contract(third_recap_context, recap.input_contract, label="recap third input")


def test_novel_recap_requires_complete_root_baseline_and_prior_final_sources(store, tmp_path):
    project_dir = tmp_path / "recap-contract"
    project = store.create_project("recap contract", "", str(project_dir))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    plan = store.create_task_plan(
        project["id"], task_plan(active["id"], workflow, task_ref="chapter_01",
            title="chapter one", input_data=novel_task_input(1)),
    )
    first = store.materialize_task_plan(
        project["id"], task_plan_id=plan["id"], proposed_task_ref="chapter_01"
    )
    recap = workflow.column("recap")
    runtime = WorkflowRuntime(store, build_core_registry(), "recap-contract")
    paths = ["outline.md", "world_and_era.md", "characters.md", "symbols.md",
             "style.md", "long_arc_pacing.md"]
    baseline_dir = project_dir / "baseline"
    baseline_dir.mkdir()
    for name in paths[:-1]:
        path = baseline_dir / name
        path.write_text(name, encoding="utf-8")
        content = path.read_bytes()
        store.register_artifact(project["id"], first["id"], None, "baseline",
            f"baseline/{name}", hashlib.sha256(content).hexdigest(), len(content))
    partial = runtime._input_for(store.get_task(first["id"]), workflow, recap)
    with pytest.raises(ContractError):
        validate_contract(partial, recap.input_contract, label="recap root input")

    last = baseline_dir / paths[-1]
    last.write_text(paths[-1], encoding="utf-8")
    content = last.read_bytes()
    store.register_artifact(project["id"], first["id"], None, "baseline",
        f"baseline/{paths[-1]}", hashlib.sha256(content).hexdigest(), len(content))
    complete = runtime._input_for(store.get_task(first["id"]), workflow, recap)
    assert {item["path"] for item in complete["working_artifacts"]} == {
        f"baseline/{name}" for name in paths
    }
    validate_contract(complete, recap.input_contract, label="recap root input")

    missing_chapter = {"dependencies": [{"task_id": "prior", "status": "done"}],
        "accepted_artifacts": [{"path": "summaries/01.md", "content": "accepted"}]}
    with pytest.raises(ContractError):
        validate_contract(missing_chapter, recap.input_contract, label="recap successor input")


def test_linear_task_plan_rejects_duplicate_project_work_item(store, tmp_path):
    project = store.create_project("identity guard", "", str(tmp_path / "identity-guard"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    first = task_plan(
        active["id"],
        workflow,
        task_ref="chapter_01",
        title="chapter 1",
        input_data=novel_task_input(1),
    )
    planned = store.create_task_plan(project["id"], first)
    store.materialize_task_plan(
        project["id"],
        task_plan_id=planned["id"],
        proposed_task_ref="chapter_01",
    )

    duplicate = first.model_copy(deep=True)
    duplicate.objective = "Try to duplicate the same logical work item"
    with pytest.raises(ValueError, match="already exist in the Project"):
        store.create_task_plan(project["id"], duplicate)


def test_incremental_task_plan_continues_after_legacy_string_order_value(store, tmp_path):
    project = store.create_project("incremental identity", "", str(tmp_path / "incremental-identity"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])
    first_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"], workflow, task_ref="chapter_01", title="chapter 1",
            input_data=novel_task_input(1),
        ),
    )
    first = store.materialize_task_plan(
        project["id"], task_plan_id=first_plan["id"], proposed_task_ref="chapter_01",
    )
    with store.tx(immediate=True) as db:
        legacy_input = novel_task_input(1)
        legacy_input["chapter_number"] = "1"
        db.execute(
            "UPDATE v1_tasks SET input_json=?,logical_task_key=?,status='done',current_column='done' WHERE id=?",
            (
                json.dumps(legacy_input),
                '/chapter_number="1"',
                first["id"],
            ),
        )

    second_plan = store.create_task_plan(
        project["id"],
        task_plan(
            active["id"], workflow, task_ref="chapter_02", title="chapter 2",
            input_data=novel_task_input(2),
        ),
    )
    second = store.materialize_task_plan(
        project["id"], task_plan_id=second_plan["id"], proposed_task_ref="chapter_02",
    )

    assert second["input"]["chapter_number"] == 2
    assert second["logical_task_key"] == "/chapter_number=2"
    scheduling = store.task_scheduling(project["id"], second["id"])
    assert scheduling["dependencies"][0]["resolved_task_id"] == first["id"]
    assert scheduling["dependencies"][0]["satisfied"] is True


def test_novel_task_admission_rejects_chapter_beyond_project_binding(store, tmp_path):
    bindings = {**NOVEL_BINDINGS, "chapter_count": 4}
    project = store.create_project("bounded novel", "", str(tmp_path / "bounded-novel"))
    apply_legacy_fixture(store, project["id"], "novel.production", bindings)
    active = store.get_workflow(project["id"])
    workflow = WorkflowDefinition.model_validate(active["definition"])

    with pytest.raises(ValueError, match="exceeds the Project scope"):
        store.create_task_plan(
            project["id"],
            task_plan(
                active["id"],
                workflow,
                task_ref="chapter_05",
                title="chapter 5",
                input_data=novel_task_input(5),
            ),
        )


def test_loop_inspection_rejects_executable_bundle(tmp_path):
    root = tmp_path / "loops"
    card = root / "unsafe"
    card.mkdir(parents=True)
    source = (REPO_ROOT / "loops" / "novel-production" / "loop.meta").read_text(encoding="utf-8")
    (card / "loop.meta").write_text(source, encoding="utf-8")
    (card / "loop.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="executable bundle"):
        LoopCatalog(root).get("novel.production")


def test_runtime_separates_project_loop_bindings_from_task_input(store, tmp_path):
    project = store.create_project("context ownership", "", str(tmp_path / "context"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
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
    assert context["project"]["loop"]["bindings"] == {**NOVEL_BINDINGS, 'ending_policy': 'conclude'}
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
    from app.v1.workflows.loops import LoopCatalog

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
    application = apply_legacy_fixture(store, project["id"], "novel.production", {**NOVEL_BINDINGS, "chapter_target_characters": "2000", "chapter_max_characters": "20000"})
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


def test_failed_task_reopen_cannot_skip_failed_column(store, tmp_path):
    project = store.create_project("reopen boundary", "", str(tmp_path / "reopen-boundary"))
    workflow = sequence_workflow()
    execute = workflow.column("execute")
    execute.transitions = [Transition(outcome="success", target="review"),
                           Transition(outcome="failure", target="failed")]
    review = copy.deepcopy(execute)
    review.key = "review"
    review.name = "Review"
    review.transitions = [Transition(outcome="success", target="later"),
                          Transition(outcome="failure", target="failed")]
    later = copy.deepcopy(execute)
    later.key = "later"
    later.name = "Later"
    later.transitions = [Transition(outcome="success", target="done"),
                         Transition(outcome="failure", target="failed")]
    workflow.columns.extend([review, later])
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "deliver")

    claimed = store.claim_task(task["id"], "worker")
    run = store.begin_run(claimed, {})
    store.finish_run(claimed, run["id"], {}, "success", "review")
    claimed = store.claim_task(task["id"], "worker")
    run = store.begin_run(claimed, {})
    store.finish_run(claimed, run["id"], {}, "failure", "failed",
                     terminal="failed", error="build failed")
    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    run_count = len(store.runs(project["id"], task["id"]))

    with pytest.raises(ValueError, match="cannot skip the failed Column"):
        store.reopen_task(task["id"], "later")
    assert store.get_task(task["id"])["terminal_event_id"] == failed["terminal_event_id"]
    assert len(store.runs(project["id"], task["id"])) == run_count

    reopened = store.reopen_task(task["id"], "review")
    assert reopened["status"] == "pending"
    assert reopened["current_column"] == "review"
    with pytest.raises(ValueError, match="cannot skip an uncorrected failed Column"):
        store.retry_task(task["id"], "later")
    assert store.get_task(task["id"])["current_column"] == "review"
    rewound = store.retry_task(task["id"], "execute")
    assert rewound["current_column"] == "execute"


def test_writer_rework_reuses_one_logical_agent_session(store, tmp_path):
    project = store.create_project("writer-session", "", str(tmp_path / "writer-session"))
    apply_legacy_fixture(store, project["id"], "novel.production", NOVEL_BINDINGS)
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
