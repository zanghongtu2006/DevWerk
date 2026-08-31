from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.v1.capabilities import CapabilityEntry, build_core_registry
from app.v1.domain import (
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    PollWaitPolicy,
    ToolResult,
    Transition,
    WorkflowDefinition,
)
from app.v1.runtime import WorkflowRuntime
from tests.helpers import (
    create_planned_task,
    publish_initial_workflow,
    publish_planned_workflow,
    readiness,
    sequence_workflow,
    task_plan,
    workflow_plan,
)


def _waiting_poll_task(store, project, poll_output):
    registry = store.registry
    registry.register(CapabilityEntry(
        id="test.async.failure", description="begin async work", input_schema={}, output_schema={},
        handler=lambda _args, _ctx: ToolResult(
            ok=True, status="awaiting", capability="test.async.failure",
            await_handle_draft={
                "provider": "test",
                "poll_capability": "test.poll.failure",
                "poll_arguments": {},
            },
            checkpoint={"provider_job": "job-failure"},
        ),
    ))
    registry.register(CapabilityEntry(
        id="test.poll.failure", description="poll async work", input_schema={}, output_schema={},
        handler=lambda _args, _ctx: poll_output,
    ))
    workflow = WorkflowDefinition(name="async failure", entry="execute", columns=[ColumnDefinition(
        key="execute", name="Execute",
        executor=CapabilitySequenceExecutor(steps=[
            CapabilityStep(capability="test.async.failure"),
            CapabilityStep(capability="test.poll.failure"),
        ]),
        wait_policy=PollWaitPolicy(
            poll_capability="test.poll.failure", poll_interval_seconds=1
        ),
        transitions=[
            Transition(outcome="success", target="done"),
            Transition(outcome="failure", target="failed"),
        ],
    )])
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "async failure")
    runtime = WorkflowRuntime(store, registry, "worker")
    runtime.step(task["id"])
    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_await_handles SET next_check_at=? WHERE task_id=? AND status='pending'",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), task["id"]),
        )
    return runtime, task, store.due_await_handles()[0]


def test_workflow_and_task_use_separate_persisted_plans(store, tmp_path):
    project = store.create_project("planned", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    plan = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, plan["id"])
    concrete = store.create_task_plan(project["id"], task_plan(revision["id"], workflow))
    task = store.create_task(
        project["id"], task_plan_id=concrete["id"], proposed_task_ref="primary",
    )
    assert revision["workflow_plan_id"] == plan["id"]
    assert task["task_plan_id"] == concrete["id"]


def test_dependency_releases_only_after_predecessor_done(store, tmp_path):
    project = store.create_project("dependency", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    method = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, method["id"])
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child_item = base.tasks[0].model_copy(update={"proposed_task_ref": "child", "title": "child", "dependencies": ["root"]})
    concrete = store.create_task_plan(project["id"], base.model_copy(update={"tasks": [base.tasks[0], child_item]}))
    root = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="root")
    child = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="child")
    assert child["id"] not in store.runnable_task_ids()
    WorkflowRuntime(store, build_core_registry(store.policy), "root-worker").step(root["id"])
    assert store.get_task(root["id"])["status"] == "done"
    assert child["id"] in store.runnable_task_ids()
    WorkflowRuntime(store, build_core_registry(store.policy), "child-worker").step(child["id"])
    assert store.get_task(child["id"])["status"] == "done"


def test_failed_predecessor_requires_explicit_successor(store, tmp_path):
    project = store.create_project("failed dependency", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    method = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, method["id"])
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child_item = base.tasks[0].model_copy(update={"proposed_task_ref": "child", "title": "child", "dependencies": ["root"]})
    concrete = store.create_task_plan(project["id"], base.model_copy(update={"tasks": [base.tasks[0], child_item]}))
    root = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="root")
    child = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="child")
    store.route_task_to_failed(root["id"], "visible predecessor failure")
    assert child["id"] not in store.runnable_task_ids()
    successor = store.rerun_task(root["id"])
    WorkflowRuntime(store, build_core_registry(store.policy), "successor-worker").step(successor["id"])
    assert store.get_task(successor["id"])["status"] == "done"
    assert child["id"] in store.runnable_task_ids()


def test_task_plan_queue_is_admitted_when_dependencies_are_already_satisfied(store, tmp_path):
    project = store.create_project("queue", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    _plan, revision = publish_planned_workflow(store, project["id"], workflow)
    concrete = store.create_task_plan(project["id"], task_plan(revision["id"], workflow, title="queued", readiness_data=readiness(decision="queue")))
    task = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="primary")
    assert task["id"] in store.runnable_task_ids()
    assert store.claim_task(task["id"], "worker") is not None


