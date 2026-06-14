"""
IDE-facing API endpoints (/v1/ide/*).

All routes are prefixed with /v1 in app/main.py.
"""

from __future__ import annotations

import mimetypes
import logging
import json
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile

from app.core.config import settings
from app.models.ide import IdeChatRequest, IdeChatResponse, ToolRequest, ToolResult
from app.models.plan import ExecuteRequest, PlanResponse
from app.services.coerce import coerce_to_fileops, coerce_to_patchops, coerce_to_toolrequests
from app.services.coder_harness import build_coder_skill
from app.services.kanban import add_artifact, add_event, create_task, move_task
from app.services.llm_factory import get_llm_client
from app.services.planner import Planner as build_planner
from app.services.prompt_builder import build_model_messages
from app.services.usage import usage_summary
from app.services.workflow import apply_workflow_action

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
        p = build_planner(agent_name=planner_agent)
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
        result = p.plan(messages=messages, mode=mode)
        if result.ok:
            _kanban_artifact(task_id, "plan_response", payload=result.model_dump())
            _kanban_move(task_id, "planned", {"files": len(result.files), "warnings": len(result.warnings)})
            result.task_id = task_id
            result.status_key = "planned"
        else:
            _kanban_move(task_id, "failed", {"phase": "plan", "error_code": result.error_code})
            result.task_id = task_id
            result.status_key = "failed"
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
                _log.debug(
                    "ide_execute: filtered round=%s ops=%s patch_ops=%s tool_requests=%s approved_paths=%s",
                    tool_round + 1,
                    len(ops),
                    len(patch_ops),
                    len(tool_requests),
                    sorted(approved_set),
                )

                if tool_requests and mode == "agent" and not ops and not patch_ops:
                    _kanban_event(
                        task_id,
                        "execute_tool_requests",
                        {"round": tool_round + 1, "count": len(tool_requests)},
                    )
                    tool_results = _execute_tool_requests(body.get("project_root"), tool_requests)
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
                            + json.dumps([r.model_dump(exclude_none=True) for r in tool_requests], ensure_ascii=False),
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
                    tool_requests=[],
                    patch_ops=coerce_to_patchops(patch_ops),
                    done=bool(obj.get("done") or False),
                )
                _kanban_artifact(task_id, "execute_response", payload=response.model_dump())
                _kanban_move(
                    task_id,
                    "ready_to_apply",
                    {"ops": len(response.ops), "patch_ops": len(response.patch_ops), "done": response.done},
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
    root_name = Path(project_root).name if project_root else ""
    if root_name and len(parts) > 1 and parts[0].lower() == root_name.lower():
        parts = parts[1:]
    return "/".join(parts)


def _append_workspace_context(messages: list[dict], workspace: object) -> list[dict]:
    if not isinstance(workspace, dict):
        _log.debug("append_workspace_context: no workspace dict type=%s", type(workspace).__name__)
        return messages
    _log.debug("append_workspace_context: input_messages=%s workspace=%s", len(messages), _workspace_debug_summary(workspace))
    coder_skill = build_coder_skill(workspace)
    compact = json.dumps(workspace, ensure_ascii=False, separators=(",", ":"))
    injected = list(messages)
    if coder_skill:
        injected.append({"role": "user", "content": coder_skill})
        _log.debug("append_workspace_context: injected coder_harness_skill chars=%s", len(coder_skill))
    else:
        _log.debug("append_workspace_context: coder_harness_skill not generated")
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


def _tool_list_dir(root: Path, rel: str, max_depth: int) -> str:
    target = _safe_project_path(root, rel)
    if not target.exists():
        return f"[list_dir] not found: {rel}"
    if not target.is_dir():
        return f"[list_dir] not a directory: {rel}"
    lines = [f"{target.name or '.'}/"]

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
    roots = paths or ["src", "app", ""]
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
    return query.lower().endswith((".java", ".kt", ".xml", ".yml", ".yaml", ".gradle", ".properties", ".json"))


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
        action = _workflow_action_for_status(status_key, payload)
        if action:
            apply_workflow_action(task_id, action, payload)
        else:
            move_task(task_id, status_key, force=True, payload=payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("kanban move skipped task_id=%s status=%s error=%s", task_id, status_key, exc)


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
# Original chat endpoint (unchanged)
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest) -> IdeChatResponse:
    cfg = settings()
    task_id = _ensure_chat_task(req)
    _log.debug(
        "ide_chat: received project_id=%s mode=%s messages=%s workspace_summary=%s tool_results=%s",
        req.project_id,
        req.mode,
        len(req.messages),
        _workspace_debug_summary(req.workspace.model_dump() if req.workspace else None),
        len(req.tool_results),
    )
    _kanban_artifact(task_id, "chat_request", payload=_chat_request_artifact(req))
    _kanban_move(task_id, "context_indexed", {"workspace": _workspace_debug_summary(req.workspace.model_dump() if req.workspace else None)})

    planning = _run_chat_planning(req, task_id)
    if planning.get("ok") is False:
        _kanban_move(task_id, "failed", {"phase": "planning", "error": planning.get("error_message")})
        return IdeChatResponse(
            ok=False,
            reply="",
            done=True,
            task_id=task_id,
            status_key="failed",
            planning=planning,
            error_code=planning.get("error_code") or "PLAN_ERROR",
            error_message=planning.get("error_message"),
            retryable=True,
        )
    _kanban_artifact(task_id, "planning_bundle", payload=planning)
    _kanban_move(task_id, "planned", {"files": len((planning.get("implementation_plan") or {}).get("files_to_touch") or [])})

    try:
        cfg.validate_provider("coder")
    except ValueError as ve:
        _log.warning("Provider validation failed: %s", ve)
        _kanban_move(task_id, "failed", {"phase": "coding", "error": str(ve)})
        return IdeChatResponse(
            ok=False, reply="", done=True,
            task_id=task_id,
            status_key="failed",
            planning=planning,
            error_code="CONFIG_ERROR", error_message=str(ve), retryable=False,
        )

    try:
        client = get_llm_client("coder")
    except (ValueError, NotImplementedError) as exc:
        _log.warning("LLM client creation failed: %s", exc)
        _kanban_move(task_id, "failed", {"phase": "coding", "error": str(exc)})
        return IdeChatResponse(
            ok=False, reply="", done=True,
            task_id=task_id,
            status_key="failed",
            planning=planning,
            error_code="CONFIG_ERROR", error_message=str(exc), retryable=False,
        )

    messages = build_model_messages(req, provider=cfg.get_llm_config("coder").get("protocol", cfg.llm_provider_name))
    messages.append({
        "role": "user",
        "content": "planning_bundle:\n" + json.dumps(planning, ensure_ascii=False, separators=(",", ":")),
    })
    _kanban_move(task_id, "coding", {"planning_artifact": True})

    max_retries = 2
    backoff = 0.8

    for attempt in range(max_retries + 1):
        try:
            obj = client.chat_structured(messages)
            obj = _guard_delete_ops(req, obj)

            response = IdeChatResponse(
                ok=True,
                reply=obj.get("reply", ""),
                task_id=task_id,
                status_key="ready_to_apply",
                planning=planning,
                code_tree=obj.get("code_tree"),
                ops=coerce_to_fileops(obj.get("ops") or [], tool_results=req.tool_results),
                tool_requests=coerce_to_toolrequests(obj.get("tool_requests") or []),
                patch_ops=coerce_to_patchops(obj.get("patch_ops") or []),
                done=bool(obj.get("done") or False),
            )
            _kanban_artifact(task_id, "coding_response", payload=response.model_dump())
            _kanban_move(
                task_id,
                "ready_to_apply",
                {
                    "ops": len(response.ops),
                    "patch_ops": len(response.patch_ops),
                    "tool_requests": len(response.tool_requests),
                    "done": response.done,
                },
            )
            return response
        except Exception as exc:  # noqa: BLE001
            is_timeout = (
                "ReadTimeout" in type(exc).__name__
                or "timeout" in str(exc).lower()
            )
            if attempt < max_retries and is_timeout:
                time.sleep(backoff * (attempt + 1))
                continue

            _log.exception("LLM call failed (attempt %s/%s)", attempt, max_retries)
            _kanban_move(task_id, "failed", {"phase": "coding", "error": f"{type(exc).__name__}: {exc}"})
            return IdeChatResponse(
                ok=False, reply="", done=True,
                task_id=task_id,
                status_key="failed",
                planning=planning,
                error_code="MODEL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                retryable=(attempt < max_retries),
            )

    _kanban_move(task_id, "failed", {"phase": "coding", "error": "unknown"})
    return IdeChatResponse(ok=False, reply="", done=True, task_id=task_id, status_key="failed", planning=planning, error_code="UNKNOWN")


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


def _ensure_chat_task(req: IdeChatRequest) -> str:
    if req.task_id:
        return req.task_id
    title = (_first_user_text(req) or "DevWerk coding task").strip().splitlines()[0][:120]
    try:
        result = create_task(
            project_id=req.project_id,
            title=title or "DevWerk coding task",
            description=_first_user_text(req),
            status_key="draft",
            metadata={"entrypoint": "/v1/chat", "mode": req.mode},
        )
        task_id = result["task"]["id"]
        _log.debug("ide_chat: created kanban task_id=%s project_id=%s", task_id, req.project_id)
        return task_id
    except Exception as exc:  # noqa: BLE001
        fallback = str(uuid.uuid4())
        _log.warning("ide_chat: failed to create kanban task, using ephemeral id=%s error=%s", fallback, exc)
        return fallback


def _chat_request_artifact(req: IdeChatRequest) -> dict:
    workspace = req.workspace.model_dump() if req.workspace else None
    return {
        "project_id": req.project_id,
        "mode": req.mode,
        "message_count": len(req.messages),
        "user_request": _first_user_text(req),
        "workspace_summary": _workspace_debug_summary(workspace),
        "tool_results": len(req.tool_results),
    }


def _run_chat_planning(req: IdeChatRequest, task_id: str) -> dict:
    messages = _append_workspace_context(_message_dicts(req), req.workspace.model_dump() if req.workspace else None)
    agent_name = "planner"
    try:
        settings().validate_provider(agent_name)
    except ValueError as exc:
        _log.warning("ide_chat: planner unavailable, falling back to coder for planning: %s", exc)
        agent_name = "coder"
        try:
            settings().validate_provider(agent_name)
        except ValueError as coder_exc:
            return {
                "ok": False,
                "error_code": "CONFIG_ERROR",
                "error_message": str(coder_exc),
            }

    _kanban_event(task_id, "planning_started", {"agent": agent_name})
    planner = build_planner(agent_name=agent_name)
    plan = planner.plan(messages=messages, mode=req.mode)
    bundle = _planning_bundle(req, plan.model_dump())
    bundle["ok"] = plan.ok
    if not plan.ok:
        bundle["error_code"] = plan.error_code
        bundle["error_message"] = plan.error_message
    return bundle


def _planning_bundle(req: IdeChatRequest, plan: dict) -> dict:
    user_text = _first_user_text(req)
    files = plan.get("files") or []
    file_paths = [f.get("path") for f in files if isinstance(f, dict) and f.get("path")]
    warnings = plan.get("warnings") or []
    return {
        "requirement_breakdown": {
            "summary": user_text[:500],
            "goals": [plan.get("summary") or user_text[:160] or "Implement requested code change."],
            "non_goals": [],
            "acceptance_criteria": [
                "Generated changes are returned to the IDE plugin as guarded file operations or patch operations.",
                "The IDE plugin applies changes through its snapshot-protected write path.",
            ],
            "constraints": [
                "Do not bypass the DevWerk kanban task lifecycle.",
                "Do not write files directly from the backend.",
            ],
        },
        "system_design": {
            "summary": plan.get("summary") or "",
            "components": file_paths,
            "api_changes": [],
            "storage_changes": [],
            "risks": warnings,
        },
        "implementation_plan": {
            "summary": plan.get("summary") or "",
            "files_to_touch": file_paths,
            "steps": [
                f"{f.get('nature', 'modify')} {f.get('path')}: {f.get('description', '')}".strip()
                for f in files
                if isinstance(f, dict)
            ],
            "warnings": warnings,
        },
        "verification_policy": {
            "required": ["compile", "smoke"],
            "optional": ["unit", "integration"],
            "results": {},
        },
        "raw_plan": plan,
    }


def _message_dicts(req: IdeChatRequest) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in req.messages]


