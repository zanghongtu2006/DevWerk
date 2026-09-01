from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.agent import _requested_mutation_capabilities
from app.v1.agent_protocol import ConversationTurnProtocol
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
        return AgentModelResponse(text="Workflow and Task are now tracked.")

    wakes: list[bool] = []
    registry = build_core_registry()
    agent = ConversationGateway(store, registry, on_task_created=lambda: wakes.append(True), agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Please manage this delivery.", True)
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["reply"] == "Workflow and Task are now tracked."
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
        assert "workflow.publish" in exposed[-1]
        assert "workflow.plan.save" in exposed[-1]
        assert "task.plan.save" in exposed[-1]
        assert "task.create" in exposed[-1]
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="workflow",
                name="workflow.inspect",
                arguments={},
            )])
        return AgentModelResponse(text="The Loop-created Workflow is ready for supervision.")

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Inspect the existing Workflow.", True)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    # Main-Agent Sessions keep one stable tool surface. The existing
    # Workflow invariant is enforced when loop.apply executes, not by hiding
    # the capability from the model on later Turns.
    assert "loop.apply" in exposed[0]


def test_start_task_false_keeps_stable_tools_but_rejects_mutation_execution(store, tmp_path):
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
        return AgentModelResponse(text="Discussion complete.")

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Only discuss this.", False)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    assert calls == 2
    assert "system.files.read" in exposed[0]
    assert "system.files.write" in exposed[0]
    assert "system.command.run" in exposed[0]
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
        return AgentModelResponse(text="The requested system file was written.")

    registry = build_core_registry()
    assert not any(item.startswith("system.files.") for item in registry.column_ids())
    assert "system.command.run" not in registry.column_ids()
    agent = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )

    accepted = run_turn(agent, project["id"], "Create this reusable Loop asset.", True)

    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "succeeded"
    assert loop_card.read_text(encoding="utf-8") == "name: reusable-loop\n"
    assert job["result"]["action_ledger"][0]["capability"] == "system.files.write"
    with pytest.raises(KeyError):
        store.get_workflow(project["id"])


def test_runtime_notifications_are_not_replayed_as_conversation_history(store, tmp_path):
    project = store.create_project("clean history", "", str(tmp_path / "project"))
    store.add_message(project["id"], "assistant", "automatic runtime report", {"kind": "notification", "status": "succeeded"})

    def model(messages, _tools, **_kwargs):
        assert all("automatic runtime report" not in str(item.get("content") or "") for item in messages)
        return AgentModelResponse(text="Discussion complete.")

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "Discuss the current state.", False)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"


def test_action_enabled_conversation_can_finish_with_plain_text(store, tmp_path):
    project = store.create_project("no matching loop", "", str(tmp_path / "project"))
    turns = 0
    require_tool_values: list[bool] = []

    def model(_messages, _tools, **kwargs):
        nonlocal turns
        turns += 1
        require_tool_values.append(bool(kwargs.get("require_tool")))
        return AgentModelResponse(
            text="No existing Loop matches this request, so no Workflow was created."
        )

    registry = build_core_registry()
    agent = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )
    accepted = run_turn(agent, project["id"], "Try to create a new Loop.", True)
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["reply"] == (
        "No existing Loop matches this request, so no Workflow was created."
    )
    assert job["result"]["action_ledger"] == []
    assert turns == 1
    assert require_tool_values == [False]
    assert store.conversation_agent(project["id"])["state"] != "attention"
    assert any(
        item["role"] == "assistant" and item["content"] == job["result"]["reply"]
        for item in store.messages(project["id"], 20)
    )


def test_conversation_cannot_report_an_unexecuted_mutation_as_completed(store, tmp_path):
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
    turns = 0
    require_tool_values: list[bool] = []

    def model(messages, _tools, **kwargs):
        nonlocal turns
        turns += 1
        require_tool_values.append(bool(kwargs.get("require_tool")))
        if turns == 1:
            return AgentModelResponse(text="I called test.control and completed the change.")
        if turns == 2:
            assert "unsupported_mutation_claims" in messages[-1]["content"]
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="control", name="test.control", arguments={})
            ])
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
    assert turns == 3
    assert require_tool_values == [False, True, False]
    invocations = store.tool_invocations(project["id"], result.agent_run_id)
    assert len(invocations) == 1
    assert invocations[0]["capability"] == "test.control"
    assert invocations[0]["ok"] is True


