"""
Source-map driven code context helpers.

The backend must not hard-code framework or business layout decisions. This
module turns a capability provider's source_map into a compact, factual summary that
agents can use as evidence when planning and writing code.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

_log = logging.getLogger("devwerk.coder_harness")

MAX_REPRESENTATIVE_FILES = 40
MAX_SYMBOLS = 160
MAX_IMPORTS = 120
MAX_DIRECTORIES = 100


class CoderHarness:
    """
    Build a local, zero-token code context from source_map.

    The public build_skill name is retained for existing call sites, but the
    content is now generic and evidence-only. It does not classify frameworks
    or invent writing rules from backend heuristics.
    """

    def build_skill(self, workspace: dict[str, Any] | None) -> str | None:
        summary = build_code_context_summary(workspace)
        if not summary.get("available"):
            _log.debug("CoderHarness.build_skill: summary unavailable reason=%s", summary.get("reason"))
            return None
        guidance = {
            "role": "coder",
            "source": "client source_map",
            "rules": summary.get("path_policy") or [],
            "representative_paths": [
                item.get("path")
                for item in (summary.get("representative_files") or [])[:20]
                if isinstance(item, dict) and item.get("path")
            ],
        }
        rendered = "code_context_skill:\n" + json.dumps(guidance, ensure_ascii=False, separators=(",", ":"))
        _log.debug("CoderHarness.build_skill: rendered chars=%s", len(rendered))
        return rendered


def build_coder_skill(workspace: dict[str, Any] | None) -> str | None:
    return CoderHarness().build_skill(workspace)


def build_code_context_summary(workspace: dict[str, Any] | None) -> dict[str, Any]:
    diagnostics = _extract_syntax_diagnostics(workspace)
    source_map = _extract_source_map(workspace)
    if not source_map:
        if diagnostics:
            return {
                "available": True,
                "source_map": None,
                "syntax_diagnostics": diagnostics,
                "path_policy": [
                    "All paths are project-root relative and use forward slashes.",
                    "Client-provided syntax diagnostics are direct file evidence for syntax-fix tasks.",
                    "Use tool results for exact content before modifying existing files.",
                    "Do not invent directories or package names from diagnostics alone.",
                ],
                "warnings": [
                    "No client source_map was provided; diagnostics are available but agents may need workspace.list/workspace.search/workspace.read.",
                ],
            }
        return {
            "available": False,
            "reason": "source_map_missing",
            "warnings": ["No client source_map was provided; agents must request exact context before choosing paths."],
        }

    _log_source_map(source_map)
    files = _source_files(source_map)
    normalized = [_normalize_file(item) for item in files]
    normalized = [item for item in normalized if item.get("path")]
    language_counts = Counter(str(item.get("language") or "unknown") for item in normalized)
    kind_counts = Counter(str(item.get("kind") or "unknown") for item in normalized)
    top_dirs = Counter(_top_level_dir(str(item["path"])) for item in normalized)
    directory_index = Counter(_parent_dir(str(item["path"])) for item in normalized)
    import_counts = Counter(
        str(imp)
        for item in normalized
        for imp in (item.get("imports") or [])
        if isinstance(imp, str) and imp.strip()
    )
    symbols = _symbol_index(normalized)
    representative_files = _representative_files(normalized)

    summary = {
        "available": True,
        "source_map": {
            "root": source_map.get("root"),
            "generated_at": source_map.get("generated_at"),
            "total_files": source_map.get("total_files"),
            "indexed_files": source_map.get("indexed_files"),
            "skipped_files": source_map.get("skipped_files"),
            "files_payload_count": len(normalized),
        },
        "languages": _counter_items(language_counts),
        "file_kinds": _counter_items(kind_counts),
        "top_level_dirs": _counter_items(top_dirs),
        "directory_index": _counter_items(directory_index, limit=MAX_DIRECTORIES),
        "representative_files": representative_files,
        "symbol_index": symbols,
        "common_imports": _counter_items(import_counts, limit=MAX_IMPORTS),
        "syntax_diagnostics": diagnostics,
        "path_policy": [
            "All paths are project-root relative and use forward slashes.",
            "Use source_map and tool results as evidence; do not invent directories or package names.",
            "If syntax_diagnostics are present, treat their paths/messages as direct file evidence stronger than broad workspace.search hits.",
            "If source_map lacks exact content, request workspace.read before modifying existing files.",
            "If source_map is missing or insufficient, request workspace.list/workspace.search/workspace.read instead of guessing.",
        ],
        "warnings": _summary_warnings(source_map, normalized),
    }
    _log.debug(
        "CoderHarness.code_context_summary: files=%s languages=%s dirs=%s symbols=%s representative=%s diagnostics=%s",
        len(normalized),
        summary["languages"],
        len(summary["directory_index"]),
        len(symbols),
        len(representative_files),
        len(diagnostics),
    )
    return summary


def _extract_source_map(workspace: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(workspace, dict):
        _log.debug("CoderHarness.extract_source_map: workspace is not dict")
        return None
    source_map = workspace.get("source_map")
    if not isinstance(source_map, dict):
        _log.debug("CoderHarness.extract_source_map: source_map missing or invalid type=%s", type(source_map).__name__)
        return None
    _log.debug("CoderHarness.extract_source_map: source_map found")
    return source_map


def _source_files(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    files = source_map.get("files") or []
    normalized = [f for f in files if isinstance(f, dict)]
    _log.debug(
        "CoderHarness.source_files: raw_count=%s normalized_count=%s invalid_count=%s",
        len(files) if isinstance(files, list) else "not-list",
        len(normalized),
        (len(files) - len(normalized)) if isinstance(files, list) else "unknown",
    )
    return normalized


def _extract_syntax_diagnostics(workspace: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(workspace, dict):
        return []
    raw = workspace.get("syntax_diagnostics") or []
    if not isinstance(raw, list):
        return []
    diagnostics: list[dict[str, Any]] = []
    for item in raw[:200]:
        if not isinstance(item, dict):
            continue
        path = _normalize_path(item.get("path"))
        message = str(item.get("message") or "").strip()
        if not path or _has_hidden_dir_segment(path) or not message:
            continue
        diagnostics.append(
            {
                "path": path,
                "line": _optional_int(item.get("line")),
                "column": _optional_int(item.get("column")),
                "severity": str(item.get("severity") or "error"),
                "message": message[:500],
                "source": str(item.get("source") or "ide"),
            }
        )
    if diagnostics:
        _log.debug("CoderHarness.syntax_diagnostics: count=%s sample=%s", len(diagnostics), diagnostics[:10])
    return diagnostics


def _normalize_file(item: dict[str, Any]) -> dict[str, Any]:
    path = _normalize_path(item.get("path"))
    symbols = [symbol for symbol in (item.get("symbols") or []) if isinstance(symbol, dict)]
    imports = [str(value).strip() for value in (item.get("imports") or []) if str(value).strip()]
    return {
        "path": path,
        "kind": str(item.get("kind") or "unknown").lower(),
        "language": str(item.get("language") or "unknown").lower(),
        "package": item.get("package"),
        "size": item.get("size"),
        "imports": imports,
        "symbols": symbols,
    }


def _normalize_path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    parts = [part for part in path.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _has_hidden_dir_segment(path: str) -> bool:
    parts = [part for part in str(path or "").replace("\\", "/").split("/") if part]
    return len(parts) > 1 and any(part.startswith(".") for part in parts[:-1])


def _optional_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _top_level_dir(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 1:
        return "."
    return parts[0]


def _parent_dir(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 1:
        return "."
    return "/".join(parts[:-1])


def _representative_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for item in files:
        path = str(item.get("path") or "")
        if not path:
            continue
        depth = path.count("/")
        symbol_count = len(item.get("symbols") or [])
        import_count = len(item.get("imports") or [])
        size = int(item.get("size") or 0)
        score = symbol_count * 5 + import_count * 2 + max(0, 8 - depth)
        if size:
            score += min(size // 2000, 5)
        scored.append((score, path, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    out: list[dict[str, Any]] = []
    for _, _, item in scored[:MAX_REPRESENTATIVE_FILES]:
        out.append({
            "path": item["path"],
            "language": item.get("language"),
            "kind": item.get("kind"),
            "package": item.get("package"),
            "symbol_count": len(item.get("symbols") or []),
            "import_count": len(item.get("imports") or []),
        })
    return out


def _symbol_index(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in files:
        path = str(item.get("path") or "")
        for symbol in item.get("symbols") or []:
            name = str(symbol.get("name") or "").strip()
            if not name:
                continue
            out.append({
                "path": path,
                "name": name,
                "kind": symbol.get("kind"),
                "line": symbol.get("line"),
            })
            if len(out) >= MAX_SYMBOLS:
                return out
    return out


def _counter_items(counter: Counter[str], limit: int = 50) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in counter.most_common(limit)
        if name
    ]


def _summary_warnings(source_map: dict[str, Any], files: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    total = int(source_map.get("total_files") or 0)
    indexed = int(source_map.get("indexed_files") or 0)
    if total and indexed < total:
        warnings.append(f"source_map indexed {indexed}/{total} files; missing files may require workspace.list/workspace.search.")
    if not files:
        warnings.append("source_map contains no file payload; agents must request workspace tools before planning paths.")
    return warnings


def _log_source_map(source_map: dict[str, Any]) -> None:
    files = source_map.get("files") or []
    file_count = len(files) if isinstance(files, list) else 0
    _log.debug(
        "CoderHarness.source_map: root=%s generated_at=%s total_files=%s indexed_files=%s skipped_files=%s files_payload_count=%s",
        source_map.get("root"),
        source_map.get("generated_at"),
        source_map.get("total_files"),
        source_map.get("indexed_files"),
        source_map.get("skipped_files"),
        file_count,
    )
    if isinstance(files, list):
        samples = []
        for item in files[:20]:
            if not isinstance(item, dict):
                continue
            samples.append({
                "path": item.get("path"),
                "language": item.get("language"),
                "kind": item.get("kind"),
                "symbols": len(item.get("symbols") or []),
                "imports": len(item.get("imports") or []),
            })
        _log.debug("CoderHarness.source_map: sample_files=%s", samples)
