from __future__ import annotations

from tests.workflow_test_utils import configure_kanban, noncoding_workflow


def test_project_workflow_save_normalizes_action_transition_edges(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)

    project_id = "workflow-normalize-edge"
    kanban_service.upsert_project(project_id=project_id, name="Workflow Normalize Edge")
    workflow = {
        "name": "broken-coding",
        "version": 1,
        "workflow_type": "coding",
        "requires_apply": True,
        "columns": [
            {
                "status_key": "in_progress",
                "title": "In Progress",
                "position": 10,
                "transition_to": [],
                "job_template": "project_agent_dev",
                "output_artifact": "code_patch",
                "success_action": "code_ready",
                "failure_actions": ["fail"],
                "context_policy": {"output_contract": "code_change"},
            },
            {"status_key": "ready_to_apply", "title": "Ready To Apply", "position": 20, "transition_to": []},
            {"status_key": "done", "title": "Done", "position": 90, "transition_to": []},
            {"status_key": "failed", "title": "Failed", "position": 99, "transition_to": ["in_progress"]},
        ],
        "actions": {
            "code_ready": {"to": "ready_to_apply"},
            "apply_succeeded": {"to": "done"},
            "verification_failed": {"to": "failed"},
            "workflow_done": {"to": "done"},
            "fail": {"to": "failed"},
            "abandon": {"to": "failed"},
            "retry": {"to": "in_progress"},
        },
    }

    saved = kanban_service.update_project_workflow(project_id, workflow)["workflow"]
    columns = {column["status_key"]: column for column in saved["columns"]}
    assert "ready_to_apply" in columns["in_progress"]["transition_to"]
    assert "failed" in columns["in_progress"]["transition_to"]


def test_project_conversation_starts_task_when_workflow_exists_and_no_active_task(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-start"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Start")
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))

    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {"action": "start_task", "task_request": "Write the first article.", "reply": "Starting."},
    )
    captured = {}
    monkeypatch.setattr(
        workflow_routes,
        "start_workflow_payload",
        lambda body: captured.setdefault("body", body)
        or {"ok": True, "task_id": "unreachable"},
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(action="message", message="现在开始写第一篇文章。"),
    )

    assert response["kind"] == "task_started"
    assert captured["body"]["project_id"] == project_id
    assert captured["body"]["messages"][-1]["content"] == "Write the first article."
    assert any(message["content"] for message in captured["body"]["messages"][:-1])


def test_project_conversation_continues_active_non_terminal_task(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-continue"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Continue")
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))
    task = kanban_service.create_task(project_id=project_id, title="Article draft", description="Active draft")
    task_id = task["task"]["id"]

    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {"action": "continue_task", "task_id": task_id, "task_request": "Make the title more technical."},
    )
    captured = {}
    monkeypatch.setattr(
        workflow_routes,
        "continue_workflow_payload",
        lambda task_id_arg, incoming: captured.setdefault("incoming", {"task_id": task_id_arg, **incoming})
        or {"ok": True, "task_id": task_id_arg},
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="message",
            message="刚才那个任务里，标题不要太营销。",
            metadata={"active_task_id": task_id},
        ),
    )

    assert response["kind"] == "task_continued"
    assert captured["incoming"]["task_id"] == task_id
    assert captured["incoming"]["message"] == "Make the title more technical."


def test_terminal_task_followup_can_start_new_task(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-terminal"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Terminal")
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))
    task = kanban_service.create_task(project_id=project_id, title="Done article", description="Done")
    task_id = task["task"]["id"]
    kanban_service.move_task(task_id, "done", force=True)

    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {"action": "start_task", "task_request": "Start the next article."},
    )
    monkeypatch.setattr(
        workflow_routes,
        "start_workflow_payload",
        lambda body: {"ok": True, "task_id": "next-task", "status_key": "intake"},
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="message",
            message="现在开始下一篇。",
            metadata={"active_task_id": task_id},
        ),
    )

    assert response["kind"] == "task_started"
    assert response["task_id"] == "next-task"


def test_project_conversation_explanation_request_does_not_continue_task(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-explanation"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Explanation")
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))
    task = kanban_service.create_task(project_id=project_id, title="Failed draft", description="Failed")
    task_id = task["task"]["id"]
    kanban_service.move_task(task_id, "failed", force=True)

    monkeypatch.setattr(
        kanban_routes,
        "_ask_project_conversation_agent",
        lambda **kwargs: {
            "action": "continue_task",
            "task_id": task_id,
            "task_request": "Continue the failed task.",
            "reply": "Continuing.",
        },
    )

    def _unexpected_continue(*args, **kwargs):
        raise AssertionError("explanation requests must not call continue_workflow_payload")

    monkeypatch.setattr(workflow_routes, "continue_workflow_payload", _unexpected_continue)

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="message",
            message="为什么这里会设定 retry 最多 2 次？需要解释原因。",
            metadata={"active_task_id": task_id},
        ),
    )

    assert response["kind"] == "reply"
    assert response["task_id"] == task_id
    assert "workflow_max_rework_runs=128" in response["reply"]
    assert "不是 DevWerk workflow engine 的固定规则" in response["reply"]
    assert response["decision"]["override_reason"] == "explanation_request"