def test_conversation_repeated_unsupported_mutation_claim_fails_without_execution_progress(
    store,
    tmp_path,
):
    project = store.create_project("bounded correction", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.control",
        description="Perform one test control mutation.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": True},
        handler=lambda _args, _ctx: {"changed": True},
        side_effect_kind="control",
        delegable_to_column=False,
    ))
    turns = 0

    def model(messages, tools, **kwargs):
        nonlocal turns
        turns += 1
        if turns == 2:
            assert "unsupported_mutation_claims" in messages[-1]["content"]
            assert kwargs["require_tool"] is True
            assert {
                item["function"]["name"] for item in tools
            } == {"test.control"}
        if turns == 3:
            assert kwargs["required_tool_name"] == "test.control"
            assert {
                item["function"]["name"] for item in tools
            } == {"test.control"}
        return AgentModelResponse(text="I called test.control and completed the change.")

    with pytest.raises(RuntimeError, match="did not call the selected capability"):
        AgentCore(store, registry, model).run(AgentRunSpec(
            kind="conversation",
            project=project,
            instruction="",
            instruction_revision=1,
            context={},
            capability_ids=["test.control"],
        ))

    assert turns == 3
    run = store.agent_runs(project_id=project["id"])[0]
    assert run["status"] == "failed"
    assert "did not call the selected capability" in str(run["error"])
    assert run["error_code"] == "conversation_protocol_stalled"


def test_conversation_dispatch_correction_forces_plan_save_before_task_create():
    protocol = ConversationTurnProtocol(required_capabilities=("task.create",))
    tools = [
        {
            "type": "function",
            "function": {"name": name, "description": name, "parameters": {}},
        }
        for name in (
            "project.inspect",
            "workflow.inspect",
            "task.list",
            "task.plan.list",
            "task.plan.save",
            "task.create",
        )
    ]

    protocol.select_tools(tools)

    assert protocol.select_forced_tool(["task.create"]) == "task.plan.save"
    assert protocol.forced_tool_name == "task.plan.save"
    assert {
        item["function"]["name"] for item in protocol.select_tools(tools)
    } == {"task.plan.save"}


def test_conversation_forces_claimed_capabilities_until_dispatch_receipts_exist(
    store,
    tmp_path,
):
    project = store.create_project("forced execution", "", str(tmp_path / "project"))
    registry = build_core_registry()
    executed: list[str] = []
    for capability in ("test.plan.save", "test.task.create"):
        registry.register(CapabilityEntry(
            id=capability,
            description=f"Execute {capability}.",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object", "additionalProperties": True},
            handler=lambda _args, _ctx, name=capability: executed.append(name) or {
                "executed": name,
            },
            side_effect_kind="control",
            delegable_to_column=False,
        ))
    turns = 0
    provider_contracts: list[tuple[bool, set[str]]] = []

    def model(messages, tools, **kwargs):
        nonlocal turns
        turns += 1
        provider_contracts.append((
            bool(kwargs["require_tool"]),
            {item["function"]["name"] for item in tools},
        ))
        if turns == 1:
            return AgentModelResponse(
                text="test.plan.save and test.task.create completed."
            )
        if turns == 2:
            assert "unsupported_mutation_claims" in messages[-1]["content"]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="save-plan",
                name="test.plan.save",
                arguments={},
            )])
        if turns == 3:
            assert json.loads(messages[-1]["content"])["ok"] is True
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="create-task",
                name="test.task.create",
                arguments={},
            )])
        return AgentModelResponse(
            text="The plan was saved and the task was dispatched."
        )

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.plan.save", "test.task.create", "project.inspect"],
    ))

    assert result.status == "succeeded"
    assert executed == ["test.plan.save", "test.task.create"]
    assert provider_contracts == [
        (False, {"test.plan.save", "test.task.create", "project.inspect"}),
        (True, {"test.plan.save", "test.task.create"}),
        (True, {"test.task.create"}),
        (False, {"test.plan.save", "test.task.create", "project.inspect"}),
    ]
    assert [
        item["capability"]
        for item in store.tool_invocations(project["id"], result.agent_run_id)
    ] == ["test.plan.save", "test.task.create"]


