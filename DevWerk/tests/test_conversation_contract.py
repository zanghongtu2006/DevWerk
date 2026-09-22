from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import (
    CapabilityContext,
    CapabilityEntry,
    CapabilityRegistry,
    build_core_registry,
)
from app.v1.conversation import ConversationGateway
from app.v1.domain import AgentModelResponse, AgentToolCall, WorkflowDefinition
from tests.helpers import (
    create_planned_task,
    publish_planned_workflow,
    sequence_workflow,
    task_plan,
)


def run_turn(
    gateway: ConversationGateway,
    project_id: str,
    message: str,
    start_task: bool,
    *,
    timeout: float = 15.0,
) -> dict:
    async def execute() -> dict:
        await gateway.start()
        try:
            accepted = await gateway.submit(project_id, message, start_task)
            assert await gateway.wait_for_idle(timeout=timeout)
            return accepted
        finally:
            await gateway.stop()

    return asyncio.run(execute())


def test_platform_policy_defines_concise_human_facing_replies():
    policy = (Path(__file__).resolve().parents[1] / "DEVWERK.md").read_text(encoding="utf-8")

    assert "Communicate as a human project manager, not as an audit log" in policy
    assert "one to three short sentences" in policy
    assert "at most 300 Chinese characters or 120 English words" in policy
    assert "Runtime records and inspection views rather than ordinary chat" in policy


def test_conversation_selects_loop_creates_workflow_and_finishes_with_plain_text(store, tmp_path):
    project = store.create_project("conversation", "", str(tmp_path / "project"), "project instruction")
    calls = 0

    def model(_messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        exposed = {item["function"]["name"] for item in tools}
        assert "loop.apply" in exposed
        assert "workflow.plan.save" in exposed
        assert "task.plan.save" in exposed
        assert "task.create" in exposed
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
                        "gitlab_repository": "group/project",
                    },
                },
            )])
        if calls == 3:
            active = store.get_workflow(project["id"])
            definition = WorkflowDefinition.model_validate(active["definition"])
            planned = task_plan(
                active["id"],
                definition,
                title="Deliver the managed product",
                input_data={
                    "requirements_path": "docs/requirements.md",
                    "requirements_confirmed": True,
                },
            )
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="task-plan",
                name="task.plan.save",
                arguments={"plan": planned.model_dump(mode="json")},
            )])
        if calls == 4:
            planned = store.list_task_plans(project["id"])[0]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="task",
                name="task.create",
                arguments={"task_plan_id": planned["id"], "proposed_task_ref": "primary"},
            )])
        return AgentModelResponse(text=json.dumps({'mode': 'work_result', 'tool_call_ids': ['apply', 'task-plan', 'task']}))

    wakes: list[bool] = []
    registry = build_core_registry()
    from tests.helpers import resolve_execution_before
    agent = ConversationGateway(store, registry, on_task_created=lambda: wakes.append(True), agent_core=AgentCore(store, registry, resolve_execution_before(model)))
    accepted = run_turn(agent, project["id"], "Please manage this delivery.", True)
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "succeeded"
    assert "Deliver the managed product：待执行" in job["result"]["reply"]
    assert len(job["result"]["task_ids"]) == 1
    assert wakes == [True]


def test_conversation_with_loop_workflow_can_revise_but_cannot_reapply_loop(store, tmp_path):
    project = store.create_project("revision", "", str(tmp_path / "revision"))
    store.apply_loop(
        project["id"],
        "software.gitlab_devops",
        {
            "product_name": "Managed delivery",
            "gitlab_repository": "group/project",
        },
    )
    exposed: list[set[str]] = []
    calls = 0

    def model(_messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        exposed.append({item["function"]["name"] for item in tools})
        assert "conversation.turn.resolve" in exposed[-1]
        assert {"workflow.publish", "workflow.plan.save", "task.plan.save", "task.create"}.isdisjoint(exposed[-1])
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="workflow",
                name="workflow.inspect",
                arguments={},
            )])
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': "The Loop-created Workflow is ready for supervision."}))

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Inspect the existing Workflow.", True)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    assert "loop.apply" not in exposed[0]


