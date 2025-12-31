# app/core/schema.py
from __future__ import annotations

from typing import Any, Dict

MODEL_RESPONSE_SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "required": ["reply", "code_tree", "ops", "tool_requests", "patch_ops", "done"],
  "properties": {
    "reply": {"type": "string"},

    "code_tree": {"type": ["string", "null"]},

    "ops": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", "path", "language", "content"],
        "properties": {
          "op": {"type": "string", "enum": ["create_dir","create_file","update_file","delete_path"]},
          "path": {"type": "string"},
          "language": {"type": ["string", "null"]},
          "content": {"type": ["string", "null"]},
        },
      },
    },

    "tool_requests": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "tool", "args"],
        "properties": {
          "id": {"type": "string"},
          "tool": {"type": "string", "enum": ["list_dir","read_file","search"]},
          "args": {"type": "object"},
        },
      },
    },

    "patch_ops": {
      "type": ["array", "null"],
      "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["op", "content"],
        "properties": {
          "op": {"type": "string", "enum": ["apply_patch"]},
          "content": {"type": "string"},
        },
      },
    },

    "done": {"type": ["boolean", "null"]},
  },
}
