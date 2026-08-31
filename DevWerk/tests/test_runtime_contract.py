from __future__ import annotations

import hashlib
import json
import sys

import pytest
import requests

from app.services.provider_errors import (
    LLMProviderError,
    ProviderErrorDetails,
    provider_timeout_error,
)
from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import build_core_registry
from app.v1.domain import (
    AgentModelResponse,
    AgentToolCall,
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    ExactTaskInputString,
    Transition,
    WorkflowDefinition,
)
from app.v1.runtime import RuntimeExecutionError, WorkflowRuntime
from tests.helpers import agent_workflow, create_planned_task, publish_initial_workflow, publish_planned_workflow, sequence_workflow, task_plan, workflow_plan


def test_capability_sequence_reaches_done_without_calling_llm(store, tmp_path):
    project = store.create_project("deterministic", "", str(tmp_path / "project"))
    _, revision = publish_planned_workflow(store, project["id"], sequence_workflow(content="domain-neutral"))
    task = create_planned_task(store, project["id"], "task")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("LLM must not be called by capability_sequence")

    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, forbidden))
    runtime.step(task["id"])

    finished = store.get_task(task["id"])
    assert revision["id"] == finished["workflow_revision_id"]
    assert finished["status"] == "done"
    assert finished["current_column"] == "done"
    assert (tmp_path / "project" / "result.txt").read_text(encoding="utf-8") == "domain-neutral"
    assert [item["column_key"] for item in store.runs(project["id"], task["id"])] == ["execute"]
    assert store.events(task_id=task["id"])[-1]["type"] == "task.done"
    assert store.mailbox(project["id"])[-1]["event_type"] == "task.done"


def test_runtime_artifact_context_is_bounded_and_deduplicated_across_globs(store, tmp_path):
    project = store.create_project("bounded context", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    workflow.columns[0].context.artifact_globs = ["*.md", "**/*.md"]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "bounded")
    (tmp_path / "project" / "a.md").write_text("a" * 100, encoding="utf-8")
    (tmp_path / "project" / "nested").mkdir()
    (tmp_path / "project" / "nested" / "b.md").write_text("b" * 100, encoding="utf-8")
    (tmp_path / "project" / "oversized.md").write_text(
        "x" * (store.policy.context.artifact_context_max_characters + 1),
        encoding="utf-8",
    )

    context = WorkflowRuntime(store, build_core_registry(), "context-worker")._input_for(
        task,
        workflow,
        workflow.columns[0],
    )

    paths = [item["path"] for item in context["artifacts"]]
    assert paths == ["a.md", "nested/b.md"]
    assert len(paths) == len(set(paths))
    assert sum(len(item["content"]) for item in context["artifacts"]) <= (
        store.policy.context.artifact_context_max_characters
    )
    manifest = context["context_manifest"]
    assert manifest["preloaded_content_is_authoritative"] is True
    assert [item["path"] for item in manifest["preloaded_project_artifacts"]] == paths
    assert all(item["sha256"] for item in manifest["preloaded_project_artifacts"])


def test_each_completed_column_makes_the_next_column_runnable(store, tmp_path):
    project = store.create_project("column trigger", "", str(tmp_path / "project"))
    workflow = WorkflowDefinition(
        name="two stage delivery",
        entry="prepare",
        columns=[
            ColumnDefinition(
                key="prepare",
                name="Prepare",
                executor=CapabilitySequenceExecutor(steps=[
                    CapabilityStep(
                        capability="project.files.write",
                        arguments={"path": "prepared.txt", "content": "ready"},
                    )
                ]),
                transitions=[
                    Transition(outcome="success", target="deliver"),
                    Transition(outcome="failure", target="failed"),
                ],
            ),
            ColumnDefinition(
                key="deliver",
                name="Deliver",
                executor=CapabilitySequenceExecutor(steps=[
                    CapabilityStep(
                        capability="project.files.write",
                        arguments={"path": "delivered.txt", "content": "done"},
                    )
                ]),
                transitions=[
                    Transition(outcome="success", target="done"),
                    Transition(outcome="failure", target="failed"),
                ],
            ),
        ],
    )
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "two stages")
    runtime = WorkflowRuntime(store, build_core_registry(store.policy), "worker")

    assert store.runnable_task_ids() == [task["id"]]
    runtime.step(task["id"])
    after_prepare = store.get_task(task["id"])
    assert after_prepare["status"] == "pending"
    assert after_prepare["current_column"] == "deliver"
    assert task["id"] in store.runnable_task_ids()

    runtime.step(task["id"])
    finished = store.get_task(task["id"])
    assert finished["status"] == "done"
    assert finished["current_column"] == "done"
    assert [run["column_key"] for run in store.runs(project["id"], task["id"])] == [
        "prepare",
        "deliver",
    ]