def test_natural_language_dispatch_promise_requires_real_task_receipt(
    store,
    tmp_path,
):
    project = store.create_project("dispatch promise", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()
    executed: list[str] = []
    for capability, effect_kind in (
        ("project.inspect", "read"),
        ("task.plan.save", "process"),
        ("task.create", "process"),
    ):
        registry.register(CapabilityEntry(
            id=capability,
            description=f"Execute {capability}.",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object", "additionalProperties": True},
            handler=lambda _args, _ctx, name=capability: executed.append(name) or {
                "executed": name,
            },
            side_effect_kind=effect_kind,
            delegable_to_column=False,
        ))
    turns = 0
    contracts: list[tuple[bool, set[str]]] = []

    def model(_messages, tools, **kwargs):
        nonlocal turns
        turns += 1
        contracts.append((
            bool(kwargs["require_tool"]),
            {item["function"]["name"] for item in tools},
        ))
        if turns == 1:
            return AgentModelResponse(
                text="你说得对，我先看项目当前状态再派发。"
            )
        capability = {
            2: "project.inspect",
            3: "task.plan.save",
            4: "task.create",
        }.get(turns)
        if capability:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id=f"call-{turns}",
                name=capability,
                arguments={},
            )])
        return AgentModelResponse(text="后续章节任务已经派发。")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["project.inspect", "task.plan.save", "task.create"],
    ))

    assert result.status == "succeeded"
    assert executed == ["project.inspect", "task.plan.save", "task.create"]
    assert contracts == [
        (False, {"project.inspect", "task.plan.save", "task.create"}),
        (True, {"project.inspect", "task.plan.save", "task.create"}),
        (True, {"project.inspect", "task.plan.save", "task.create"}),
        (True, {"project.inspect", "task.plan.save", "task.create"}),
        (False, {"project.inspect", "task.plan.save", "task.create"}),
    ]


def test_explicit_dispatch_request_requires_task_receipt_from_first_model_step(
    store,
    tmp_path,
):
    project = store.create_project("dispatch request", "", str(tmp_path / "project"))
    registry = CapabilityRegistry()
    executed: list[str] = []
    for capability, effect_kind in (
        ("project.inspect", "read"),
        ("task.plan.save", "process"),
        ("task.create", "process"),
    ):
        registry.register(CapabilityEntry(
            id=capability,
            description=f"Execute {capability}.",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object", "additionalProperties": True},
            handler=lambda _args, _ctx, name=capability: executed.append(name) or {
                "executed": name,
            },
            side_effect_kind=effect_kind,
            delegable_to_column=False,
        ))
    turns = 0

    def model(messages, tools, **kwargs):
        nonlocal turns
        turns += 1
        if turns <= 3:
            assert kwargs["require_tool"] is True
            if turns == 1:
                turn = json.loads(messages[-1]["content"])
                assert turn["execution_obligation"] == {
                    "required_successful_receipts": ["task.create"],
                    "text_cannot_complete_turn": True,
                    "instruction": (
                        "This request explicitly requires a Project state change. "
                        "Call the available inspection or prerequisite tools as needed, then continue "
                        "until every required successful receipt exists. Do not answer with a plan, "
                        "promise, explanation, or future action before those receipts exist."
                    ),
                }
            assert {item["function"]["name"] for item in tools} == {
                "project.inspect", "task.plan.save", "task.create",
            }
            capability = ("project.inspect", "task.plan.save", "task.create")[turns - 1]
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id=f"call-{turns}",
                name=capability,
                arguments={},
            )])
        assert kwargs["require_tool"] is False
        return AgentModelResponse(text="后续章节任务已经派发。")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={
            "current_request": {
                "message_id": 1,
                "content": "后续章节任务仍未派发，请现在实际派发。",
            },
        },
        capability_ids=["project.inspect", "task.plan.save", "task.create"],
    ))

    assert result.status == "succeeded"
    assert turns == 4
    assert executed == ["project.inspect", "task.plan.save", "task.create"]


