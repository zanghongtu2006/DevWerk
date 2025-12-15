# app/core/schema.py
from __future__ import annotations

from typing import Any, Dict

MODEL_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "code_tree", "ops"],
    "properties": {
        "reply": {"type": "string"},
        "code_tree": {"type": "string"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["op", "path", "language", "content"],
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["create_dir", "create_file", "update_file", "delete_path"],
                    },
                    "path": {"type": "string"},
                    "language": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                },
            },
        },
    },
}