def test_start_task_false_hides_mutations_and_rejects_injected_calls(store, tmp_path):
    project = store.create_project("discussion", "", str(tmp_path / "project"))
    exposed: list[set[str]] = []
    calls = 0

    def model(messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        exposed.append({item["function"]["name"] for item in tools})
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="blocked-write",
                name="system.files.write",
                arguments={"path": str(tmp_path / "blocked.txt"), "content": "blocked"},
            )])
        tool_result = json.loads(messages[-1]["content"])
        assert tool_result["error"]["type"] == "ConversationMutationDisabled"
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': "Discussion complete."}))

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Only discuss this.", False)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    assert calls == 2
    assert "conversation.turn.resolve" in exposed[0]
    assert "system.files.write" not in exposed[0]
    assert "system.command.run" not in exposed[0]
    assert exposed[0] == exposed[1]
    assert not (tmp_path / "blocked.txt").exists()


def test_conversation_has_generic_system_file_authority_without_delegating_it_to_columns(store, tmp_path):
    project = store.create_project("system authority", "", str(tmp_path / "project"))
    loop_card = tmp_path / "runtime-library" / "new-loop" / "loop.meta"
    calls = 0

    def model(_messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        exposed = {item["function"]["name"] for item in tools}
        assert {
            "system.files.list",
            "system.files.read",
            "system.files.write",
            "system.files.search",
            "system.command.run",
        } <= exposed
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="write-loop-card",
                name="system.files.write",
                arguments={"path": str(loop_card), "content": "name: reusable-loop\n"},
            )])
        return AgentModelResponse(text=json.dumps({'mode': 'work_result', 'tool_call_ids': ['write-loop-card']}))

    registry = build_core_registry()
    assert not any(item.startswith("system.files.") for item in registry.column_ids())
    assert "system.command.run" not in registry.column_ids()
    from tests.helpers import resolve_execution_before
    agent = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, resolve_execution_before(model)),
    )

    accepted = run_turn(agent, project["id"], "Create this reusable Loop asset.", True)

    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "succeeded"
    assert loop_card.read_text(encoding="utf-8") == "name: reusable-loop\n"
    assert [x['capability'] for x in job['result']['action_ledger'] if x['effect_kind'] == 'write'] == ['system.files.write']
    with pytest.raises(KeyError):
        store.get_workflow(project["id"])


def test_runtime_notifications_are_not_replayed_as_conversation_history(store, tmp_path):
    project = store.create_project("clean history", "", str(tmp_path / "project"))
    store.add_message(project["id"], "assistant", "automatic runtime report", {"kind": "notification", "status": "succeeded"})

    def model(messages, _tools, **_kwargs):
        assert all("automatic runtime report" not in str(item.get("content") or "") for item in messages)
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': "Discussion complete."}))

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Discuss the current state.", False)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"


def test_unresolved_conversation_accepts_legacy_blocked_report_without_business_effects(store, tmp_path):
    project = store.create_project("no matching loop", "", str(tmp_path / "project"))
    turns = 0
    require_tool_values: list[bool] = []

    def model(_messages, _tools, **kwargs):
        nonlocal turns
        turns += 1
        require_tool_values.append(bool(kwargs.get("require_tool")))
        return AgentModelResponse(
            text=json.dumps({'mode': 'blocked', 'message': "No existing Loop matches this request, so no Workflow was created."})
        )

    registry = build_core_registry()
    agent = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )
    accepted = run_turn(agent, project["id"], "Try to create a new Loop.", True)
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "failed"
    assert job['result']['business_outcome'] == 'blocked'
    assert job["result"]["reply"] == (
        "No existing Loop matches this request, so no Workflow was created."
    )
    assert job["result"]["action_ledger"] == []
    assert turns == 1
    assert require_tool_values == [True]
    assert store.conversation_agent(project["id"])["state"] != "attention"
    assert any(
        item["role"] == "assistant" and item["content"] == job["result"]["reply"]
        for item in store.messages(project["id"], 20)
    )


