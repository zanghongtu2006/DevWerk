# app/services/prompt_builder.py
from __future__ import annotations

import json
from typing import Dict, List

from app.core.prompt import SYSTEM_PROMPT
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.models.ide import IdeChatRequest


def build_model_messages(req: IdeChatRequest) -> List[Dict[str, str]]:
    schema_json = json.dumps(MODEL_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    sys_prompt = SYSTEM_PROMPT.replace("__SCHEMA_JSON__", schema_json)

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]

    # 用户/助手历史
    for m in req.messages:
        role = (m.role or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": m.content or ""})

    # request_meta：让模型知道现在是什么模式
    meta = {
        "mode": req.mode,
        "project_root": req.project_root,
        "path_rules": "所有路径必须相对 project_root，使用 /，不得包含 .. 或绝对路径",
    }
    messages.append({"role": "user", "content": "request_meta:\n" + json.dumps(meta, ensure_ascii=False)})

    # workspace 摘要（agent 模式时尤其重要）
    if req.workspace is not None:
        messages.append(
            {
                "role": "user",
                "content": "workspace_summary:\n" + req.workspace.model_dump_json(exclude_none=True),
            }
        )

    # tool_results（插件执行 tool_requests 后回传的结果）
    if req.tool_results:
        messages.append(
            {
                "role": "user",
                "content": "tool_results:\n"
                + json.dumps([r.model_dump(exclude_none=True) for r in req.tool_results], ensure_ascii=False),
            }
        )

    # 额外提醒：只输出 JSON
    if req.project_root:
        messages.append(
            {
                "role": "user",
                "content": (
                    "注意：只输出 JSON。所有路径必须相对 project_root，使用 /，不得包含 .. 或绝对路径。\n"
                    f"project_root={req.project_root}"
                ),
            }
        )

    return messages
