"""
IDE-facing API endpoints (/v1/ide/*).

All routes are prefixed with /v1 in app/main.py.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, Request

from app.core.config import settings
from app.models.ide import IdeChatRequest, IdeChatResponse
from app.models.plan import ExecuteRequest, PlanResponse
from app.services.coerce import coerce_to_fileops, coerce_to_patchops, coerce_to_toolrequests
from app.services.llm_factory import get_llm_client
from app.services.planner import Planner as build_planner
from app.services.prompt_builder import build_model_messages

router = APIRouter()
_log = logging.getLogger("devwerk.ide")


@router.post("/debug/raw")
async def debug_raw(request: Request):
    """Echo the raw request body for debugging."""
    body = await request.body()
    _log.debug("RAW BODY: %s", body)
    return {"ok": True}


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

    if not any(m.get("role", "").lower() == "user" for m in messages):
        return PlanResponse(
            ok=False,
            error_code="BAD_REQUEST",
            error_message="messages must contain at least one user message",
        )

    cfg = settings()
    try:
        cfg.validate_provider()
    except ValueError as ve:
        _log.warning("Provider validation failed: %s", ve)
        return PlanResponse(
            ok=False,
            error_code="CONFIG_ERROR",
            error_message=str(ve),
        )

    try:
        p = build_planner(config=cfg.get_llm_config())
    except (ValueError, NotImplementedError) as exc:
        _log.warning("Planner creation failed: %s", exc)
        return PlanResponse(
            ok=False,
            error_code="CONFIG_ERROR",
            error_message=str(exc),
        )

    mode = str(body.get("mode", "agent")).strip().lower() or "agent"

    try:
        result = p.plan(messages=messages, mode=mode)
        return result
    except Exception as exc:  # noqa: BLE001
        _log.exception("Planner raised unhandled exception")
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
        cfg.validate_provider()
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
        client = get_llm_client()
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

    approved_set = set(approved_paths)

    # Inject execution guard after the last user message.
    guard_message = {
        "role": "system",
        "content": (
            f"EXECUTION GUARD: You may ONLY produce file operations for these paths:\n"
            + "\n".join(f"  - {p}" for p in sorted(approved_set))
            + "\nAll other paths are forbidden. Do not output ops for any path not listed above."
        ),
    }

    if messages and messages[-1].get("role", "").lower() == "user":
        messages = messages[:-1] + [guard_message, messages[-1]]
    else:
        messages = messages + [guard_message]

    mode = str(body.get("mode", "agent")).strip().lower() or "agent"

    max_retries = 2
    backoff = 0.8

    for attempt in range(max_retries + 1):
        try:
            obj = client.chat_structured(
                build_model_messages(_ChatProxy(messages), provider=cfg.llm_provider)
            )

            ops = _filter_ops(obj.get("ops") or [], approved_set)
            patch_ops = _filter_patch_ops(obj.get("patch_ops") or [], approved_set)

            return IdeChatResponse(
                ok=True,
                reply=obj.get("reply", ""),
                code_tree=obj.get("code_tree"),
                ops=coerce_to_fileops(ops, tool_results=[]),
                tool_requests=coerce_to_toolrequests(obj.get("tool_requests") or []),
                patch_ops=coerce_to_patchops(patch_ops, tool_results=[]),
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

            _log.exception("Execute LLM call failed (attempt %s/%s)", attempt, max_retries)
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
    return [op for op in ops if isinstance(op, dict) and op.get("path", "").strip() in approved]


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
            continue
        result.append(po)
    return result


# ---------------------------------------------------------------------------
# Original chat endpoint (unchanged)
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest) -> IdeChatResponse:
    cfg = settings()

    try:
        cfg.validate_provider()
    except ValueError as ve:
        _log.warning("Provider validation failed: %s", ve)
        return IdeChatResponse(
            ok=False, reply="", done=True,
            error_code="CONFIG_ERROR", error_message=str(ve), retryable=False,
        )

    try:
        client = get_llm_client()
    except (ValueError, NotImplementedError) as exc:
        _log.warning("LLM client creation failed: %s", exc)
        return IdeChatResponse(
            ok=False, reply="", done=True,
            error_code="CONFIG_ERROR", error_message=str(exc), retryable=False,
        )

    messages = build_model_messages(req, provider=cfg.llm_provider)

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
