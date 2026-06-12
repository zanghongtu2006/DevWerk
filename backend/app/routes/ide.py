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
from app.models.ide import IdeChatRequest, IdeChatResponse
from app.models.plan import ExecuteRequest, PlanResponse
from app.services.coerce import coerce_to_fileops, coerce_to_patchops, coerce_to_toolrequests
from app.services.coder_harness import build_coder_skill
from app.services.kanban import add_artifact, add_event, move_task
from app.services.llm_factory import get_llm_client
from app.services.planner import Planner as build_planner
from app.services.prompt_builder import build_model_messages
from app.services.usage import usage_summary

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
    _log.debug(
        "ide_plan: received project_id=%s mode=%s messages=%s workspace_summary=%s",
        body.get("project_id"),
        body.get("mode", "agent"),
        len(messages),
        _workspace_debug_summary(body.get("workspace")),
    )
    messages = _append_workspace_context(messages, body.get("workspace"))
    _log.debug("ide_plan: messages_after_workspace_context=%s", len(messages))

    if not any(m.get("role", "").lower() == "user" for m in messages):
        return PlanResponse(
            ok=False,
            error_code="BAD_REQUEST",
            error_message="messages must contain at least one user message",
        )

    cfg = settings()
    try:
        cfg.validate_provider("planner")
    except ValueError as ve:
        _log.warning("Provider validation failed: %s", ve)
        return PlanResponse(
            ok=False,
            error_code="CONFIG_ERROR",
            error_message=str(ve),
        )

    try:
        p = build_planner(agent_name="planner")
    except (ValueError, NotImplementedError) as exc:
        _log.warning("Planner creation failed: %s", exc)
        return PlanResponse(
            ok=False,
            error_code="CONFIG_ERROR",
            error_message=str(exc),
        )

    mode = str(body.get("mode", "agent")).strip().lower() or "agent"
    task_id = _body_task_id(body)
    _kanban_event(task_id, "plan_started", {"mode": mode})

    try:
        result = p.plan(messages=messages, mode=mode)
        if result.ok:
            _kanban_artifact(task_id, "plan_response", payload=result.model_dump())
            _kanban_move(task_id, "planned", {"files": len(result.files), "warnings": len(result.warnings)})
        else:
            _kanban_move(task_id, "failed", {"phase": "plan", "error_code": result.error_code})
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

    approved_paths = body.get("approved_paths", [])
    if not isinstance(approved_paths, list):
        return IdeChatResponse(
            ok=False,
            done=True,
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
            error_code="CONFIG_ERROR",
            error_message=str(exc),
            retryable=False,
        )

    messages = body.get("messages", [])
    if not isinstance(messages, list):
        return IdeChatResponse(
            ok=False,
            done=True,
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

    approved_set = set(approved_paths)

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
    task_id = _body_task_id(body)
    _kanban_event(task_id, "execute_started", {"mode": mode, "approved_paths": approved_paths})
    _kanban_move(task_id, "coding", {"approved_paths": approved_paths})

    max_retries = 2
    backoff = 0.8

    for attempt in range(max_retries + 1):
        try:
            obj = client.chat_structured(
                build_model_messages(_ChatProxy(messages), provider=cfg.llm_provider_name)
            )
            _log.debug(
                "ide_execute: model_response keys=%s ops=%s patch_ops=%s tool_requests=%s done=%s",
                sorted(obj.keys()) if isinstance(obj, dict) else type(obj).__name__,
                len(obj.get("ops") or []),
                len(obj.get("patch_ops") or []),
                len(obj.get("tool_requests") or []),
                bool(obj.get("done") or False),
            )

            ops = _filter_ops(obj.get("ops") or [], approved_set)
            patch_ops = _filter_patch_ops(obj.get("patch_ops") or [], approved_set)
            _log.debug(
                "ide_execute: filtered ops=%s patch_ops=%s approved_paths=%s",
                len(ops),
                len(patch_ops),
                sorted(approved_set),
            )

            response = IdeChatResponse(
                ok=True,
                reply=obj.get("reply", ""),
                code_tree=obj.get("code_tree"),
                ops=coerce_to_fileops(ops, tool_results=[]),
                tool_requests=coerce_to_toolrequests(obj.get("tool_requests") or []),
                patch_ops=coerce_to_patchops(patch_ops, tool_results=[]),
                done=bool(obj.get("done") or False),
            )
            _kanban_artifact(task_id, "execute_response", payload=response.model_dump())
            _kanban_move(
                task_id,
                "verification",
                {"ops": len(response.ops), "patch_ops": len(response.patch_ops), "done": response.done},
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

            _log.exception("Execute LLM call failed (attempt %s/%s)", attempt, max_retries)
            _kanban_move(task_id, "failed", {"phase": "execute", "error": f"{type(exc).__name__}: {exc}"})
            return IdeChatResponse(
                ok=False,
                reply="",
                done=True,
                error_code="MODEL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                retryable=(attempt < max_retries),
            )

    return IdeChatResponse(ok=False, reply="", done=True, error_code="UNKNOWN")


def _filter_ops(ops: list[dict], approved: set[str]) -> list[dict]:
    result = [op for op in ops if isinstance(op, dict) and op.get("path", "").strip() in approved]
    dropped = [
        op.get("path", "") if isinstance(op, dict) else type(op).__name__
        for op in ops
        if not (isinstance(op, dict) and op.get("path", "").strip() in approved)
    ]
    if dropped:
        _log.debug("filter_ops: dropped_unapproved_or_invalid=%s", dropped)
    return result


def _filter_patch_ops(patch_ops: list[dict], approved: set[str]) -> list[dict]:
    result = []
    for po in patch_ops:
        if not isinstance(po, dict):
            continue
        content = po.get("content") or ""
        import re as _re
        diff_paths = set()
        for m in _re.finditer(r"^\+\+\+ b/(.+)$", content, _re.MULTILINE):
            diff_paths.add(m.group(1).strip())
        if diff_paths and not diff_paths.issubset(approved):
            _log.debug("filter_patch_ops: dropped diff_paths=%s approved=%s", sorted(diff_paths), sorted(approved))
            continue
        result.append(po)
    return result


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
        move_task(task_id, status_key, force=True, payload=payload)
    except Exception as exc:  # noqa: BLE001
        _log.debug("kanban move skipped task_id=%s status=%s error=%s", task_id, status_key, exc)


# ---------------------------------------------------------------------------
# Original chat endpoint (unchanged)
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest) -> IdeChatResponse:
    cfg = settings()
    _log.debug(
        "ide_chat: received project_id=%s mode=%s messages=%s workspace_summary=%s tool_results=%s",
        req.project_id,
        req.mode,
        len(req.messages),
        _workspace_debug_summary(req.workspace.model_dump() if req.workspace else None),
        len(req.tool_results),
    )

    try:
        cfg.validate_provider("coder")
    except ValueError as ve:
        _log.warning("Provider validation failed: %s", ve)
        return IdeChatResponse(
            ok=False, reply="", done=True,
            error_code="CONFIG_ERROR", error_message=str(ve), retryable=False,
        )

    try:
        client = get_llm_client("coder")
    except (ValueError, NotImplementedError) as exc:
        _log.warning("LLM client creation failed: %s", exc)
        return IdeChatResponse(
            ok=False, reply="", done=True,
            error_code="CONFIG_ERROR", error_message=str(exc), retryable=False,
        )

    messages = build_model_messages(req, provider=cfg.llm_provider_name)

    max_retries = 2
    backoff = 0.8

    for attempt in range(max_retries + 1):
        try:
            obj = client.chat_structured(messages)
            obj = _guard_delete_ops(req, obj)

            return IdeChatResponse(
                ok=True,
                reply=obj.get("reply", ""),
                code_tree=obj.get("code_tree"),
                ops=coerce_to_fileops(obj.get("ops") or [], tool_results=req.tool_results),
                tool_requests=coerce_to_toolrequests(obj.get("tool_requests") or []),
                patch_ops=coerce_to_patchops(obj.get("patch_ops") or []),
                done=bool(obj.get("done") or False),
            )
        except Exception as exc:  # noqa: BLE001
            is_timeout = (
                "ReadTimeout" in type(exc).__name__
                or "timeout" in str(exc).lower()
            )
            if attempt < max_retries and is_timeout:
                time.sleep(backoff * (attempt + 1))
                continue

            _log.exception("LLM call failed (attempt %s/%s)", attempt, max_retries)
            return IdeChatResponse(
                ok=False, reply="", done=True,
                error_code="MODEL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                retryable=(attempt < max_retries),
            )

    return IdeChatResponse(ok=False, reply="", done=True, error_code="UNKNOWN")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _first_user_text(req: IdeChatRequest) -> str:
    for m in reversed(req.messages):
        if (m.role or "").lower() == "user":
            return m.content or ""
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
    def __init__(self, messages: list[dict]):
        self.messages = [_Message(m) for m in messages]
        self.mode = "agent"
        self.project_root = None
        self.workspace = None
        self.tool_results = []


class _Message:
    def __init__(self, d: dict):
        self.role = d.get("role", "user")
        self.content = d.get("content", "")
