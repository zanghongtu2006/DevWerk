from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.v1.capabilities import CapabilityContext, build_core_registry
from app.v1.domain import (
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    ConflictDomain,
    ExactTaskInputString,
    TaskPlan,
    TaskPlanItem,
    Transition,
    WorkflowDefinition,
    WorkflowPlan,
)
from app.v1.runtime import WorkflowRuntime
from tests.helpers import create_planned_task, publish_initial_workflow, publish_planned_workflow, sequence_workflow, readiness
from tests.helpers import agent_workflow, task_plan, workflow_plan


def test_project_has_one_persistent_agent_and_versioned_instruction(store, tmp_path):
    project = store.create_project("alpha", "", str(tmp_path / "alpha"), "initial")
    first = store.conversation_agent(project["id"])
    updated = store.update_conversation_instruction(project["id"], "changed in conversation")

    assert first["logical_id"] == updated["logical_id"]
    assert updated["instruction"] == "changed in conversation"
    assert updated["instruction_revision"] == first["instruction_revision"] + 1


def test_conversation_messages_page_by_stable_message_id(store, tmp_path):
    project = store.create_project("messages", "", str(tmp_path / "messages"))
    messages = [
        store.add_message(project["id"], "user" if index % 2 == 0 else "assistant", f"message-{index}")
        for index in range(5)
    ]

    assert [item["id"] for item in store.messages(project["id"], 2)] == [messages[3]["id"], messages[4]["id"]]
    assert [item["id"] for item in store.messages(project["id"], 10, after_id=messages[2]["id"])] == [
        messages[3]["id"], messages[4]["id"]
    ]
    assert [item["id"] for item in store.messages(project["id"], 2, before_id=messages[3]["id"])] == [
        messages[1]["id"], messages[2]["id"]
    ]
    with pytest.raises(ValueError, match="mutually exclusive"):
        store.messages(project["id"], after_id=messages[0]["id"], before_id=messages[-1]["id"])


def test_conversation_session_projects_human_dialogue_without_internal_notifications(
    store,
    tmp_path,
):
    project = store.create_project("session projection", "", str(tmp_path / "session"))
    identity = store.conversation_agent(project["id"])
    first = store.add_message(project["id"], "user", "Build the Project.")
    reply = store.add_message(project["id"], "assistant", "The Workflow is ready.")
    store.add_message(
        project["id"],
        "assistant",
        "Internal task notification.",
        {"kind": "notification", "status": "succeeded"},
    )
    store.add_message(
        project["id"],
        "assistant",
        "Failed status bubble.",
        {"kind": "reply", "status": "failed"},
    )
    current = store.add_message(project["id"], "user", "Continue delivery.")

    projected = store.conversation_session_messages(
        project["id"],
        identity["logical_id"],
        before_message_id=current["id"],
    )

    assert projected == [
        {"role": "user", "content": first["content"]},
        {"role": "assistant", "content": reply["content"]},
    ]


def test_kanban_projection_uses_task_plan_order_not_materialization_time(store, tmp_path):
    project = store.create_project("projection ordering", "", str(tmp_path / "projection-order"))
    workflow = sequence_workflow()
    _, revision = publish_planned_workflow(store, project["id"], workflow)
    template = task_plan(revision["id"], workflow)
    first = template.tasks[0].model_copy(update={
        "proposed_task_ref": "unit-01",
        "title": "Unit 01",
    })
    second = template.tasks[0].model_copy(update={
        "proposed_task_ref": "unit-02",
        "title": "Unit 02",
    })
    plan_id = store.create_task_plan(
        project["id"],
        TaskPlan(
            objective="Keep the declared Task Plan order",
            workflow_revision_id=revision["id"],
            tasks=[first, second],
        ),
    )["id"]
    store.create_task(project["id"], task_plan_id=plan_id, proposed_task_ref="unit-02")
    store.create_task(project["id"], task_plan_id=plan_id, proposed_task_ref="unit-01")

    projected = store.project_projection(project["id"])["projection"]["tasks"]

    assert [task["proposed_task_ref"] for task in projected] == ["unit-01", "unit-02"]
    assert [task["task_plan_order"] for task in projected] == [0, 1]


def test_failed_conversation_job_changes_status_without_creating_assistant_speech(store, tmp_path):
    project = store.create_project("failed conversation", "", str(tmp_path / "failed-conversation"))
    job = store.create_conversation_job(project["id"], "Please inspect.", True)
    assert store.claim_conversation_job(job["id"], "session-owner") is not None

    store.fail_conversation_job(job["id"], "protocol did not complete")

    assert [item["role"] for item in store.messages(project["id"])] == ["user"]
    state = store.conversation_state(project["id"])
    assert state["job"]["id"] == job["id"]
    assert state["job"]["status"] == "failed"
    assert state["job"]["has_error"] is True


