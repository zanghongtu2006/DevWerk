# app/services/prompt_builder.py
from __future__ import annotations

import json
from typing import Dict, List

from app.core.prompt import SYSTEM_PROMPT
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.models.ide import IdeChatRequest

def build_model_messages(req: IdeChatRequest) -> List[Dict[str, str]]:
    schema_json = json.dumps(MODEL_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    sys_prompt = SYSTEM_PROMPT.format(schema_json=schema_json)

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]

    for m in req.messages:
        role = (m.role or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": m.content or ""})

    if req.project_root:
        messages.append(
            {
                "role": "user",
                "content": (
                    "workspace_hint:\n"
                    f"project_root={req.project_root}\n"
                    "\n"
                    "注意：所有 ops.path 必须相对 project_root（或工作区根），使用 /，不得包含 .. 或绝对路径。\n"
                    "只输出 JSON。\n"
                ),
            }
        )

    return messages
