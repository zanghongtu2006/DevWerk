"""
IDE-facing API endpoints (/v1/ide/*).

All routes are prefixed with /v1 in app/main.py.
"""

from __future__ import annotations

import mimetypes
import asyncio
import logging
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.models.ide import IdeChatResponse, ToolRequest, ToolResult
from app.models.plan import PlanResponse
from app.services.coerce import coerce_to_fileops, coerce_to_patchops, coerce_to_toolrequests
from app.services.coder_harness import build_code_context_summary, build_coder_skill
from app.services.kanban import add_artifact, add_event, create_task, get_task, list_events, move_task
from app.services.llm_factory import get_llm_client
from app.services.planner import Planner as build_planner
from app.services.prompt_builder import build_model_messages
from app.services.usage import clear_request, finish_request, start_request, usage_summary
from app.services.workflow import apply_workflow_action, record_phase_output
from app.services.workflow_engine import WorkflowEngine

router = APIRouter()
_log = logging.getLogger("devwerk.ide")


@router.post("/debug/raw")
async def debug_raw(request: Request):
    """Echo the raw request body for debugging."""
    body = await request.body()
    _log.debug("RAW BODY: %s", body)
    return {"ok": True}


@router.get("/usage/summary")
async def get_usage_summary(project_id: str | None = None, start: str | None = None, end: str | None = None):
    return usage_summary(project_id=project_id, start=start, end=end)


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

    messages = body.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {
            "ok": False,
            "error_code": "BAD_REQUEST",
            "error_message": "messages must be a non-empty list",
        }

    task_id = _ensure_workflow_task(body)
    body["task_id"] = task_id
    project_id = str(body.get("project_id") or "default")
    _kanban_artifact(task_id, "workflow_request", payload=_plan_request_artifact(body))
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


@router.get("/workflows/{task_id}")
async def get_workflow(task_id: str):
    return _workflow_state_payload(task_id, include_result=True)


