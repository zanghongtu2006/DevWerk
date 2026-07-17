from __future__ import annotations

from app.v1.agent import AgentCore
from app.v1.capabilities import build_core_registry
from app.v1.conversation import ConversationAgent
from app.v1.domain import AgentModelResponse, AgentToolCall
from tests.helpers import sequence_workflow, readiness


def test_conversation_agent_publishes_data_defined_workflow_then_creates_task(store, tmp_path):
    project = store.create_project("conversation", "", str(tmp_path / "project"), "project instruction")
    workflow = sequence_workflow(name="generated in conversation").model_dump(mode="json")
    responses = iter(
        [
            AgentModelResponse(tool_calls=[AgentToolCall(id="wf", name="workflow.publish", arguments={"workflow": workflow})]),
            AgentModelResponse(tool_calls=[AgentToolCall(id="task", name="task.create", arguments={"title": "formal", "brief": "deliver", "input": {}, "readiness": readiness()})]),
            AgentModelResponse(text="Workflow and Task are now tracked."),
        ]
    )
    registry = build_core_registry()
    core = AgentCore(store, registry, lambda *_args, **_kwargs: next(responses))
    wakes: list[bool] = []
    agent = ConversationAgent(store, registry, on_task_created=lambda: wakes.append(True), workers=1, agent_core=core)
    try:
        accepted = agent.submit(project["id"], "Please manage this delivery.", True)
        assert accepted["status"] == "accepted"
        assert agent.wait_for_idle()
        job = store.get_conversation_job(accepted["job"]["id"])
        assert job["status"] == "succeeded"
        assert job["agent_run_id"]
        assert job["result"]["reply"] == "Workflow and Task are now tracked."
        assert job["result"]["task_ids"] == [job["task_id"]]
        assert len(job["result"]["workflow_revision_ids"]) == 1
        assert store.get_task(job["task_id"])["title"] == "formal"
        assert store.get_workflow(project["id"])["definition"]["name"] == "generated in conversation"
        assert wakes == [True]
        assert [item["role"] for item in store.messages(project["id"])] == ["user", "assistant"]
    finally:
        agent.stop()


def test_start_task_false_removes_workflow_and_task_tools(store, tmp_path):
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
        assert "workflow.publish" not in exposed[0]
        assert "task.create" not in exposed[0]
        assert store.list_tasks(project["id"]) == []
    finally:
        agent.stop()


def test_tool_failure_is_returned_to_model_instead_of_crashing_turn(store, tmp_path):
    project = store.create_project("recover-tool", "", str(tmp_path / "project"))
    seen_messages = []

    def model(messages, _tools, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id="read", name="project.files.read", arguments={"path": "missing.txt"})])
        assert '"ok": false' in messages[-1]["content"]
        return AgentModelResponse(text="The missing file was diagnosed.")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=1, agent_core=AgentCore(store, registry, model))
    try:
        accepted = agent.submit(project["id"], "Diagnose the project.", False)
        assert agent.wait_for_idle()
        assert store.get_conversation_job(accepted["job"]["id"])["status"] == "succeeded"
    finally:
        agent.stop()


def test_conversation_direct_effect_budget_forces_delegation_and_blocks_post_task_writes(store, tmp_path):
    project = store.create_project("delegation-boundary", "", str(tmp_path / "project"))
    workflow = sequence_workflow(name="delegated").model_dump(mode="json")
    step = 0

    def model(messages, _tools, **_kwargs):
        nonlocal step
        step += 1
        if step <= 4:
            return AgentModelResponse(
                tool_calls=[
                    AgentToolCall(
                        id=f"write-{step}",
                        name="project.files.write",
                        arguments={"path": f"direct-{step}.txt", "content": str(step)},
                    )
                ]
            )
        if step == 5:
            assert '"type": "DelegationRequired"' in messages[-1]["content"]
            return AgentModelResponse(
                tool_calls=[AgentToolCall(id="wf", name="workflow.publish", arguments={"workflow": workflow})]
            )
        if step == 6:
            return AgentModelResponse(
                tool_calls=[AgentToolCall(id="task", name="task.create", arguments={"title": "formal", "brief": "deliver", "readiness": readiness()})]
            )
        if step == 7:
            return AgentModelResponse(
                tool_calls=[
                    AgentToolCall(
                        id="post-task-write",
                        name="project.files.write",
                        arguments={"path": "post-task.txt", "content": "blocked"},
                    )
                ]
            )
        assert '"type": "DelegationBoundary"' in messages[-1]["content"]
        return AgentModelResponse(text="Delegated and now supervising the tracked Task.")

    registry = build_core_registry()
    agent = ConversationAgent(store, registry, workers=1, agent_core=AgentCore(store, registry, model))
    try:
        accepted = agent.submit(project["id"], "Manage a delivery.", True)
        assert agent.wait_for_idle()
        job = store.get_conversation_job(accepted["job"]["id"])
        assert job["status"] == "succeeded"
        assert job["task_id"]
        assert (tmp_path / "project" / "direct-1.txt").is_file()
        assert (tmp_path / "project" / "direct-3.txt").is_file()
        assert not (tmp_path / "project" / "direct-4.txt").exists()
        assert not (tmp_path / "project" / "post-task.txt").exists()
        failures = [item for item in store.tool_invocations(project["id"], job["agent_run_id"]) if not item["ok"]]
        assert [item["result"]["error"]["type"] for item in failures] == [
            "DelegationRequired",
            "DelegationBoundary",
        ]
    finally:
        agent.stop()
