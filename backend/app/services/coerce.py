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
    用于“对齐匹配”的 canonical key（尽量保守）：
    - 统一斜杠
    - 去掉扩展名前的空格：Main. java -> Main.java
    - 去掉斜杠两侧的多余空格：test/ Main.java -> test/Main.java
      （仅用于匹配，不会直接改写真实路径，真实写回用 candidates 里的路径）
    """
    s = _normalize_rel_path(p)

    #  修正 "/ Main.java" 这种
    s = _SLASH_SPACE_RE.sub("/", s)   # "/   " -> "/"
    s = _SPACE_SLASH_RE.sub("/", s)   # "   /" -> "/"

    #  只修扩展名空格 ". java"
    s = _EXT_SPACE_RE.sub(r".\1", s)
    return s


def _extract_paths_from_tool_results(tool_results: List[ToolResult]) -> Set[str]:
    """
    WorkspaceTools.search 的返回你现在是纯路径逐行输出：
      src/Main.java
      src/main/java/...
    这里尽量保守解析：过滤掉 [search] no hits 等。
    """
    out: Set[str] = set()
    for tr in tool_results or []:
        if not tr.ok or not tr.content:
            continue
        for line in tr.content.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("["):  # 比如 [search] no hits
                continue
            # 保守：只收“看起来像相对路径”的行
            # 允许包含空格（理论上路径可以有空格），但我们不在这里做额外改写
            s = _normalize_rel_path(s)
            if s:
                out.add(s)
    return out


def _align_delete_paths(ops: List[FileOp], tool_results: Optional[List[ToolResult]]) -> List[FileOp]:
    """
    对齐规则（保守）：
    - 只对 delete_path 生效
    - 如果 delete_path.path 不在 tool_results 的候选路径中：
        * 尝试把 ". java" 修成 ".java" 再匹配
        * 若匹配成功，用真实路径替换
      （避免出现你 log 里那种 Main. java 导致删不到）
    """
    if not tool_results:
        return ops

    candidates = _extract_paths_from_tool_results(tool_results)
    if not candidates:
        return ops

    # 构建 canonical -> real 的映射（同一个 canonical 多个真实值时，保持第一个）
    canon_to_real: Dict[str, str] = {}
    for p in candidates:
        k = _canonicalize_for_match(p)
        canon_to_real.setdefault(k, p)

    aligned: List[FileOp] = []
    for op in ops:
        if op.op != "delete_path":
            aligned.append(op)
            continue

        raw = _normalize_rel_path(op.path or "")
        if not raw:
            aligned.append(op)
            continue

        # 1) 直接命中
        if raw in candidates:
            aligned.append(op)
            continue

        # 2) canonical 命中（修 ". java"）
        k = _canonicalize_for_match(raw)
        real = canon_to_real.get(k)
        if real:
            aligned.append(
                FileOp(op=op.op, path=real, language=op.language, content=op.content)
            )
        else:
            # 找不到就原样返回（不做危险猜测）
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

    #  关键：对齐 delete_path
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
