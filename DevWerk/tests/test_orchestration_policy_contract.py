from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.v1.capabilities import CapabilityEntry, build_core_registry
from app.v1.domain import (
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    OrchestrationTaskPlan,
    PollWaitPolicy,
    ToolResult,
    Transition,
    WorkflowDefinition,
)
from app.v1.runtime import WorkflowRuntime
from tests.helpers import (
    create_planned_task,
    orchestration_plan,
    publish_planned_workflow,
    readiness,
    sequence_workflow,
)


def test_workflow_and_task_require_persisted_orchestration(store, tmp_path):
    project = store.create_project("planned", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    plan = store.create_orchestration_plan(project["id"], orchestration_plan(workflow))
    revision = store.publish_workflow(project["id"], workflow, plan["id"])
    task = store.create_task(
        project["id"], "planned task", "", {}, readiness(),
        orchestration_plan_id=plan["id"], proposed_task_ref="primary",
    )
    assert revision["orchestration_plan_id"] == plan["id"]
    assert task["orchestration_plan_id"] == plan["id"]


def test_dependency_releases_only_after_predecessor_done(store, tmp_path):
    project = store.create_project("dependency", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    base = orchestration_plan(workflow)
    tasks = [
        OrchestrationTaskPlan(
            proposed_task_ref="root", objective="Deliver root",
            workflow_fit="Every process column applies.", agent_execution="forbidden",
            review_scope="Review root.", retry_scope="Retry root.",
        ),
        OrchestrationTaskPlan(
            proposed_task_ref="child", objective="Deliver child",
            workflow_fit="Every process column applies.", agent_execution="forbidden",
            dependencies=["root"], review_scope="Review child.", retry_scope="Retry child.",
        ),
    ]
    plan = store.create_orchestration_plan(project["id"], base.model_copy(update={
        "task_portfolio": tasks,
        "representative_task_ref": "root",
    }))
    store.publish_workflow(project["id"], workflow, plan["id"])
    root = store.create_task(
        project["id"], "root", "", {}, readiness(objective="Deliver root"),
        orchestration_plan_id=plan["id"], proposed_task_ref="root",
    )
    child = store.create_task(
        project["id"], "child", "", {}, readiness(objective="Deliver child", dependencies=["root"]),
        orchestration_plan_id=plan["id"], proposed_task_ref="child",
    )
    assert child["id"] not in store.runnable_task_ids()
    WorkflowRuntime(store, build_core_registry(store.policy), "root-worker").step(root["id"])
    assert store.get_task(root["id"])["status"] == "done"
    assert child["id"] in store.runnable_task_ids()
    WorkflowRuntime(store, build_core_registry(store.policy), "child-worker").step(child["id"])
    assert store.get_task(child["id"])["status"] == "done"


def test_failed_predecessor_requires_explicit_successor(store, tmp_path):
    project = store.create_project("failed dependency", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    base = orchestration_plan(workflow)
    base.task_portfolio = [
        OrchestrationTaskPlan(
            proposed_task_ref="root", objective="Deliver root",
            workflow_fit="Every process column applies.", agent_execution="forbidden",
            review_scope="Review root.", retry_scope="Retry root.",
        ),
        OrchestrationTaskPlan(
            proposed_task_ref="child", objective="Deliver child",
            workflow_fit="Every process column applies.", agent_execution="forbidden",
            dependencies=["root"], review_scope="Review child.", retry_scope="Retry child.",
        ),
    ]
    base.representative_task_ref = "root"
    plan = store.create_orchestration_plan(project["id"], base)
    store.publish_workflow(project["id"], workflow, plan["id"])
    root = store.create_task(
        project["id"], "root", "", {}, readiness(objective="Deliver root"),
        orchestration_plan_id=plan["id"], proposed_task_ref="root",
    )
    child = store.create_task(
        project["id"], "child", "", {}, readiness(objective="Deliver child"),
        orchestration_plan_id=plan["id"], proposed_task_ref="child",
    )
    store.route_task_to_failed(root["id"], "visible predecessor failure")
    assert child["id"] not in store.runnable_task_ids()
    successor = store.rerun_task(root["id"])
    WorkflowRuntime(store, build_core_registry(store.policy), "successor-worker").step(successor["id"])
    assert store.get_task(successor["id"])["status"] == "done"
    assert child["id"] in store.runnable_task_ids()


def test_explicit_queue_is_not_auto_admitted(store, tmp_path):
    project = store.create_project("queue", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    plan, _revision = publish_planned_workflow(store, project["id"], workflow)
    task = store.create_task(
        project["id"], "queued", "", {}, readiness(decision="queue"),
        orchestration_plan_id=plan["id"], proposed_task_ref="primary",
    )
    assert task["id"] not in store.runnable_task_ids()
    assert store.claim_task(task["id"], "worker") is None


def test_poll_wait_resumes_without_deadline_or_timeout_route(store, tmp_path):
    project = store.create_project("async", "", str(tmp_path / "project"))
    registry = store.registry
    registry.register(CapabilityEntry(
        id="test.async", description="begin async work", input_schema={}, output_schema={},
        handler=lambda _args, _ctx: ToolResult(
            ok=True, status="awaiting", capability="test.async",
            await_handle_draft={"provider": "test", "poll_capability": "test.poll", "poll_arguments": {}},
            checkpoint={"provider_job": "job-1"},
        ),
    ))
    registry.register(CapabilityEntry(
        id="test.poll", description="poll async work", input_schema={}, output_schema={},
        handler=lambda _args, _ctx: {"status": "succeeded", "output": {"value": "ready"}},
    ))
    workflow = WorkflowDefinition(name="async", entry="execute", columns=[ColumnDefinition(
        key="execute", name="Execute",
        executor=CapabilitySequenceExecutor(steps=[CapabilityStep(capability="test.async"), CapabilityStep(capability="test.poll")]),
        wait_policy=PollWaitPolicy(poll_capability="test.poll", poll_interval_seconds=1),
        transitions=[Transition(outcome="success", target="done"), Transition(outcome="failure", target="failed")],
    )])
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "async sequence")
    runtime = WorkflowRuntime(store, registry, "worker")
    runtime.step(task["id"])
    assert store.get_task(task["id"])["status"] == "waiting"
    handle = store.await_handle(store.due_await_handles(limit=1)[0]["id"]) if store.due_await_handles(limit=1) else None
    if handle is None:
        with store.tx(immediate=True) as db:
            db.execute("UPDATE v1_await_handles SET next_check_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        handle = store.due_await_handles()[0]
    assert "hard_deadline_at" not in handle
    runtime.reconcile_await(handle)
    assert store.get_task(task["id"])["status"] == "done"


def test_runtime_audit_records_policy_identity_without_execution_budget(store, tmp_path):
    project = store.create_project("audit", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "execute")
    WorkflowRuntime(store, build_core_registry(store.policy), "worker").step(task["id"])
    run = store.runs(project["id"], task["id"])[0]
    assert run["runtime_policy_hash"] == store.policy.policy_hash
    assert run.get("budget", {}) == {}


def test_mailbox_acknowledgement_is_audited(store, tmp_path):
    project = store.create_project("mailbox", "", str(tmp_path / "project"))
    with store.tx(immediate=True) as db:
        store._mailbox(db, project["id"], "task.failed", None, None, {"reason": "test"})
    pending = store.mailbox(project["id"])
    assert store.observe_mailbox(project["id"], pending[0]["id"])
    acknowledged = store.mailbox(project["id"], state="acknowledged")
    assert acknowledged[0]["governance_decision_id"]


def test_mailbox_observation_does_not_invalidate_running_task_state(store, tmp_path):
    project = store.create_project("mailbox-running-task", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "execute")
    claimed = store.claim_task(task["id"], "runtime-worker")
    assert claimed is not None
    state_version = claimed["state_version"]

    with store.tx(immediate=True) as db:
        store._mailbox(db, project["id"], "column.waiting", task["id"], None, {"reason": "observe"})
    mailbox_id = store.mailbox(project["id"])[0]["id"]
    job = store.create_conversation_job(project["id"], "", False)
    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_conversation_jobs SET mailbox_ids_json=? WHERE id=?",
            (f"[{mailbox_id}]", job["id"]),
        )
    assert store.claim_conversation_job(job["id"], "conversation-worker") is not None
    store.finish_conversation_job(job["id"], task["id"], result={})

    observed = store.get_task(task["id"])
    assert observed["observed_at"] is not None
    assert observed["state_version"] == state_version


def test_platform_policy_is_compact_project_manager_identity(store):
    content = store.latest_platform_policy().content
    assert len(content) < 2_000
    assert "professional project manager and agile coach" in content
    assert "Project and Kanban APIs" in content
    assert "supervise them to `done` or `failed`" in content
    assert "Never describe an intended state change as completed" in content
