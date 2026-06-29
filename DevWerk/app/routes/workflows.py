"""
Provider-neutral workflow API endpoints.

All routes are prefixed with /v1 in app/main.py.
"""

from __future__ import annotations

import mimetypes
import asyncio
import hashlib
import logging
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import quote
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.models.protocol import IdeChatResponse, ToolResult
from app.services.kanban import (
    add_artifact,
    add_event,
    append_conversation_message,
    create_task,
    ensure_conversation,
    get_conversation,
    get_project_settings,
    get_project_workflow,
    get_task,
    get_workflow_runtime_state,
    list_events,
    move_task,
    update_conversation,
)
from app.services.provider_errors import (
    LLMProviderError,
    is_retryable_llm_error,
    llm_error_code,
    llm_error_log_payload,
    llm_error_message,
)
from app.services.usage import clear_request, finish_request, start_request, usage_summary
from app.services.workflow import apply_workflow_action
from app.services.workflow_engine import WorkflowEngine

router = APIRouter()
_log = logging.getLogger("devwerk.workflows")
_workflow_dispatch_lock = threading.RLock()
_active_workflows: dict[str, tuple[threading.Thread, float, str]] = {}
_pending_workflows: dict[str, tuple[dict, str]] = {}


def _positive_int(value: object, default: int, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = parsed if parsed > 0 else default
    return min(parsed, maximum) if maximum is not None else parsed


@router.post("/debug/raw")
async def debug_raw(request: Request):
    """Echo the raw request body for debugging."""
    body = await request.body()
    _log.debug("RAW BODY: %s", body)
    return {"ok": True}


@router.get("/usage/summary")
async def get_usage_summary(
    project_id: str | None = None,
    task_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    return usage_summary(project_id=project_id, task_id=task_id, start=start, end=end)


@router.post("/workflows")
async def start_workflow(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        _log.warning("Failed to parse workflow request body: %s", exc)
        return {
            "ok": False,
            "error_code": "BAD_REQUEST",
            "error_message": f"Failed to parse JSON: {exc}",
        }

    return start_workflow_payload(body)


def start_workflow_payload(body: dict) -> dict:
    """Start a workflow from a transport-neutral request payload."""
    body = dict(body)
    messages = body.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {
            "ok": False,
            "error_code": "BAD_REQUEST",
            "error_message": "messages must be a non-empty list",
        }

    project_id = str(body.get("project_id") or "default")
    workflow = get_project_workflow(project_id).get("workflow") or {}
    if not workflow.get("columns"):
        return {
            "ok": False,
            "project_id": project_id,
            "error_code": "PROJECT_WORKFLOW_REQUIRED",
            "error_message": "Project workflow is not configured. Use the project conversation agent to design columns, actions, and node agents before starting tasks.",
        }

    task_id = _ensure_workflow_task(body)
    body["task_id"] = task_id
    ensure_conversation(task_id, metadata={"interaction_mode": body.get("interaction_mode", "auto")})
    conversation = get_conversation(task_id) or {}
    if not conversation.get("messages"):
        for message in messages:
            if isinstance(message, dict) and str(message.get("content") or "").strip():
                append_conversation_message(
                    task_id,
                    role=str(message.get("role") or "user"),
                    content=str(message.get("content") or ""),
                )
    _kanban_artifact(task_id, "workflow_request", payload=_plan_request_artifact(body))
    _kanban_artifact(task_id, "workflow_request_body", payload=_workflow_request_body_artifact(body))
    _kanban_event(
        task_id,
        "workflow_queued",
        {
            "entrypoint": "/v1/workflows",
            "project_id": project_id,
            "workspace": _workspace_debug_summary(body.get("workspace")),
        },
    )
    _start_workflow_thread(task_id, body)
    return _workflow_state_payload(task_id, include_result=False)


@router.post("/workflows/{task_id}/messages")
async def continue_workflow(task_id: str, request: Request):
    try:
        incoming = await request.json()
    except Exception as exc:
        return {"ok": False, "task_id": task_id, "error_code": "BAD_REQUEST", "error_message": f"Failed to parse JSON: {exc}"}
    return continue_workflow_payload(task_id, incoming)


def continue_workflow_payload(task_id: str, incoming: dict) -> dict:
    """Continue a workflow from REST, MCP, or another backend transport."""
    incoming = dict(incoming)
    try:
        detail = get_task(task_id)
    except KeyError:
        return {"ok": False, "task_id": task_id, "error_code": "NOT_FOUND", "error_message": "workflow task not found"}

    action = str(incoming.get("action") or "message").strip().lower().replace("-", "_")
    if action not in {"message", "confirm_plan", "revise_plan", "cancel", "tool_result"}:
        return {"ok": False, "task_id": task_id, "error_code": "BAD_ACTION", "error_message": f"unsupported conversation action: {action}"}
    task = detail.get("task") or {}
    status_key = str(task.get("status_key") or "")
    if status_key in {"done", "failed"}:
        return {
            "ok": False,
            "task_id": task_id,
            "status_key": status_key,
            "error_code": "WORKFLOW_TERMINAL",
            "error_message": f"Workflow task is already terminal ({status_key}); create a new task or use the explicit retry action.",
            "retryable": False,
        }
    if action == "cancel":
        apply_workflow_action(task_id, "abandon", {"reason": str(incoming.get("message") or "user cancelled")})
        update_conversation(task_id, state="cancelled", waiting_for=None)
        return _workflow_state_payload(task_id, include_result=True)

    if action == "tool_result" and (
        not isinstance(incoming.get("tool_results"), list) or not incoming.get("tool_results")
    ):
        return {
            "ok": False,
            "task_id": task_id,
            "error_code": "BAD_TOOL_RESULT",
            "error_message": "tool_result action requires tool_results array",
        }
    if action == "tool_result":
        try:
            incoming["tool_results"] = [
                ToolResult.model_validate(item).model_dump(exclude_none=True)
                for item in incoming["tool_results"]
            ]
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "task_id": task_id,
                "error_code": "BAD_TOOL_RESULT",
                "error_message": f"invalid tool result payload: {exc}",
            }

    content = str(incoming.get("message") or "").strip()
    if content or action == "confirm_plan":
        append_conversation_message(
            task_id,
            role="user",
            content=content or "Confirm the proposed plan and continue.",
            message_type="plan_confirmation" if action == "confirm_plan" else "message",
            metadata={"action": action},
        )

    previous_body = _latest_artifact_payload(task, "workflow_request_body") or {}
    conversation = get_conversation(task_id) or {}
    body = dict(previous_body)
    body.update(
        {
            "task_id": task_id,
            "project_id": task.get("project_id"),
            "resume_action": "revise_plan" if action == "message" and conversation.get("waiting_for") == "plan_confirmation" else action,
            "messages": [
                {"role": item.get("role"), "content": item.get("content")}
                for item in conversation.get("messages") or []
                if not item.get("compressed")
            ],
        }
    )
    if isinstance(incoming.get("workspace"), dict):
        body["workspace"] = incoming["workspace"]
    if isinstance(incoming.get("tool_results"), list):
        body["tool_results"] = incoming["tool_results"]
        if action == "tool_result":
            payload = {
                "waiting_for": conversation.get("waiting_for"),
                "results": incoming["tool_results"],
            }
            _kanban_artifact(task_id, "client_tool_result", payload=payload)
            _kanban_event(
                task_id,
                "workflow_client_tool_result_received",
                {
                    "result_count": len(incoming["tool_results"]),
                    "result_ids": [
                        str(item.get("id") or "")
                        for item in incoming["tool_results"]
                        if isinstance(item, dict)
                    ],
                    "all_ok": all(
                        bool(item.get("ok"))
                        for item in incoming["tool_results"]
                        if isinstance(item, dict)
                    ),
                },
            )
    if isinstance(incoming.get("client_capabilities"), dict):
        body["client_capabilities"] = incoming["client_capabilities"]
    cursor = _latest_artifact_created_at(task, "workflow_result")
    update_conversation(task_id, state="queued", waiting_for=None)
    _kanban_event(task_id, "workflow_resume_queued", {"action": body["resume_action"], "result_after": cursor})
    _start_workflow_thread(task_id, body)
    return _workflow_state_payload(task_id, include_result=False, result_after=cursor)


@router.get("/workflows/{task_id}")
async def get_workflow(task_id: str, result_after: str | None = None):
    return _workflow_state_payload(task_id, include_result=True, result_after=result_after)


@router.get("/workflows/{task_id}/events")
async def stream_workflow_events(task_id: str, result_after: str | None = None):
    return StreamingResponse(
        _workflow_event_stream(task_id, result_after=result_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/workflows/{task_id}/result")
async def get_workflow_result(task_id: str, result_after: str | None = None):
    return _workflow_result_payload(task_id, result_after=result_after)


@router.post("/ide/attachments")
async def upload_attachment(file: UploadFile = File(...), project_id: str | None = Form(default=None)):
    """
    Store a capability-client attachment on the local backend filesystem.

    This is intentionally local-only for now. The returned local_path can be
    referenced by later DevWerk requests, but the file content is not pushed
    into the LLM prompt automatically.
    """
    upload_root = _upload_root()
    upload_root.mkdir(parents=True, exist_ok=True)

    attachment_id = uuid.uuid4().hex
    safe_name = _safe_filename(file.filename or "attachment.bin")
    stored_name = f"{attachment_id}-{safe_name}"
    dst = upload_root / stored_name

    size = 0
    with dst.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)

    content_type = file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    _log.debug(
        "upload_attachment: project_id=%s attachment_id=%s filename=%s content_type=%s size=%s path=%s",
        project_id,
        attachment_id,
        safe_name,
        content_type,
        size,
        dst,
    )

    return {
        "ok": True,
        "id": attachment_id,
        "filename": safe_name,
        "content_type": content_type,
        "size": size,
        "local_path": str(dst),
    }

# Legacy /plan and /execute endpoints were intentionally removed. All clients now use /v1/workflows.


def _upload_root() -> Path:
    configured = os.environ.get("DEVWERK_UPLOAD_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".devwerk" / "uploads"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip()
    return cleaned or "attachment.bin"


def _model_response_summary(obj: object) -> dict:
    if not isinstance(obj, dict):
        return {"type": type(obj).__name__}
    reply = str(obj.get("reply") or obj.get("raw_text") or "")
    ops = obj.get("ops") or []
    patch_ops = obj.get("patch_ops") or []
    tool_requests = obj.get("tool_requests") or []
    return {
        "keys": sorted(obj.keys()),
        "reply_chars": len(reply),
        "reply_preview": reply[:240],
        "ops": len(ops) if isinstance(ops, list) else 0,
        "op_paths": [
            str(op.get("path") or "")
            for op in ops[:30]
            if isinstance(op, dict)
        ] if isinstance(ops, list) else [],
        "patch_ops": len(patch_ops) if isinstance(patch_ops, list) else 0,
        "tool_requests": len(tool_requests) if isinstance(tool_requests, list) else 0,
        "tool_request_tools": [
            str(req.get("tool") or "")
            for req in tool_requests[:30]
            if isinstance(req, dict)
        ] if isinstance(tool_requests, list) else [],
        "done": bool(obj.get("done") or False),
    }


def _compact_tool_evidence(results: object, *, max_items: int = 12, max_chars: int = 24000) -> list[dict]:
    evidence: list[dict] = []
    remaining = max_chars
    for result in list(results)[:max_items]:
        content = str(result.content or "")
        excerpt = content[: min(remaining, 6000)]
        remaining -= len(excerpt)
        evidence.append(
            {
                "id": result.id,
                "ok": result.ok,
                "content": excerpt,
                "content_truncated": len(excerpt) < len(content),
                "error": result.error,
            }
        )
        if remaining <= 0:
            break
    return evidence


def _truncate_invalid_response(obj: object) -> dict:
    if not isinstance(obj, dict):
        return {"type": type(obj).__name__}
    out: dict[str, object] = {}
    for key in ("reply", "code_tree", "done", "raw_model_text"):
        if key in obj:
            value = obj.get(key)
            if isinstance(value, str):
                out[key] = value[:1000]
            else:
                out[key] = value
    for key in ("ops", "patch_ops", "tool_requests"):
        value = obj.get(key)
        if isinstance(value, list):
            out[key] = value[:5]
    return out


def _body_task_id(body: dict) -> str | None:
    value = body.get("task_id")
    if value is None:
        value = body.get("taskId")
    text = str(value or "").strip()
    return text or None


def _kanban_event(task_id: str | None, event_type: str, payload: dict) -> None:
    if not task_id:
        return
    try:
        add_event(task_id, event_type, payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("kanban event skipped task_id=%s event=%s error=%s", task_id, event_type, exc)


def _kanban_artifact(task_id: str | None, artifact_type: str, payload: dict) -> None:
    if not task_id:
        return
    try:
        add_artifact(task_id, artifact_type=artifact_type, payload=payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("kanban artifact skipped task_id=%s type=%s error=%s", task_id, artifact_type, exc)


def _kanban_move(task_id: str | None, status_key: str, payload: dict) -> None:
    if not task_id:
        return
    try:
        move_task(task_id, status_key, force=True, payload=payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("kanban move skipped task_id=%s status=%s error=%s", task_id, status_key, exc)


def _start_workflow_thread(task_id: str, body: dict) -> bool:
    payload = json.loads(json.dumps(body, ensure_ascii=False))
    fingerprint = _workflow_payload_fingerprint(payload)
    with _workflow_dispatch_lock:
        active = _active_workflows.get(task_id)
        if active is not None and active[0].is_alive():
            pending = _pending_workflows.get(task_id)
            if fingerprint == active[2] or (pending is not None and fingerprint == pending[1]):
                _kanban_event(task_id, "workflow_dispatch_deduplicated", {"fingerprint": fingerprint})
                return False
            _kanban_artifact(task_id, "workflow_run_request", payload=payload)
            _pending_workflows[task_id] = (payload, fingerprint)
            _kanban_event(
                task_id,
                "workflow_dispatch_deferred",
                {"fingerprint": fingerprint, "active_fingerprint": active[2]},
            )
            return False

    _kanban_artifact(task_id, "workflow_run_request", payload=payload)
    thread = threading.Thread(
        target=_run_managed_workflow_thread,
        args=(task_id, payload, fingerprint),
        name=f"devwerk-workflow-{task_id[:8]}",
        daemon=True,
    )
    with _workflow_dispatch_lock:
        _active_workflows[task_id] = (thread, time.monotonic(), fingerprint)
    try:
        thread.start()
    except Exception:
        with _workflow_dispatch_lock:
            _active_workflows.pop(task_id, None)
        raise
    _kanban_event(task_id, "workflow_worker_started", {"fingerprint": fingerprint, "thread": thread.name})
    return True


def _run_managed_workflow_thread(task_id: str, body: dict, fingerprint: str) -> None:
    try:
        _run_workflow_thread(task_id, body)
    finally:
        deferred: tuple[dict, str] | None = None
        with _workflow_dispatch_lock:
            active = _active_workflows.get(task_id)
            if active is not None and active[2] == fingerprint:
                _active_workflows.pop(task_id, None)
                deferred = _pending_workflows.pop(task_id, None)
        _kanban_event(task_id, "workflow_worker_stopped", {"fingerprint": fingerprint})
        if deferred is not None:
            _kanban_event(task_id, "workflow_deferred_dispatch_started", {"fingerprint": deferred[1]})
            _start_workflow_thread(task_id, deferred[0])


def workflow_worker_age(task_id: str) -> float | None:
    with _workflow_dispatch_lock:
        active = _active_workflows.get(task_id)
        if active is None or not active[0].is_alive():
            return None
        return max(time.monotonic() - active[1], 0.0)


def _workflow_payload_fingerprint(body: dict) -> str:
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _run_workflow_thread(task_id: str, body: dict) -> None:
    project_id = str(body.get("project_id") or "default")
    ctx = start_request(project_id, route=f"/v1/workflows/{task_id}/run", action="BACKGROUND", task_id=task_id)
    try:
        asyncio.run(_run_workflow(task_id, body))
        finish_request(ctx, status_code=200, success=True)
    except Exception as exc:  # noqa: BLE001
        _log.exception("workflow runner failed task_id=%s", task_id)
        response = IdeChatResponse(
            ok=False,
            reply="",
            done=True,
            task_id=task_id,
            status_key="failed",
            error_code="WORKFLOW_ERROR",
            error_message=f"{type(exc).__name__}: {exc}",
            retryable=True,
        )
        _kanban_artifact(task_id, "workflow_result", payload=response.model_dump())
        try:
            apply_workflow_action(task_id, "fail", {"phase": "workflow", "error": response.error_message})
        except Exception:
            _kanban_move(task_id, response.status_key, {"phase": "workflow", "error": response.error_message})
        finish_request(ctx, status_code=500, success=False, error_type=type(exc).__name__)
    finally:
        clear_request()


async def _run_workflow(task_id: str, body: dict) -> None:
    engine = WorkflowEngine()
    await engine.run(task_id, body)


def _workflow_state_payload(task_id: str, *, include_result: bool, result_after: str | None = None) -> dict:
    try:
        runtime = get_workflow_runtime_state(task_id, result_after=result_after)
    except KeyError:
        return {"ok": False, "task_id": task_id, "error_code": "NOT_FOUND", "error_message": "workflow task not found"}
    task = runtime.get("task") or {}
    result = runtime.get("result")
    status_key = task.get("status_key")
    terminal_statuses = _workflow_terminal_statuses(str(task.get("project_id") or "default"))
    ready = result is not None or status_key in terminal_statuses
    conversation = runtime.get("conversation") or {}
    query = f"?result_after={quote(result_after)}" if result_after else ""
    payload = {
        "ok": True,
        "task_id": task_id,
        "project_id": task.get("project_id"),
        "status_key": status_key,
        "ready": ready,
        "done": status_key in terminal_statuses,
        "conversation_state": conversation.get("state"),
        "waiting_for": conversation.get("waiting_for"),
        "poll_url": f"/v1/workflows/{task_id}{query}",
        "result_url": f"/v1/workflows/{task_id}/result{query}",
        "events_url": f"/v1/workflows/{task_id}/events{query}",
    }
    if include_result and result is not None:
        payload["result"] = result
    return payload


def _workflow_terminal_statuses(project_id: str) -> set[str]:
    workflow = get_project_workflow(project_id).get("workflow") or {}
    actions = workflow.get("actions") if isinstance(workflow.get("actions"), dict) else {}
    terminals = set()
    for action in ("workflow_done", "complete", "completed", "fail", "abandon"):
        rule = actions.get(action) if isinstance(actions, dict) else None
        target = str((rule or {}).get("to") or "").strip().lower() if isinstance(rule, dict) else ""
        if target:
            terminals.add(target)
    return terminals


def workflow_state_payload(task_id: str, *, include_result: bool = True, result_after: str | None = None) -> dict:
    """Return the public workflow state without binding callers to HTTP."""
    return _workflow_state_payload(task_id, include_result=include_result, result_after=result_after)


async def _workflow_event_stream(task_id: str, *, result_after: str | None = None):
    sent_ids: set[str] = set()
    sent_state_status: str | None = None
    heartbeat_at = time.monotonic()

    while True:
        state = _workflow_state_payload(task_id, include_result=True, result_after=result_after)
        if not state.get("ok"):
            yield _sse("workflow_error", state)
            return

        status_key = str(state.get("status_key") or "")
        project_id = str(state.get("project_id") or "default")
        if status_key != sent_state_status:
            yield _sse("workflow_state", _workflow_public_state(state))
            sent_state_status = status_key

        try:
            event_payload = list_events(project_id=project_id, task_id=task_id, limit=500)
            events = event_payload.get("events") if isinstance(event_payload, dict) else []
            if isinstance(events, list):
                for event in reversed(events):
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("id") or "")
                    if not event_id or event_id in sent_ids:
                        continue
                    sent_ids.add(event_id)
                    yield _sse("kanban_event", event)
        except Exception as exc:  # noqa: BLE001
            _log.debug("workflow event stream skipped kanban events task_id=%s error=%s", task_id, exc)

        result = state.get("result")
        if isinstance(result, dict):
            yield _sse("workflow_result", {"task_id": task_id, "status_key": status_key, "result": result})
            return

        if status_key == "failed":
            yield _sse("workflow_error", _workflow_public_state(state))
            return

        now = time.monotonic()
        if now - heartbeat_at >= 15:
            yield _sse("heartbeat", _workflow_public_state(state))
            heartbeat_at = now

        await asyncio.sleep(0.75)


def _workflow_public_state(state: dict) -> dict:
    return {
        "ok": bool(state.get("ok")),
        "task_id": state.get("task_id"),
        "project_id": state.get("project_id"),
        "status_key": state.get("status_key"),
        "ready": bool(state.get("ready")),
        "done": bool(state.get("done")),
        "conversation_state": state.get("conversation_state"),
        "waiting_for": state.get("waiting_for"),
        "poll_url": state.get("poll_url"),
        "result_url": state.get("result_url"),
        "events_url": state.get("events_url"),
        "error_code": state.get("error_code"),
        "error_message": state.get("error_message"),
    }


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _workflow_result_payload(task_id: str, *, result_after: str | None = None) -> dict:
    state = _workflow_state_payload(task_id, include_result=True, result_after=result_after)
    if not state.get("ok"):
        return state
    if "result" not in state:
        return {
            "ok": False,
            "task_id": task_id,
            "status_key": state.get("status_key"),
            "error_code": "PENDING",
            "error_message": "workflow result is not ready",
            "retryable": True,
        }
    return {
        "ok": True,
        "task_id": task_id,
        "status_key": state.get("status_key"),
        "result": state["result"],
    }


def workflow_result_payload(task_id: str, *, result_after: str | None = None) -> dict:
    """Return a workflow result for non-REST transports."""
    return _workflow_result_payload(task_id, result_after=result_after)


def _latest_artifact_payload(task: dict, artifact_type: str, *, result_after: str | None = None) -> dict | None:
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            created_at = str(artifact.get("created_at") or "")
            if result_after and created_at <= result_after:
                continue
            payload = artifact.get("payload")
            return payload if isinstance(payload, dict) else {}
    return None


def _latest_artifact_created_at(task: dict, artifact_type: str) -> str | None:
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            return str(artifact.get("created_at") or "") or None
    return None



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _plan_request_artifact(body: dict) -> dict:
    messages = body.get("messages") if isinstance(body, dict) else []
    return {
        "project_id": body.get("project_id"),
        "mode": body.get("mode", "agent"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "user_request": _first_user_text_from_messages(messages),
        "workspace_summary": _workspace_debug_summary(body.get("workspace")),
    }


def _workflow_request_body_artifact(body: dict) -> dict:
    return {
        "project_id": body.get("project_id"),
        "task_id": body.get("task_id"),
        "mode": body.get("mode", "agent"),
        "interaction_mode": body.get("interaction_mode", "auto"),
        "project_root": body.get("project_root"),
        "messages": body.get("messages") if isinstance(body.get("messages"), list) else [],
        "workspace": body.get("workspace") if isinstance(body.get("workspace"), dict) else None,
        "client_capabilities": body.get("client_capabilities") if isinstance(body.get("client_capabilities"), dict) else {},
    }


def _ensure_workflow_task(body: dict) -> str:
    task_id = _body_task_id(body)
    if task_id:
        return task_id

    messages = body.get("messages") if isinstance(body, dict) else []
    user_text = _first_user_text_from_messages(messages)
    title = (user_text or "DevWerk workflow task").strip().splitlines()[0][:120]
    try:
        workflow = get_project_workflow(body.get("project_id")).get("workflow") or {}
        columns = workflow.get("columns") if isinstance(workflow.get("columns"), list) else []
        initial_status = str((columns[0] or {}).get("status_key") or "").strip() if columns else ""
        if not initial_status:
            raise ValueError("project workflow has no initial column")
        result = create_task(
            project_id=body.get("project_id"),
            title=title or "DevWerk workflow task",
            description=user_text,
            status_key=initial_status,
            metadata={"entrypoint": "/v1/workflows", "mode": body.get("mode", "agent")},
        )
        task_id = result["task"]["id"]
        _log.debug("workflow: created kanban task_id=%s project_id=%s", task_id, body.get("project_id"))
        return task_id
    except Exception as exc:  # noqa: BLE001
        fallback = str(uuid.uuid4())
        _log.warning("workflow: failed to create kanban task, using ephemeral id=%s error=%s", fallback, exc)
        return fallback


def _first_user_text_from_messages(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
            return str(item.get("content") or "")
    return ""


# ---------------------------------------------------------------------------
