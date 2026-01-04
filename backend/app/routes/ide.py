# app/routes/ide.py
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, Request

from app.core.config import Settings
from app.models.ide import IdeChatRequest, IdeChatResponse
from app.services.coerce import coerce_to_fileops, coerce_to_patchops, coerce_to_toolrequests
from app.services.llm_factory import get_llm_client
from app.services.prompt_builder import build_model_messages

router = APIRouter()


@router.post("/debug/raw")
async def debug_raw(request: Request):
    body = await request.body()
    print("RAW BODY:", body)
    return {"ok": True}


@router.post("/v1/ide/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest):
    settings = Settings.from_env()
    client = get_llm_client(settings)

    messages = build_model_messages(req, provider=settings.llm_provider)

    max_retries = 2
    backoff = 0.8
    last_ex: Exception | None = None

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
        except Exception as ex:
            last_ex = ex
            is_timeout = "ReadTimeout" in type(ex).__name__ or "timeout" in str(ex).lower()
            if attempt < max_retries and is_timeout:
                time.sleep(backoff * (attempt + 1))
                continue
            break

    msg = f"{type(last_ex).__name__}: {last_ex}" if last_ex else "Unknown error"
    return IdeChatResponse(
        ok=False,
        reply="",
        done=True,
        error_code="MODEL_TIMEOUT",
        error_message=msg,
        retryable=True,
    )


def _first_user_text(req: IdeChatRequest) -> str:
    for m in reversed(req.messages):
        if (m.role or "").lower() == "user":
            return m.content or ""
    return ""


def _extract_target_names(user_text: str) -> list[str]:
    names = re.findall(r"[A-Za-z0-9_\-]+\.(?:java|kt|py|js|ts|go|rs|c|cpp|h|hpp)", user_text)
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
    p = Path(project_root) / rel_path
    return p.exists()


def _guard_delete_ops(req: IdeChatRequest, obj: dict) -> dict:
    if req.mode != "agent":
        return obj

    ops = obj.get("ops") or []
    if not isinstance(ops, list) or not ops:
        return obj

    deletes = [o for o in ops if isinstance(o, dict) and o.get("op") == "delete_path"]
    if not deletes:
        return obj

    # 同轮有 tool_requests => 清空 ops/patch_ops
    if obj.get("tool_requests"):
        obj["ops"] = []
        obj["patch_ops"] = []
        return obj

    # delete_path 必须真实存在，否则强制先 search
    missing = []
    for o in deletes:
        path = (o.get("path") or "").strip()
        if not path or not _exists(req.project_root, path):
            missing.append(path)

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
                "args": {"query": query, "paths": ["src/", "test/", ""], "max_results": 200},
            }
        ],
        "patch_ops": [],
        "done": False,
    }