@router.get("/workflows/{task_id}/events")
async def stream_workflow_events(task_id: str):
    return StreamingResponse(
        _workflow_event_stream(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/workflows/{task_id}/result")
async def get_workflow_result(task_id: str):
    return _workflow_result_payload(task_id)


@router.post("/ide/attachments")
async def upload_attachment(file: UploadFile = File(...), project_id: str | None = Form(default=None)):
    """
    Store an IDE attachment on the local backend filesystem.

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


# ---------------------------------------------------------------------------
# Plan endpoint
# ---------------------------------------------------------------------------

@router.post("/plan", response_model=PlanResponse)
async def ide_plan(request: Request) -> PlanResponse:
    """
    Plan phase — research the codebase and return a file-level change plan.

    This is a READ-ONLY operation: no files are created or modified.
    """
    try:
        body = await request.json()
    except Exception as exc:
        _log.warning("Failed to parse plan request body: %s", exc)
        return PlanResponse(
            ok=False,
            error_code="BAD_REQUEST",
            error_message=f"Failed to parse JSON: {exc}",
        )

    messages = body.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return PlanResponse(
            ok=False,
            error_code="BAD_REQUEST",
            error_message="messages must be a non-empty list",
        )
    task_id = _ensure_plan_task(body)
    _log.debug(
        "ide_plan: received project_id=%s task_id=%s mode=%s messages=%s workspace_summary=%s",
        body.get("project_id"),
        task_id,
        body.get("mode", "agent"),
        len(messages),
        _workspace_debug_summary(body.get("workspace")),
    )
    _kanban_artifact(task_id, "plan_request", payload=_plan_request_artifact(body))
    _kanban_move(task_id, "context_indexed", {"workspace": _workspace_debug_summary(body.get("workspace"))})
    messages = _append_workspace_context(messages, body.get("workspace"))
    _log.debug("ide_plan: messages_after_workspace_context=%s", len(messages))

    if not any(m.get("role", "").lower() == "user" for m in messages):
        _kanban_move(task_id, "failed", {"phase": "plan", "error": "messages must contain at least one user message"})
        return PlanResponse(
            ok=False,
            task_id=task_id,
            status_key="failed",
            error_code="BAD_REQUEST",
            error_message="messages must contain at least one user message",
        )

    cfg = settings()
    planner_agent = "planner"
    try:
        cfg.validate_provider(planner_agent)
    except ValueError as ve:
        _log.warning("Planner provider unavailable, falling back to coder: %s", ve)
        planner_agent = "coder"
        try:
            cfg.validate_provider(planner_agent)
        except ValueError as coder_ve:
            _log.warning("Provider validation failed: %s", coder_ve)
            _kanban_move(task_id, "failed", {"phase": "plan", "error": str(coder_ve)})
            return PlanResponse(
                ok=False,
                task_id=task_id,
                status_key="failed",
                error_code="CONFIG_ERROR",
                error_message=str(coder_ve),
            )

    try:
        p = build_planner(
            agent_name=planner_agent,
            event_sink=lambda event_type, payload: _kanban_event(task_id, event_type, payload),
        )
    except (ValueError, NotImplementedError) as exc:
        _log.warning("Planner creation failed: %s", exc)
        _kanban_move(task_id, "failed", {"phase": "plan", "error": str(exc)})
        return PlanResponse(
            ok=False,
            task_id=task_id,
            status_key="failed",
            error_code="CONFIG_ERROR",
            error_message=str(exc),
        )

    mode = str(body.get("mode", "agent")).strip().lower() or "agent"
    _kanban_event(task_id, "plan_started", {"mode": mode, "agent": planner_agent})

    try:
        result = p.plan(messages=messages, mode=mode, project_root=body.get("project_root"))
        if result.ok:
            _kanban_artifact(task_id, "plan_response", payload=result.model_dump())
            phase_output = _record_phase_output(
                task_id,
                phase="plan",
                agent=planner_agent,
                status_key="planned",
                summary=result.summary,
                inputs={
                    "mode": mode,
                    "message_count": len(messages),
                    "workspace": _workspace_debug_summary(body.get("workspace")),
                },
                outputs={
                    "files": [f.model_dump() for f in result.files],
                    "file_count": len(result.files),
                },
                warnings=result.warnings,
                next_action="execute",
            )
            _kanban_move(task_id, "planned", {"files": len(result.files), "warnings": len(result.warnings), "session_id": phase_output.get("session_id") if phase_output else None})
            result.task_id = task_id
            result.status_key = "planned"
            if phase_output:
                result.session_id = phase_output.get("session_id")
                result.phase_output = phase_output
                result.next_action = phase_output.get("next_action")
        else:
            phase_output = _record_phase_output(
                task_id,
                phase="plan",
                agent=planner_agent,
                status_key="failed",
                summary=result.error_message or result.summary or "Planner failed to produce a file-level plan.",
                inputs={
                    "mode": mode,
                    "message_count": len(messages),
                    "workspace": _workspace_debug_summary(body.get("workspace")),
                },
                outputs={
                    "files": [],
                    "file_count": 0,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                },
                warnings=result.warnings,
                next_action="retry",
            )
            _kanban_move(task_id, "failed", {"phase": "plan", "error_code": result.error_code})
            result.task_id = task_id
            result.status_key = "failed"
            if phase_output:
                result.session_id = phase_output.get("session_id")
                result.phase_output = phase_output
                result.next_action = phase_output.get("next_action")
        _log.debug(
            "ide_plan: planner_result ok=%s files=%s warnings=%s summary=%s",
            result.ok,
            len(result.files),
            len(result.warnings),
            result.summary,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        _log.exception("Planner raised unhandled exception")
        _kanban_move(task_id, "failed", {"phase": "plan", "error": f"{type(exc).__name__}: {exc}"})
        return PlanResponse(
            ok=False,
            task_id=task_id,
            status_key="failed",
            error_code="PLAN_ERROR",
            error_message=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# Execute endpoint
# ---------------------------------------------------------------------------

@router.post("/execute", response_model=IdeChatResponse)
async def ide_execute(request: Request) -> IdeChatResponse:
    """
    Execute phase — run the approved plan.

    approved_paths whitelist is enforced: any op targeting a path not in
    the whitelist is silently dropped.
    """
    try:
        body = await request.json()
    except Exception as exc:
        _log.warning("Failed to parse execute request body: %s", exc)
        return IdeChatResponse(
            ok=False,
            done=True,
            error_code="BAD_REQUEST",
            error_message=f"Failed to parse JSON: {exc}",
        )

    task_id = _body_task_id(body)
    approved_paths = body.get("approved_paths", [])
    if not isinstance(approved_paths, list):
        return IdeChatResponse(
            ok=False,
            done=True,
            task_id=task_id,
            status_key="failed",
            error_code="BAD_REQUEST",
            error_message="approved_paths must be a list",
        )

    cfg = settings()
    try:
        cfg.validate_provider("executor")
    except ValueError as ve:
        _log.warning("Provider validation failed: %s", ve)
        return IdeChatResponse(
            ok=False,
            done=True,
            task_id=task_id,
            status_key="failed",
            error_code="CONFIG_ERROR",
            error_message=str(ve),
            retryable=False,
        )

    try:
        client = get_llm_client("executor")
    except (ValueError, NotImplementedError) as exc:
        _log.warning("LLM client creation failed: %s", exc)
        return IdeChatResponse(
            ok=False,
            done=True,
            task_id=task_id,
            status_key="failed",
            error_code="CONFIG_ERROR",
            error_message=str(exc),
            retryable=False,
        )

    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return IdeChatResponse(
            ok=False,
            done=True,
            task_id=task_id,
            status_key="failed",
            error_code="BAD_REQUEST",
            error_message="messages must be a list",
        )
    _log.debug(
        "ide_execute: received project_id=%s mode=%s messages=%s approved_paths=%s workspace_summary=%s",
        body.get("project_id"),
        body.get("mode", "agent"),
        len(messages),
        approved_paths,
        _workspace_debug_summary(body.get("workspace")),
    )
    messages = _append_workspace_context(messages, body.get("workspace"))
    _log.debug("ide_execute: messages_after_workspace_context=%s", len(messages))

    approved_set = _approved_path_set(approved_paths, body.get("project_root"))

    # Keep the execution guard as the final instruction after workspace/coder context.
    guard_message = {
        "role": "system",
        "content": (
            f"EXECUTION GUARD: You may ONLY produce file operations for these paths:\n"
            + "\n".join(f"  - {p}" for p in sorted(approved_set))
            + "\nAll other paths are forbidden. Do not output ops for any path not listed above."
        ),
    }
    messages = messages + [guard_message]
    _log.debug("ide_execute: appended_execution_guard approved_count=%s final_messages=%s", len(approved_set), len(messages))

    mode = str(body.get("mode", "agent")).strip().lower() or "agent"
    _kanban_event(task_id, "execute_started", {"mode": mode, "approved_paths": approved_paths})
    _kanban_move(task_id, "coding", {"approved_paths": approved_paths})

    max_retries = 2
    backoff = 0.8

    tool_results: list[ToolResult] = []
    max_tool_rounds = 6

    for attempt in range(max_retries + 1):
        try:
            for tool_round in range(max_tool_rounds):
                _kanban_event(
                    task_id,
                    "execute_llm_round_started",
                    {
                        "round": tool_round + 1,
                        "attempt": attempt + 1,
                        "max_tool_rounds": max_tool_rounds,
                        "agent": "executor",
                        "input": {
                            "message_count": len(messages),
                            "tool_result_count": len(tool_results),
                            "approved_paths": sorted(approved_set),
                            "roles": [str(m.get("role") or "") for m in messages],
                        },
                    },
                )
                obj = client.chat_structured(
                    build_model_messages(
                        _ChatProxy(
                            messages,
                            project_root=body.get("project_root"),
                            tool_results=tool_results,
                        ),
                        provider=cfg.get_llm_config("executor").get("protocol", cfg.llm_provider_name),
                    )
                )
                _kanban_event(
                    task_id,
                    "execute_llm_round_result",
                    {
                        "round": tool_round + 1,
                        "attempt": attempt + 1,
                        "agent": "executor",
                        "output": _model_response_summary(obj),
                    },
                )
                _log.debug(
                    "ide_execute: model_response round=%s keys=%s ops=%s patch_ops=%s tool_requests=%s done=%s",
                    tool_round + 1,
                    sorted(obj.keys()) if isinstance(obj, dict) else type(obj).__name__,
                    len(obj.get("ops") or []),
                    len(obj.get("patch_ops") or []),
                    len(obj.get("tool_requests") or []),
                    bool(obj.get("done") or False),
                )

                ops = _filter_ops(obj.get("ops") or [], approved_set, body.get("project_root"))
                patch_ops = _filter_patch_ops(obj.get("patch_ops") or [], approved_set, body.get("project_root"))
                tool_requests = coerce_to_toolrequests(obj.get("tool_requests") or [])
                backend_tool_requests = [req for req in tool_requests if _is_backend_tool_request(req)]
                client_tool_requests = [req for req in tool_requests if not _is_backend_tool_request(req)]
                _log.debug(
                    "ide_execute: filtered round=%s ops=%s patch_ops=%s backend_tool_requests=%s client_tool_requests=%s approved_paths=%s",
                    tool_round + 1,
                    len(ops),
                    len(patch_ops),
                    len(backend_tool_requests),
                    len(client_tool_requests),
                    sorted(approved_set),
                )

                if backend_tool_requests and mode == "agent" and not ops and not patch_ops:
                    _kanban_event(
                        task_id,
                        "execute_tool_requests",
                        {"round": tool_round + 1, "count": len(backend_tool_requests)},
                    )
                    tool_results = _execute_tool_requests(body.get("project_root"), backend_tool_requests)
                    _kanban_event(
                        task_id,
                        "execute_tool_results",
                        {
                            "round": tool_round + 1,
                            "results": [
                                {"id": r.id, "ok": r.ok, "content_chars": len(r.content or ""), "error": r.error}
                                for r in tool_results
                            ],
                        },
                    )
                    _log.debug(
                        "ide_execute: tool_results round=%s results=%s",
                        tool_round + 1,
                        [
                            {"id": r.id, "ok": r.ok, "content_chars": len(r.content or ""), "error": r.error}
                            for r in tool_results
                        ],
                    )
                    messages = messages + [
                        {
                            "role": "assistant",
                            "content": "tool_requests:\n"
                            + json.dumps([r.model_dump(exclude_none=True) for r in backend_tool_requests], ensure_ascii=False),
                        }
                    ]
                    continue

                response = IdeChatResponse(
                    ok=True,
                    reply=obj.get("reply", ""),
                    task_id=task_id,
                    status_key="ready_to_apply",
                    code_tree=obj.get("code_tree"),
                    ops=coerce_to_fileops(ops, tool_results=[]),
                    tool_requests=client_tool_requests,
                    patch_ops=coerce_to_patchops(patch_ops),
                    done=bool(obj.get("done") or False),
                )
                phase_output = _record_phase_output(
                    task_id,
                    phase="coding",
                    agent="executor",
                    status_key="ready_to_apply",
                    summary=response.reply or "Generated changes are ready for plugin apply.",
                    inputs={
                        "mode": mode,
                        "approved_paths": sorted(approved_set),
                        "tool_round": tool_round + 1,
                    },
                    outputs={
                        "ops": [{"op": op.op, "path": op.path, "language": op.language} for op in response.ops],
                        "patch_ops": len(response.patch_ops),
                        "client_tool_requests": [
                            {"id": req.id, "tool": req.tool, "args": req.args}
                            for req in response.tool_requests
                        ],
                        "done": response.done,
                    },
                    warnings=[],
                    next_action="apply_result",
                )
                if phase_output:
                    response.session_id = phase_output.get("session_id")
                    response.phase_output = phase_output
                    response.next_action = phase_output.get("next_action")
                _kanban_artifact(task_id, "execute_response", payload=response.model_dump())
                _kanban_event(
                    task_id,
                    "execute_response_ready",
                    {
                        "round": tool_round + 1,
                        "ops": len(response.ops),
                        "paths": [op.path for op in response.ops],
                        "patch_ops": len(response.patch_ops),
                        "client_tool_requests": [
                            {"id": req.id, "tool": req.tool}
                            for req in response.tool_requests
                        ],
                        "done": response.done,
                    },
                )
                _kanban_move(
                    task_id,
                    "ready_to_apply",
                    {
                        "ops": len(response.ops),
                        "patch_ops": len(response.patch_ops),
                        "tool_requests": len(response.tool_requests),
                        "done": response.done,
                        "session_id": response.session_id,
                    },
                )
                return response

            _log.debug(
                "ide_execute: tool loop exhausted rounds=%s last_tool_results=%s",
                max_tool_rounds,
                len(tool_results),
            )
            _kanban_move(task_id, "failed", {"phase": "execute", "error": "tool loop exhausted"})
            return IdeChatResponse(
                ok=False,
                reply="",
                done=True,
                task_id=task_id,
                status_key="failed",
                error_code="TOOL_LOOP_EXHAUSTED",
                error_message=f"Executor requested tools for {max_tool_rounds} rounds without producing file operations.",
                retryable=True,
            )

        except Exception as exc:  # noqa: BLE001
            is_timeout = (
                "ReadTimeout" in type(exc).__name__
                or "timeout" in str(exc).lower()
            )
            if attempt < max_retries and is_timeout:
                time.sleep(backoff * (attempt + 1))
                continue

            _log.exception("Execute LLM call failed (attempt %s/%s)", attempt, max_retries)
            _kanban_move(task_id, "failed", {"phase": "execute", "error": f"{type(exc).__name__}: {exc}"})
            return IdeChatResponse(
                ok=False,
                reply="",
                done=True,
                task_id=task_id,
                status_key="failed",
                error_code="MODEL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                retryable=(attempt < max_retries),
            )

    return IdeChatResponse(ok=False, reply="", done=True, task_id=task_id, status_key="failed", error_code="UNKNOWN")


def _filter_ops(ops: list[dict], approved: set[str], project_root: str | None = None) -> list[dict]:
    result = []
    dropped = []
    for op in ops:
        if not isinstance(op, dict):
            dropped.append(type(op).__name__)
            continue
        path = _canonical_rel_path(str(op.get("path") or ""), project_root)
        if path and path in approved:
            normalized = dict(op)
            normalized["path"] = path
            result.append(normalized)
            continue
        dropped.append(str(op.get("path") or ""))
    if dropped:
        _log.debug("filter_ops: dropped_unapproved_or_invalid=%s", dropped)
    return result


def _filter_patch_ops(patch_ops: list[dict], approved: set[str], project_root: str | None = None) -> list[dict]:
    result = []
    for po in patch_ops:
        if not isinstance(po, dict):
            continue
        content = po.get("content") or ""
        import re as _re
        diff_paths = set()
        for m in _re.finditer(r"^\+\+\+ b/(.+)$", content, _re.MULTILINE):
            diff_paths.add(_canonical_rel_path(m.group(1).strip(), project_root))
        if diff_paths and not diff_paths.issubset(approved):
            _log.debug("filter_patch_ops: dropped diff_paths=%s approved=%s", sorted(diff_paths), sorted(approved))
            continue
        result.append(po)
    return result


def _approved_path_set(paths: list, project_root: str | None) -> set[str]:
    approved = set()
    for item in paths:
        path = _canonical_rel_path(str(item or ""), project_root)
        if path:
            approved.add(path)
    return approved


def _canonical_rel_path(path: str, project_root: str | None = None) -> str:
    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("/"):
        text = text[1:]
    parts = [part for part in text.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _append_workspace_context(messages: list[dict], workspace: object) -> list[dict]:
    if not isinstance(workspace, dict):
        _log.debug("append_workspace_context: no workspace dict type=%s", type(workspace).__name__)
        return messages
    _log.debug("append_workspace_context: input_messages=%s workspace=%s", len(messages), _workspace_debug_summary(workspace))
    code_context = build_code_context_summary(workspace)
    coder_skill = build_coder_skill(workspace)
    compact = json.dumps(workspace, ensure_ascii=False, separators=(",", ":"))
    injected = list(messages)
    if code_context.get("available"):
        code_context_json = json.dumps(code_context, ensure_ascii=False, separators=(",", ":"))
        injected.append({"role": "user", "content": "code_context_summary:\n" + code_context_json})
        _log.debug("append_workspace_context: injected code_context_summary chars=%s", len(code_context_json))
    else:
        _log.debug("append_workspace_context: code_context_summary unavailable reason=%s", code_context.get("reason"))
    if coder_skill:
        injected.append({"role": "user", "content": coder_skill})
        _log.debug("append_workspace_context: injected code_context_skill chars=%s", len(coder_skill))
    else:
        _log.debug("append_workspace_context: code_context_skill not generated")
    injected.append({"role": "user", "content": "workspace_summary:\n" + compact})
    _log.debug("append_workspace_context: injected workspace_summary chars=%s output_messages=%s", len(compact), len(injected))
    return injected


def _workspace_debug_summary(workspace: object) -> dict[str, object]:
    if not isinstance(workspace, dict):
        return {"type": type(workspace).__name__, "present": False}
    source_map = workspace.get("source_map")
    files = source_map.get("files") if isinstance(source_map, dict) else None
    sample_paths = []
    if isinstance(files, list):
        for item in files[:12]:
            if isinstance(item, dict) and item.get("path"):
                sample_paths.append(item.get("path"))
    return {
        "present": True,
        "keys": sorted(workspace.keys()),
        "tree_preview_chars": len(workspace.get("tree_preview") or ""),
        "source_map_present": isinstance(source_map, dict),
        "source_map_root": source_map.get("root") if isinstance(source_map, dict) else None,
        "source_map_total_files": source_map.get("total_files") if isinstance(source_map, dict) else None,
        "source_map_indexed_files": source_map.get("indexed_files") if isinstance(source_map, dict) else None,
        "source_map_files_payload": len(files) if isinstance(files, list) else 0,
        "sample_paths": sample_paths,
    }


def _execute_tool_requests(project_root: str | None, reqs: list[ToolRequest]) -> list[ToolResult]:
    if not project_root:
        return [ToolResult(id=req.id, ok=False, error="project_root is null") for req in reqs]
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return [ToolResult(id=req.id, ok=False, error=f"project_root is not a directory: {project_root}") for req in reqs]

    results: list[ToolResult] = []
    for req in reqs:
        try:
            if req.tool == "list_dir":
                rel = _canonical_rel_path(str(req.args.get("path") or ""), str(root))
                if rel and _contains_hidden_segment(rel):
                    results.append(ToolResult(id=req.id, ok=False, error=f"blocked hidden directory path: {rel}"))
                    continue
                max_depth = _int_arg(req.args.get("max_depth"), 2, 1, 8)
                results.append(ToolResult(id=req.id, ok=True, content=_tool_list_dir(root, rel, max_depth)))
            elif req.tool == "read_file":
                rel = _canonical_rel_path(str(req.args.get("path") or ""), str(root))
                if _has_hidden_dir_segment(rel):
                    results.append(ToolResult(id=req.id, ok=False, error=f"blocked hidden directory path: {rel}"))
                    continue
                start_line = _int_arg(req.args.get("start_line"), 1, 1, 1_000_000)
                end_line = _int_arg(req.args.get("end_line"), start_line + 200, start_line, 1_000_000)
                results.append(ToolResult(id=req.id, ok=True, content=_tool_read_file(root, rel, start_line, end_line)))
            elif req.tool == "search":
                query = str(req.args.get("query") or "")
                max_results = _int_arg(req.args.get("max_results"), 50, 1, 500)
                raw_paths = req.args.get("paths")
                paths = raw_paths if isinstance(raw_paths, list) else []
                safe_paths = []
                for item in paths:
                    rel = _canonical_rel_path(str(item or ""), str(root))
                    if not rel or not _contains_hidden_segment(rel):
                        safe_paths.append(rel)
                results.append(ToolResult(id=req.id, ok=True, content=_tool_search(root, query, safe_paths, max_results)))
            else:
                results.append(ToolResult(id=req.id, ok=False, error=f"unknown tool: {req.tool}"))
        except Exception as exc:  # noqa: BLE001
            results.append(ToolResult(id=req.id, ok=False, error=f"{type(exc).__name__}: {exc}"))
    return results


def _is_backend_tool_request(req: ToolRequest) -> bool:
    return req.tool in {"list_dir", "read_file", "search"}


def _tool_list_dir(root: Path, rel: str, max_depth: int) -> str:
    target = _safe_project_path(root, rel)
    if not target.exists():
        return f"[list_dir] not found: {rel}"
    if not target.is_dir():
        return f"[list_dir] not a directory: {rel}"
    label = "." if not rel or rel == "." else (target.name or ".")
    lines = [f"{label}/"]

    def walk(path: Path, depth: int, indent: str) -> None:
        if depth >= max_depth:
            return
        children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            if child.is_dir() and child.name.startswith("."):
                continue
            if child.is_dir():
                lines.append(f"{indent}  {child.name}/")
                walk(child, depth + 1, indent + "  ")
            else:
                lines.append(f"{indent}  {child.name}")

    walk(target, 0, "")
    return "\n".join(lines).rstrip()


def _tool_read_file(root: Path, rel: str, start_line: int, end_line: int) -> str:
    target = _safe_project_path(root, rel)
    if not target.exists():
        return f"[read_file] not found: {rel}"
    if target.is_dir():
        return f"[read_file] is a directory: {rel}"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1) - 1
    end = max(end_line, start_line)
    sliced = lines[start:end]
    return f"FILE: {rel} (lines {start_line}-{end_line})\n" + "\n".join(sliced)


def _tool_search(root: Path, query: str, paths: list[str], max_results: int) -> str:
    needle = query.strip()
    if not needle:
        return "[search] empty query"
    roots = paths or [""]
    filename_mode = _looks_like_filename_query(needle)
    results: list[str] = []
    for rel in roots:
        base = _safe_project_path(root, rel)
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            if len(results) >= max_results:
                break
            if not path.is_file():
                continue
            rel_path = path.relative_to(root).as_posix()
            if _has_hidden_dir_segment(rel_path):
                continue
            if any(part.lower() in {"build", "out", "node_modules"} for part in path.relative_to(root).parts[:-1]):
                continue
            if filename_mode:
                matched = path.name.lower() == needle.lower()
            else:
                if path.stat().st_size > 1_000_000:
                    continue
                matched = needle.lower() in path.read_text(encoding="utf-8", errors="ignore").lower()
            if matched:
                results.append(rel_path)
        if len(results) >= max_results:
            break
    return "\n".join(results) if results else "[search] no hits"


def _safe_project_path(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes project_root: {rel}")
    return target


def _contains_hidden_segment(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.replace("\\", "/").split("/") if part)


def _has_hidden_dir_segment(rel: str) -> bool:
    parts = [part for part in rel.replace("\\", "/").split("/") if part]
    return len(parts) > 1 and any(part.startswith(".") for part in parts[:-1])


def _looks_like_filename_query(query: str) -> bool:
    if any(ch in query for ch in ("/", "\\", "\n", "\t")) or "." not in query:
        return False
    return bool(re.fullmatch(r"[^./\\\s][^/\\\s]*\.[^./\\\s][^/\\\s]*", query.strip()))


def _int_arg(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


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


def _record_phase_output(
    task_id: str | None,
    *,
    phase: str,
    agent: str,
    status_key: str,
    summary: str,
    inputs: dict,
    outputs: dict,
    warnings: list[str],
    next_action: str | None,
) -> dict | None:
    if not task_id:
        return None
    try:
        return record_phase_output(
            task_id,
            phase=phase,
            agent=agent,
            status_key=status_key,
            summary=summary,
            inputs=inputs,
            outputs=outputs,
            warnings=warnings,
            next_action=next_action,
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("workflow phase output skipped task_id=%s phase=%s error=%s", task_id, phase, exc)
        return None


def _kanban_move(task_id: str | None, status_key: str, payload: dict) -> None:
    if not task_id:
        return
    try:
        action = _workflow_action_for_status(status_key, payload)
        if action:
            apply_workflow_action(task_id, action, payload)
        else:
            move_task(task_id, status_key, force=True, payload=payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("kanban move skipped task_id=%s status=%s error=%s", task_id, status_key, exc)


class _BodyRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self) -> dict:
        return self._body


def _start_workflow_thread(task_id: str, body: dict) -> None:
    payload = json.loads(json.dumps(body, ensure_ascii=False))
    thread = threading.Thread(
        target=_run_workflow_thread,
        args=(task_id, payload),
        name=f"devwerk-workflow-{task_id[:8]}",
        daemon=True,
    )
    thread.start()


def _run_workflow_thread(task_id: str, body: dict) -> None:
    project_id = str(body.get("project_id") or "default")
    ctx = start_request(project_id, route=f"/v1/workflows/{task_id}/run", action="BACKGROUND")
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
        _kanban_move(task_id, "failed", {"phase": "workflow", "error": response.error_message})
        finish_request(ctx, status_code=500, success=False, error_type=type(exc).__name__)
    finally:
        clear_request()


async def _run_workflow(task_id: str, body: dict) -> None:
    engine = WorkflowEngine(
        plan_runner=_run_plan_phase,
        coding_runner=_run_coding_phase,
    )
    await engine.run(task_id, body)


async def _run_plan_phase(body: dict) -> PlanResponse:
    return await ide_plan(_BodyRequest(body))


async def _run_coding_phase(body: dict) -> IdeChatResponse:
    return await ide_execute(_BodyRequest(body))


def _workflow_state_payload(task_id: str, *, include_result: bool) -> dict:
    try:
        task_detail = get_task(task_id)
    except KeyError:
        return {"ok": False, "task_id": task_id, "error_code": "NOT_FOUND", "error_message": "workflow task not found"}
    task = task_detail.get("task") or {}
    result = _latest_artifact_payload(task, "workflow_result")
    status_key = task.get("status_key")
    ready = result is not None or status_key in {"ready_to_apply", "done", "failed"}
    payload = {
        "ok": True,
        "task_id": task_id,
        "project_id": task.get("project_id"),
        "status_key": status_key,
        "ready": ready,
        "done": status_key in {"done", "failed"},
        "poll_url": f"/v1/workflows/{task_id}",
        "result_url": f"/v1/workflows/{task_id}/result",
        "events_url": f"/v1/workflows/{task_id}/events",
    }
    if include_result and result is not None:
        payload["result"] = result
    return payload


async def _workflow_event_stream(task_id: str):
    sent_ids: set[str] = set()
    sent_state_status: str | None = None
    heartbeat_at = time.monotonic()

    while True:
        state = _workflow_state_payload(task_id, include_result=True)
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
        "poll_url": state.get("poll_url"),
        "result_url": state.get("result_url"),
        "events_url": state.get("events_url"),
        "error_code": state.get("error_code"),
        "error_message": state.get("error_message"),
    }


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _workflow_result_payload(task_id: str) -> dict:
    state = _workflow_state_payload(task_id, include_result=True)
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


def _latest_artifact_payload(task: dict, artifact_type: str) -> dict | None:
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    if not isinstance(artifacts, list):
        return None
    for artifact in reversed(artifacts):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == artifact_type:
            payload = artifact.get("payload")
            return payload if isinstance(payload, dict) else {}
    return None


def _workflow_action_for_status(status_key: str, payload: dict) -> str | None:
    status = str(status_key or "").strip().lower()
    if status == "context_indexed":
        return "context_indexed"
    if status == "planned":
        return "plan_ready"
    if status == "coding":
        return "coding_started"
    if status == "ready_to_apply":
        return "ready_to_apply"
    if status == "failed":
        phase = str((payload or {}).get("phase") or "").strip().lower()
        if phase == "plan":
            return "plan_failed"
        return "coding_failed"
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_plan_task(body: dict) -> str:
    task_id = _body_task_id(body)
    if task_id:
        return task_id

    messages = body.get("messages") if isinstance(body, dict) else []
    user_text = _first_user_text_from_messages(messages)
    title = (user_text or "DevWerk planning task").strip().splitlines()[0][:120]
    try:
        result = create_task(
            project_id=body.get("project_id"),
            title=title or "DevWerk planning task",
            description=user_text,
            status_key="draft",
            metadata={"entrypoint": "/v1/plan", "mode": body.get("mode", "agent")},
        )
        task_id = result["task"]["id"]
        _log.debug("ide_plan: created kanban task_id=%s project_id=%s", task_id, body.get("project_id"))
        return task_id
    except Exception as exc:  # noqa: BLE001
        fallback = str(uuid.uuid4())
        _log.warning("ide_plan: failed to create kanban task, using ephemeral id=%s error=%s", fallback, exc)
        return fallback


def _plan_request_artifact(body: dict) -> dict:
    messages = body.get("messages") if isinstance(body, dict) else []
    return {
        "project_id": body.get("project_id"),
        "mode": body.get("mode", "agent"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "user_request": _first_user_text_from_messages(messages),
        "workspace_summary": _workspace_debug_summary(body.get("workspace")),
    }


def _ensure_workflow_task(body: dict) -> str:
    task_id = _body_task_id(body)
    if task_id:
        return task_id

    messages = body.get("messages") if isinstance(body, dict) else []
    user_text = _first_user_text_from_messages(messages)
    title = (user_text or "DevWerk workflow task").strip().splitlines()[0][:120]
    try:
        result = create_task(
            project_id=body.get("project_id"),
            title=title or "DevWerk workflow task",
            description=user_text,
            status_key="draft",
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
# Minimal proxy so build_model_messages() works with plain dict messages
# ---------------------------------------------------------------------------

class _ChatProxy:
    def __init__(self, messages: list[dict], project_root: str | None = None, tool_results: list[ToolResult] | None = None):
        self.messages = [_Message(m) for m in messages]
        self.mode = "agent"
        self.project_root = project_root
        self.workspace = None
        self.tool_results = tool_results or []


class _Message:
    def __init__(self, d: dict):
        self.role = d.get("role", "user")
        self.content = d.get("content", "")