def test_provider_timeout_recovers_same_task_and_column(store, tmp_path):
    project = store.create_project("recover provider timeout", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], agent_workflow())
    task = create_planned_task(store, project["id"], "recover me")
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            raise provider_timeout_error(
                requests.Timeout("read timed out"),
                provider="test",
                api_name="test",
                timeout_seconds=600,
            )
        if turn == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="write-after-recovery",
                name="project.files.write",
                arguments={"path": "recovered.txt", "content": "recovered"},
            )])
        evidence_id = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="complete-after-recovery",
            name="column.complete",
            arguments={
                "outcome": "success",
                "output": {"delivered": True},
                "summary": "recovered",
                "evidence_ids": [evidence_id],
            },
        )])

    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, model))
    runtime.step(task["id"])

    recovering = store.get_task(task["id"])
    assert recovering["id"] == task["id"]
    assert recovering["status"] == "recovering"
    assert recovering["current_column"] == "work"
    assert recovering["terminal_artifact_id"] is None
    assert recovering["next_retry_at"]
    assert store.runnable_task_ids() == []
    assert store.project_quiescence(project["id"])["blockers"]["nonterminal_tasks"] == 1

    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_tasks SET next_retry_at='2000-01-01T00:00:00.000+00:00' WHERE id=?",
            (task["id"],),
        )
    assert store.runnable_task_ids() == [task["id"]]

    runtime.step(task["id"])

    finished = store.get_task(task["id"])
    assert finished["id"] == task["id"]
    assert finished["status"] == "done"
    assert finished["supervision_action"] is None
    assert (tmp_path / "project" / "recovered.txt").read_text(encoding="utf-8") == "recovered"
    event_types = [event["type"] for event in store.events(task_id=task["id"])]
    assert "task.recovering" in event_types
    assert "task.recovery_started" in event_types
    assert "task.recovered" in event_types
    assert event_types[-1] == "task.done"
    assert [run["column_key"] for run in store.runs(project["id"], task["id"])] == ["work", "work"]


def test_token_limit_remains_terminal(store, tmp_path):
    project = store.create_project("terminal token limit", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], agent_workflow())
    task = create_planned_task(store, project["id"], "must stop")

    def model(_messages, _tools, **_kwargs):
        raise LLMProviderError(ProviderErrorDetails(
            provider="test",
            api_name="test",
            status_code=200,
            error_code="LLM_TOKEN_LIMIT",
            message="token plan limit",
        ))

    registry = build_core_registry()
    with pytest.raises(LLMProviderError, match="token plan limit"):
        WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, model)).step(task["id"])

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert failed["terminal_artifact_id"]
    assert "task.recovering" not in [event["type"] for event in store.events(task_id=task["id"])]


def test_task_admission_rejects_missing_dynamic_reference_before_runtime(
    store,
    tmp_path,
):
    project = store.create_project(
        "missing runtime reference",
        "",
        str(tmp_path / "project"),
    )
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps[0].arguments["path"] = {
        "$ref": "/input/task/input/contract/missing_path"
    }
    publish_planned_workflow(store, project["id"], workflow)
    with pytest.raises(ValueError, match="Task input cannot resolve Column execute"):
        create_planned_task(store, project["id"], "missing input", input_data={})

    assert store.list_tasks(project["id"]) == []