def test_task_plan_queue_auto_admits_after_dependency_completes(store, tmp_path):
    project = store.create_project("dependency queue", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    _plan, revision = publish_planned_workflow(store, project["id"], workflow)
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child = base.tasks[0].model_copy(update={
        "proposed_task_ref": "child",
        "title": "child",
        "dependencies": ["root"],
        "readiness": base.tasks[0].readiness.model_copy(update={"decision": "queue"}),
    })
    concrete = store.create_task_plan(project["id"], base.model_copy(update={"tasks": [base.tasks[0], child]}))
    root = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="root")
    queued = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="child")
    assert queued["id"] not in store.runnable_task_ids()
    WorkflowRuntime(store, build_core_registry(store.policy), "root-worker").step(root["id"])
    assert queued["id"] in store.runnable_task_ids()


def test_workflow_owned_queue_keeps_auto_admission_across_hold_and_release(store, tmp_path):
    project = store.create_project("held dependency queue", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    _plan, revision = publish_planned_workflow(store, project["id"], workflow)
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child = base.tasks[0].model_copy(update={
        "proposed_task_ref": "child",
        "title": "child",
        "dependencies": ["root"],
        "readiness": base.tasks[0].readiness.model_copy(update={"decision": "queue"}),
    })
    concrete = store.create_task_plan(
        project["id"], base.model_copy(update={"tasks": [base.tasks[0], child]})
    )
    root = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="root")
    queued = store.create_task(project["id"], task_plan_id=concrete["id"], proposed_task_ref="child")

    store.schedule_task(project["id"], queued["id"], "hold", 0, None, None, None, None)
    held = store.task_scheduling(project["id"], queued["id"])
    assert held["state"] == "hold"
    assert held["auto_admit"] is True

    store.schedule_task(project["id"], queued["id"], "queued", 0, None, None, None, None)
    released = store.task_scheduling(project["id"], queued["id"])
    assert released["state"] == "queued"
    assert released["auto_admit"] is True

    WorkflowRuntime(store, build_core_registry(store.policy), "root-worker").step(root["id"])
    assert queued["id"] in store.runnable_task_ids()


