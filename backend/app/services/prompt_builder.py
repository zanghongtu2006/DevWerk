# app/services/prompt_builder.py
from __future__ import annotations

import json
from typing import Dict, List

from app.core.prompt_factory import build_system_prompt
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.models.ide import IdeChatRequest


def build_model_messages(req: IdeChatRequest, provider: str) -> List[Dict[str, str]]:
    schema_json = json.dumps(MODEL_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    sys_prompt = build_system_prompt(provider=provider, schema_json=schema_json)

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]

    # 用户/助手历史
    for m in req.messages:
        role = (m.role or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": m.content or ""})

    # request_meta
    meta = {
        "mode": req.mode,
        "project_root": req.project_root,
        "path_rules": "所有路径必须相对 project_root，使用 /，不得包含 .. 或绝对路径",
    }
    messages.append({"role": "user", "content": "request_meta:\n" + json.dumps(meta, ensure_ascii=False)})

    # workspace 摘要
    if req.workspace is not None:
        messages.append(
            {
                "role": "user",
                "content": "workspace_summary:\n" + req.workspace.model_dump_json(exclude_none=True),
            }
        )

    # tool_results
    if req.tool_results:
        messages.append(
            {
                "role": "user",
                "content": "tool_results:\n"
                + json.dumps([r.model_dump(exclude_none=True) for r in req.tool_results], ensure_ascii=False),
            }
        )

    # 强提醒
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