def test_terminal_guard_enforces_immutable_task_agent_execution_policy(store, tmp_path, monkeypatch):
    project = store.create_project("terminal agent policy", "", str(tmp_path / "project"))
    _plan_row, _revision = publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "task")
    runtime = WorkflowRuntime(store, build_core_registry(), "worker")

    runtime._validate_task_agent_terminal(task, "done")

    original_agent_runs = store.agent_runs
    monkeypatch.setattr(store, "agent_runs", lambda **_kwargs: [{"id": "arun_test"}])
    with pytest.raises(ValueError, match="forbidden"):
        runtime._validate_task_agent_terminal(task, "done")

    original_get_plan = store.get_task_plan
    required_plan = original_get_plan(project["id"], task["task_plan_id"])
    required_plan = {
        **required_plan,
        "plan": {
            **required_plan["plan"],
            "tasks": [
                {**item, "agent_execution": "required"}
                for item in required_plan["plan"]["tasks"]
            ],
        },
    }
    monkeypatch.setattr(
        store,
        "get_task_plan",
        lambda *_args, **_kwargs: required_plan,
    )
    monkeypatch.setattr(store, "agent_runs", lambda **_kwargs: [])
    with pytest.raises(ValueError, match="required"):
        runtime._validate_task_agent_terminal(task, "done")

    monkeypatch.setattr(store, "agent_runs", original_agent_runs)


def test_capability_sequence_routes_file_assertion_mismatch_from_evidence(store, tmp_path):
    project = store.create_project("deterministic assertion", "", str(tmp_path / "project"))
    workflow = sequence_workflow(content="DEVWERK_CASE_A_OK")
    workflow.columns[0].executor.steps.extend(
        [
            CapabilityStep(
                capability="project.files.verify",
                arguments={
                    "path": "result.txt",
                    "expected_content": {"$ref": "/input/task/input/contract/expected_content"},
                    "expected_ends_with_newline": True,
                },
                save_as="verification",
            )
        ]
    )
    workflow.columns[0].executor.completed_outcome = None
    workflow.columns[0].executor.outcome_from = "/steps/verification/output/outcome"
    workflow.columns[0].transitions = [
        workflow.columns[0].transitions[0].model_copy(
            update={"outcome": "matched"}
        ),
        workflow.columns[0].transitions[1].model_copy(
            update={"outcome": "mismatch"}
        ),
        workflow.columns[0].transitions[1],
    ]
    workflow_plan_record = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, workflow_plan_record["id"])
    planned_tasks = store.create_task_plan(
        project["id"],
        task_plan(
            revision["id"],
            workflow,
            title="must reject false success",
            input_data={"contract": {"expected_content": "DEVWERK_CASE_A_OK\n"}},
            exact_input_strings=[
            ExactTaskInputString(
                pointer="/contract/expected_content",
                escaped_value="DEVWERK_CASE_A_OK\\n",
            )
            ],
        ),
    )
    task = create_planned_task(
        store,
        project["id"],
        "must reject false success",
        plan_id=planned_tasks["id"],
    )

    WorkflowRuntime(store, build_core_registry(), "worker").step(task["id"])

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert failed["current_column"] == "failed"
    output = store.runs(project["id"], task["id"])[0]["output"]
    verification = next(
        item for item in output["steps"] if item["save_as"] == "verification"
    )
    assert verification["output"]["outcome"] == "mismatch"
    assert verification["output"]["mismatches"] == [
        "expected_content",
        "expected_ends_with_newline",
    ]


