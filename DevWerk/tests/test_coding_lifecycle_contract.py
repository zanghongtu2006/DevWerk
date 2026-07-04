from __future__ import annotations

import json

import pytest


class FakeSettings:
    def __init__(self, tmp_path):
        self.devwerk_db_path = str(tmp_path / "devwerk-coding-lifecycle.db")
        self.devwerk_usage_tracking = False
        self.devwerk_session_dir = str(tmp_path / "sessions")


def configure(monkeypatch, tmp_path):
    import app.services.kanban as kanban_service
    import app.services.memory_system as memory_system
    import app.services.session_store as session_store

    fake = FakeSettings(tmp_path)
    monkeypatch.setattr(kanban_service, "settings", lambda: fake)
    monkeypatch.setattr(memory_system, "settings", lambda: fake)
    monkeypatch.setattr(session_store, "settings", lambda: fake)
    kanban_service._initialized = False
    memory_system._initialized = False
    return kanban_service, memory_system


def coding_workflow(*, allow_skip_verification: bool = False) -> dict:
    return {
        "name": "coding-lifecycle-flow",
        "version": 1,
        "workflow_type": "coding",
        "requires_apply": True,
        "parameters": {
            "coding_lifecycle": {
                "allow_done_without_verification": allow_skip_verification,
            }
        },
        "columns": [
            {
                "status_key": "implement",
                "title": "Implement",
                "position": 10,
                "transition_to": ["ready_to_apply", "failed"],
                "job_template": "make_code_change",
                "output_artifact": "implementation_bundle",
                "success_action": "code_ready",
                "failure_actions": ["fail"],
            },
            {
                "status_key": "ready_to_apply",
                "title": "Ready To Apply",
                "position": 20,
                "transition_to": ["applied", "failed"],
            },
            {"status_key": "applied", "title": "Applied", "position": 30, "transition_to": ["done", "failed"]},
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["implement"]},
        ],
        "actions": {
            "code_ready": {"to": "ready_to_apply"},
            "apply_succeeded": {"to": "applied"},
            "verification_passed": {"to": "done"},
            "verification_failed": {"to": "failed"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "implement"},
        },
    }


def invalid_coding_workflow() -> dict:
    workflow = coding_workflow()
    workflow["columns"] = [column for column in workflow["columns"] if column["status_key"] != "ready_to_apply"]
    workflow["actions"].pop("apply_succeeded")
    return workflow


@pytest.mark.asyncio
async def test_code_output_is_forced_to_ready_to_apply_even_when_llm_requests_done(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "code-ready-gate"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Generate a code patch")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeCodeClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            assert payload["phase"] == "implement"
            return {
                "phase": "implement",
                "summary": "Generated a code patch.",
                "outputs": {"changed_paths": ["src/App.java"]},
                "ops": [{"op": "create_file", "path": "src/App.java", "content": "class App {}\n", "language": "java"}],
                "decision": "approve",
                "next_action": "workflow_done",
                "done": True,
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeCodeClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {"project_id": project_id, "messages": [{"role": "user", "content": "Create src/App.java"}]},
    )

    detail = kanban.get_task(task["id"])["task"]
    result = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]["payload"]
    ready_bundle = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "code_ready_bundle"][-1]["payload"]

    assert detail["status_key"] == "ready_to_apply"
    assert result["status_key"] == "ready_to_apply"
    assert result["next_action"] == "apply_result"
    assert result["waiting_for"] == "apply_result"
    assert result["planning"]["revision_id"] == ready_bundle["revision_id"]
    assert result["planning"]["changed_paths"] == ["src/App.java"]
    assert result["planning"]["requires_apply"] is True
    assert result["planning"]["requires_verification"] is True
    assert result["ops"][0]["path"] == "src/App.java"
    conversation = kanban.get_conversation(task["id"])
    assert conversation["state"] == "waiting_client"
    assert conversation["waiting_for"] == "apply_result"
    assert "workflow_done" not in [event["event_type"] for event in detail["events"]]
    assert "workflow_waiting_apply_result" in [event["event_type"] for event in detail["events"]]
    assert "code_ready" in [event["event_type"] for event in detail["events"]]