def test_conversation_prose_never_mutates_state_or_forces_a_capability(store, tmp_path):
    project = store.create_project("mutation evidence", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.control",
        description="Perform one test control mutation.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "required": ["changed"],
            "properties": {"changed": {"type": "boolean"}},
            "additionalProperties": False,
        },
        handler=lambda _args, _ctx: {"changed": True},
        side_effect_kind="control",
        delegable_to_column=False,
    ))
    require_tool_values: list[bool] = []
    exposed_tools: list[set[str]] = []

    def model(_messages, tools, **kwargs):
        require_tool_values.append(bool(kwargs.get("require_tool")))
        exposed_tools.append({item["function"]["name"] for item in tools})
        return AgentModelResponse(text="I called test.control and completed the change.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.control"],
    ))

    assert result.status == "succeeded"
    assert require_tool_values == [False]
    assert exposed_tools == [{"test.control"}]
    invocations = store.tool_invocations(project["id"], result.agent_run_id)
    assert invocations == []


def test_current_request_never_creates_a_kernel_execution_obligation(
    store,
    tmp_path,
):
    project = store.create_project("discussion boundary", "", str(tmp_path / "project"))
    provider_requests = 0

    def model(messages, tools, **kwargs):
        nonlocal provider_requests
        provider_requests += 1
        assert kwargs.get("require_tool") is False
        assert kwargs.get("required_tool_name") is None
        assert "execution_obligation" not in json.loads(messages[-1]["content"])
        assert {item["function"]["name"] for item in tools} == {
            "task.plan.save", "task.create"
        }
        return AgentModelResponse(text="我们先讨论核心方向，不创建任何任务。")

    result = AgentCore(store, build_core_registry(), model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="discuss",
        instruction_revision=1,
        context={
            "current_request": {
                "message_id": 1,
                "content": "现在派发任务。",
            }
        },
        capability_ids=["task.plan.save", "task.create"],
        start_task=True,
    ))

    assert result.status == "succeeded"
    assert provider_requests == 1


def test_conversation_keeps_full_granted_tool_surface_after_a_receipt(store, tmp_path):
    project = store.create_project("stable tools", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()
    for capability, effect_kind in (("test.inspect", "read"), ("test.change", "control")):
        registry.register(CapabilityEntry(
            id=capability,
            description=capability,
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object", "additionalProperties": True},
            handler=lambda _args, _ctx, name=capability: {"capability": name},
            side_effect_kind=effect_kind,
            delegable_to_column=False,
        ))
    exposed: list[set[str]] = []

    def model(messages, tools, **kwargs):
        exposed.append({item["function"]["name"] for item in tools})
        assert kwargs["require_tool"] is False
        assert kwargs["required_tool_name"] is None
        if len(exposed) == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="inspect", name="test.inspect", arguments={}
            )])
        assert json.loads(messages[-1]["content"])["ok"] is True
        return AgentModelResponse(text="Inspection complete.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.inspect", "test.change"],
    ))

    assert result.status == "succeeded"
    assert exposed == [
        {"test.inspect", "test.change"},
        {"test.inspect", "test.change"},
    ]


def test_conversation_rejects_an_identical_failed_tool_operation(store, tmp_path):
    project = store.create_project("duplicate failure", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()
    executions = 0

    def fail(_args, _ctx):
        nonlocal executions
        executions += 1
        raise ValueError("invalid operation")

    registry.register(CapabilityEntry(
        id="test.mutate",
        description="A failing mutation.",
        input_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
            "additionalProperties": False,
        },
        output_schema={"type": "object", "additionalProperties": True},
        handler=fail,
        side_effect_kind="control",
        delegable_to_column=False,
    ))

    def model(_messages, _tools, **_kwargs):
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="same-call",
            name="test.mutate",
            arguments={"value": 1},
        )])

    with pytest.raises(RuntimeError, match="repeated an identical failed tool operation"):
        AgentCore(store, registry, model).run(AgentRunSpec(
            kind="conversation",
            project=project,
            instruction="",
            instruction_revision=1,
            context={},
            capability_ids=["test.mutate"],
        ))

    assert executions == 1
    run = store.agent_runs(project_id=project["id"])[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "conversation_protocol_stalled"
    assert len(store.tool_invocations(project["id"], run["id"])) == 1


def test_conversation_allows_reinspection_after_successful_repair(store, tmp_path):
    project = store.create_project("repair then inspect", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()
    repaired = False
    inspections = 0

    def inspect(_args, _ctx):
        nonlocal inspections
        inspections += 1
        if not repaired:
            raise ValueError("invalid asset")
        return {"valid": True}

    def repair(_args, _ctx):
        nonlocal repaired
        repaired = True
        return {"written": True}

    schema = {"type": "object", "additionalProperties": False}
    registry.register(CapabilityEntry(
        id="test.inspect", description="Inspect an asset.", input_schema=schema,
        output_schema={"type": "object", "additionalProperties": True},
        handler=inspect, side_effect_kind="read", delegable_to_column=False,
    ))
    registry.register(CapabilityEntry(
        id="test.repair", description="Repair an asset.", input_schema=schema,
        output_schema={"type": "object", "additionalProperties": True},
        handler=repair, side_effect_kind="write", delegable_to_column=False,
    ))
    calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="inspect-before", name="test.inspect", arguments={}
            )])
        if calls == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="repair", name="test.repair", arguments={}
            )])
        if calls == 3:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="inspect-after", name="test.inspect", arguments={}
            )])
        return AgentModelResponse(text="Asset repaired and verified.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.inspect", "test.repair"],
        start_task=True,
    ))

    assert result.status == "succeeded"
    assert inspections == 2
    assert [item["ok"] for item in store.tool_invocations(project["id"], result.agent_run_id)] == [
        False,
        True,
        True,
    ]