def test_task_owned_exact_text_survives_reference_write_and_verification(store, tmp_path):
    project = store.create_project("lossless task binding", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    column = workflow.columns[0]
    column.executor.steps = [
        CapabilityStep(
            capability="project.files.write",
            arguments={
                "path": {"$ref": "/input/task/input/contract/path"},
                "content": {"$ref": "/input/task/input/contract/content"},
            },
            save_as="write",
        ),
        CapabilityStep(
            capability="project.files.verify",
            arguments={
                "path": {"$ref": "/input/task/input/contract/path"},
                "expected_content": {"$ref": "/input/task/input/contract/content"},
                "expected_sha256": {"$ref": "/input/task/input/contract/sha256"},
                "expected_ends_with_newline": {
                    "$ref": "/input/task/input/contract/ends_with_newline"
                },
            },
            save_as="verification",
        ),
    ]
    column.executor.completed_outcome = None
    column.executor.outcome_from = "/steps/verification/output/outcome"
    column.transitions = [
        column.transitions[0].model_copy(update={"outcome": "matched"}),
        column.transitions[1].model_copy(update={"outcome": "mismatch"}),
        column.transitions[1],
    ]
    exact_content = "LOSSLESS\n"
    exact_digest = hashlib.sha256(exact_content.encode("utf-8")).hexdigest()
    workflow_plan_record = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, workflow_plan_record["id"])
    planned_tasks = store.create_task_plan(
        project["id"],
        task_plan(
            revision["id"],
            workflow,
            title="preserve exact task input",
            input_data={
                "contract": {
                    "path": "exact/result.txt",
                    "content": exact_content,
                    "sha256": exact_digest,
                    "ends_with_newline": True,
                }
            },
            exact_input_strings=[
            ExactTaskInputString(pointer="/contract/path", escaped_value="exact/result.txt"),
            ExactTaskInputString(pointer="/contract/content", escaped_value="LOSSLESS\\n"),
            ExactTaskInputString(pointer="/contract/sha256", escaped_value=exact_digest),
            ],
        ),
    )
    task = create_planned_task(
        store,
        project["id"],
        "preserve exact task input",
        plan_id=planned_tasks["id"],
    )

    WorkflowRuntime(store, build_core_registry(), "worker").step(task["id"])

    assert store.get_task(task["id"])["status"] == "done"
    assert (tmp_path / "project" / "exact" / "result.txt").read_bytes() == b"LOSSLESS\n"
    output = store.runs(project["id"], task["id"])[0]["output"]
    verification = next(
        item for item in output["steps"] if item["save_as"] == "verification"
    )
    assert verification["output"]["outcome"] == "matched"
    assert verification["output"]["actual"]["ends_with_newline"]


def test_ephemeral_column_agent_uses_same_tool_loop_and_declared_contract(store, tmp_path):
    project = store.create_project("agent", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], agent_workflow(instruction="Handle an arbitrary deliverable."))
    task = create_planned_task(store, project["id"], "unknown domain", "do it", {"shape": "unseen"})
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(
                    id="write-1",
                    name="project.files.write",
                    arguments={"path": "arbitrary/output.data", "content": "value"},
                )
            ])
        evidence_id = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
        return AgentModelResponse(tool_calls=[
            AgentToolCall(
                id="complete-1",
                name="column.complete",
                arguments={
                    "outcome": "success",
                    "output": {"delivered": True},
                    "summary": "complete",
                    "evidence_ids": [evidence_id],
                },
            )
        ])

    registry = build_core_registry()
    core = AgentCore(store, registry, model)
    runtime = WorkflowRuntime(store, registry, "worker", core)
    runtime.step(task["id"])

    assert store.get_task(task["id"])["status"] == "done"
    assert (tmp_path / "project" / "arbitrary" / "output.data").read_text(encoding="utf-8") == "value"
    runs = store.agent_runs(project_id=project["id"], task_id=task["id"])
    assert len(runs) == 1
    assert runs[0]["kind"] == "column"
    assert runs[0]["capabilities"] == ["project.files.write", "project.files.read", "column.complete"]
    assert [item["capability"] for item in store.tool_invocations(project["id"], runs[0]["id"])] == ["project.files.write", "column.complete"]


