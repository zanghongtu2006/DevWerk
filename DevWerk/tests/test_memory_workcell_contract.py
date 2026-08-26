from __future__ import annotations

import requests

from app.services.provider_errors import provider_timeout_error
from app.v1.agent import AgentCore
from app.v1.capabilities import build_core_registry
from app.v1.domain import (
    AgentModelResponse,
    AgentToolCall,
    CapabilityStep,
    ColumnDefinition,
    ContextSelection,
    MemorySelector,
    Transition,
    WorkflowDefinition,
    WorkcellAgentParticipant,
    WorkcellCapabilityParticipant,
    WorkcellExecutor,
    WorkcellState,
    WorkcellTerminal,
    WorkcellTransition,
)
from app.v1.runtime import WorkflowRuntime
from app.v1.memory import FileMemoryStore
from app.v1.store import V1Store
from tests.helpers import create_planned_task, publish_planned_workflow


def test_file_memory_is_project_local_searchable_and_supersedable(store, tmp_path):
    project = store.create_project("memory", "file-first memory", str(tmp_path / "project"))

    root = tmp_path / "project" / ".devwerk" / "memory"
    assert (root / "PROJECT.md").is_file()
    assert (root / "DECISIONS.md").is_file()

    first = store.memory_write(project["id"], {
        "kind": "decision",
        "scope": "project",
        "authority": "user_confirmed",
        "content": "Use PostgreSQL for the production deployment.",
        "source_type": "conversation",
        "source_id": "message-1",
    })
    assert first["reference"].endswith(".md")
    assert store.memory_search(project["id"], "PostgreSQL")[0]["metadata"]["id"] == first["metadata"]["id"]

    replacement = store.memory_supersede(project["id"], first["reference"], {
        "kind": "decision",
        "scope": "project",
        "authority": "user_confirmed",
        "content": "Use SQLite for the local-first v0.1.0 release.",
        "source_type": "conversation",
        "source_id": "message-2",
    })
    assert replacement["metadata"]["revision"] == 2
    assert store.memory_read(project["id"], first["reference"])["metadata"]["status"] == "superseded"
    appended = store.memory_append(
        project["id"],
        "CURRENT.md",
        "Implementation is in progress.",
        source_type="conversation",
        source_id="message-3",
    )
    assert appended["metadata"]["revision"] == 2
    assert "Implementation is in progress." in appended["content"]


def test_memory_store_provider_is_replaceable_without_runtime_branching(tmp_path):
    class TrackingFileMemoryStore(FileMemoryStore):
        name = "tracking-file"

        def __init__(self):
            self.writes = 0

        def write(self, project, record):
            self.writes += 1
            return super().write(project, record)

    provider = TrackingFileMemoryStore()
    value = V1Store(
        str(tmp_path / "provider.db"),
        registry=build_core_registry(),
        memory_store=provider,
    )
    project = value.create_project("provider", "replaceable", str(tmp_path / "project"))
    written = value.memory_write(project["id"], {
        "kind": "fact",
        "scope": "project",
        "content": "Provider substitution works.",
    })

    assert provider.writes == 1
    assert written["provider"] == "tracking-file"
    assert value.memory.build_context(project)["manifest"]["store_provider"] == "tracking-file"


def _workcell_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="generic paired delivery",
        entry="collaborate",
        columns=[ColumnDefinition(
            key="collaborate",
            name="Collaborate",
            executor=WorkcellExecutor(
                entry="produce",
                participants=[
                    WorkcellAgentParticipant(
                        key="producer",
                        instruction="Produce a candidate.",
                        capabilities=["project.files.read"],
                        context=ContextSelection(memory=[MemorySelector(scope="project")]),
                    ),
                    WorkcellAgentParticipant(
                        key="reviewer",
                        instruction="Review the candidate independently.",
                        capabilities=["project.files.read"],
                    ),
                ],
                states=[
                    WorkcellState(
                        key="produce",
                        participant="producer",
                        require_evidence=False,
                        output_contract={"type": "object", "required": ["candidate"]},
                        transitions=[WorkcellTransition(signal="candidate_ready", target="review", receivers=["reviewer"])],
                    ),
                    WorkcellState(
                        key="review",
                        participant="reviewer",
                        require_evidence=False,
                        output_contract={"type": "object", "required": ["delivered"]},
                        transitions=[
                            WorkcellTransition(signal="revision_requested", target="produce", receivers=["producer"]),
                            WorkcellTransition(signal="accepted", target="finished", receivers=["producer"]),
                        ],
                    ),
                ],
                terminals=[WorkcellTerminal(key="finished", outcome="success")],
            ),
            output_contract={"type": "object", "required": ["delivered"]},
            transitions=[
                Transition(outcome="success", target="done"),
                Transition(outcome="failure", target="failed"),
            ],
        )],
    )


def test_workcell_supports_deterministic_participants_without_an_llm(store, tmp_path):
    project = store.create_project("deterministic workcell", "", str(tmp_path / "project"))
    workflow = WorkflowDefinition(
        name="deterministic workcell",
        entry="coordinate",
        columns=[ColumnDefinition(
            key="coordinate",
            name="Coordinate",
            executor=WorkcellExecutor(
                entry="execute",
                participants=[WorkcellCapabilityParticipant(
                    key="tool",
                    steps=[CapabilityStep(capability="system.noop", save_as="noop")],
                    completed_signal="completed",
                )],
                states=[WorkcellState(
                    key="execute",
                    participant="tool",
                    transitions=[WorkcellTransition(signal="completed", target="finished")],
                )],
                terminals=[WorkcellTerminal(key="finished", outcome="success")],
            ),
            output_contract={
                "type": "object",
                "required": ["summary", "steps"],
                "properties": {"summary": {"type": "string"}, "steps": {"type": "array"}},
            },
            transitions=[Transition(outcome="success", target="done")],
        )],
    )
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "deterministic")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("deterministic Workcell participant must not call an LLM")

    registry = build_core_registry()
    WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, forbidden)).step(task["id"])

    assert store.get_task(task["id"])["status"] == "done"
    assert store.agent_runs(project_id=project["id"], task_id=task["id"]) == []