def test_initial_task_creation_exposes_loop_application_prerequisites():
    protocol = ConversationTurnProtocol(required_capabilities=("task.create",))
    tools = [
        {
            "type": "function",
            "function": {"name": name, "parameters": {"type": "object"}},
        }
        for name in (
            "loop.list",
            "loop.inspect",
            "loop.apply",
            "project.inspect",
            "workflow.inspect",
            "task.plan.list",
            "task.plan.save",
            "task.create",
        )
    ]

    selected = {
        item["function"]["name"]
        for item in protocol.select_tools(tools)
    }

    assert selected == {
        "loop.list",
        "loop.inspect",
        "loop.apply",
        "project.inspect",
        "workflow.inspect",
        "task.plan.list",
        "task.plan.save",
        "task.create",
    }


def test_dispatch_intent_distinguishes_current_command_from_negative_status():
    effect_kinds = {"task.create": "process"}

    assert _requested_mutation_capabilities(
        "后续章节任务仍未派发，请现在实际派发。",
        effect_kinds,
    ) == ["task.create"]
    assert _requested_mutation_capabilities(
        "任务尚未派发，我现在无法派发。",
        effect_kinds,
    ) == []
    assert _requested_mutation_capabilities(
        "这个冲突是否足够？仍先不要创建任务。",
        effect_kinds,
    ) == []
    assert _requested_mutation_capabilities(
        "先讨论人物关系，暂不创建任务。",
        effect_kinds,
    ) == []


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


def test_conversation_allows_staged_corrections_when_mutation_receipts_advance(
    store,
    tmp_path,
):
    project = store.create_project("progress correction", "", str(tmp_path / "project"))
    registry = build_core_registry()
    for capability in ("test.first", "test.second"):
        registry.register(CapabilityEntry(
            id=capability,
            description=f"Perform {capability}.",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object", "additionalProperties": True},
            handler=lambda _args, _ctx, name=capability: {"changed": name},
            side_effect_kind="control",
            delegable_to_column=False,
        ))
    turns = 0

    def model(messages, tools, **kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            return AgentModelResponse(text="test.first and test.second completed.")
        if turns == 2:
            assert "unsupported_mutation_claims" in messages[-1]["content"]
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="first", name="test.first", arguments={})
            ])
        if turns == 3:
            assert kwargs["require_tool"] is True
            assert {item["function"]["name"] for item in tools} == {"test.second"}
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id="second", name="test.second", arguments={})
            ])
        return AgentModelResponse(text="test.first and test.second completed.")

    result = AgentCore(store, registry, model).run(AgentRunSpec(
        kind="conversation",
        project=project,
        instruction="",
        instruction_revision=1,
        context={},
        capability_ids=["test.first", "test.second"],
    ))

    assert result.status == "succeeded"
    assert turns == 4
    assert [
        item["capability"]
        for item in store.tool_invocations(project["id"], result.agent_run_id)
    ] == ["test.first", "test.second"]


def test_protocol_stall_is_published_as_a_human_readable_failed_reply(
    store,
    tmp_path,
):
    project = store.create_project("visible protocol failure", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.dispatch",
        description="Dispatch test work.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": True},
        handler=lambda _args, _ctx: {"dispatched": True},
        side_effect_kind="control",
        delegable_to_column=False,
    ))

    def model(_messages, _tools, **_kwargs):
        return AgentModelResponse(text="test.dispatch completed.")

    gateway = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )

    async def execute() -> dict:
        await gateway.start()
        try:
            accepted = await gateway.submit(project["id"], "Dispatch it now.", True)
            assert await gateway.wait_for_idle(timeout=10)
            return accepted
        finally:
            await gateway.stop()

    accepted = asyncio.run(execute())
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "failed"
    assert job["agent_run_id"] is not None
    assert job["result"]["durable_progress"] is False
    assistant = [
        item for item in store.messages(project["id"])
        if item["role"] == "assistant"
    ]
    assert assistant[-1]["content"] == (
        "本轮操作未执行：Conversation Agent 没有完成所需的项目工具调用，项目状态未改变。"
    )
    assert assistant[-1]["meta"]["error_code"] == "conversation_protocol_stalled"