def test_unknown_capability_is_rejected_before_execution(store, tmp_path):
    project = store.create_project("invalid", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps[0].capability = "unknown.capability"
    plan = store.create_workflow_plan(project["id"], workflow_plan(workflow))

    with pytest.raises(ValueError, match="unknown or non-delegable capabilities"):
        publish_initial_workflow(store, project["id"], workflow, plan["id"])

    assert store.list_tasks(project["id"]) == []


def test_capability_sequence_cannot_reach_done_after_failed_command(store, tmp_path):
    project = store.create_project("failed-command", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    workflow.columns[0].executor.steps = [
        CapabilityStep(
            capability="project.command.run",
            arguments={"argv": [sys.executable, "-c", "raise SystemExit(9)"]},
        )
    ]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "must fail")

    with pytest.raises(RuntimeExecutionError, match="exited with code 9"):
        WorkflowRuntime(store, build_core_registry(), "worker").step(task["id"])

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert "exited with code 9" in (failed["error"] or "")
    attempt = store.attempts(project["id"], task["id"])[0]
    assert attempt["checkpoint"]["failed_result"]["output"]["exit_code"] == 9


def test_agent_column_cannot_claim_success_from_failed_capability_evidence(store, tmp_path):
    project = store.create_project("failed agent command", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    workflow.columns[0].executor.capabilities = ["project.command.run"]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "must not claim success")
    turn = 0
    command_evidence = ""

    def model(messages, _tools, **_kwargs):
        nonlocal turn, command_evidence
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="command",
                name="project.command.run",
                arguments={"argv": [sys.executable, "-c", "raise SystemExit(7)"]},
            )])
        if turn == 2:
            command_evidence = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="false-success",
                name="column.complete",
                arguments={
                    "outcome": "success",
                    "output": {"delivered": True},
                    "summary": "unsupported success",
                    "evidence_ids": [command_evidence],
                },
            )])
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="explicit-failure",
            name="column.complete",
            arguments={
                "outcome": "failure",
                "output": {"delivered": False},
                "summary": "command failure recorded",
                "evidence_ids": [command_evidence],
            },
        )])

    registry = build_core_registry()
    with pytest.raises(RuntimeError, match="command exited with code 7"):
        WorkflowRuntime(
            store,
            registry,
            "worker",
            AgentCore(store, registry, model),
        ).step(task["id"])
    assert store.get_task(task["id"])["status"] == "failed"


def test_agent_can_abandon_rejected_write_and_complete_with_valid_evidence(store, tmp_path):
    project = store.create_project("repair rejected write", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    workflow.columns[0].executor.capabilities = ["project.files.write"]
    workflow.columns[0].metadata["writable_paths"] = ["allowed.txt"]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "repair tool input")
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="rejected-write",
                name="project.files.write",
                arguments={"path": "outside.txt", "content": "wrong"},
            )])
        if turn == 2:
            rejected = json.loads(messages[-1]["content"])
            assert rejected["ok"] is False
            assert rejected["checkpoint"]["failure_disposition"] == "rejected_before_effect"
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="valid-write",
                name="project.files.write",
                arguments={"path": "allowed.txt", "content": "corrected"},
            )])
        evidence_id = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="complete-after-correction",
            name="column.complete",
            arguments={
                "outcome": "success",
                "output": {"delivered": True},
                "summary": "invalid write was abandoned before effect",
                "evidence_ids": [evidence_id],
            },
        )])

    registry = build_core_registry()
    WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, model)).step(task["id"])

    assert store.get_task(task["id"])["status"] == "done"
    assert (tmp_path / "project" / "allowed.txt").read_text(encoding="utf-8") == "corrected"
    assert not (tmp_path / "project" / "outside.txt").exists()
    invocations = store.tool_invocations(project["id"], store.agent_runs(project_id=project["id"])[0]["id"])
    assert invocations[0]["ok"] is False
    assert invocations[0]["result"]["checkpoint"]["failure_disposition"] == "rejected_before_effect"


