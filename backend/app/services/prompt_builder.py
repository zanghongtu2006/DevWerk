# app/services/prompt_builder.py
from __future__ import annotations

import json
import logging
from typing import Dict, List

from app.core.prompt_factory import build_system_prompt
from app.core.schema import MODEL_RESPONSE_SCHEMA
from app.models.ide import IdeChatRequest
from app.services.coder_harness import build_code_context_summary, build_coder_skill

_log = logging.getLogger("devwerk.prompt_builder")


def build_model_messages(req: IdeChatRequest, provider: str) -> List[Dict[str, str]]:
    schema_json = json.dumps(MODEL_RESPONSE_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    sys_prompt = build_system_prompt(provider=provider, schema_json=schema_json)

    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    _log.debug(
        "build_model_messages: start provider=%s mode=%s project_root=%s input_messages=%s has_workspace=%s tool_results=%s",
        provider,
        req.mode,
        req.project_root,
        len(req.messages),
        req.workspace is not None,
        len(req.tool_results or []),
    )

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
        workspace_obj = req.workspace.model_dump(exclude_none=True)
        _log.debug("build_model_messages: workspace_summary=%s", _workspace_debug_summary(workspace_obj))
        code_context = build_code_context_summary(workspace_obj)
        if code_context.get("available"):
            messages.append(
                {
                    "role": "user",
                    "content": "code_context_summary:\n" + json.dumps(code_context, ensure_ascii=False, separators=(",", ":")),
                }
            )
            _log.debug("build_model_messages: injected code_context_summary")
        else:
            _log.debug("build_model_messages: code_context_summary unavailable reason=%s", code_context.get("reason"))
        coder_skill = build_coder_skill(workspace_obj)
        if coder_skill:
            messages.append({"role": "user", "content": coder_skill})
            _log.debug("build_model_messages: injected code_context_skill chars=%s", len(coder_skill))
        else:
            _log.debug("build_model_messages: code_context_skill not generated")

        messages.append(
            {
                "role": "user",
                "content": "workspace_summary:\n" + json.dumps(workspace_obj, ensure_ascii=False, separators=(",", ":")),
            }
        )
        _log.debug("build_model_messages: injected workspace_summary")

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

    _log.debug("build_model_messages: done output_messages=%s", len(messages))
    return messages


def _workspace_debug_summary(workspace: dict) -> dict[str, object]:
    source_map = workspace.get("source_map")
    files = source_map.get("files") if isinstance(source_map, dict) else None
    diagnostics = workspace.get("syntax_diagnostics")
    sample_paths = []
    if isinstance(files, list):
        for item in files[:12]:
            if isinstance(item, dict) and item.get("path"):
                sample_paths.append(item.get("path"))
    return {
        "keys": sorted(workspace.keys()),
        "tree_preview_chars": len(workspace.get("tree_preview") or ""),
        "source_map_present": isinstance(source_map, dict),
        "source_map_root": source_map.get("root") if isinstance(source_map, dict) else None,
        "source_map_total_files": source_map.get("total_files") if isinstance(source_map, dict) else None,
        "source_map_indexed_files": source_map.get("indexed_files") if isinstance(source_map, dict) else None,
        "source_map_files_payload": len(files) if isinstance(files, list) else 0,
        "syntax_diagnostics_count": len(diagnostics) if isinstance(diagnostics, list) else 0,
        "sample_paths": sample_paths,
    }
