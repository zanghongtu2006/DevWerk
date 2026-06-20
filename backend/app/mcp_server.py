"""Model Context Protocol transport for DevWerk backend capabilities."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from app.routes.ide import (
    continue_workflow_payload,
    start_workflow_payload,
    workflow_result_payload,
    workflow_state_payload,
)
from app.routes.kanban import WorkflowActionRequest, kanban_task_action
from app.services.kanban import get_task, list_events, list_projects


MCP_INSTRUCTIONS = """
DevWerk is a backend-owned coding workflow and Kanban state machine.

Start coding work with devwerk_start_workflow, then inspect it with
devwerk_get_workflow. A workflow may pause for plan confirmation, client tool
results, or file application. Continue a paused workflow with
devwerk_continue_workflow. When DevWerk returns file operations, apply them in
the current coding client and report the outcome with
devwerk_report_apply_result. Never report an apply as successful until the
client has actually written the files. Use devwerk_get_events when detailed
agent, phase, and transition history is needed.
""".strip()


def _result(value: dict[str, Any]) -> dict[str, Any]:
    return value


def devwerk_start_workflow(
    project_id: str,
    request: str,
    project_root: str | None = None,
    workspace: dict[str, Any] | None = None,
    interaction_mode: Literal["auto", "confirm_plan"] = "auto",
    client_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a DevWerk coding workflow and return its task ID and state."""
    request_text = request.strip()
    if not request_text:
        return _result({"ok": False, "error_code": "BAD_REQUEST", "error_message": "request is required"})

    workspace_payload = dict(workspace or {})
    workspace_payload.setdefault("root_id", project_id)
    body: dict[str, Any] = {
        "project_id": project_id,
        "mode": "agent",
        "interaction_mode": interaction_mode,
        "messages": [{"role": "user", "content": request_text}],
        "workspace": workspace_payload,
    }
    if project_root:
        body["project_root"] = project_root
    if client_capabilities:
        body["client_capabilities"] = client_capabilities
    return _result(start_workflow_payload(body))


def devwerk_get_workflow(
    task_id: str,
    include_result: bool = True,
    result_after: str | None = None,
) -> dict[str, Any]:
    """Get current workflow state and, when ready, its generated result."""
    return _result(
        workflow_state_payload(task_id, include_result=include_result, result_after=result_after)
    )


def devwerk_get_workflow_result(task_id: str, result_after: str | None = None) -> dict[str, Any]:
    """Get only the latest workflow result, returning PENDING until available."""
    return _result(workflow_result_payload(task_id, result_after=result_after))


def devwerk_continue_workflow(
    task_id: str,
    action: Literal["message", "confirm_plan", "revise_plan", "tool_result"] = "message",
    message: str = "",
    workspace: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    client_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Continue a paused workflow with guidance, confirmation, or tool results."""
    incoming: dict[str, Any] = {"action": action, "message": message}
    if workspace is not None:
        incoming["workspace"] = workspace
    if tool_results is not None:
        incoming["tool_results"] = tool_results
    if client_capabilities is not None:
        incoming["client_capabilities"] = client_capabilities
    return _result(continue_workflow_payload(task_id, incoming))


def devwerk_cancel_workflow(task_id: str, reason: str = "Cancelled by MCP client.") -> dict[str, Any]:
    """Cancel a non-terminal workflow through the workflow state machine."""
    return _result(continue_workflow_payload(task_id, {"action": "cancel", "message": reason}))


def devwerk_report_apply_result(
    task_id: str,
    ok: bool,
    changed_paths: list[str] | None = None,
    snapshot_id: str | None = None,
    error_message: str | None = None,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report actual client-side file application and verification results."""
    payload: dict[str, Any] = {
        "ok": ok,
        "changed_paths": changed_paths or [],
    }
    if snapshot_id:
        payload["snapshot_id"] = snapshot_id
    if error_message:
        payload["error_message"] = error_message
    if verification is not None:
        payload["verification"] = verification
    try:
        return _result(
            kanban_task_action(task_id, WorkflowActionRequest(action="apply_result", payload=payload))
        )
    except HTTPException as exc:
        return _result(
            {
                "ok": False,
                "task_id": task_id,
                "error_code": "WORKFLOW_ACTION_REJECTED",
                "error_message": str(exc.detail),
            }
        )


def devwerk_get_events(
    project_id: str,
    task_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Read persisted workflow, agent, and Kanban events in newest-first order."""
    return _result(list_events(project_id=project_id, task_id=task_id, limit=max(1, min(limit, 1000))))


def devwerk_get_task(task_id: str) -> dict[str, Any]:
    """Read a Kanban task with its persisted artifacts and event history."""
    try:
        return _result(get_task(task_id))
    except KeyError:
        return _result(
            {"ok": False, "task_id": task_id, "error_code": "NOT_FOUND", "error_message": "task not found"}
        )


def devwerk_list_projects() -> dict[str, Any]:
    """List projects known to the DevWerk backend."""
    return _result({"ok": True, "projects": list_projects()})


MCP_TOOLS = (
    devwerk_start_workflow,
    devwerk_get_workflow,
    devwerk_get_workflow_result,
    devwerk_continue_workflow,
    devwerk_cancel_workflow,
    devwerk_report_apply_result,
    devwerk_get_events,
    devwerk_get_task,
    devwerk_list_projects,
)


def create_mcp_server() -> tuple[FastMCP, Any]:
    """Create an isolated MCP transport for one FastAPI application instance."""
    server = FastMCP(
        "DevWerk",
        instructions=MCP_INSTRUCTIONS,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )
    for tool in MCP_TOOLS:
        server.tool(structured_output=True)(tool)
    return server, server.streamable_http_app()
