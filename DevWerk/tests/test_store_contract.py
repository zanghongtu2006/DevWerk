from __future__ import annotations

import sqlite3

import pytest

from tests.helpers import sequence_workflow, readiness


def test_project_has_one_persistent_agent_and_versioned_instruction(store, tmp_path):
    project = store.create_project("alpha", "", str(tmp_path / "alpha"), "initial")
    first = store.conversation_agent(project["id"])
    updated = store.update_conversation_instruction(project["id"], "changed in conversation")

    assert first["logical_id"] == updated["logical_id"]
    assert updated["instruction"] == "changed in conversation"
    assert updated["instruction_revision"] == first["instruction_revision"] + 1


def test_project_base_dir_is_canonical_unique_and_isolated(store, tmp_path):
    alpha = store.create_project("alpha", "", str(tmp_path / "alpha" / ".." / "alpha"))
    beta = store.create_project("beta", "", str(tmp_path / "beta"))
    store.add_message(alpha["id"], "user", "alpha-only")

    assert alpha["base_dir"] == str((tmp_path / "alpha").resolve())
    assert store.messages(beta["id"]) == []
    with pytest.raises(sqlite3.IntegrityError):
        store.create_project("duplicate", "", str(tmp_path / "alpha"))


def test_workflow_revisions_are_immutable_and_tasks_remain_pinned(store, tmp_path):
    project = store.create_project("revision", "", str(tmp_path / "project"))
    first = store.publish_workflow(project["id"], sequence_workflow(name="one"))
    first_task = store.create_task(project["id"], "first", "", {}, readiness())
    second = store.publish_workflow(project["id"], sequence_workflow(name="two", path="two.txt"))

    assert second["revision"] == first["revision"] + 1
    assert store.get_task(first_task["id"])["workflow_revision_id"] == first["id"]
    assert store.workflow_by_id(project["id"], first["id"]).name == "one"


def test_agent_run_messages_and_tool_invocations_are_auditable(store, tmp_path):
    project = store.create_project("audit", "", str(tmp_path / "project"))
    run = store.begin_agent_run(
        project_id=project["id"],
        kind="conversation",
        instruction_revision=1,
        instruction_snapshot="",
        context_snapshot={"value": 1},
        capabilities=["system.noop"],
    )
    store.add_agent_message(run["id"], "assistant", "", [{"id": "call-1"}])
    store.record_tool_invocation(
        agent_run_id=run["id"],
        tool_call_id="call-1",
        capability="system.noop",
        arguments={},
        result={"ok": True},
        ok=True,
    )
    finished = store.finish_agent_run(run["id"], "succeeded", "done", None, 1, 1)

    assert finished["context"] == {"value": 1}
    assert store.agent_messages(project["id"], run["id"])[0]["tool_calls"] == [{"id": "call-1"}]
    assert store.tool_invocations(project["id"], run["id"])[0]["capability"] == "system.noop"


def test_sqlite_has_wal_and_runtime_read_indexes(store):
    with store.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_v1_tasks_dispatch", "idx_v1_runs_task", "idx_v1_agent_runs_project", "idx_v1_tool_invocations_run", "idx_v1_mailbox_pending"} <= indexes


def test_nonterminal_pending_and_pause_deadlines_fail_explicitly(store, tmp_path):
    project = store.create_project("deadlines", "", str(tmp_path / "project"))
    store.publish_workflow(project["id"], sequence_workflow())

    pending = store.create_task(project["id"], "pending", "", {}, readiness())
    with store.connect() as db:
        db.execute("UPDATE v1_tasks SET pending_deadline_at='2000-01-01T00:00:00+00:00' WHERE id=?", (pending["id"],))
    assert store.expire_nonterminal_deadlines() == 1
    failed_pending = store.get_task(pending["id"])
    assert failed_pending["status"] == "failed"
    assert store.mailbox(project["id"])[-1]["event_type"] == "task.failed"

    paused = store.rerun_task(pending["id"])
    paused = store.pause_task(paused["id"], 60)
    assert paused["control_state"] == "paused"
    with store.connect() as db:
        db.execute("UPDATE v1_tasks SET pause_deadline_at='2000-01-01T00:00:00+00:00' WHERE id=?", (paused["id"],))
    assert store.expire_nonterminal_deadlines() == 1
    assert store.get_task(paused["id"])["status"] == "failed"
