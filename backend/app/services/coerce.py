# app/services/coerce.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from app.models.ide import FileOp, PatchOp, ToolRequest, ToolResult


_EXT_SPACE_RE = re.compile(r"\.\s+([A-Za-z0-9]{1,8})$")
_SLASH_SPACE_RE = re.compile(r"/\s+")
_SPACE_SLASH_RE = re.compile(r"\s+/")


def _normalize_rel_path(p: str) -> str:
    s = (p or "").strip().replace("\\", "/")
    while s.startswith("/"):
        s = s[1:]
    return s


def _canonicalize_for_match(p: str) -> str:
    """
    Conservative canonical key for matching paths only.

    It normalizes slashes, removes whitespace around slashes, and fixes a space
    before an extension. The real path written back still comes from the
    verified candidate list when possible.
    """
    s = _normalize_rel_path(p)
    s = _SLASH_SPACE_RE.sub("/", s)
    s = _SPACE_SLASH_RE.sub("/", s)
    return _EXT_SPACE_RE.sub(r".\1", s)


def _extract_paths_from_tool_results(tool_results: List[ToolResult]) -> Set[str]:
    """
    Workspace search returns plain project-relative paths, one per line.
    Parse conservatively and ignore marker lines such as [search] no hits.
    """
    out: Set[str] = set()
    for tr in tool_results or []:
        if not tr.ok or not tr.content:
            continue
        for line in tr.content.splitlines():
            s = line.strip()
            if not s or s.startswith("["):
                continue
            s = _normalize_rel_path(s)
            if s:
                out.add(s)
    return out


def _align_delete_paths(ops: List[FileOp], tool_results: Optional[List[ToolResult]]) -> List[FileOp]:
    """
    Align delete_path operations with exact tool-result paths when available.
    This avoids deleting guessed paths while still tolerating minor model
    formatting issues such as spaces around slashes or before extensions.
    """
    if not tool_results:
        return ops

    candidates = _extract_paths_from_tool_results(tool_results)
    if not candidates:
        return ops

    canon_to_real: Dict[str, str] = {}
    for p in candidates:
        canon_to_real.setdefault(_canonicalize_for_match(p), p)

    aligned: List[FileOp] = []
    for op in ops:
        if op.op != "delete_path":
            aligned.append(op)
            continue

        raw = _normalize_rel_path(op.path or "")
        if not raw or raw in candidates:
            aligned.append(op)
            continue

        real = canon_to_real.get(_canonicalize_for_match(raw))
        if real:
            aligned.append(FileOp(op=op.op, path=real, language=op.language, content=op.content))
        else:
            aligned.append(op)

    return aligned


def coerce_to_fileops(
    obj_ops: List[Dict[str, Any]],
    *,
    tool_results: Optional[List[ToolResult]] = None,
) -> List[FileOp]:
    ops = [
        FileOp(
            op=o.get("op"),
            path=o.get("path"),
            language=o.get("language", None),
            content=o.get("content", None),
        )
        for o in (obj_ops or [])
        if isinstance(o, dict)
    ]
    return _align_delete_paths(ops, tool_results)


def coerce_to_toolrequests(obj_reqs: List[Dict[str, Any]]) -> List[ToolRequest]:
    return [
        ToolRequest(
            id=r.get("id", ""),
            tool=r.get("tool", ""),
            args=r.get("args") if isinstance(r.get("args"), dict) else {},
        )
        for r in (obj_reqs or [])
        if isinstance(r, dict)
    ]


def coerce_to_patchops(obj_ops: List[Dict[str, Any]]) -> List[PatchOp]:
    return [
        PatchOp(
            op=o.get("op", ""),
            content=o.get("content", ""),
        )
        for o in (obj_ops or [])
        if isinstance(o, dict)
    ]