def test_repeated_failed_operation_is_reported_without_business_claims(store, tmp_path):
    project = store.create_project("visible duplicate failure", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()
    registry.register(CapabilityEntry(
        id="test.mutate",
        description="A failing mutation.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": True},
        handler=lambda _args, _ctx: (_ for _ in ()).throw(ValueError("invalid")),
        side_effect_kind="control",
        delegable_to_column=False,
    ))

    def model(_messages, _tools, **_kwargs):
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="same", name="test.mutate", arguments={}
        )])

    gateway = ConversationGateway(
        store, registry, agent_core=AgentCore(store, registry, model)
    )

    async def execute() -> dict:
        await gateway.start()
        try:
            accepted = await gateway.submit(project["id"], "执行操作", True)
            assert await gateway.wait_for_idle(timeout=10)
            return accepted
        finally:
            await gateway.stop()

    accepted = asyncio.run(execute())
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "failed"
    assistant = [
        item for item in store.messages(project["id"])
        if item["role"] == "assistant"
    ]
    assert '本轮新建 0 个任务，其中 0 个已启动' in assistant[-1]['content']
    assert 'TurnUnresolved' in assistant[-1]['content']
    assert '重复失败调用已停止' in assistant[-1]['content']


def test_user_turn_provider_failure_is_visible_in_conversation(store, tmp_path):
    project = store.create_project("visible provider failure", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()

    def model(_messages, _tools, **_kwargs):
        raise ConnectionError("provider connection unavailable")

    gateway = ConversationGateway(
        store, registry, agent_core=AgentCore(store, registry, model)
    )

    async def execute() -> dict:
        await gateway.start()
        try:
            accepted = await gateway.submit(project["id"], "执行操作", True)
            assert await gateway.wait_for_idle(timeout=10)
            return accepted
        finally:
            await gateway.stop()

    accepted = asyncio.run(execute())
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "failed"
    assistant = [
        item for item in store.messages(project["id"])
        if item["role"] == "assistant"
    ]
    assert assistant[-1]["content"] == (
        "本轮新建 0 个任务，其中 0 个已启动。"
        "本轮未完成：ConnectionError: provider connection unavailable"
    )
    assert assistant[-1]["meta"]["error_code"] == "conversation_processing_failed"


def test_conversation_system_prefix_is_stable_and_project_state_is_turn_input(
    store,
    tmp_path,
):
    project = store.create_project("stable session", "", str(tmp_path / "project"))
    registry = build_core_registry()
    observed: list[list[dict]] = []

    def model(messages, _tools, **_kwargs):
        observed.append([dict(item) for item in messages])
        return AgentModelResponse(text="Observed current state.")

    core = AgentCore(store, registry, model)
    session_id = store.conversation_agent(project["id"])["logical_id"]
    for message_id, state in ((1, "before"), (2, "after")):
        core.run(AgentRunSpec(
            kind="conversation",
            project=project,
            instruction="main agent",
            instruction_revision=1,
            context={
                "current_request": {
                    "message_id": message_id,
                    "content": f"request {message_id}",
                },
                "tasks": [{"id": "task", "status": state}],
            },
            capability_ids=["project.inspect"],
            agent_session_id=session_id,
        ))

    assert observed[0][0]["content"] == observed[1][0]["content"]
    assert "authoritative_project_state" not in observed[0][0]["content"]
    first_turn = json.loads(observed[0][-1]["content"])
    second_turn = json.loads(observed[1][-1]["content"])
    assert first_turn["authoritative_project_state"]["tasks"][0]["status"] == "before"
    assert second_turn["authoritative_project_state"]["tasks"][0]["status"] == "after"


def test_same_project_jobs_remain_ordered_by_session_gateway(store, tmp_path):
    project = store.create_project("ordered", "", str(tmp_path / "project"))
    first_entered = threading.Event()
    release_first = threading.Event()
    observed: list[str] = []

    def model(messages, _tools, **_kwargs):
        current = json.loads(messages[-1]["content"])["authoritative_current_request"]["content"]
        observed.append(current)
        if current == "first":
            first_entered.set()
            assert release_first.wait(timeout=3)
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': f"Handled {current}."}))

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))

    async def execute() -> tuple[dict, dict]:
        await agent.start()
        try:
            first = await agent.submit(project["id"], "first", False)
            assert await asyncio.to_thread(first_entered.wait, 3)
            second = await agent.submit(project["id"], "second", False)
            release_first.set()
            assert await agent.wait_for_idle(timeout=10)
            return first, second
        finally:
            release_first.set()
            await agent.stop()

    first, second = asyncio.run(execute())
    first_job = store.get_conversation_job(first["job"]["id"])
    second_job = store.get_conversation_job(second["job"]["id"])
    assert first_job["status"] == "succeeded"
    assert second_job["status"] == "succeeded"
    assert first_job["conversation_session_id"] == second_job["conversation_session_id"]
    assert observed == ["first", "second"]


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