@pytest.mark.asyncio
async def test_code_patch_file_bundle_is_treated_as_applyable_code_result(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "code-patch-file-bundle"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Generate a scaffold")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeCodePatchClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            assert payload["phase"] == "implement"
            return {
                "phase": "implement",
                "summary": "Generated a file bundle, but cannot write it locally in this phase.",
                "outputs": {
                    "code_patch": {
                        "format": "file-bundle",
                        "files": [
                            {
                                "path": "backend-java/pom.xml",
                                "content": "<project></project>\n",
                            },
                            {
                                "path": "README.md",
                                "content": "# Scaffold\n",
                            },
                        ],
                    }
                },
                "decision": "fail",
                "next_action": "fail",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeCodePatchClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {"project_id": project_id, "messages": [{"role": "user", "content": "Create the scaffold"}]},
    )

    detail = kanban.get_task(task["id"])["task"]
    result = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]["payload"]
    ready_bundle = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "code_ready_bundle"][-1]["payload"]

    assert detail["status_key"] == "ready_to_apply"
    assert result["ok"] is True
    assert result["waiting_for"] == "apply_result"
    assert [op["path"] for op in result["ops"]] == ["backend-java/pom.xml", "README.md"]
    assert result["ops"][0]["op"] == "create_file"
    assert ready_bundle["ops_count"] == 2
    assert "fail" not in [event["event_type"] for event in detail["events"]]


@pytest.mark.asyncio
async def test_summary_embedded_staged_patch_is_treated_as_applyable_code_result(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "summary-staged-patch"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Generate a scaffold")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeSummaryPatchClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            assert payload["phase"] == "implement"
            return {
                "summary": json.dumps(
                    {
                        "phase": "implement",
                        "summary": "Generated a staged patch.",
                        "outputs": {
                            "staged_patch": {
                                "files": [
                                    {"path": "README.md", "content": "# Scaffold\n"},
                                    {"path": "src/App.java", "content": "class App {}\n"},
                                ]
                            }
                        },
                        "next_action": "code_ready",
                    }
                ),
                "decision": "approve",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeSummaryPatchClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {"project_id": project_id, "messages": [{"role": "user", "content": "Create the scaffold"}]},
    )

    detail = kanban.get_task(task["id"])["task"]
    result = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]["payload"]
    assert detail["status_key"] == "ready_to_apply"
    assert result["ok"] is True
    assert result["waiting_for"] == "apply_result"
    assert [op["path"] for op in result["ops"]] == ["README.md", "src/App.java"]
    assert "fail" not in [event["event_type"] for event in detail["events"]]


