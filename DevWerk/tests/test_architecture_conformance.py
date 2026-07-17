from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import build_core_registry
from app.v1.domain import (
    AgentExecutor,
    AgentModelResponse,
    AgentToolCall,
    ColumnDefinition,
    Transition,
    WaitPolicy,
    WorkflowDefinition,
)
from app.v1.files import ProjectFiles
from app.v1.runtime import WorkflowRuntime
from tests.helpers import readiness, terminals


def test_readiness_is_a_required_dispatch_fact(store, tmp_path):
    project = store.create_project("readiness", "", str(tmp_path / "project"))
    from tests.helpers import sequence_workflow

    store.publish_workflow(project["id"], sequence_workflow())
    with pytest.raises(ValueError, match="dispatch readiness"):
        store.create_task(project["id"], "held", "", {}, readiness(decision="hold"))
    task = store.create_task(project["id"], "ready", "", {}, readiness())
    assert task["readiness"]["decision"] == "dispatch"
    runs = store.runs(project["id"], task["id"])
    assert [(item["column_key"], item["status"]) for item in runs] == [("execute", "pending")]


def test_concrete_glob_match_cannot_escape_through_symlink(tmp_path):
    root = tmp_path / "project"
    outside = tmp_path / "outside.txt"
    root.mkdir()
    outside.write_text("secret", encoding="utf-8")
    link = root / "linked.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows account")
    files = ProjectFiles(str(root))
    with pytest.raises(ValueError, match="escapes"):
        files.existing_texts("**/*")
    with pytest.raises(ValueError, match="escapes"):
        files.list_paths("**/*")


def test_provider_transient_error_retries_inside_one_agent_run(store, tmp_path):
    project = store.create_project("provider retry", "", str(tmp_path / "project"))
    attempts = 0

    def model(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TimeoutError("temporary transport timeout")
        return AgentModelResponse(text="recovered")

    result = AgentCore(store, build_core_registry(), model).run(
        AgentRunSpec(
            kind="conversation", project=project, instruction="", instruction_revision=1,
            context={}, capability_ids=[], provider_max_attempts=3,
        )
    )
    assert result.status == "succeeded"
    assert attempts == 3


def test_durable_await_handle_resumes_by_declared_outcome(store, tmp_path):
    project = store.create_project("await", "", str(tmp_path / "project"))
    (tmp_path / "project" / "ready.txt").write_text("ready", encoding="utf-8")
    workflow = WorkflowDefinition(
        name="async work",
        entry="external",
        columns=[
            ColumnDefinition(
                key="external", name="External", instruction="Start or poll external work.",
                executor=AgentExecutor(capabilities=["project.files.read"], max_iterations=2, max_tool_calls=2),
                wait_policy=WaitPolicy(timeout_seconds=60, heartbeat_seconds=5, stale_after_seconds=10),
                transitions=[Transition(outcome="success", target="done"), Transition(outcome="failure", target="failed")],
            ),
            *terminals(),
        ],
    )
    store.publish_workflow(project["id"], workflow)
    task = store.create_task(project["id"], "external", "", {}, readiness())

    model = lambda *_args, **_kwargs: AgentModelResponse(tool_calls=[AgentToolCall(
        id="await", name="column.await",
        arguments={"provider": "generic", "poll_capability": "project.files.read", "poll_arguments": {"path": "ready.txt"}, "next_check_seconds": 5},
    )])
    runtime = WorkflowRuntime(store, build_core_registry(), "test-worker", AgentCore(store, build_core_registry(), model))
    runtime.step(task["id"])
    assert store.get_task(task["id"])["status"] == "waiting"
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_await_handles SET next_check_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    handle = store.due_await_handles()[0]
    runtime.reconcile_await(handle)
    resumed = store.get_task(task["id"])
    assert resumed["status"] == "pending"
    assert resumed["current_column"] == "done"
    runtime.step(task["id"])
    terminal = store.get_task(task["id"])
    assert terminal["status"] == "done"
    assert terminal["terminal_artifact_id"]
    assert terminal["notified_at"]


def test_oversized_agent_payload_is_offloaded_from_sqlite(store, tmp_path):
    project = store.create_project("bounded", "", str(tmp_path / "project"))
    run = store.begin_agent_run(
        project_id=project["id"], kind="conversation", instruction_revision=1,
        instruction_snapshot="", context_snapshot={}, capabilities=[],
    )
    store.add_agent_message(run["id"], "assistant", "x" * 200_000)
    message = store.agent_messages(project["id"], run["id"])[0]
    assert "$artifact_ref" in message["content"]
    with store.connect() as db:
        stored_size = db.execute("SELECT length(content) FROM v1_agent_messages WHERE agent_run_id=?", (run["id"],)).fetchone()[0]
    assert stored_size < 1000