def test_missing_capability_entity_is_returned_for_model_repair(store, tmp_path):
    project = store.create_project("missing entity", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.lookup",
        description="Look up one test entity.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        handler=lambda _args, _ctx: (_ for _ in ()).throw(KeyError("missing-task")),
        side_effect_kind="control",
        delegable_to_column=False,
    ))
    calls = 0

    def model(messages, _tools, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="missing", name="test.lookup", arguments={})
            ])
        assert '"ok": false' in messages[-1]["content"]
        assert "KeyError" in messages[-1]["content"]
        assert "missing-task" in messages[-1]["content"]
        return AgentModelResponse(text="The requested entity was not found; no change was made.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.lookup"],
    ))

    assert result.status == "succeeded"
    assert calls == 2
    invocation = store.tool_invocations(project["id"], result.agent_run_id)[0]
    assert invocation["ok"] is False
    assert invocation["result"]["error"]["type"] == "KeyError"


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
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': "Acknowledged."}))

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "current instruction", False)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    encoded = json.dumps(captured[0], ensure_ascii=False)
    assert encoded.count("current instruction") == 1


def test_terminal_mailbox_reports_durable_state_without_model(store, tmp_path):
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
        return AgentModelResponse(text=json.dumps({'mode': 'work_result', 'task_ids': [task['id']]}))

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))

    async def execute() -> None:
        await agent.start()
        try:
            await agent.wake_async()
            assert await agent.wait_for_idle(timeout=10)
        finally:
            await agent.stop()

    asyncio.run(execute())
    assistant = [item for item in store.messages(project["id"]) if item["role"] == "assistant"]
    assert "visible failure：失败" in assistant[-1]["content"]
    assert assistant[-1]["meta"]["subject_status"] == "failed"
    assert calls == 0
    assert assistant[-1]['meta']['llm_used'] is False
    assert store.get_task(task['id'])['status'] == 'failed'


