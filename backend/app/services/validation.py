# app/services/validation.py
from __future__ import annotations

from typing import Any, Dict, List

from app.services.tool_protocol import BACKEND_RESEARCH_TOOLS, normalize_tool_requests

ALLOWED_FILE_OPS = {"create_dir", "create_file", "update_file", "delete_path"}
ALLOWED_PATCH_OPS = {"apply_patch"}


class ModelResponseValidationError(ValueError):
    def __init__(self, message: str, obj: dict[str, Any] | None = None):
        super().__init__(message)
        self.obj = obj or {}


def _validate_rel_path(p: str, where: str) -> None:
    if not isinstance(p, str) or not p:
        raise ValueError(f"{where}.path invalid")
    if p.startswith("/") or p.startswith("\\") or "://" in p:
        raise ValueError(f"{where}.path must be relative: {p}")
    if ".." in p.split("/"):
        raise ValueError(f"{where}.path must not contain '..': {p}")


def validate_model_response(obj: Dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise ValueError("Response must be a JSON object")

    if "reply" not in obj or not isinstance(obj["reply"], str):
        raise ValueError("Missing/invalid reply")

    # 至少要有一种输出形态
    has_scaffold = obj.get("code_tree") is not None or obj.get("ops") is not None
    has_tools = obj.get("tool_requests") is not None
    has_patch = obj.get("patch_ops") is not None
    done = bool(obj.get("done") or False)

    if not done and not has_scaffold and not has_tools and not has_patch:
        raise ValueError("Must output scaffold/tools/patch or done=true")

    # scaffold：ops
    ops = obj.get("ops") or []
    if ops is not None:
        if not isinstance(ops, list):
            raise ValueError("ops must be array or null")
        for i, op in enumerate(ops):
            if not isinstance(op, dict):
                raise ValueError(f"ops[{i}] must be object")
            for k in ("op", "path", "language", "content"):
                if k not in op:
                    raise ValueError(f"ops[{i}] missing {k}")
            if op["op"] not in ALLOWED_FILE_OPS:
                raise ValueError(f"ops[{i}].op invalid: {op['op']}")
            _validate_rel_path(op["path"], f"ops[{i}]")

    # agent：tool_requests
    tool_requests = obj.get("tool_requests") or []
    if tool_requests is not None:
        if not isinstance(tool_requests, list):
            raise ValueError("tool_requests must be array or null")
        tool_requests = normalize_tool_requests(tool_requests)
        obj["tool_requests"] = tool_requests
        for i, tr in enumerate(tool_requests):
            if not isinstance(tr, dict):
                raise ValueError(f"tool_requests[{i}] must be object")
            if not isinstance(tr.get("tool"), str) or not tr.get("tool"):
                raise ValueError(f"tool_requests[{i}].tool invalid")
            if not isinstance(tr.get("id"), str) or not tr.get("id"):
                raise ValueError(f"tool_requests[{i}].id invalid")
            args = tr.get("args")
            if not isinstance(args, dict):
                raise ValueError(f"tool_requests[{i}].args must be object")
            # 粗略约束 read_file 必须有限制范围
            if tr["tool"] == "read_file":
                if "path" not in args:
                    raise ValueError(f"tool_requests[{i}].args.path missing")
                # _validate_rel_path(args["path"], f"tool_requests[{i}].args")
                # if "start_line" not in args or "end_line" not in args:
                #     raise ValueError(f"tool_requests[{i}] read_file must set start_line/end_line")
                if "start_line" not in args:
                    args["start_line"] = None
                if "end_line" not in args:
                    args["end_line"] = None

    # agent：patch_ops
    patch_ops = obj.get("patch_ops") or []
    if patch_ops is not None:
        if not isinstance(patch_ops, list):
            raise ValueError("patch_ops must be array or null")
        for i, po in enumerate(patch_ops):
            if not isinstance(po, dict):
                raise ValueError(f"patch_ops[{i}] must be object")
            if po.get("op") not in ALLOWED_PATCH_OPS:
                raise ValueError(f"patch_ops[{i}].op invalid")
            content = po.get("content")
            if not isinstance(content, str) or len(content) < 20:
                raise ValueError(f"patch_ops[{i}].content invalid")
            # 粗校验 unified diff 特征
            if ("--- " not in content) or ("+++ " not in content) or ("@@ " not in content):
                raise ValueError(f"patch_ops[{i}] must be unified diff")

    ops = obj.get("ops") or []
    tool_requests = obj.get("tool_requests") or []
    patch_ops = obj.get("patch_ops") or []

    backend_tools = [tr for tr in tool_requests if isinstance(tr, dict) and tr.get("tool") in BACKEND_RESEARCH_TOOLS]
    if backend_tools and (ops or patch_ops):
        raise ValueError("Backend research tools cannot be returned with ops/patch_ops in the same response.")