def test_starting_task_plan_materializes_complete_dependency_graph(store, tmp_path):
    project = store.create_project("plan start", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    _plan, revision = publish_planned_workflow(store, project["id"], workflow)
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child = base.tasks[0].model_copy(update={
        "proposed_task_ref": "child",
        "title": "child",
        "dependencies": ["root"],
        "readiness": base.tasks[0].readiness.model_copy(update={"decision": "queue"}),
    })
    concrete = store.create_task_plan(
        project["id"],
        base.model_copy(update={"tasks": [base.tasks[0], child]}),
    )

    requested = store.materialize_task_plan(
        project["id"],
        task_plan_id=concrete["id"],
        proposed_task_ref="root",
    )

    tasks = store.list_tasks(project["id"])
    assert requested["proposed_task_ref"] == "root"
    assert {task["proposed_task_ref"] for task in tasks} == {"root", "child"}
    queued = next(task for task in tasks if task["proposed_task_ref"] == "child")
    assert queued["id"] not in store.runnable_task_ids()

    WorkflowRuntime(store, build_core_registry(store.policy), "root-worker").step(requested["id"])
    assert queued["id"] in store.runnable_task_ids()

    repeated = store.materialize_task_plan(
        project["id"],
        task_plan_id=concrete["id"],
        proposed_task_ref="root",
    )
    assert repeated["id"] == requested["id"]
    assert len(store.list_tasks(project["id"])) == 2


def test_task_plan_preflight_failure_materializes_zero_tasks(store, tmp_path, monkeypatch):
    project = store.create_project("plan preflight", "", str(tmp_path / "preflight"))
    workflow = sequence_workflow()
    _plan, revision = publish_planned_workflow(store, project["id"], workflow)
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child = base.tasks[0].model_copy(update={
        "proposed_task_ref": "child",
        "title": "child",
        "dependencies": ["root"],
        "readiness": base.tasks[0].readiness.model_copy(update={"decision": "queue"}),
    })
    concrete = store.create_task_plan(
        project["id"], base.model_copy(update={"tasks": [base.tasks[0], child]})
    )
    original = store._prepare_task_materialization

    def fail_second(*args, **kwargs):
        prepared = original(*args, **kwargs)
        if args[3].proposed_task_ref == "child":
            raise ValueError("second Task preflight failed")
        return prepared

    monkeypatch.setattr(store, "_prepare_task_materialization", fail_second)
    with pytest.raises(ValueError, match="second Task preflight failed"):
        store.materialize_task_plan(
            project["id"], task_plan_id=concrete["id"], proposed_task_ref="root"
        )

    assert store.list_tasks(project["id"]) == []


def test_task_plan_insert_failure_rolls_back_complete_graph(store, tmp_path, monkeypatch):
    project = store.create_project("plan rollback", "", str(tmp_path / "rollback"))
    workflow = sequence_workflow()
    _plan, revision = publish_planned_workflow(store, project["id"], workflow)
    base = task_plan(revision["id"], workflow, task_ref="root", title="root")
    child = base.tasks[0].model_copy(update={
        "proposed_task_ref": "child",
        "title": "child",
        "dependencies": ["root"],
        "readiness": base.tasks[0].readiness.model_copy(update={"decision": "queue"}),
    })
    concrete = store.create_task_plan(
        project["id"], base.model_copy(update={"tasks": [base.tasks[0], child]})
    )
    original = store._insert_prepared_task
    calls = 0

    def fail_during_insert(db, prepared):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("materialization interrupted")
        return original(db, prepared)

    monkeypatch.setattr(store, "_insert_prepared_task", fail_during_insert)
    with pytest.raises(RuntimeError, match="materialization interrupted"):
        store.materialize_task_plan(
            project["id"], task_plan_id=concrete["id"], proposed_task_ref="root"
        )

    assert store.list_tasks(project["id"]) == []


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


def test_recoverable_await_failure_atomically_enters_recovery(store, tmp_path):
    project = store.create_project("recoverable await", "", str(tmp_path / "recoverable"))
    runtime, task, handle = _waiting_poll_task(
        store,
        project,
        {
            "status": "failed",
            "recoverable": True,
            "error_category": "external_transient",
            "error": {"type": "RemoteTransient", "message": "try again"},
        },
    )

    runtime.reconcile_await(handle)

    resolved = store.get_task(task["id"])
    assert resolved["status"] == "recovering"
    assert store.await_handle(handle["id"])["status"] == "failed"
    assert store.runs(project["id"], task["id"])[0]["status"] == "interrupted"
    assert store.attempts(project["id"], task["id"])[0]["status"] == "interrupted"


def test_permanent_await_failure_atomically_reaches_failed(store, tmp_path):
    project = store.create_project("permanent await", "", str(tmp_path / "permanent"))
    runtime, task, handle = _waiting_poll_task(
        store,
        project,
        {
            "status": "failed",
            "error": {"type": "RemoteRejected", "message": "invalid request"},
        },
    )

    runtime.reconcile_await(handle)

    resolved = store.get_task(task["id"])
    assert resolved["status"] == "failed"
    assert store.await_handle(handle["id"])["status"] == "failed"
    assert store.runs(project["id"], task["id"])[0]["status"] == "failed"
    assert store.attempts(project["id"], task["id"])[0]["status"] == "failed"
    assert any(item["type"] == "task.failed" for item in store.events(project["id"]))


def test_waiting_task_retry_is_rejected_without_orphaning_await(store, tmp_path):
    project = store.create_project("waiting retry", "", str(tmp_path / "waiting-retry"))
    _runtime, task, handle = _waiting_poll_task(
        store,
        project,
        {"status": "pending"},
    )

    with pytest.raises(ValueError, match="Await Handle is active"):
        store.retry_task(task["id"])

    assert store.get_task(task["id"])["status"] == "waiting"
    assert store.await_handle(handle["id"])["status"] == "pending"


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
    assert store.claim_conversation_job(job["id"], "conversation-session-owner") is not None
    store.finish_conversation_job(job["id"], task["id"], result={})

    observed = store.get_task(task["id"])
    assert observed["observed_at"] is not None
    assert observed["state_version"] == state_version


def test_platform_policy_is_compact_project_manager_identity(store):
    content = store.latest_platform_policy().content
    assert len(content) < 2_000
    assert "professional project manager and agile coach" in content
    assert "Discover filesystem Loops first" in content
    assert "never invent an initial Workflow" in content
    assert "supervise them to `done` or `failed`" in content
    assert "Never describe an intended state change as completed" in content