def test_different_successful_operation_does_not_recover_failed_column_action(store, tmp_path):
    project = store.create_project("column operation identity", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    workflow.columns[0].executor.capabilities = ["project.command.run"]
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "preserve failed operation")
    turn = 0
    failed_evidence = ""
    successful_evidence = ""

    def model(messages, _tools, **_kwargs):
        nonlocal turn, failed_evidence, successful_evidence
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="command-a",
                name="project.command.run",
                arguments={"argv": [sys.executable, "-c", "raise SystemExit(7)"]},
            )])
        if turn == 2:
            failed_evidence = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="command-b",
                name="project.command.run",
                arguments={"argv": [sys.executable, "-c", "print('different operation')"]},
            )])
        if turn == 3:
            successful_evidence = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="unsupported-success",
                name="column.complete",
                arguments={
                    "outcome": "success",
                    "output": {"delivered": True},
                    "summary": "wrong operation recovered",
                    "evidence_ids": [successful_evidence],
                },
            )])
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="explicit-failure",
            name="column.complete",
            arguments={
                "outcome": "failure",
                "output": {"delivered": False},
                "summary": "first operation remains failed",
                "evidence_ids": [failed_evidence, successful_evidence],
            },
        )])

    registry = build_core_registry()
    with pytest.raises(RuntimeError, match="command exited with code 7"):
        WorkflowRuntime(
            store,
            registry,
            "worker",
            AgentCore(store, registry, model),
        ).step(task["id"])
    assert store.get_task(task["id"])["status"] == "failed"


def test_agent_repairs_non_final_column_complete_before_later_actions_can_run(store, tmp_path):
    project = store.create_project("column completion ordering", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], agent_workflow())
    task = create_planned_task(store, project["id"], "ordered completion")
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(
                    id="early-complete",
                    name="column.complete",
                    arguments={
                        "outcome": "success",
                        "output": {"delivered": True},
                        "summary": "too early",
                        "evidence_ids": [],
                    },
                ),
                AgentToolCall(
                    id="write-after",
                    name="project.files.write",
                    arguments={"path": "ordered.txt", "content": "written"},
                ),
            ])
        if turn == 2:
            rejected_complete = json.loads(messages[-2]["content"])
            rejected_write = json.loads(messages[-1]["content"])
            assert rejected_complete["error"]["type"] == "ColumnCompletionProtocolError"
            assert rejected_write["error"]["type"] == "ColumnCompletionProtocolError"
            assert "No tool call from this response was executed" in rejected_write["error"]["message"]
            assert not (tmp_path / "project" / "ordered.txt").exists()
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="repaired-write",
                name="project.files.write",
                arguments={"path": "ordered.txt", "content": "written"},
            )])
        evidence_id = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="final-complete",
            name="column.complete",
            arguments={
                "outcome": "success",
                "output": {"delivered": True},
                "summary": "grounded",
                "evidence_ids": [evidence_id],
            },
        )])

    registry = build_core_registry()
    WorkflowRuntime(
        store,
        registry,
        "worker",
        AgentCore(store, registry, model),
    ).step(task["id"])

    assert turn == 3
    assert store.get_task(task["id"])["status"] == "done"
    assert (tmp_path / "project" / "ordered.txt").read_text(encoding="utf-8") == "written"


def test_agent_repairs_multiple_column_complete_calls_without_accepting_either(store, tmp_path):
    project = store.create_project("multiple completions", "", str(tmp_path / "project"))
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(
                    id="first-complete",
                    name="column.complete",
                    arguments={
                        "outcome": "success",
                        "output": {},
                        "summary": "first fragment",
                        "evidence_ids": [],
                    },
                ),
                AgentToolCall(
                    id="second-complete",
                    name="column.complete",
                    arguments={
                        "outcome": "success",
                        "output": {},
                        "summary": "second fragment",
                        "evidence_ids": [],
                    },
                ),
            ])
        if turn == 2:
            first_error = json.loads(messages[-2]["content"])
            second_error = json.loads(messages[-1]["content"])
            assert first_error["error"]["type"] == "ColumnCompletionProtocolError"
            assert second_error["error"]["type"] == "ColumnCompletionProtocolError"
            assert "only one column.complete" in second_error["error"]["message"]
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="work", name="system.noop", arguments={}),
            ])
        evidence_id = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="combined-complete",
            name="column.complete",
            arguments={
                "outcome": "success",
                "output": {},
                "summary": "combined completion",
                "evidence_ids": [evidence_id],
            },
        )])

    registry = build_core_registry()
    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="column",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["system.noop"],
        completion_outcomes={"success"},
        completion_targets={"success": "done"},
    ))

    assert result.status == "succeeded"
    assert result.iterations == 3


