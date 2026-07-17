from __future__ import annotations

from app.v1.capabilities import CapabilityContext, build_core_registry, resolve_references
from tests.helpers import agent_workflow, sequence_workflow, readiness


def test_registry_dispatches_generic_file_capability_and_tracks_artifact(store, tmp_path):
    project = store.create_project("caps", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    result = registry.dispatch(
        "project.files.write",
        {"path": "any/domain.txt", "content": "content"},
        context,
    )

    assert result.ok
    assert result.output["file"]["path"] == "any/domain.txt"
    assert (tmp_path / "project" / "any" / "domain.txt").is_file()


def test_registry_rejects_bad_arguments_and_path_escape(store, tmp_path):
    project = store.create_project("caps", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    missing = registry.dispatch("project.files.write", {"path": "x"}, context)
    escaped = registry.dispatch("project.files.read", {"path": "../outside"}, context)
    assert not missing.ok
    assert missing.error["type"] == "ContractError"
    assert not escaped.ok
    assert "escapes" in escaped.error["message"]


def test_json_references_are_explicit_and_do_not_evaluate_templates():
    scope = {"input": {"task": {"value": "resolved"}}}
    value = resolve_references(
        {"copied": {"$ref": "/input/task/value"}, "literal": "${input.task.value}"},
        scope,
    )
    assert value == {"copied": "resolved", "literal": "${input.task.value}"}


def test_workflow_publish_capability_persists_conversation_generated_data(store, tmp_path):
    project = store.create_project("workflow", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store, start_task=True)
    result = registry.dispatch(
        "workflow.publish",
        {"workflow": sequence_workflow(name="created during dialogue").model_dump(mode="json")},
        context,
    )
    assert result.ok
    assert store.get_workflow(project["id"])["definition"]["name"] == "created during dialogue"


def test_workflow_publish_schema_exposes_live_capability_catalog(store, tmp_path):
    project = store.create_project("catalog", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    schema = registry.schemas(["workflow.publish"], context)[0]["function"]["parameters"]

    agent_capabilities = schema["$defs"]["AgentExecutor"]["properties"]["capabilities"]
    step_capability = schema["$defs"]["CapabilityStep"]["properties"]["capability"]
    assert "capabilities" in schema["$defs"]["AgentExecutor"]["required"]
    assert agent_capabilities["minItems"] == 1
    assert "project.files.write" in agent_capabilities["items"]["enum"]
    assert "project.files.write" in step_capability["enum"]
    assert "task.create" not in agent_capabilities["items"]["enum"]
    assert "novel.writing" not in agent_capabilities["items"]["enum"]


def test_workflow_publish_rejects_conversation_control_capability_for_column(store, tmp_path):
    project = store.create_project("role-boundary", "", str(tmp_path / "project"))
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    workflow = agent_workflow()
    workflow.columns[0].executor.capabilities = ["task.create"]

    result = registry.dispatch(
        "workflow.publish",
        {"workflow": workflow.model_dump(mode="json")},
        context,
    )
    assert not result.ok
    assert "input rejected" in result.error["message"]


def test_conversation_agent_control_tools_preserve_terminal_immutability_and_rerun(store, tmp_path):
    project = store.create_project("control", "", str(tmp_path / "project"))
    store.publish_workflow(project["id"], sequence_workflow())
    task = store.create_task(project["id"], "controlled", "", {}, readiness())
    registry = build_core_registry()
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    failed_route = registry.dispatch("task.fail", {"task_id": task["id"], "reason": "operator intervention"}, context)
    assert failed_route.ok
    assert failed_route.output["current_column"] == "failed"
    retried = registry.dispatch("task.retry", {"task_id": task["id"], "clear_context": True}, context)
    assert not retried.ok
    assert "terminal Tasks are immutable" in retried.error["message"]
    rerun = registry.dispatch("task.rerun", {"task_id": task["id"]}, context)
    assert rerun.ok
    assert rerun.output["id"] != task["id"]
    assert rerun.output["rerun_of_task_id"] == task["id"]
    assert rerun.output["status"] == "pending"
