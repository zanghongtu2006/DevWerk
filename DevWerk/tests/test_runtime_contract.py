from __future__ import annotations

from app.v1.agent import AgentCore
from app.v1.capabilities import build_core_registry
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.runtime import WorkflowRuntime
from tests.helpers import agent_workflow, sequence_workflow, readiness


def test_capability_sequence_reaches_done_without_calling_llm(store, tmp_path):
    project = store.create_project("deterministic", "", str(tmp_path / "project"))
    revision = store.publish_workflow(project["id"], sequence_workflow(content="domain-neutral"))
    task = store.create_task(project["id"], "task", "", {}, readiness())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("LLM must not be called by capability_sequence")

    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker", AgentCore(store, registry, forbidden))
    runtime.step(task["id"])

    finished = store.get_task(task["id"])
    assert revision["id"] == finished["workflow_revision_id"]
    assert finished["status"] == "done"
    assert (tmp_path / "project" / "result.txt").read_text(encoding="utf-8") == "domain-neutral"
    assert [item["column_key"] for item in store.runs(project["id"], task["id"])] == ["execute"]
    assert store.events(task_id=task["id"])[-1]["type"] == "task.done"
    assert store.mailbox(project["id"])[-1]["event_type"] == "task.done"


def test_ephemeral_column_agent_uses_same_tool_loop_and_declared_contract(store, tmp_path):
    project = store.create_project("agent", "", str(tmp_path / "project"))
    store.publish_workflow(project["id"], agent_workflow(instruction="Handle an arbitrary deliverable."))
    task = store.create_task(project["id"], "unknown domain", "do it", {"shape": "unseen"}, readiness())
    responses = iter(
        [
            AgentModelResponse(
                tool_calls=[
                    AgentToolCall(
                        id="write-1",
                        name="project.files.write",
                        arguments={"path": "arbitrary/output.data", "content": "value"},
                    )
                ]
            ),
            AgentModelResponse(
                tool_calls=[
                    AgentToolCall(
                        id="complete-1",
                        name="column.complete",
                        arguments={"outcome": "success", "output": {"delivered": True}, "summary": "complete"},
                    )
                ]
            ),
        ]
    )

    registry = build_core_registry()
    core = AgentCore(store, registry, lambda *_args, **_kwargs: next(responses))
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
    store.publish_workflow(project["id"], workflow)
    task = store.create_task(project["id"], "invalid", "", {}, readiness())
    registry = build_core_registry()

    WorkflowRuntime(store, registry, "worker").step(task["id"])

    failed = store.get_task(task["id"])
    assert failed["status"] == "failed"
    assert "unknown capability" in (failed["error"] or "")


def test_expired_task_lease_is_recoverable_and_visible(store, tmp_path):
    project = store.create_project("recover", "", str(tmp_path / "project"))
    store.publish_workflow(project["id"], sequence_workflow())
    task = store.create_task(project["id"], "recover", "", {}, readiness())
    claimed = store.claim_task(task["id"], "dead-worker", lease_seconds=1)
    assert claimed is not None
    with store.connect() as db:
        db.execute("UPDATE v1_tasks SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))

    assert store.recover_expired() == 1
    assert store.get_task(task["id"])["status"] == "recovering"
    assert store.mailbox(project["id"])[-1]["event_type"] == "task_recovering"


def test_run_output_does_not_recursively_embed_task_context(store, tmp_path):
    project = store.create_project("bounded", "", str(tmp_path / "project"))
    store.publish_workflow(project["id"], sequence_workflow())
    task = store.create_task(project["id"], "bounded", "", {}, readiness())
    registry = build_core_registry()
    runtime = WorkflowRuntime(store, registry, "worker")
    runtime.step(task["id"])

    output = store.runs(project["id"], task["id"])[0]["output"]
    assert "context" not in output
    assert "execute" in store.get_task(task["id"])["context"]