def test_workcell_routes_typed_handoffs_and_keeps_participant_sessions(store, tmp_path):
    project = store.create_project("workcell", "generic collaboration", str(tmp_path / "project"))
    workflow = _workcell_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "deliver")
    calls = 0

    def model(messages, tools, **_kwargs):
        nonlocal calls
        calls += 1
        assert any(item["function"]["name"] == "workcell.signal" for item in tools)
        if calls in {1, 3}:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id=f"candidate-{calls}",
                name="workcell.signal",
                arguments={
                    "outcome": "candidate_ready",
                    "output": {"candidate": f"implementation-v{1 if calls == 1 else 2}"},
                    "summary": "candidate ready",
                    "evidence_ids": [],
                },
            )])
        if calls == 2:
            return AgentModelResponse(tool_calls=[AgentToolCall(
                id="revision",
                name="workcell.signal",
                arguments={
                    "outcome": "revision_requested",
                    "output": {"delivered": False},
                    "summary": "revise the candidate",
                    "evidence_ids": [],
                },
            )])
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id="accepted",
            name="workcell.signal",
            arguments={
                "outcome": "accepted",
                "output": {"delivered": True},
                "summary": "accepted",
                "evidence_ids": [],
            },
        )])

    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, model))
    runtime.step(task["id"])

    assert store.get_task(task["id"])["status"] == "done"
    workcells = store.workcells(project["id"], task_id=task["id"])
    assert len(workcells) == 1
    assert workcells[0]["status"] == "completed"
    participants = store.workcell_participants(project["id"], workcells[0]["id"])
    assert {item["participant_key"] for item in participants} == {"producer", "reviewer"}
    assert all(item["agent_session_id"] for item in participants)
    handoffs = store.workcell_handoffs(project["id"], workcells[0]["id"])
    assert [item["signal"] for item in handoffs] == [
        "candidate_ready",
        "revision_requested",
        "candidate_ready",
        "accepted",
    ]
    snapshots = sorted(
        (tmp_path / "project" / ".devwerk" / "memory" / "snapshots" / "workcell" / workcells[0]["id"]).glob("*.json")
    )
    assert snapshots
    restored = next(
        state
        for state in (
            store.memory.store.restore(
                project,
                path.relative_to(tmp_path / "project" / ".devwerk" / "memory").as_posix(),
            )
            for path in snapshots
        )
        if state["workcell"]["status"] == "completed"
    )
    assert restored["workcell"]["status"] == "completed"
    assert restored["workcell"]["current_state"] == "finished"
    assert handoffs[0]["receivers"] == ["reviewer"]
    assert handoffs[1]["receivers"] == ["producer"]
    agent_runs = store.agent_runs(project_id=project["id"], task_id=task["id"])
    assert len({item["agent_session_id"] for item in agent_runs}) == 2
    assert sorted(
        sum(item["agent_session_id"] == session_id for item in agent_runs)
        for session_id in {item["agent_session_id"] for item in agent_runs}
    ) == [2, 2]


def test_workcell_recovers_same_node_and_participant_session_after_provider_timeout(store, tmp_path):
    project = store.create_project("recovery", "workcell recovery", str(tmp_path / "project"))
    workflow = _workcell_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "recover")
    call = 0

    def model(_messages, _tools, **_kwargs):
        nonlocal call
        call += 1
        if call == 1:
            raise provider_timeout_error(
                requests.Timeout("read timed out"),
                provider="test",
                api_name="test",
                timeout_seconds=600,
            )
        if call == 2:
            outcome, output = "candidate_ready", {"candidate": "recovered"}
        else:
            outcome, output = "accepted", {"delivered": True}
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id=f"signal-{call}",
            name="workcell.signal",
            arguments={
                "outcome": outcome,
                "output": output,
                "summary": outcome,
                "evidence_ids": [],
            },
        )])

    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, model))
    runtime.step(task["id"])

    recovering_task = store.get_task(task["id"])
    assert recovering_task["status"] == "recovering"
    workcell = store.workcells(project["id"], task_id=task["id"])[0]
    assert workcell["status"] == "recovering"
    assert workcell["current_state"] == "produce"
    producer_session = next(
        item["agent_session_id"]
        for item in store.workcell_participants(project["id"], workcell["id"])
        if item["participant_key"] == "producer"
    )

    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_tasks SET next_retry_at='2000-01-01T00:00:00.000+00:00' WHERE id=?",
            (task["id"],),
        )
    runtime.step(task["id"])

    assert store.get_task(task["id"])["status"] == "done"
    resumed = store.get_workcell(project["id"], workcell["id"])
    assert resumed["status"] == "completed"
    producer_runs = [
        item
        for item in store.agent_runs(project_id=project["id"], task_id=task["id"])
        if item["agent_session_id"] == producer_session
    ]
    assert len(producer_runs) == 2
