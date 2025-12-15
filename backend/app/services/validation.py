# app/services/validation.py
from __future__ import annotations

from typing import Any, Dict

def validate_model_response(obj: Dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise ValueError("Response must be a JSON object")
    for k in ("reply", "code_tree", "ops"):
        if k not in obj:
            raise ValueError(f"Missing field: {k}")
    if not isinstance(obj["reply"], str):
        raise ValueError("reply must be string")
    if not isinstance(obj["code_tree"], str):
        raise ValueError("code_tree must be string")
    if not isinstance(obj["ops"], list):
        raise ValueError("ops must be array")

    for i, op in enumerate(obj["ops"]):
        if not isinstance(op, dict):
            raise ValueError(f"ops[{i}] must be object")

        for k in ("op", "path", "language", "content"):
            if k not in op:
                raise ValueError(f"ops[{i}] missing {k}")

        if op["op"] not in ("create_dir", "create_file", "update_file", "delete_path"):
            raise ValueError(f"ops[{i}].op invalid: {op['op']}")

        p = op["path"]
        if not isinstance(p, str) or not p:
            raise ValueError(f"ops[{i}].path invalid")

        if p.startswith("/") or p.startswith("\\") or "://" in p:
            raise ValueError(f"ops[{i}].path must be relative: {p}")

        if ".." in p.split("/"):
            raise ValueError(f"ops[{i}].path must not contain '..': {p}")
