from __future__ import annotations

import json
import threading

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import CapabilityContext, CapabilityEntry, build_core_registry
from app.v1.conversation import ConversationAgent
from app.v1.domain import AgentModelResponse, AgentToolCall
from tests.helpers import (
    create_planned_task,
    publish_planned_workflow,
    sequence_workflow,
)


def test_conversation_selects_loop_creates_workflow_and_finishes_with_plain_text(store, tmp_path):
    project = store.create_project("conversation", "", str(tmp_path / "project"), "project instruction")
    calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id="loops", name="loop.list", arguments={"query": "GitLab software delivery"})])
        if calls == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="apply",
                name="loop.apply",
                arguments={
                    "loop_key": "software.gitlab_devops",
                    "bindings": {
                        "product_name": "Managed delivery",
                        "requirements_path": "docs/requirements.md",
                        "requirements_confirmed": True,
                        "gitlab_repository": "group/project",
                    },
                },
            )])
        return AgentModelResponse(text="Workflow and Task are now tracked.")

    wakes: list[bool] = []
    registry = build_core_registry()
    agent = ConversationAgent(store, registry, on_task_created=lambda: wakes.append(True), workers=1, agent_core=AgentCore(store, registry, model))
    try:
        accepted = agent.submit(project["id"], "Please manage this delivery.", True)
        assert agent.wait_for_idle()
        job = store.get_conversation_job(accepted["job"]["id"])
        assert job["status"] == "succeeded"
        assert job["result"]["reply"] == "Workflow and Task are now tracked."
        assert len(job["result"]["task_ids"]) == 1
        assert wakes == [True]
    finally:
        agent.stop()


def test_start_task_false_exposes_only_read_capabilities(store, tmp_path):
    project = store.create_project("discussion", "", str(tmp_path / "project"))
    exposed: list[set[str]] = []

    def model(_messages, tools, **_kwargs):
        exposed.append({item["function"]["name"] for item in tools})
        return AgentModelResponse(text="Discussion complete.")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=1, agent_core=AgentCore(store, registry, model))
    try:
        accepted = agent.submit(project["id"], "Only discuss this.", False)
        assert agent.wait_for_idle()
        assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
        assert all(registry.side_effect_kind(item) in {"none", "read"} for item in exposed[0])
    finally:
        agent.stop()


def test_runtime_notifications_are_not_replayed_as_conversation_history(store, tmp_path):
    project = store.create_project("clean history", "", str(tmp_path / "project"))
    store.add_message(project["id"], "assistant", "automatic runtime report", {"kind": "notification", "status": "succeeded"})

    def model(messages, _tools, **_kwargs):
        assert all("automatic runtime report" not in str(item.get("content") or "") for item in messages)
        return AgentModelResponse(text="Discussion complete.")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=1, agent_core=AgentCore(store, registry, model))
    try:
        accepted = agent.submit(project["id"], "Discuss the current state.", False)
        assert agent.wait_for_idle()
        assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    finally:
        agent.stop()


def test_executing_conversation_cannot_finish_with_unevidenced_prose(store, tmp_path):
    project = store.create_project("evidenced execution", "", str(tmp_path / "project"))
    turns = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal turns
        turns += 1
        return AgentModelResponse(text="I inspected and changed the project.")

    registry = build_core_registry()
    agent = ConversationAgent(
        store,
        registry,
        workers=1,
        agent_core=AgentCore(store, registry, model),
    )
    try:
        accepted = agent.submit(project["id"], "Inspect before acting.", True)
        assert agent.wait_for_idle()
        job = store.get_conversation_job(accepted["job"]["id"])
        assert job["status"] == "failed"
        assert turns == 1
        assert job["result"]["action_ledger"] == []
        assert store.conversation_agent(project["id"])["state"] == "attention"
    finally:
        agent.stop()


