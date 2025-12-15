# app/routes/ide.py
from __future__ import annotations

from fastapi import APIRouter, Request

from app.models.ide import IdeChatRequest, IdeChatResponse
from app.services.ollama_client import OllamaClient
from app.services.prompt_builder import build_model_messages
from app.services.coerce import coerce_to_fileops

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
    try:
        obj = client.chat_structured(messages)
    except Exception as ex:
        return IdeChatResponse(
            reply=f"模型调用失败：{type(ex).__name__}",
            code_tree="",
            ops=[],
        )
    return IdeChatResponse(
        reply=obj["reply"],
        code_tree=obj.get("code_tree") or "",
        ops=coerce_to_fileops(obj.get("ops") or []),
    )