def test_failed_conversation_job_releases_claimed_mailbox_for_redelivery(store, tmp_path):
    project = store.create_project("mailbox redelivery", "", str(tmp_path / "mailbox-redelivery"))
    with store.tx(immediate=True) as db:
        store._mailbox(db, project["id"], "task.failed", None, None, {"reason": "provider unavailable"})
    mailbox_id = store.mailbox(project["id"])[0]["id"]
    job = store.create_conversation_job(project["id"], "Inspect the failure.", True)

    assert store.claim_conversation_job(job["id"], "session-owner") is not None
    assert [item["id"] for item in store.mailbox(project["id"], state="claimed")] == [mailbox_id]

    store.fail_conversation_job(job["id"], "model called an unavailable capability")

    assert [item["id"] for item in store.mailbox(project["id"], state="pending")] == [mailbox_id]
    replacement_jobs = store.enqueue_governance_jobs()
    assert len(replacement_jobs) == 1
    replacement = store.get_conversation_job(replacement_jobs[0])
    assert replacement["trigger_kind"] == "mailbox"
    assert replacement["mailbox_ids"] == [mailbox_id]


def test_protocol_failed_conversation_quarantines_trigger_without_blocking_later_mailbox(store, tmp_path):
    project = store.create_project("mailbox attention", "", str(tmp_path / "mailbox-attention"))
    with store.tx(immediate=True) as db:
        store._mailbox(db, project["id"], "task.failed", None, None, {"reason": "recovery needs evidence"})
    mailbox_id = store.mailbox(project["id"])[0]["id"]
    job = store.create_conversation_job(project["id"], "Inspect the failure.", True)
    assert store.claim_conversation_job(job["id"], "session-owner") is not None

    store.fail_conversation_job(job["id"], "execution report had no evidence", attention=True)

    assert store.mailbox(project["id"], state="pending") == []
    assert [item["id"] for item in store.mailbox(project["id"], state="attention")] == [mailbox_id]
    assert store.conversation_agent(project["id"])["state"] == "attention"
    assert store.enqueue_governance_jobs() == []

    with store.tx(immediate=True) as db:
        store._mailbox(db, project["id"], "task.done", None, None, {"result": "later terminal fact"})
    later_mailbox_id = store.mailbox(project["id"], state="pending")[0]["id"]

    replacement_jobs = store.enqueue_governance_jobs()
    assert len(replacement_jobs) == 1
    replacement = store.get_conversation_job(replacement_jobs[0])
    assert replacement["trigger_kind"] == "mailbox"
    assert replacement["mailbox_ids"] == [later_mailbox_id]


def test_startup_releases_mailbox_claimed_by_interrupted_conversation(store, tmp_path):
    project = store.create_project("mailbox restart", "", str(tmp_path / "mailbox-restart"))
    with store.tx(immediate=True) as db:
        store._mailbox(db, project["id"], "task.failed", None, None, {"reason": "provider unavailable"})
    mailbox_id = store.mailbox(project["id"])[0]["id"]
    job = store.create_conversation_job(project["id"], "Inspect the failure.", True)
    assert store.claim_conversation_job(job["id"], "session-owner") is not None

    store.startup_conversation_jobs()

    assert store.get_conversation_job(job["id"])["status"] == "failed"
    assert [item["id"] for item in store.mailbox(project["id"], state="pending")] == [mailbox_id]


def test_task_creation_materializes_plan_owned_readiness_fields(store, tmp_path):
    project = store.create_project(
        "canonical planned task",
        "",
        str(tmp_path / "project"),
    )
    workflow = sequence_workflow()
    workflow_plan_record = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, workflow_plan_record["id"])
    plan_definition = task_plan(revision["id"], workflow)
    planned_task = plan_definition.tasks[0].model_copy(update={
        "objective": "Deliver the canonical planned objective.",
        "conflict_domains": [
        ConflictDomain(kind="workspace_path", identity="result.txt"),
        ],
    })
    plan_definition = plan_definition.model_copy(update={"tasks": [planned_task]})
    plan = store.create_task_plan(project["id"], plan_definition)

    registry = build_core_registry()
    result = registry.dispatch(
        "task.create",
        {
            "task_plan_id": plan["id"],
            "proposed_task_ref": planned_task.proposed_task_ref,
        },
        CapabilityContext(
            project_id=project["id"],
            project=project,
            store=store,
        ),
    )
    assert result.ok, result.error
    task = result.output

    assert task["readiness"]["objective"] == planned_task.objective
    assert task["readiness"]["dependencies"] == []
    assert task["readiness"]["conflict_domains"] == [
        {"kind": "workspace_path", "identity": "result.txt"},
    ]
    assert task["conflict_domains"] == ["workspace_path:result.txt"]
    with pytest.raises(ValueError, match="already materialized"):
        store.create_task(
            project["id"],
            task_plan_id=plan["id"],
            proposed_task_ref=planned_task.proposed_task_ref,
        )
    assert len(store.list_tasks(project["id"])) == 1