def test_agent_repairs_column_complete_combined_with_await(store, tmp_path):
    project = store.create_project("completion and await", "", str(tmp_path / "project"))
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(
                    id="await",
                    name="column.await",
                    arguments={"poll_capability": "system.noop", "token": "later"},
                ),
                AgentToolCall(
                    id="complete",
                    name="column.complete",
                    arguments={
                        "outcome": "failure",
                        "output": {},
                        "summary": "ambiguous",
                        "evidence_ids": [],
                    },
                ),
            ])
        await_error = json.loads(messages[-2]["content"])
        complete_error = json.loads(messages[-1]["content"])
        assert await_error["error"]["type"] == "ColumnCompletionProtocolError"
        assert "mutually exclusive" in complete_error["error"]["message"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="repaired-complete",
            name="column.complete",
            arguments={
                "outcome": "failure",
                "output": {},
                "summary": "explicit failure",
                "evidence_ids": [],
            },
        )])

    registry = build_core_registry()
    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="column",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["system.noop"],
        completion_outcomes={"failure"},
        completion_targets={"failure": "failed"},
        wait_config={"poll_capability": "system.noop"},
    ))

    assert result.status == "succeeded"
    assert result.iterations == 2


def test_agent_can_repair_invalid_column_complete_arguments(store, tmp_path):
    project = store.create_project("repair completion", "", str(tmp_path / "project"))
    turn = 0

    def model(messages, _tools, **_kwargs):
        nonlocal turn
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="work", name="system.noop", arguments={}),
                AgentToolCall(
                    id="invalid-complete",
                    name="column.complete",
                    arguments={
                        "outcome": "success",
                        "output": "not-an-object",
                        "summary": "invalid first attempt",
                        "evidence_ids": [],
                    },
                ),
            ])
        failed_completion = json.loads(messages[-1]["content"])
        assert failed_completion["error"]["message"] == "column.complete output must be an object"
        evidence_id = json.loads(messages[-2]["content"])["evidence"]["evidence_id"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="repaired-complete",
            name="column.complete",
            arguments={
                "outcome": "success",
                "output": {},
                "summary": "repaired",
                "evidence_ids": [evidence_id],
            },
        )])

    registry = build_core_registry()
    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="column",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["system.noop"],
        completion_outcomes={"success"},
        completion_targets={"success": "done"},
    ))

    assert result.status == "succeeded"
    assert result.iterations == 2


def test_agent_can_repair_invalid_custom_completion_without_poisoning_evidence(
    store,
    tmp_path,
):
    project = store.create_project(
        "repair custom completion",
        "",
        str(tmp_path / "project"),
    )
    turn = 0
    write_evidence = ""

    def model(messages, _tools, **_kwargs):
        nonlocal turn, write_evidence
        turn += 1
        if turn == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="write",
                name="project.files.write",
                arguments={"path": "candidate.txt", "content": "ready"},
            )])
        if turn == 2:
            write_evidence = json.loads(messages[-1]["content"])["evidence"]["evidence_id"]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="invalid-signal",
                name="workcell.signal",
                arguments={
                    "outcome": "ready",
                    "output": {},
                    "summary": "missing evidence",
                    "evidence_ids": [],
                },
            )])
        rejected = json.loads(messages[-1]["content"])
        assert rejected["error"]["message"] == (
            "successful Column completion requires capability evidence"
        )
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="repaired-signal",
            name="workcell.signal",
            arguments={
                "outcome": "ready",
                "output": {},
                "summary": "grounded",
                "evidence_ids": [write_evidence],
            },
        )])

    registry = build_core_registry()
    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="column",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["project.files.write"],
        completion_outcomes={"ready"},
        completion_targets={"ready": "next"},
        completion_tool_name="workcell.signal",
        completion_requires_evidence=True,
    ))

    assert result.status == "succeeded"
    assert result.iterations == 3
    assert (tmp_path / "project" / "candidate.txt").read_text(encoding="utf-8") == "ready"