def test_coding_workflow_rejects_done_without_apply_result(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "done-guard"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Do not finish yet", status_key="ready_to_apply")["task"]

    import app.services.workflow as workflow_service

    result = workflow_service.apply_workflow_action(
        task["id"],
        "workflow_done",
        {"phase": "implement", "reason": "LLM tried to finish before apply."},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert result["action_ignored"] is True
    assert detail["status_key"] == "ready_to_apply"
    event_types = [event["event_type"] for event in detail["events"]]
    assert "workflow_done_guard_blocked" in event_types


def test_coding_workflow_rejects_forged_done_guard_without_apply_artifact(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "forged-done-guard"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Forged done", status_key="applied")["task"]

    import app.services.workflow as workflow_service

    result = workflow_service.apply_workflow_action(
        task["id"],
        "workflow_done",
        {"phase": "workflow", "reason": "forged", "lifecycle_done_guard_passed": True},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert result["action_ignored"] is True
    assert detail["status_key"] == "applied"
    event_types = [event["event_type"] for event in detail["events"]]
    assert "workflow_done_guard_blocked" in event_types


def test_apply_result_without_verification_policy_does_not_default_to_done(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "verification-required"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Apply without verification", status_key="ready_to_apply")["task"]

    import app.services.workflow as workflow_service

    result = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {"ok": True, "snapshot_id": "snap-1", "changed_paths": ["src/App.java"], "verification": {}},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert result["task"]["status_key"] == "failed"
    assert detail["status_key"] == "failed"
    artifact_types = [artifact["artifact_type"] for artifact in detail["artifacts"]]
    assert "failure_bundle" in artifact_types
    assert "verification_skipped" not in artifact_types
    event_types = [event["event_type"] for event in detail["events"]]
    assert "workflow_done_guard_blocked" in event_types
    assert "failure_bundle_created" in event_types


def test_explicit_verification_skip_records_artifact_and_allows_done(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "verification-skip"
    kanban.update_project_workflow(project_id, coding_workflow(allow_skip_verification=True))
    task = kanban.create_task(project_id=project_id, title="Apply with explicit skip", status_key="ready_to_apply")["task"]

    import app.services.workflow as workflow_service

    result = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {"ok": True, "snapshot_id": "snap-2", "changed_paths": ["src/App.java"], "verification": {}},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert result["task"]["status_key"] == "done"
    artifact_types = [artifact["artifact_type"] for artifact in detail["artifacts"]]
    assert "verification_skipped" in artifact_types
    event_types = [event["event_type"] for event in detail["events"]]
    assert "verification_skipped" in event_types
    assert "workflow_done_guard_passed" in event_types
    assert "apply_succeeded" in event_types
    assert "verification_passed" in event_types


def test_post_apply_verification_column_can_complete_workflow(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "post-apply-verification"
    workflow = coding_workflow()
    workflow["columns"][2]["transition_to"] = ["verifying", "failed"]
    workflow["columns"].insert(
        3,
        {
            "status_key": "verifying",
            "title": "Verifying",
            "position": 50,
            "transition_to": ["done", "failed"],
            "job_template": "verify.scaffold",
            "input_artifacts": ["apply_result"],
            "output_artifact": "verification_report",
            "success_action": "workflow_done",
            "failure_actions": ["verification_failed", "fail"],
        },
    )
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Apply then verify", status_key="ready_to_apply")["task"]

    import app.services.workflow as workflow_service

    applied = workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {"ok": True, "snapshot_id": "snap-verify", "changed_paths": ["README.md"], "verification": {}},
    )
    assert applied["task"]["status_key"] == "applied"

    kanban.move_task(task["id"], "verifying", force=True)
    done = workflow_service.apply_workflow_action(
        task["id"],
        "workflow_done",
        {"phase": "verifying", "reason": "verification column passed"},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert done["task"]["status_key"] == "done"
    assert detail["status_key"] == "done"
    event_types = [event["event_type"] for event in detail["events"]]
    assert "workflow_done_guard_blocked" not in event_types
    assert "workflow_transition_decided" in event_types
    assert any(
        event["event_type"] == "workflow_transition_decided"
        and (event.get("payload") or {}).get("action") == "workflow_done"
        and (event.get("payload") or {}).get("to_status") == "done"
        for event in detail["events"]
    )


def test_post_apply_verification_accepts_backend_local_apply_result(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "post-apply-verification-backend-local"
    workflow = coding_workflow()
    workflow["columns"][2]["transition_to"] = ["verifying", "failed"]
    workflow["columns"].insert(
        3,
        {
            "status_key": "verifying",
            "title": "Verifying",
            "position": 50,
            "transition_to": ["done", "failed"],
            "job_template": "verify.scaffold",
            "input_artifacts": ["backend_local_apply_result"],
            "output_artifact": "verification_report",
            "success_action": "workflow_done",
            "failure_actions": ["verification_failed", "fail"],
        },
    )
    kanban.update_project_workflow(project_id, workflow)
    task = kanban.create_task(project_id=project_id, title="Apply then verify", status_key="verifying")["task"]
    kanban.add_artifact(
        task["id"],
        artifact_type="backend_local_apply_result",
        payload={"ok": True, "changed_paths": ["README.md"], "verification": {}},
    )

    import app.services.workflow as workflow_service

    done = workflow_service.apply_workflow_action(
        task["id"],
        "workflow_done",
        {"phase": "verifying", "reason": "backend-local verification column passed"},
    )

    detail = kanban.get_task(task["id"])["task"]
    assert done["task"]["status_key"] == "done"
    assert detail["status_key"] == "done"
    assert "workflow_done_guard_blocked" not in [event["event_type"] for event in detail["events"]]


def test_apply_failure_creates_failure_bundle_and_task_memory(monkeypatch, tmp_path):
    kanban, memory = configure(monkeypatch, tmp_path)
    project_id = "apply-failure-bundle"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Apply should fail", status_key="ready_to_apply")["task"]

    import app.services.workflow as workflow_service

    workflow_service.apply_workflow_action(
        task["id"],
        "apply_result",
        {
            "ok": False,
            "snapshot_id": "snap-failed",
            "error_message": "Snapshot restore failed.",
            "changed_paths": ["src/App.java"],
            "verification": {"required": ["compile"], "results": {"compile": "not_run"}},
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    failure_bundle = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "failure_bundle"][-1]["payload"]
    task_memory = memory.read_task_memory(task["id"])

    assert detail["status_key"] == "failed"
    assert failure_bundle["failure_stage"] == "apply"
    assert failure_bundle["reason"] == "Snapshot restore failed."
    assert failure_bundle["changed_paths"] == ["src/App.java"]
    assert failure_bundle["retryable"] is True
    assert task_memory["task_handoff_summary"]["items"][-1]["failure_bundle_id"] == failure_bundle["id"]
    assert task_memory["task_test_state"]["latest"]["failure_stage"] == "apply"
    assert task_memory["patch_summary"]["latest"]["changed_paths"] == ["src/App.java"]


def test_direct_fail_action_creates_failure_bundle(monkeypatch, tmp_path):
    kanban, memory = configure(monkeypatch, tmp_path)
    project_id = "direct-fail-bundle"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Agent failed", status_key="implement")["task"]

    import app.services.workflow as workflow_service

    workflow_service.apply_workflow_action(
        task["id"],
        "fail",
        {"phase": "implement", "reason": "Agent could not produce a coherent patch."},
    )

    detail = kanban.get_task(task["id"])["task"]
    failure_bundle = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "failure_bundle"][-1]["payload"]
    task_memory = memory.read_task_memory(task["id"])

    assert detail["status_key"] == "failed"
    assert failure_bundle["failure_stage"] == "agent"
    assert failure_bundle["reason"] == "Agent could not produce a coherent patch."
    assert failure_bundle["target_status_key"] == "failed"
    assert task_memory["task_handoff_summary"]["items"][-1]["failure_bundle_id"] == failure_bundle["id"]


def test_context_pack_loads_latest_failure_bundle_for_rework(monkeypatch, tmp_path):
    kanban, memory = configure(monkeypatch, tmp_path)
    project_id = "failure-context-pack"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Retry failed apply", status_key="failed")["task"]
    kanban.add_artifact(
        task["id"],
        artifact_type="failure_bundle",
        payload={"id": "failure-1", "failure_stage": "verification", "reason": "compile failed"},
    )

    pack = memory.build_context_pack(
        project_id=project_id,
        task_id=task["id"],
        workflow_id="coding-lifecycle-flow",
        agent_role="implement-agent",
        stage="implement",
        token_budget=1200,
    )["context_pack"]

    assert pack["task"]["latest_failure_bundle"]["id"] == "failure-1"
    assert pack["task"]["latest_failure_bundle"]["reason"] == "compile failed"


@pytest.mark.asyncio
async def test_backend_local_code_ops_are_applied_and_complete_workflow(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    project_id = "backend-local-apply"
    project_root = tmp_path / "generated-project"
    kanban.update_project_workflow(project_id, coding_workflow())
    task = kanban.create_task(project_id=project_id, title="Generate scaffold")["task"]

    import app.services.workflow_engine as workflow_engine_service

    class FakeCodeClient:
        def chat_json(self, messages: list[dict]) -> dict:
            payload = json.loads(messages[-1]["content"])
            assert payload["phase"] == "implement"
            return {
                "phase": "implement",
                "summary": "Generated a backend-local scaffold.",
                "outputs": {
                    "code_patch": {
                        "target_root": str(project_root),
                        "files": [
                            {"path": "README.md", "content": "# Generated\n"},
                            {"path": "src/App.java", "content": "class App {}\n"},
                        ],
                    }
                },
                "decision": "approve",
                "next_action": "code_ready",
            }

    monkeypatch.setattr(workflow_engine_service, "get_llm_client", lambda route: FakeCodeClient())

    await workflow_engine_service.WorkflowEngine().run(
        task["id"],
        {
            "project_id": project_id,
            "backend_local": True,
            "messages": [{"role": "user", "content": "Create a tiny scaffold"}],
        },
    )

    detail = kanban.get_task(task["id"])["task"]
    result = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "workflow_result"][-1]["payload"]
    apply_result = [artifact for artifact in detail["artifacts"] if artifact["artifact_type"] == "backend_local_apply_result"][-1]["payload"]

    assert (project_root / "README.md").read_text(encoding="utf-8") == "# Generated\n"
    assert (project_root / "src" / "App.java").read_text(encoding="utf-8") == "class App {}\n"
    assert detail["status_key"] == "done"
    assert result["ok"] is True
    assert result["done"] is True
    assert result["status_key"] == "done"
    assert apply_result["ok"] is True
    assert sorted(apply_result["changed_paths"]) == ["README.md", "src/App.java"]


def test_coding_workflow_validator_requires_lifecycle_contract(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="ready_to_apply"):
        kanban.update_project_workflow("invalid-coding", invalid_coding_workflow())

    workflow = coding_workflow()
    workflow["actions"]["workflow_done"] = {"to": "ready_to_apply"}
    with pytest.raises(ValueError, match="code_ready"):
        kanban.update_project_workflow("bad-done-target", workflow)

    workflow = coding_workflow()
    workflow["columns"][0]["success_action"] = "workflow_done"
    with pytest.raises(ValueError, match="must use success_action='code_ready'"):
        kanban.update_project_workflow("bad-code-success-action", workflow)


def test_coding_workflow_accepts_project_specific_terminal_columns(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    workflow = coding_workflow()
    for column in workflow["columns"]:
        if column["status_key"] == "done":
            column["status_key"] = "workflow_complete"
            column["title"] = "Workflow Complete"
        elif column["status_key"] == "failed":
            column["status_key"] = "workflow_failed"
            column["title"] = "Workflow Failed"
        column["transition_to"] = [
            "workflow_complete" if item == "done" else "workflow_failed" if item == "failed" else item
            for item in column.get("transition_to", [])
        ]
    workflow["actions"]["workflow_done"] = {"to": "workflow_complete"}
    workflow["actions"]["verification_passed"] = {"to": "workflow_complete"}
    workflow["actions"]["fail"] = {"to": "workflow_failed"}
    workflow["actions"]["abandon"] = {"to": "workflow_failed"}
    workflow["actions"]["verification_failed"] = {"to": "workflow_failed"}

    kanban.update_project_workflow("custom-terminal-coding", workflow)

    saved = kanban.get_project_workflow("custom-terminal-coding")
    assert saved["workflow"]["actions"]["workflow_done"]["to"] == "workflow_complete"
    assert saved["workflow"]["actions"]["fail"]["to"] == "workflow_failed"


def test_ready_to_apply_packaging_column_is_not_treated_as_code_producer(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    workflow = coding_workflow()
    for column in workflow["columns"]:
        if column["status_key"] == "ready_to_apply":
            column["job_template"] = "code.package"
            column["output_artifact"] = "apply_package.json"
            column["success_action"] = "apply_succeeded"

    kanban.update_project_workflow("ready-package-coding", workflow)

    saved = kanban.get_project_workflow("ready-package-coding")
    ready = next(item for item in saved["workflow"]["columns"] if item["status_key"] == "ready_to_apply")
    assert ready["success_action"] == "apply_succeeded"


def test_implementation_plan_column_is_not_treated_as_code_producer(monkeypatch, tmp_path):
    kanban, _memory = configure(monkeypatch, tmp_path)
    workflow = coding_workflow()
    workflow["name"] = "implementation-plan-column"
    workflow["columns"].insert(
        1,
        {
            "status_key": "ready_to_implement",
            "title": "Ready To Implement",
            "position": 15,
            "transition_to": ["implement", "failed"],
            "job_template": "implementation_plan",
            "input_artifacts": ["analysis_bundle"],
            "output_artifact": "implementation_plan",
            "success_action": "implementation_planned",
            "failure_actions": ["fail"],
        },
    )
    workflow["columns"][0]["transition_to"] = ["ready_to_implement", "failed"]
    workflow["actions"]["implementation_planned"] = {"to": "implement"}

    kanban.update_project_workflow("implementation-plan-column", workflow)

    saved = kanban.get_project_workflow("implementation-plan-column")
    plan_col = next(item for item in saved["workflow"]["columns"] if item["status_key"] == "ready_to_implement")
    assert plan_col["success_action"] == "implementation_planned"