def test_project_session_replays_human_dialogue_without_raw_tool_evidence(store, tmp_path):
    project = store.create_project("durable session", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.session.inspect",
        description="Return durable evidence for a Session continuity test.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        handler=lambda _args, _ctx: {"evidence": "workflow-revision-7"},
        side_effect_kind="read",
    ))
    model_calls = 0

    def model(messages, _tools, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="inspect-session",
                name="test.session.inspect",
                arguments={},
            )])
        if model_calls == 2:
            return AgentModelResponse(text=json.dumps({'mode': 'work_result', 'tool_call_ids': ['inspect-session']}))
        encoded = json.dumps(messages, ensure_ascii=False)
        assert "Remember the inspected workflow." in encoded
        assert "workflow-revision-7" not in encoded
        assert "所引用的操作已完成。" in encoded
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': "The same Project Session is continuing."}))

    first_gateway = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )

    async def first_turn() -> dict:
        await first_gateway.start()
        try:
            accepted = await first_gateway.submit(
                project["id"],
                "Remember the inspected workflow.",
                False,
            )
            assert await first_gateway.wait_for_idle(timeout=15)
            return accepted
        finally:
            await first_gateway.stop()

    first = asyncio.run(first_turn())
    second_gateway = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )

    async def second_turn() -> dict:
        await second_gateway.start()
        try:
            accepted = await second_gateway.submit(
                project["id"],
                "What did you inspect in the previous turn?",
                False,
            )
            assert await second_gateway.wait_for_idle(timeout=15)
            return accepted
        finally:
            await second_gateway.stop()

    second = asyncio.run(second_turn())
    first_job = store.get_conversation_job(first["job"]["id"])
    second_job = store.get_conversation_job(second["job"]["id"])
    assert first_job["status"] == "succeeded"
    assert second_job["status"] == "succeeded"
    assert first_job["conversation_session_id"] == second_job["conversation_session_id"]
    runs = list(reversed(store.agent_runs(project_id=project["id"])))
    assert len(runs) == 2
    assert runs[0]["agent_session_id"] == runs[1]["agent_session_id"]
    assert runs[0]["agent_session_id"] == store.conversation_agent(project["id"])["logical_id"]


def test_failed_turn_does_not_destroy_project_session(store, tmp_path):
    project = store.create_project("failure isolation", "", str(tmp_path / "project"))
    registry = build_core_registry()
    model_calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            raise RuntimeError("provider unavailable for this turn")
        return AgentModelResponse(text=json.dumps({'mode': 'discussion', 'message': "The next turn still runs in this Project Session."}))

    gateway = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )

    async def execute() -> tuple[dict, dict]:
        await gateway.start()
        try:
            failed = await gateway.submit(project["id"], "first turn", False)
            assert await gateway.wait_for_idle()
            succeeded = await gateway.submit(project["id"], "second turn", False)
            assert await gateway.wait_for_idle()
            return failed, succeeded
        finally:
            await gateway.stop()

    failed, succeeded = asyncio.run(execute())
    assert store.get_conversation_job(failed["job"]["id"])["status"] == "failed"
    assert store.get_conversation_job(succeeded["job"]["id"])["status"] == "succeeded"
    assert model_calls == 2


def test_mailbox_usage_limit_failure_never_forms_an_automatic_llm_retry_loop(store, tmp_path):
    project = store.create_project("mailbox usage limit", "", str(tmp_path / "project"))
    with store.tx(immediate=True) as db:
        store._mailbox(
            db,
            project["id"],
            "task.failed",
            None,
            None,
            {"reason": "column provider plan exhausted"},
        )
    mailbox_id = store.mailbox(project["id"])[0]["id"]
    model_calls = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        raise RuntimeError("LLM_USAGE_LIMIT:http_429:provider_2056")

    gateway = ConversationGateway(
        store,
        build_core_registry(),
        agent_core=AgentCore(store, build_core_registry(), model),
    )

    async def execute() -> None:
        await gateway.start()
        try:
            assert await gateway.wait_for_idle(timeout=10)
            for _ in range(5):
                await gateway.wake_async()
                assert await gateway.wait_for_idle(timeout=10)
        finally:
            await gateway.stop()

    asyncio.run(execute())

    assert model_calls == 0
    with store.connect() as db:
        failed_job_count = db.execute(
            "SELECT COUNT(*) FROM v1_conversation_jobs "
            "WHERE project_id=? AND trigger_kind='mailbox' AND status='failed'",
            (project["id"],),
        ).fetchone()[0]
    assert failed_job_count == 0
    observed = store.mailbox(project["id"], state="acknowledged")
    assert [item["id"] for item in observed] == [mailbox_id]
    deliveries = store.mailbox_deliveries(project["id"], mailbox_id)
    assert len(deliveries) == 1 and deliveries[0]['state'] == 'acknowledged'
    assert 'column provider plan exhausted' in store.messages(project['id'])[-1]['content'] or any(
        'column provider plan exhausted' in m['content'] for m in store.messages(project['id']))