def test_logical_agent_session_replays_only_latest_structured_checkpoint(store, tmp_path):
    project = store.create_project("session checkpoint", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "session task")
    session = store.get_or_create_agent_session(
        project["id"],
        task["id"],
        "participant",
    )
    activation = 0

    def model(messages, _tools, **_kwargs):
        nonlocal activation
        activation += 1
        history_messages = [
            json.loads(item["content"])
            for item in messages
            if item.get("role") == "user"
            and "logical_agent_session_history" in item.get("content", "")
        ]
        if activation == 1:
            assert history_messages == []
        else:
            assert len(history_messages) == 1
            checkpoints = history_messages[0]["logical_agent_session_history"]
            assert len(checkpoints) == 1
            previous_run = json.loads(checkpoints[0]["content"])
            previous = json.loads(previous_run["final_text"])
            assert previous["summary"] == f"checkpoint-{activation - 1}"
            assert f"checkpoint-{activation - 2}" not in checkpoints[0]["content"]
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id=f"signal-{activation}",
            name="workcell.signal",
            arguments={
                "outcome": "ready",
                "output": {"revision": activation},
                "summary": f"checkpoint-{activation}",
                "evidence_ids": [],
            },
        )])

    registry = build_core_registry()
    core = AgentCore(store, registry, model)
    for _ in range(3):
        result = core.run(AgentRunSpec(
            kind="column",
            project=project,
            instruction="",
            instruction_revision=1,
            context={},
            capability_ids=[],
            task_id=task["id"],
            agent_session_id=session["id"],
            completion_outcomes={"ready"},
            completion_targets={"ready": "next"},
            completion_tool_name="workcell.signal",
        ))
        assert result.status == "succeeded"

    checkpoints = store.agent_session_messages(project["id"], session["id"])
    assert len(checkpoints) == 1
    latest_run = json.loads(checkpoints[0]["content"])
    assert json.loads(latest_run["final_text"])["summary"] == "checkpoint-3"


def test_expired_task_lease_interrupts_old_attempt_and_becomes_runnable(store, tmp_path):
    project = store.create_project("recover", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "recover")
    claimed = store.claim_task(task["id"], "dead-worker", lease_seconds=1)
    assert claimed is not None
    run = store.begin_run(claimed, {"task": claimed, "column": "execute"})
    receipt = store.start_execution_receipt(
        project["id"], f"{run['id']}:step:0", "system.noop", {}
    )
    assert receipt["status"] == "started"
    with store.connect() as db:
        db.execute("UPDATE v1_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))

    assert task["id"] in store.runnable_task_ids()
    recovered = store.get_task(task["id"])
    assert recovered["status"] == "recovering"
    assert recovered["lease_owner"] is None
    assert store.runs(project["id"], task["id"])[0]["status"] == "interrupted"
    assert store.attempts(project["id"], task["id"])[0]["status"] == "interrupted"
    with store.connect() as db:
        receipt_status = db.execute(
            "SELECT status FROM v1_execution_receipts WHERE id=?", (receipt["id"],)
        ).fetchone()[0]
    assert receipt_status == "failed"

    reclaimed = store.claim_task(task["id"], "replacement-worker")
    assert reclaimed is not None
    resumed = store.begin_run(reclaimed, {"task": reclaimed, "column": "execute"})
    assert resumed["id"] == run["id"]
    assert resumed["attempt_no"] == 2

    stale_evidence = store.prepare_terminal_evidence(
        claimed,
        run["id"],
        "failed",
        {"summary": "late result from abandoned worker"},
        "late result",
    )
    with pytest.raises(RuntimeError, match="stale Task state_version"):
        store.fail_task_from_exception(
            claimed,
            run["id"],
            "late result",
            stale_evidence,
        )
    fenced = store.get_task(task["id"])
    assert fenced["status"] == "running"
    assert fenced["lease_owner"] == "replacement-worker"


def test_run_output_does_not_recursively_embed_task_context(store, tmp_path):
    project = store.create_project("bounded", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "bounded")
    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker")
    runtime.step(task["id"])

    output = store.runs(project["id"], task["id"])[0]["output"]
    assert "context" not in output
    assert "execute" in store.get_task(task["id"])["context"]