def test_protocol_failure_preserves_partial_execution_receipts(
    store,
    tmp_path,
):
    project = store.create_project("partial protocol progress", "", str(tmp_path / "project"))
    registry = build_core_registry()
    registry.register(CapabilityEntry(
        id="test.plan.persist",
        description="Persist a test plan.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": True},
        handler=lambda _args, _ctx: {"plan_id": "plan-1"},
        side_effect_kind="process",
        delegable_to_column=False,
    ))
    registry.register(CapabilityEntry(
        id="test.work.dispatch",
        description="Create a test task.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": True},
        handler=lambda _args, _ctx: {"task_id": "task-1"},
        side_effect_kind="process",
        delegable_to_column=False,
    ))
    turns = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            return AgentModelResponse(
                text="test.plan.persist and test.work.dispatch completed."
            )
        if turns == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="save-plan",
                name="test.plan.persist",
                arguments={},
            )])
        return AgentModelResponse(text="test.work.dispatch completed.")

    gateway = ConversationGateway(
        store,
        registry,
        agent_core=AgentCore(store, registry, model),
    )

    async def execute() -> dict:
        await gateway.start()
        try:
            accepted = await gateway.submit(project["id"], "Create the work now.", True)
            assert await gateway.wait_for_idle(timeout=10)
            return accepted
        finally:
            await gateway.stop()

    accepted = asyncio.run(execute())
    job = store.get_conversation_job(accepted["job"]["id"])
    assert job["status"] == "failed"
    assert job["agent_run_id"] is not None
    assert job["result"]["durable_progress"] is True
    assert [
        item["capability"] for item in job["result"]["action_ledger"]
    ] == ["test.plan.persist"]
    assistant = [
        item for item in store.messages(project["id"])
        if item["role"] == "assistant"
    ]
    assert assistant[-1]["content"] == (
        "本轮操作未完成：Conversation Agent 没有完成全部所需的项目工具调用；"
        "已经成功执行的操作及其回执已保留。"
    )
    assert assistant[-1]["meta"]["durable_progress"] is True


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
        return AgentModelResponse(text=f"Handled {current}.")

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
        return AgentModelResponse(text="Acknowledged.")

    registry = build_core_registry()
    agent = ConversationGateway(store, registry, agent_core=AgentCore(store, registry, model))
    accepted = run_turn(agent, project["id"], "current instruction", False)
    assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    encoded = json.dumps(captured[0], ensure_ascii=False)
    assert encoded.count("current instruction") == 1


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
    assert assistant[-1]["content"] == "任务失败，原因已核实：synthetic terminal failure"
    assert assistant[-1]["meta"]["subject_status"] == "failed"


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
            return AgentModelResponse(text="I inspected workflow revision 7.")
        encoded = json.dumps(messages, ensure_ascii=False)
        assert "Remember the inspected workflow." in encoded
        assert "workflow-revision-7" not in encoded
        assert "I inspected workflow revision 7." in encoded
        return AgentModelResponse(text="The same Project Session is continuing.")

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
        return AgentModelResponse(text="The next turn still runs in this Project Session.")

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

    assert model_calls == 1
    with store.connect() as db:
        failed_job_count = db.execute(
            "SELECT COUNT(*) FROM v1_conversation_jobs "
            "WHERE project_id=? AND trigger_kind='mailbox' AND status='failed'",
            (project["id"],),
        ).fetchone()[0]
    assert failed_job_count == 1
    failed_mailbox = store.mailbox(project["id"], state="failed")
    assert [item["id"] for item in failed_mailbox] == [mailbox_id]
    assert failed_mailbox[0]["last_error"].endswith("provider_2056")
    assert store.mailbox_deliveries(project["id"], mailbox_id)[0]["state"] == "failed"