def test_same_project_jobs_remain_ordered_with_multiple_workers(store, tmp_path):
    project = store.create_project("ordered", "", str(tmp_path / "project"))
    first_entered = threading.Event()
    release_first = threading.Event()
    observed: list[str] = []

    def model(messages, _tools, **_kwargs):
        current = json.loads(messages[0]["content"])["context"]["current_request"]["content"]
        observed.append(current)
        if current == "first":
            first_entered.set()
            assert release_first.wait(timeout=3)
        return AgentModelResponse(text=f"Handled {current}.")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=2, agent_core=AgentCore(store, registry, model))
    try:
        first = agent.submit(project["id"], "first", False)
        assert first_entered.wait(timeout=3)
        second = agent.submit(project["id"], "second", False)
        release_first.set()
        assert agent.wait_for_idle(timeout=10)
        assert store.get_conversation_job(first["job"]["id"])["status"] == "succeeded"
        assert store.get_conversation_job(second["job"]["id"])["status"] == "succeeded"
        assert observed == ["first", "second"]
    finally:
        release_first.set()
        agent.stop()


def test_capability_validation_failure_is_returned_for_model_repair(store, tmp_path):
    project = store.create_project("failure", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.failure",
        description="Raise an observable failure.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        handler=lambda _args, _ctx: (_ for _ in ()).throw(ValueError("visible failure")),
        side_effect_kind="read",
    ))

    calls = 0

    def model(messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id="fail", name="test.failure", arguments={})])
        assert '"ok": false' in messages[-1]["content"]
        assert "visible failure" in messages[-1]["content"]
        return AgentModelResponse(text="The tool rejection was observed.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.failure"],
    ))
    assert result.status == "succeeded"
    run = store.agent_runs(project_id=project["id"])[0]
    assert run["status"] == "succeeded"
    invocation = store.tool_invocations(project["id"], run["id"])[0]
    assert invocation["ok"] is False
    assert invocation["result"]["error"]["message"] == "visible failure"


def test_unavailable_capability_is_returned_for_model_repair(store, tmp_path):
    project = store.create_project("unavailable-tool", "", str(tmp_path / "project"))
    registry = build_core_registry()
    calls = 0

    def model(messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="unknown", name="mailbox", arguments={"id": 12}),
            ])
        assert '"ok": false' in messages[-1]["content"]
        assert "CapabilityUnavailable" in messages[-1]["content"]
        assert "mailbox" in messages[-1]["content"]
        return AgentModelResponse(text="The unavailable tool was observed and corrected.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["system.noop"],
    ))

    assert result.status == "succeeded"
    run = store.agent_runs(project_id=project["id"])[0]
    invocation = store.tool_invocations(project["id"], run["id"])[0]
    assert invocation["capability"] == "mailbox"
    assert invocation["ok"] is False
    assert invocation["result"]["error"]["type"] == "CapabilityUnavailable"


def test_missing_project_file_is_returned_for_model_repair(store, tmp_path):
    project = store.create_project("missing-file", "", str(tmp_path / "project"))
    registry = build_core_registry()

    result = registry.dispatch(
        "project.files.read",
        {"path": "guides/outline.md"},
        CapabilityContext(project["id"], project, store, agent_run_id="arun_test"),
    )

    assert result.ok is False
    assert result.error["type"] == "FileNotFoundError"
    assert "outline.md" in result.error["message"]


def test_current_request_is_authoritative_and_not_duplicated(store, tmp_path):
    project = store.create_project("history", "", str(tmp_path / "project"))
    captured: list[list[dict]] = []

    def model(messages, _tools, **_kwargs):
        captured.append(messages)
        return AgentModelResponse(text="Acknowledged.")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=1, agent_core=AgentCore(store, registry, model))
    try:
        accepted = agent.submit(project["id"], "current instruction", False)
        assert agent.wait_for_idle()
        assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
        encoded = json.dumps(captured[0], ensure_ascii=False)
        assert encoded.count("current instruction") == 2
    finally:
        agent.stop()


def test_terminal_mailbox_turn_reports_model_text_to_user(store, tmp_path):
    project = store.create_project("terminal", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "visible failure")
    store.route_task_to_failed(task["id"], "synthetic terminal failure")
    calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id="inspect", name="task.inspect", arguments={"task_id": task["id"]})])
        return AgentModelResponse(text="任务失败，原因已核实：synthetic terminal failure")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=1, agent_core=AgentCore(store, registry, model))
    try:
        agent.wake()
        assert agent.wait_for_idle(timeout=10)
        assistant = [item for item in store.messages(project["id"]) if item["role"] == "assistant"]
        assert assistant[-1]["content"] == "任务失败，原因已核实：synthetic terminal failure"
        assert assistant[-1]["meta"]["subject_status"] == "failed"
    finally:
        agent.stop()