def _first_user_text(req: IdeChatRequest) -> str:
    for m in reversed(req.messages):
        if (m.role or "").lower() == "user":
            return m.content or ""
    return ""


def _first_user_text_from_messages(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role") or "").lower() == "user":
            return str(item.get("content") or "")
    return ""


def _extract_target_names(user_text: str) -> list[str]:
    names = re.findall(
        r"[A-Za-z0-9_\-]+\.(?:java|kt|py|js|ts|go|rs|c|cpp|h|hpp)",
        user_text,
    )
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _exists(project_root: str | None, rel_path: str) -> bool:
    if not project_root:
        return False
    return (Path(project_root) / rel_path).exists()


def _guard_delete_ops(req: IdeChatRequest, obj: dict) -> dict:
    if req.mode != "agent":
        return obj

    ops = obj.get("ops") or []
    if not isinstance(ops, list) or not ops:
        return obj

    deletes = [o for o in ops if isinstance(o, dict) and o.get("op") == "delete_path"]
    if not deletes:
        return obj

    if obj.get("tool_requests"):
        obj["ops"] = []
        obj["patch_ops"] = []
        return obj

    missing = [
        o["path"]
        for o in deletes
        if not (o.get("path") or "").strip() or not _exists(req.project_root, o["path"])
    ]
    if not missing:
        return obj

    user_text = _first_user_text(req)
    targets = _extract_target_names(user_text)
    if not targets:
        targets = [Path(p).name for p in missing if p]

    query = targets[0] if targets else "Main.java"

    return {
        "reply": "需要先定位目标文件",
        "code_tree": None,
        "ops": [],
        "tool_requests": [
            {
                "id": "guard-1",
                "tool": "search",
                "args": {
                    "query": query,
                    "paths": ["src/", "test/", ""],
                    "max_results": 200,
                },
            }
        ],
        "patch_ops": [],
        "done": False,
    }


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