def test_concurrent_initial_task_creation_materializes_once(store, tmp_path):
    project = store.create_project(
        "concurrent planned task",
        "",
        str(tmp_path / "project"),
    )
    workflow = sequence_workflow()
    workflow_plan_record = store.create_workflow_plan(project["id"], workflow_plan(workflow))
    revision = publish_initial_workflow(store, project["id"], workflow, workflow_plan_record["id"])
    plan = store.create_task_plan(project["id"], task_plan(revision["id"], workflow))
    barrier = threading.Barrier(2)

    def create_once(index: int):
        barrier.wait()
        try:
            task = store.create_task(
                project["id"],
                task_plan_id=plan["id"],
                proposed_task_ref="primary",
            )
            return "created", task["id"]
        except ValueError as exc:
            return "rejected", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_once, (1, 2)))

    assert sorted(item[0] for item in results) == ["created", "rejected"]
    assert "already materialized" in next(
        item[1] for item in results if item[0] == "rejected"
    )
    assert len(store.list_tasks(project["id"])) == 1


def test_task_plan_save_enforces_task_agent_execution_policy(store, tmp_path):
    project = store.create_project("agent execution policy", "", str(tmp_path / "project"))

    deterministic = sequence_workflow()
    _, deterministic_revision = publish_planned_workflow(store, project["id"], deterministic)
    with pytest.raises(ValueError, match="requires a Task Agent Run"):
        store.create_task_plan(
            project["id"],
            task_plan(deterministic_revision["id"], deterministic, agent_execution="required"),
        )

    agent_based = agent_workflow()
    _, agent_revision = publish_planned_workflow(store, project["id"], agent_based)
    with pytest.raises(ValueError, match="forbids Task Agent Runs"):
        store.create_task_plan(
            project["id"],
            task_plan(agent_revision["id"], agent_based, agent_execution="forbidden"),
        )


