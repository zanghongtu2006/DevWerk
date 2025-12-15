# app/services/coerce.py
from __future__ import annotations

from typing import Any, Dict, List

from app.models.ide import FileOp

def coerce_to_fileops(obj_ops: List[Dict[str, Any]]) -> List[FileOp]:
    return [
        FileOp(
            op=o.get("op"),
            path=o.get("path"),
            language=o.get("language", None),
            content=o.get("content", None),
        )
        for o in obj_ops
    ]