def test_project_conversation_explicit_retry_uses_workflow_retry_action(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-retry"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Retry")
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))
    task = kanban_service.create_task(project_id=project_id, title="Failed draft", description="Failed")
    task_id = task["task"]["id"]
    kanban_service.add_artifact(
        task_id,
        artifact_type="workflow_request_body",
        payload={
            "project_id": project_id,
            "messages": [{"role": "user", "content": "Write the article."}],
            "workspace": {"root_id": project_id},
        },
    )
    kanban_service.move_task(task_id, "failed", force=True)

    def _unexpected_project_llm(*args, **kwargs):
        raise AssertionError("explicit retry should not ask the project LLM to guess routing")

    resumed = {}

    monkeypatch.setattr(kanban_routes, "_ask_project_conversation_agent", _unexpected_project_llm)
    monkeypatch.setattr(
        workflow_routes,
        "_start_workflow_thread",
        lambda task_id_arg, body: resumed.setdefault("payload", {"task_id": task_id_arg, "body": body}),
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="message",
            message="retry task",
            metadata={"active_task_id": task_id},
        ),
    )

    detail = kanban_service.get_task(task_id)["task"]
    event_types = [event["event_type"] for event in detail["events"]]
    assert response["kind"] == "task_retried"
    assert response["task_id"] == task_id
    assert response["workflow_resume"]["reason"] == "retry"
    assert detail["status_key"] == "intake"
    assert resumed["payload"]["task_id"] == task_id
    assert resumed["payload"]["body"]["retry_nonce"]
    assert "workflow_retry_queued" in event_types
    assert "manual_retry_requested" in event_types


def test_project_conversation_chinese_restart_skips_cleanup_task(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-retry-chinese"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Retry Chinese")
    workflow = noncoding_workflow(domain="coding")
    kanban_service.update_project_workflow(project_id, workflow)

    delivery = kanban_service.create_task(
        project_id=project_id,
        title="M0-bootstrap: 创建工程骨架并落盘",
        description="真实交付任务，生成 Spring Boot scaffold",
    )
    delivery_id = delivery["task"]["id"]
    kanban_service.add_artifact(
        delivery_id,
        artifact_type="workflow_request_body",
        payload={
            "project_id": project_id,
            "messages": [{"role": "user", "content": "创建工程骨架并落盘"}],
            "workspace": {"root_id": project_id},
        },
    )
    kanban_service.add_artifact(
        delivery_id,
        artifact_type="code_ready_bundle",
        payload={"changed_paths": ["pom.xml"]},
    )
    kanban_service.move_task(delivery_id, "failed", force=True)

    cleanup = kanban_service.create_task(
        project_id=project_id,
        title=f"Abandon task {delivery_id[:8]}",
        description="将失败任务标记为 abandoned，并生成 support-ticket 工单。无需重试。",
    )
    cleanup_id = cleanup["task"]["id"]
    kanban_service.add_artifact(
        cleanup_id,
        artifact_type="workflow_request_body",
        payload={
            "project_id": project_id,
            "messages": [{"role": "user", "content": "abandon cleanup"}],
            "workspace": {"root_id": project_id},
        },
    )
    kanban_service.move_task(cleanup_id, "failed", force=True)

    def _unexpected_project_llm(*args, **kwargs):
        raise AssertionError("explicit retry should not ask the project LLM to guess routing")

    resumed = {}
    monkeypatch.setattr(kanban_routes, "_ask_project_conversation_agent", _unexpected_project_llm)
    monkeypatch.setattr(
        workflow_routes,
        "_start_workflow_thread",
        lambda task_id_arg, body: resumed.setdefault("payload", {"task_id": task_id_arg, "body": body}),
    )

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="message",
            message="你刚才的任务没有成功，需要重新启动",
            metadata={},
        ),
    )

    assert response["kind"] == "task_retried"
    assert response["task_id"] == delivery_id
    assert resumed["payload"]["task_id"] == delivery_id
    assert kanban_service.get_task(delivery_id)["task"]["status_key"] == "intake"
    assert kanban_service.get_task(cleanup_id)["task"]["status_key"] == "failed"


def test_explicit_continue_terminal_task_returns_reply(monkeypatch, tmp_path):
    kanban_service = configure_kanban(monkeypatch, tmp_path)
    import app.routes.kanban as kanban_routes
    from app.routes import workflows as workflow_routes

    project_id = "dispatch-terminal-guard"
    kanban_service.upsert_project(project_id=project_id, name="Dispatch Terminal Guard")
    kanban_service.update_project_workflow(project_id, noncoding_workflow(domain="writing"))
    task = kanban_service.create_task(project_id=project_id, title="Failed draft", description="Failed")
    task_id = task["task"]["id"]
    kanban_service.move_task(task_id, "failed", force=True)

    def _unexpected_continue(*args, **kwargs):
        raise AssertionError("terminal tasks must not call continue_workflow_payload")

    monkeypatch.setattr(workflow_routes, "continue_workflow_payload", _unexpected_continue)

    response = kanban_routes.kanban_project_conversation_message(
        project_id,
        kanban_routes.ProjectConversationRequest(
            action="continue_task",
            message="补充说明",
            metadata={"active_task_id": task_id},
        ),
    )

    assert response["kind"] == "reply"
    assert response["task_id"] == task_id
    assert "已经是终态" in response["reply"]
    assert response["decision"]["override_reason"] == "terminal_task"