def test_task_plan_save_canonicalizes_provider_primitive_strings(store, tmp_path):
    project = store.create_project("provider primitive transport", "", str(tmp_path / "project"))
    workflow = agent_workflow()
    method = workflow_plan(workflow)
    method.task_contract.input_schema = {
        "type": "object",
        "required": ["chapter_number", "is_final_chapter"],
        "properties": {
            "chapter_number": {"type": "integer", "minimum": 1},
            "is_final_chapter": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    method_record = store.create_workflow_plan(project["id"], method)
    revision = publish_initial_workflow(store, project["id"], workflow, method_record["id"])

    stored = store.create_task_plan(
        project["id"],
        task_plan(
            revision["id"],
            workflow,
            input_data={"chapter_number": "1", "is_final_chapter": "false"},
        ),
    )

    task_input = stored["plan"]["tasks"][0]["input"]
    assert task_input == {"chapter_number": 1, "is_final_chapter": False}


def test_task_plan_must_bind_every_exact_string_to_fixed_workflow_revision(store, tmp_path):
    project = store.create_project("exact workflow binding", "", str(tmp_path / "project"))
    workflow = sequence_workflow()
    exact_strings = [
        ExactTaskInputString(pointer="/contract/content", escaped_value="done"),
    ]
    _, literal_revision = publish_planned_workflow(store, project["id"], workflow)

    with pytest.raises(ValueError, match="does not consume through Task-input \\$ref"):
        store.create_task_plan(
            project["id"],
            task_plan(
                literal_revision["id"],
                workflow,
                input_data={"contract": {"content": "done"}},
                exact_input_strings=exact_strings,
            ),
        )

    workflow.columns[0].executor.steps[0].arguments["content"] = {
        "$ref": "/input/task/input/contract/content"
    }
    _, published = publish_planned_workflow(store, project["id"], workflow)
    stored_task_plan = store.create_task_plan(
        project["id"],
        task_plan(
            published["id"],
            workflow,
            input_data={"contract": {"content": "done"}},
            exact_input_strings=exact_strings,
        ),
    )

    assert published["definition"]["columns"][0]["executor"]["steps"][0]["arguments"]["content"] == {
        "$ref": "/input/task/input/contract/content"
    }
    assert stored_task_plan["workflow_revision_id"] == published["id"]


def test_successful_task_successor_resolves_failed_lineage_without_erasing_history(
    store,
    tmp_path,
):
    project = store.create_project("task lineage", "", str(tmp_path / "project"))
    _plan, _revision = publish_planned_workflow(
        store,
        project["id"],
        sequence_workflow(content="resolved"),
    )
    failed = create_planned_task(
        store,
        project["id"],
        "first execution",
    )
    store.route_task_to_failed(failed["id"], "frozen revision could not deliver")
    failed_mailbox = store.mailbox(project["id"])
    assert failed_mailbox
    assert store.observe_mailbox(project["id"], failed_mailbox[0]["id"])
    old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    with store.tx(immediate=True) as db:
        db.execute(
            "UPDATE v1_projects SET updated_at=? WHERE id=?",
            (old, project["id"]),
        )
        db.execute(
            "UPDATE v1_events SET created_at=? WHERE project_id=?",
            (old, project["id"]),
        )
        db.execute(
            "UPDATE v1_tasks SET created_at=? WHERE project_id=?",
            (old, project["id"]),
        )
        db.execute(
            "UPDATE v1_artifacts SET created_at=? WHERE project_id=?",
            (old, project["id"]),
        )
    unresolved_quiescence = store.project_quiescence(project["id"])
    assert unresolved_quiescence["quiescent"]
    assert unresolved_quiescence["unresolved_failures"]["tasks"] == 1
    assert unresolved_quiescence["governance_outcome"] == "attention_required"

    successor = store.rerun_task(failed["id"])
    assert successor["rerun_of_task_id"] == failed["id"]

    WorkflowRuntime(
        store,
        build_core_registry(),
        "lineage-worker",
    ).step(successor["id"])

    assert store.get_task(successor["id"])["status"] == "done"
    retained_failure = store.get_task(failed["id"])
    assert retained_failure["status"] == "failed"
    assert retained_failure["resolved_by_task_id"] == successor["id"]
    assert store.unresolved_failures(project["id"])["tasks"] == []
    assert any(
        item["type"] == "task.failure_lineage_resolved"
        for item in store.events(project_id=project["id"])
    )


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
    _, first = publish_planned_workflow(store, project["id"], sequence_workflow(name="one"))
    first_task = create_planned_task(store, project["id"], "first")
    _, second = publish_planned_workflow(store, project["id"], sequence_workflow(name="two", path="two.txt"))

    assert second["revision"] == first["revision"] + 1
    assert store.get_task(first_task["id"])["workflow_revision_id"] == first["id"]
    assert store.workflow_by_id(project["id"], first["id"]).name == "one"


def test_workflow_plan_rejects_concrete_task_inventory():
    payload = workflow_plan(sequence_workflow()).model_dump(mode="json")
    payload["tasks"] = [{"proposed_task_ref": "unit_01"}]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowPlan.model_validate(payload)


def test_agent_run_messages_and_tool_invocations_are_auditable(store, tmp_path):
    project = store.create_project("audit", "", str(tmp_path / "project"))
    run = store.begin_agent_run(
        project_id=project["id"],
        kind="conversation",
        instruction_revision=1,
        instruction_snapshot="",
        context_snapshot={"value": 1},
        capabilities=["system.noop"],
        platform_policy=store.latest_platform_policy(),
        runtime_policy=store.policy,
    )
    tool_call = {"id": "call-1", "function": {"name": "system.noop", "arguments": {}}}
    store.add_agent_message(run["id"], "assistant", "", [tool_call])
    store.record_tool_invocation(
        agent_run_id=run["id"],
        tool_call_id="call-1",
        capability="system.noop",
        arguments={},
        result={"ok": True},
        ok=True,
    )
    store.add_agent_message(run["id"], "tool", '{"ok":true}', [], "call-1")
    finished = store.finish_agent_run(run["id"], "succeeded", "done", None, 1, 1)

    assert finished["context"] == {"value": 1}
    assert store.agent_messages(project["id"], run["id"])[0]["tool_calls"] == [tool_call]
    assert store.tool_invocations(project["id"], run["id"])[0]["capability"] == "system.noop"
    progress = [item for item in store.events(project_id=project["id"]) if item["type"] == "conversation.progress"]
    assert [item["data"]["kind"] for item in progress] == ["model_output", "tool_result"]
    assert "system.noop" in progress[0]["data"]["content"]
    assert '{"ok":true}' in progress[1]["data"]["content"]


def test_conversation_agent_text_is_projected_as_incremental_progress(store, tmp_path):
    project = store.create_project("streaming", "", str(tmp_path / "project"))
    run = store.begin_agent_run(
        project_id=project["id"],
        kind="conversation",
        instruction_revision=1,
        instruction_snapshot="",
        context_snapshot={},
        capabilities=[],
        platform_policy=store.latest_platform_policy(),
        runtime_policy=store.policy,
        conversation_job_id=None,
    )

    message = store.add_agent_message(run["id"], "assistant", "I am designing the Workflow.")
    progress = [item for item in store.events(project_id=project["id"]) if item["type"] == "conversation.progress"]

    assert len(progress) == 1
    assert progress[0]["data"] == {
        "agent_run_id": run["id"],
        "conversation_job_id": None,
        "kind": "model_output",
        "chunk_index": 1,
        "chunk_count": 1,
        "sequence": message["sequence"],
        "content": "I am designing the Workflow.",
    }

    complete_content = "思" * 30_000
    store.record_conversation_progress(run["id"], kind="model_output", content=complete_content)
    chunks = [
        item["data"]["content"]
        for item in store.events(project_id=project["id"])
        if item["type"] == "conversation.progress" and item["data"].get("sequence") is None
    ]
    assert "".join(chunks) == complete_content


def test_sqlite_has_wal_and_runtime_read_indexes(store):
    with store.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_v1_tasks_dispatch", "idx_v1_runs_task", "idx_v1_agent_runs_project", "idx_v1_tool_invocations_run", "idx_v1_mailbox_pending"} <= indexes


def test_large_tool_result_is_persisted_and_returned_in_full(store, tmp_path):
    project = store.create_project("payload hydration", "", str(tmp_path / "project"))
    run = store.begin_agent_run(
        project_id=project["id"],
        kind="conversation",
        instruction_revision=1,
        instruction_snapshot="",
        context_snapshot={},
        capabilities=["project.inspect"],
        platform_policy=store.latest_platform_policy(),
        runtime_policy=store.policy,
    )
    logical_result = {
        "ok": True,
        "capability": "project.inspect",
        "status": "completed",
        "output": {"content": "x" * 300_000},
        "error": None,
        "await_handle_draft": None,
        "checkpoint": None,
    }
    store.record_tool_invocation(
        agent_run_id=run["id"],
        tool_call_id="large-result",
        capability="project.inspect",
        arguments={},
        result=logical_result,
        ok=True,
    )

    public = store.tool_invocations(project["id"], run["id"])[0]
    internal = store.tool_invocations(
        project["id"],
        run["id"],
        hydrate_payloads=True,
    )[0]

    assert public["result"] == logical_result
    assert internal["result"] == logical_result


def test_running_task_cannot_be_failed_while_column_attempt_is_active(store, tmp_path):
    project = store.create_project("terminal-race", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "work")
    claimed = store.claim_task(task["id"], "worker")
    assert claimed is not None
    run = store.begin_run(claimed, {"task": claimed, "column": "execute"})

    with pytest.raises(ValueError, match="active Attempt"):
        store.route_task_to_failed(task["id"], "incorrect supervision intervention")

    current = store.get_task(task["id"])
    assert current["status"] == "running"
    attempts = store.attempts(project["id"], task["id"])
    assert attempts[-1]["id"] == run["attempt_id"]
    assert attempts[-1]["status"] == "running"


def test_begin_run_refreshes_kanban_projection_to_running(store, tmp_path):
    project = store.create_project("running projection", "", str(tmp_path / "project"))
    publish_planned_workflow(store, project["id"], sequence_workflow())
    task = create_planned_task(store, project["id"], "projected work")
    claimed = store.claim_task(task["id"], "worker")
    assert claimed is not None

    store.begin_run(claimed, {"task": claimed, "column": "execute"})

    projected = {
        item["id"]: item
        for item in store.project_projection(project["id"])["projection"]["tasks"]
    }
    current = store.get_task(task["id"])
    assert current["status"] == "running"
    assert projected[task["id"]]["status"] == "running"
    assert projected[task["id"]]["state_version"] == current["state_version"]
