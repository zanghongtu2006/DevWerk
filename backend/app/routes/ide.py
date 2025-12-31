# app/routes/ide.py
from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, Request

from app.models.ide import IdeChatRequest, IdeChatResponse
from app.services.coerce import coerce_to_fileops, coerce_to_patchops, coerce_to_toolrequests
from app.services.ollama_client import OllamaClient
from app.services.prompt_builder import build_model_messages
import time

router = APIRouter()


@router.post("/debug/raw")
async def debug_raw(request: Request):
    body = await request.body()
    print("RAW BODY:", body)
    return {"ok": True}

@router.post("/v1/ide/chat", response_model=IdeChatResponse)
def ide_chat(req: IdeChatRequest):
    client = OllamaClient()
    messages = build_model_messages(req)

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
            # 你可以更精细判断：ReadTimeout / ConnectError / 502 等
            is_timeout = "ReadTimeout" in type(ex).__name__ or "timeout" in str(ex).lower()
            if attempt < max_retries and is_timeout:
                time.sleep(backoff * (attempt + 1))
                continue
            break

    #  失败：返回结构化错误，不要把“模型调用失败”混成正常 reply
    msg = f"{type(last_ex).__name__}: {last_ex}" if last_ex else "Unknown error"
    retryable = True  # timeout 基本可重试，你也可以按异常类型判断
    return IdeChatResponse(
        ok=False,
        reply="",                 # 让插件显示 system 错误，而不是 assistant 回复
        done=True,
        error_code="MODEL_TIMEOUT",
        error_message=msg,
        retryable=retryable,
    )

def _first_user_text(req: IdeChatRequest) -> str:
    # 找最后一条 user
    for m in reversed(req.messages):
        if (m.role or "").lower() == "user":
            return m.content or ""
    return ""

def _extract_target_names(user_text: str) -> list[str]:
    # 非严格：先覆盖你当前场景（Main.java / Test.java）
    # 你也可以做得更通用，但先把 bug 卡住最重要
    names = re.findall(r"[A-Za-z0-9_\-]+\.(?:java|kt|py|js|ts|go|rs|c|cpp|h|hpp)", user_text)
    # 去重保持顺序
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

    # 1) 如果模型同时给了 tool_requests，本轮按你规则应该不允许边问边改：清空 ops
    if obj.get("tool_requests"):
        obj["ops"] = []
        obj["patch_ops"] = []
        return obj

    # 2) 任何 delete_path 都必须指向真实存在的路径，否则强制先 search
    missing = []
    for o in deletes:
        path = (o.get("path") or "").strip()
        if not path or not _exists(req.project_root, path):
            missing.append(path)

    if not missing:
        return obj

    # 从用户输入提取目标文件名，比如 Main.java
    user_text = _first_user_text(req)
    targets = _extract_target_names(user_text)
    # 没提取到，就退化成用 missing 的 basename
    if not targets:
        targets = [Path(p).name for p in missing if p]

    # 组装一个 tool_request：让下一轮 tool_results 给出真实路径
    query = targets[0] if targets else "Main.java"

    obj = {
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
                    "max_results": 200
                },
            }
        ],
        "patch_ops": [],
        "done": False,
        # 如果你 response schema 扩展了 ok/error_code，也可以一并带上
        "ok": True,
        "error_code": None,
        "error_message": None,
        "retryable": False,
    }
    return obj
