from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import CapabilityContext, build_core_registry
from app.v1.contracts import ContractError
from app.v1.domain import AgentExecutor, AgentModelResponse, CapabilityStep
from app.v1.runtime import RuntimeExecutionError, WorkflowRuntime
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow


def test_agent_execution_contract_has_no_platform_fuses():
    forbidden = {
        "max_iterations",
        "max_model_iterations",
        "max_tool_calls",
        "timeout_seconds",
        "wall_clock_timeout_seconds",
        "provider_max_attempts",
        "max_continuations",
        "direct_effect_limit",
    }

    assert forbidden.isdisjoint(AgentRunSpec.__dataclass_fields__)
    assert forbidden.isdisjoint(AgentExecutor.model_fields)
    with pytest.raises(ValidationError):
        AgentExecutor(capabilities=["system.noop"], max_iterations=16)


def test_provider_failure_is_attempted_once_and_raised(store, tmp_path):
    project = store.create_project("provider failure", "", str(tmp_path / "project"))
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider did not answer")

    with pytest.raises(TimeoutError, match="provider did not answer"):
        AgentCore(store, build_core_registry(), model).run(AgentRunSpec(
            kind="conversation",
            project=project,
            instruction="",
            instruction_revision=1,
            context={},
            capability_ids=[],
            start_task=False,
        ))

    assert calls == 1
    run = store.agent_runs(project_id=project["id"])[0]
    assert run["status"] == "failed"
    assert "provider did not answer" in run["error"]


def test_capability_contract_error_is_not_converted_to_tool_result(store, tmp_path):
    project = store.create_project("capability failure", "", str(tmp_path / "project"))
    context = CapabilityContext(project_id=project["id"], project=project, store=store)

    with pytest.raises(ContractError, match="content"):
        build_core_registry().dispatch("project.files.write", {"path": "missing.txt"}, context)


def test_runtime_failure_is_persisted_and_re_raised(store, tmp_path):
    project = store.create_project("runtime failure", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps = [CapabilityStep(
        capability="project.command.run",
        arguments={"argv": [sys.executable, "-c", "raise SystemExit(23)"]},
    )]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "surface failure")

    with pytest.raises(RuntimeExecutionError, match="exited with code 23"):
        WorkflowRuntime(store, build_core_registry(), "worker").step(task["id"])

    persisted = store.get_task(task["id"])
    assert persisted["status"] == "failed"
    assert "exited with code 23" in persisted["error"]
    attempt = store.attempts(project["id"], task["id"])[0]
    assert attempt["checkpoint"]["failed_result"]["output"]["exit_code"] == 23


def test_expired_lease_enters_explicit_recovery(store, tmp_path):
    project = store.create_project("expired lease", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "owned task")
    assert store.claim_task(task["id"], "dead-worker", lease_seconds=1)
    with store.connect() as db:
        db.execute(
            "UPDATE v1_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )

    assert task["id"] in store.runnable_task_ids()
    recovered = store.get_task(task["id"])
    assert recovered["status"] == "recovering"
    assert "WorkerLeaseExpired" in recovered["error"]


def test_command_capability_has_no_runtime_timeout_argument(store, tmp_path):
    project = store.create_project("command schema", "", str(tmp_path / "project"))
    context = CapabilityContext(project_id=project["id"], project=project, store=store)
    schema = build_core_registry().input_schema("project.command.run")

    assert "timeout_seconds" not in schema["properties"]
    with pytest.raises(ContractError, match="Additional properties"):
        build_core_registry().dispatch(
            "project.command.run",
            {"argv": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 1},
            context,
        )
