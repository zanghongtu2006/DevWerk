from __future__ import annotations

import hashlib
import json
import sys
import time
import threading
import sqlite3
import shutil
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import requests

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import CapabilityContext, CapabilityEntry, build_core_registry
from app.v1.completion_protocol import CompletionAttemptGuard, CompletionProtocolStalled, parse_completion_submission
from app.v1.domain import AgentModelResponse, AgentToolCall, ToolResult
from app.v1.domain import Transition
from app.v1.execution_control import ExecutionBudgetExceeded, ExecutionOwnershipLost, ExecutionReplayUncertain
from app.v1.execution_ledger import ledger_entry
from app.v1.policy import ExecutionLimits
from app.v1.process_runner import run_command
from app.v1.runtime import WorkflowRuntime, _column_completion_contract
from app.v1.runtime import RuntimeSupervisor, LeaseKeeper
from app.v1.services.completion_admission import CompletionAdmissionService
from app.v1.services.dependency_resolver import canonical_task
from tests.helpers import agent_workflow, sequence_workflow, publish_planned_workflow, create_planned_task
from tests.test_loop_contract import NOVEL_BINDINGS
from tests.test_orchestration_policy_contract import _waiting_poll_task
from app.v1.loops import LoopCatalog


def setup_task(store, tmp_path, workflow=None):
    project = store.create_project("P0", "", str(tmp_path / "project"))
    workflow = workflow or sequence_workflow()
    publish_planned_workflow(store, project["id"], workflow)
    task = create_planned_task(store, project["id"], "work")
    return project, task


def test_runtime_completion_accepts_preloaded_context_and_ignores_model_ids():
    workflow = agent_workflow()
    contract = _column_completion_contract(workflow, workflow.column("work"))
    submission = parse_completion_submission({"outcome": "success", "output": {"delivered": True},
                                            "summary": "reviewed preloaded facts", "evidence_ids": ["forged"]}, contract, [])
    assert submission.evidence_ids == ()
    assert CompletionAdmissionService().evaluate(submission, contract, []).accepted
    ledger = [ledger_entry("r", str(i), "project.command.run", "process", ToolResult(ok=True, capability="project.command.run", output={"exit_code": 0})) for i in range(20)]
    submission = parse_completion_submission({"outcome": "success", "output": {"delivered": True}, "summary": "delivered"}, contract, ledger)
    assert len(submission.evidence_ids) == 20
    assert CompletionAdmissionService().evaluate(submission, contract, ledger).accepted


def test_fake_progress_cannot_reset_completion_rejection_ceiling():
    guard = CompletionAttemptGuard()
    with pytest.raises(CompletionProtocolStalled):
        for i in range(10):
            guard.observe_rejection({"summary": str(i)}, ValueError("invalid"), [
                ledger_entry("r", str(i), "project.command.run", "process", ToolResult(ok=True, capability="project.command.run", output={"text": str(i)}))
            ], completion_tool_name="column.complete")


def test_stale_generation_cannot_write(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    owned = store.claim_task(task["id"], "old")
    run = store.begin_run(owned, {})
    runtime = WorkflowRuntime(store, store.registry, "old")
    context = CapabilityContext(project["id"], project, store, task_id=task["id"], column_run_id=run["id"], execution_control=runtime._control(owned))
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET state_version=state_version+1,lease_owner='new' WHERE id=?", (task["id"],))
    with pytest.raises(ExecutionOwnershipLost):
        store.registry.dispatch("project.files.write", {"path": "stale.txt", "content": "bad"}, context)
    assert not (tmp_path / "project" / "stale.txt").exists()


def test_pause_does_not_invalidate_current_column_finish(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    owned = store.claim_task(task["id"], "worker")
    run = store.begin_run(owned, {})
    paused = store.pause_task(task["id"])
    assert paused["state_version"] == owned["state_version"]
    store.finish_run(owned, run["id"], {}, "success", "execute")
    assert store.get_task(task["id"])["control_state"] == "paused"
    assert store.claim_task(task["id"], "another") is None


def test_protocol_failure_is_runtime_blocked_without_business_terminal(store, tmp_path):
    project, task = setup_task(store, tmp_path, agent_workflow())
    def model(*a, **kw):
        raise CompletionProtocolStalled("completion rejected")
    runtime = WorkflowRuntime(store, store.registry, "worker", AgentCore(store, store.registry, model))
    with pytest.raises(CompletionProtocolStalled):
        runtime.step(task["id"])
    failed = store.get_task(task["id"])
    assert (failed["status"], failed["control_state"], failed["failure_origin"]) == ("recovering", "paused", "agent_contract")
    assert failed["terminal_artifact_id"] is None
    assert failed["id"] not in store.runnable_task_ids()


def test_provider_connection_recovery_is_bounded(store, tmp_path):
    store.policy = store.policy.model_copy(update={"execution": ExecutionLimits(max_recovery_attempts=1)})
    project, task = setup_task(store, tmp_path, agent_workflow())
    def model(*a, **kw):
        raise requests.ConnectionError("connection reset")
    runtime = WorkflowRuntime(store, store.registry, "worker", AgentCore(store, store.registry, model, policy=store.policy))
    runtime.step(task["id"])
    assert store.get_task(task["id"])["status"] == "recovering"
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET next_retry_at=NULL WHERE id=?", (task["id"],))
    runtime.step(task["id"])
    blocked = store.get_task(task["id"])
    assert blocked["failure_disposition"] == "intervention_required"
    assert blocked["control_state"] == "paused"


def test_agent_wall_clock_exits_even_when_provider_hangs(store, tmp_path):
    project, task = setup_task(store, tmp_path, agent_workflow())
    def model(*a, **kw):
        time.sleep(1)
        return AgentModelResponse(text="late")
    policy = store.policy.model_copy(update={"execution": ExecutionLimits(agent_wall_seconds=0.1)})
    core = AgentCore(store, store.registry, model, policy=policy)
    started = time.monotonic()
    with pytest.raises(ExecutionBudgetExceeded):
        core.run(AgentRunSpec(kind="conversation", project=project, instruction="", instruction_revision=1, context={}, capability_ids=[]))
    assert time.monotonic() - started < 0.8


def test_command_timeout_and_output_are_bounded(tmp_path):
    result = run_command([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, ExecutionLimits(command_timeout_seconds=0.15))
    assert result["timed_out"]
    result = run_command([sys.executable, "-c", "while True: print('x'*8192,flush=True)"], tmp_path, ExecutionLimits(command_max_output_bytes=1024, command_timeout_seconds=3))
    assert result["output_truncated"]
    assert len(result["stdout"].encode()) <= 1024


def test_agent_cannot_redefine_command_success(store, tmp_path):
    project, _ = setup_task(store, tmp_path)
    result = store.registry.dispatch("project.command.run", {"argv": [sys.executable, "-c", "raise SystemExit(1)"], "success_exit_codes": [1]}, CapabilityContext(project["id"], project, store))
    assert not result.ok and result.output["exit_code"] == 1
    with store.connect() as db:
        receipt = db.execute("SELECT status,result_json FROM v1_execution_receipts WHERE capability='project.command.run'").fetchone()
    assert receipt[0] == "failed" and json.loads(receipt[1])["exit_code"] == 1


def test_completed_effect_replays_and_unknown_effect_blocks(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    owned = store.claim_task(task["id"], "worker")
    run = store.begin_run(owned, {})
    context = CapabilityContext(project["id"], project, store, task_id=task["id"], column_run_id=run["id"], execution_key=run["id"]+":effect:write:1")
    args = {"path": "once.txt", "content": "once"}
    first = store.registry.dispatch("project.files.write", args, context)
    second = store.registry.dispatch("project.files.write", args, context)
    assert first.output == second.output
    uncertain = replace(context, execution_key=run["id"]+":effect:unknown:1")
    store.start_execution_receipt(project["id"], uncertain.execution_key, "project.files.write", args)
    with pytest.raises(ExecutionReplayUncertain):
        store.registry.dispatch("project.files.write", args, uncertain)


def test_artifact_versions_preserve_old_bytes(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    context = CapabilityContext(project["id"], project, store, task_id=task["id"])
    for version in ["one", "two", "three"]:
        store.registry.dispatch("project.files.write", {"path": "same.txt", "content": version}, replace(context, execution_key=version))
    versions = store.artifacts(project["id"], task["id"])
    assert len(versions) == 3
    assert {store.artifact_repository.snapshot_text(v) for v in versions} == {"one", "two", "three"}


def test_failed_direct_dependency_resolves_successor(store, tmp_path):
    project, predecessor = setup_task(store, tmp_path)
    store.route_task_to_failed(predecessor["id"], "retry needed")
    successor = store.rerun_task(predecessor["id"])
    downstream = create_planned_task(store, project["id"], "downstream")
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_scheduling_entries SET state='queued',auto_admit=1,dependencies_json=? WHERE task_id=?", (json.dumps([predecessor["id"]]), downstream["id"]))
        db.execute("INSERT INTO v1_task_dependencies VALUES(?,?,?,'done',?)", (downstream["id"], predecessor["id"], project["id"], datetime.now(timezone.utc).isoformat()))
    assert downstream["id"] not in store.runnable_task_ids()
    WorkflowRuntime(store, store.registry, "worker").step(successor["id"])
    assert downstream["id"] in store.runnable_task_ids()
    context = store.task_dependency_context(project["id"], downstream["id"])
    assert {item["task_id"] for item in context} == {successor["id"]}
    with store.connect() as db:
        assert canonical_task(db, project["id"], predecessor["id"])["id"] == successor["id"]


def test_loop_apply_rolls_back_when_binding_event_fails(store, tmp_path, monkeypatch):
    project = store.create_project("atomic", "", str(tmp_path / "project"))
    original = store._event
    def fail(db, project_id, task_id, run_id, kind, data):
        if kind == "workflow.loop.applied":
            raise RuntimeError("injected apply failure")
        return original(db, project_id, task_id, run_id, kind, data)
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_workflows WHERE project_id=?", (project["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM v1_workflow_plans WHERE project_id=?", (project["id"],)).fetchone()[0] == 0


def test_loop_assets_survive_catalog_removal(store, tmp_path, monkeypatch):
    project = store.create_project("durable-loop", "", str(tmp_path / "project"))
    store.apply_loop(project["id"], "novel.production", NOVEL_BINDINGS)
    before = store.get_project_loop_assets(project["id"])
    assert before
    monkeypatch.setattr(store.loops, "get", lambda key: (_ for _ in ()).throw(KeyError(key)))
    assert store.get_project_loop_assets(project["id"]) == before


def test_completion_rejection_identifies_exact_repair_operation():
    workflow = agent_workflow()
    contract = _column_completion_contract(workflow, workflow.column("work"))
    args = {"argv": ["python", "-c", "import yaml; print(yaml.__version__)"]}
    failed = ledger_entry("r", "1", "project.command.run", "process", ToolResult(ok=False, capability="project.command.run", error={"message": "missing yaml"}), arguments=args)
    complete = {"outcome": "success", "output": {"delivered": True}, "summary": "done"}
    submission = parse_completion_submission(complete, contract, [failed])
    decision = CompletionAdmissionService().evaluate(submission, contract, [failed])
    assert not decision.accepted and "import yaml" in decision.rejection.message
    repaired = ledger_entry("r", "2", "project.command.run", "process", ToolResult(ok=True, capability="project.command.run", output={"exit_code": 0}), arguments={**args, "cwd": ".", "success_exit_codes": [0]})
    ledger = [failed, repaired]
    assert CompletionAdmissionService().evaluate(parse_completion_submission(complete, contract, ledger), contract, ledger).accepted


def test_await_claim_is_exclusive_and_waiting_keeps_resource(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    other = create_planned_task(store, project["id"], "other")
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_scheduling_entries SET resources_json=? WHERE project_id=?", (json.dumps(["workspace_path:shared"]), project["id"]))
    owned = store.claim_task(task["id"], "worker")
    run = store.begin_run(owned, {})
    handle = store.create_await_handle(owned, run["id"], provider="test", token=None, poll_capability=None, poll_arguments={}, next_check_seconds=1, success_outcome="success", waiting_kind="timer")
    assert store.claim_task(other["id"], "other-worker") is None
    first = store.claim_await(handle["id"], "first")
    assert first is not None
    assert store.claim_await(handle["id"], "second") is None
    store.release_await_claim(first)
    second = store.claim_await(handle["id"], "second")
    assert second is not None and second["state_version"] > first["state_version"]
    with pytest.raises(ExecutionOwnershipLost):
        store.settle_await_handle(handle["id"], "pending", {}, next_check_seconds=1, expected_task=first)


def test_conversation_lease_and_workflow_claim_do_not_overlap(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    job = store.create_conversation_job(project["id"], "discuss", True)
    job = store.claim_conversation_job(job["id"], "conversation-owner")
    assert store.claim_task(task["id"], "worker") is None
    store.fail_conversation_job(job["id"], "test turn ended")
    owned = store.claim_task(task["id"], "worker")
    assert owned is not None
    result = store.registry.dispatch("project.files.write", {"path": "conflict.txt", "content": "bad"}, CapabilityContext(project["id"], project, store, agent_run_id="direct-agent"))
    assert not result.ok and "active Workflow" in result.error["message"]
    assert not (tmp_path / "project" / "conflict.txt").exists()


def test_mailbox_can_reopen_but_discussion_cannot(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    store.route_task_to_failed(task["id"], "retry needed")
    def run_turn(supervision):
        turns = iter([AgentModelResponse(tool_calls=[AgentToolCall(id="reopen", name="task.reopen", arguments={"task_id": task["id"]})]), AgentModelResponse(text="handled")])
        core = AgentCore(store, store.registry, lambda *a, **kw: next(turns))
        return core.run(AgentRunSpec(kind="conversation", project=project, instruction="", instruction_revision=1,
                                    context={}, capability_ids=["task.reopen"], start_task=False, supervision_turn=supervision))
    run_turn(False)
    assert store.get_task(task["id"])["status"] == "failed"
    run_turn(True)
    assert store.get_task(task["id"])["status"] == "pending"


def test_supervisor_survives_one_failed_tick(store, monkeypatch):
    supervisor = RuntimeSupervisor(store, store.registry, interval=0.01)
    recovered = threading.Event()
    calls = 0
    def tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("temporary database lock")
        recovered.set()
    monkeypatch.setattr(supervisor, "_tick", tick)
    supervisor.start()
    try:
        assert recovered.wait(2)
        assert supervisor.health()["running"]
    finally:
        supervisor.stop()


def test_lease_keeper_exception_revokes_old_worker(store, tmp_path, monkeypatch):
    project, task = setup_task(store, tmp_path)
    owned = store.claim_task(task["id"], "worker")
    monkeypatch.setattr(store, "renew_lease", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    keeper = LeaseKeeper(store, task["id"], "worker", interval=0.01)
    keeper.start()
    keeper._thread.join(timeout=2)
    with pytest.raises(ExecutionOwnershipLost):
        store.assert_execution_owner(owned)
    keeper.stop()


def test_expired_conversation_job_settles_once(store, tmp_path):
    project, _ = setup_task(store, tmp_path)
    job = store.create_conversation_job(project["id"], "discuss", True)
    owned = store.claim_conversation_job(job["id"], "dead")
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_conversation_agents SET lease_until=? WHERE project_id=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), project["id"]))
    assert store.recover_expired_conversation_jobs() == [job["id"]]
    assert store.recover_expired_conversation_jobs() == []
    assert store.get_conversation_job(job["id"])["status"] == "failed"
    with pytest.raises(ExecutionOwnershipLost):
        store.assert_conversation_owner(owned)


def test_read_connection_closes_without_closing_borrowed_transaction(store):
    with store.connect() as db:
        assert db.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
    with store.tx(immediate=True) as owner:
        with store.connect() as borrowed:
            assert borrowed.execute("SELECT 1").fetchone()[0] == 1
        assert owner.execute("SELECT 2").fetchone()[0] == 2


def test_provider_retry_reuses_real_command_effect_and_restores_ledger(store, tmp_path):
    workflow = agent_workflow()
    workflow.column("work").executor.capabilities.append("project.command.run")
    project, task = setup_task(store, tmp_path, workflow)
    args = {"argv": [sys.executable, "-c", "from pathlib import Path; p=Path('once.txt'); p.open('a').write('x')"]}
    responses = iter([
        AgentModelResponse(tool_calls=[AgentToolCall(id="first", name="project.command.run", arguments=args)]),
        requests.ConnectionError("connection lost after committed command"),
        AgentModelResponse(tool_calls=[AgentToolCall(id="done", name="column.complete", arguments={"outcome": "success", "output": {"delivered": True}, "summary": "done"})]),
    ])
    def model(*a, **kw):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response
    runtime = WorkflowRuntime(store, store.registry, "worker", AgentCore(store, store.registry, model))
    runtime.step(task["id"])
    assert store.get_task(task["id"])["status"] == "recovering"
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET next_retry_at=NULL WHERE id=?", (task["id"],))
    runtime.step(task["id"])
    assert store.get_task(task["id"])["status"] == "done"
    assert (tmp_path / "project" / "once.txt").read_text() == "x"
    with store.connect() as db:
        runs = db.execute("SELECT id FROM v1_column_runs WHERE task_id=?", (task["id"],)).fetchall()
        assert len(runs) == 1
        assert db.execute("SELECT COUNT(*) FROM v1_column_attempts WHERE task_id=?", (task["id"],)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM v1_execution_receipts WHERE capability='project.command.run' AND project_id=?", (project["id"],)).fetchone()[0] == 1
    assert len(runtime._column_action_ledger(project["id"], runs[0][0])) == 1


def test_workflow_cycle_stops_at_visit_budget(store, tmp_path):
    workflow = agent_workflow()
    workflow.column("work").transitions.append(Transition(outcome="again", target="work"))
    store.policy = store.policy.model_copy(update={"execution": ExecutionLimits(max_column_visits=2)})
    _, task = setup_task(store, tmp_path, workflow)
    def model(*a, **kw):
        return AgentModelResponse(tool_calls=[AgentToolCall(id="again", name="column.complete", arguments={"outcome": "again", "output": {"delivered": False}, "summary": "more work"})])
    runtime = WorkflowRuntime(store, store.registry, "worker", AgentCore(store, store.registry, model))
    runtime.step(task["id"])
    runtime.step(task["id"])
    with pytest.raises(ExecutionBudgetExceeded):
        runtime.step(task["id"])
    blocked = store.get_task(task["id"])
    assert (blocked["status"], blocked["control_state"]) == ("recovering", "paused")
    assert blocked["terminal_artifact_id"] is None


def test_command_timeout_kills_descendant_after_parent_exit(tmp_path):
    child = "import time; from pathlib import Path; time.sleep(2); Path('late.txt').write_text('orphan')"
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    result = run_command([sys.executable, "-c", parent], tmp_path, ExecutionLimits(command_timeout_seconds=0.15))
    time.sleep(2.1)
    assert not (tmp_path / "late.txt").exists()
    assert result["timed_out"] or result["exit_code"] == 0


def test_wait_runtime_failure_preserves_handle_and_is_bounded(store, tmp_path, monkeypatch):
    project = store.create_project("poll retry", "", str(tmp_path / "project"))
    runtime, task, handle = _waiting_poll_task(store, project, {"status": "succeeded", "output": {"ready": True}})
    store.policy = store.policy.model_copy(update={"execution": ExecutionLimits(max_recovery_attempts=1)})
    original = store.registry.dispatch
    def disconnect(capability, *a, **kw):
        if capability == handle["poll_capability"]:
            raise requests.ConnectionError("poll network unavailable")
        return original(capability, *a, **kw)
    monkeypatch.setattr(store.registry, "dispatch", disconnect)
    runtime.reconcile_await(handle)
    assert store.get_task(task["id"])["status"] == "waiting"
    assert store.await_handle(handle["id"])["status"] == "pending"
    runtime.reconcile_await(store.await_handle(handle["id"]))
    blocked = store.get_task(task["id"])
    assert blocked["control_state"] == "paused" and blocked["terminal_artifact_id"] is None
    monkeypatch.setattr(store.registry, "dispatch", original)
    store.resume_task(task["id"])
    runtime.reconcile_await(store.await_handle(handle["id"]))
    assert store.get_task(task["id"])["status"] == "done"


def test_startup_pause_preserves_durable_wait(store, tmp_path):
    project = store.create_project("wait restart", "", str(tmp_path / "project"))
    runtime, task, handle = _waiting_poll_task(store, project, {"status": "succeeded", "output": {"ready": True}})
    store.prepare_workflow_startup(False)
    assert store.get_task(task["id"])["status"] == "waiting"
    assert store.await_handle(handle["id"])["status"] == "pending"
    assert store.due_await_handles() == []
    store.resume_task(task["id"])
    runtime.reconcile_await(store.await_handle(handle["id"]))
    assert store.get_task(task["id"])["status"] == "done"


def test_repeated_worker_loss_is_bounded(store, tmp_path):
    _, task = setup_task(store, tmp_path)
    store.policy = store.policy.model_copy(update={"execution": ExecutionLimits(max_recovery_attempts=1)})
    for i in range(2):
        owned = store.claim_task(task["id"], f"worker-{i}")
        assert owned
        store.begin_run(owned, {})
        with store.tx(immediate=True) as db:
            db.execute("UPDATE v1_tasks SET lease_until=? WHERE id=?", ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), task["id"]))
        assert store.recover_expired_task_leases() == [task["id"]]
    assert store.get_task(task["id"])["control_state"] == "paused"
    assert store.claim_task(task["id"], "third") is None


def test_invalid_successor_lineage_does_not_stop_other_tasks(store, tmp_path):
    project, predecessor = setup_task(store, tmp_path)
    store.route_task_to_failed(predecessor["id"], "failed")
    dependent = create_planned_task(store, project["id"], "dependent")
    independent = create_planned_task(store, project["id"], "independent")
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET resolved_by_task_id=id WHERE id=?", (predecessor["id"],))
        db.execute("UPDATE v1_scheduling_entries SET dependencies_json=? WHERE task_id=?", (json.dumps([predecessor["id"]]), dependent["id"]))
    assert independent["id"] in store.runnable_task_ids()
    assert dependent["id"] not in store.runnable_task_ids()
    assert store.claim_task(dependent["id"], "worker") is None
    with store.connect() as db:
        with pytest.raises(ValueError, match="cycle"):
            canonical_task(db, project["id"], predecessor["id"])
        assert canonical_task(db, "different-project", independent["id"]) is None
        assert canonical_task(db, project["id"], "missing") is None


def test_bad_loop_metadata_is_isolated(tmp_path):
    root = tmp_path / "loops"
    root.mkdir()
    shutil.copytree(Path(__file__).resolve().parents[1] / "loops" / "novel-production", root / "valid")
    (root / "broken").mkdir()
    (root / "broken" / "loop.meta").write_text("invalid", encoding="utf-8")
    catalog = LoopCatalog(root)
    assert [item["loop_key"] for item in catalog.list()] == ["novel.production"]
    assert "broken" in catalog.errors


def test_structured_poll_error_does_not_assert_external_failure(store, tmp_path, monkeypatch):
    project = store.create_project("poll ToolResult", "", str(tmp_path / "project"))
    runtime, task, handle = _waiting_poll_task(store, project, {"status": "succeeded", "output": {"ready": True}})
    original = store.registry.dispatch
    def transient(capability, *a, **kw):
        if capability == handle["poll_capability"]:
            return ToolResult(ok=False, capability=capability, error={"type": "RemoteTransient", "message": "network retry"})
        return original(capability, *a, **kw)
    monkeypatch.setattr(store.registry, "dispatch", transient)
    runtime.reconcile_await(handle)
    current = store.get_task(task["id"])
    assert current["status"] == "waiting" and current["control_state"] == "active"
    assert current["failure_origin"] == "tool"
    assert store.await_handle(handle["id"])["checkpoint"]["runtime_retry"]["count"] == 1
    monkeypatch.setattr(store.registry, "dispatch", original)
    runtime.reconcile_await(store.await_handle(handle["id"]))
    assert store.get_task(task["id"])["status"] == "done"


@pytest.mark.parametrize("symbolic", [False, True])
def test_three_generation_successor_is_used_for_schedule_context_and_artifacts(store, tmp_path, symbolic):
    project, first = setup_task(store, tmp_path)
    store.route_task_to_failed(first["id"], "first failed")
    second = store.rerun_task(first["id"])
    store.route_task_to_failed(second["id"], "second failed")
    third = store.rerun_task(second["id"])
    downstream = create_planned_task(store, project["id"], "downstream")
    reference = f"task-plan:{first['task_plan_id']}:{first['proposed_task_ref']}" if symbolic else first["id"]
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_scheduling_entries SET state='queued',auto_admit=1,dependencies_json=? WHERE task_id=?", (json.dumps([reference]), downstream["id"]))
        if not symbolic:
            db.execute("INSERT INTO v1_task_dependencies VALUES(?,?,?,'done',?)", (downstream["id"], first["id"], project["id"], datetime.now(timezone.utc).isoformat()))
    assert downstream["id"] not in store.runnable_task_ids()
    WorkflowRuntime(store, store.registry, "worker").step(third["id"])
    assert downstream["id"] in store.runnable_task_ids()
    assert {item["task_id"] for item in store.task_dependency_context(project["id"], downstream["id"])} == {third["id"]}
    accepted = store.accepted_dependency_artifacts(project["id"], downstream["id"])
    assert accepted and {item["task_id"] for item in accepted} == {third["id"]}


def test_legacy_successful_command_receipt_is_reused_after_normalization(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    args = {"argv": [sys.executable, "-c", "from pathlib import Path; Path('must-not-run.txt').touch()"], "success_exit_codes": [0, 1]}
    key = "legacy:effect:command:1"
    store.start_execution_receipt(project["id"], key, "project.command.run", args)
    store.finish_execution_receipt(project["id"], key, True, {"exit_code": 0, "stdout": "already ran"}, None)
    result = store.registry.dispatch("project.command.run", {"argv": args["argv"], "cwd": "."}, CapabilityContext(project["id"], project, store, execution_key=key))
    assert result.ok and result.output["stdout"] == "already ran"
    assert not (tmp_path / "project" / "must-not-run.txt").exists()


def test_unknown_command_receipt_never_starts_process(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    owned = store.claim_task(task["id"], "worker")
    run = store.begin_run(owned, {})
    key = run["id"] + ":effect:unknown:1"
    args = {"argv": [sys.executable, "-c", "from pathlib import Path; Path('duplicate.txt').touch()"]}
    store.start_execution_receipt(project["id"], key, "project.command.run", args)
    with pytest.raises(ExecutionReplayUncertain):
        store.registry.dispatch("project.command.run", args, CapabilityContext(project["id"], project, store, task_id=task["id"], column_run_id=run["id"], execution_key=key))
    assert not (tmp_path / "project" / "duplicate.txt").exists()


def test_effect_with_invalid_result_stays_unknown(store, tmp_path):
    project, task = setup_task(store, tmp_path)
    def invalid_result(_args, _context):
        (tmp_path / "project" / "effect.txt").write_text("occurred")
        return {"unexpected": True}
    store.registry.register(CapabilityEntry(id="test.bad-result", description="fault", input_schema={},
        output_schema={"type": "object", "required": ["expected"]}, handler=invalid_result, side_effect_kind="write"))
    with pytest.raises(ExecutionReplayUncertain):
        store.registry.dispatch("test.bad-result", {}, CapabilityContext(project["id"], project, store))
    with store.connect() as db:
        assert db.execute("SELECT status FROM v1_execution_receipts WHERE capability='test.bad-result'").fetchone()[0] == "started"
    assert (tmp_path / "project" / "effect.txt").read_text() == "occurred"
