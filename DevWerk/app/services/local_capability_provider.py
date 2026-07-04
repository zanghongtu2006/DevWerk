from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.models.protocol import FileOp, PatchOp, ToolRequest, ToolResult
from app.services.tool_protocol import ALL_CAPABILITIES

_log = logging.getLogger("devwerk.local_capability")

BACKEND_PROVIDER = "devwerk-backend"
BACKEND_CAPABILITIES = {
    "workspace.list",
    "workspace.read",
    "workspace.search",
    "workspace.write",
    "process.run",
    "project.compile",
    "source.diagnostics",
}


class LocalCapabilityError(ValueError):
    pass


def backend_capability_declaration() -> dict[str, Any]:
    return {
        "provider": BACKEND_PROVIDER,
        "capabilities": [
            {
                "capability": name,
                "provider": BACKEND_PROVIDER,
                "implementation": name,
            }
            for name in sorted(BACKEND_CAPABILITIES.intersection(ALL_CAPABILITIES))
        ],
    }


def merge_backend_capabilities(declaration: object) -> dict[str, Any]:
    base: dict[str, Any] = dict(declaration) if isinstance(declaration, dict) else {}
    existing = base.get("capabilities") if isinstance(base.get("capabilities"), list) else []
    seen = {
        str(item.get("capability") or item.get("name") or item).strip()
        for item in existing
        if isinstance(item, (dict, str))
    }
    merged = list(existing)
    for item in backend_capability_declaration()["capabilities"]:
        if item["capability"] not in seen:
            merged.append(item)
    base["capabilities"] = merged
    base.setdefault("provider", BACKEND_PROVIDER)
    return base


def local_backend_enabled(body: dict[str, Any]) -> bool:
    value = body.get("backend_local")
    if value is None:
        value = body.get("local_backend")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "backend"}
    return bool(value)


def execute_tool_requests(
    requests: list[ToolRequest],
    *,
    project_root: object,
) -> list[ToolResult]:
    root = _resolve_project_root(project_root, create=True)
    results: list[ToolResult] = []
    for request in requests:
        try:
            content = _execute_one(request, root)
            results.append(ToolResult(id=request.id, ok=True, content=content))
        except Exception as exc:  # noqa: BLE001
            _log.exception(
                "backend local tool failed id=%s tool=%s root=%s",
                request.id,
                request.tool,
                root,
            )
            results.append(ToolResult(id=request.id, ok=False, error=f"{type(exc).__name__}: {exc}"))
    return results


def apply_file_changes(
    *,
    ops: list[FileOp],
    patch_ops: list[PatchOp],
    project_root: object,
) -> dict[str, Any]:
    root = _resolve_project_root(project_root, create=True)
    changed_paths: list[str] = []
    errors: list[str] = []

    for op in ops:
        try:
            changed = _apply_file_op(root, op)
            if changed:
                changed_paths.append(changed)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{op.path}: {type(exc).__name__}: {exc}")

    if patch_ops:
        errors.append("patch_ops are not supported by the backend local provider yet; emit concrete file ops instead.")

    ok = not errors
    return {
        "ok": ok,
        "provider": BACKEND_PROVIDER,
        "snapshot_id": "backend-local-apply",
        "project_root": str(root),
        "changed_paths": changed_paths,
        "errors": errors,
        "error_message": "; ".join(errors) if errors else None,
        "verification": {
            "required": ["backend_apply"],
            "results": {"backend_apply": "passed" if ok else "failed"},
            "tool_results": [
                {
                    "id": "backend_apply",
                    "tool": "backend.local.apply",
                    "ok": ok,
                    "content": f"Changed paths: {', '.join(changed_paths)}" if ok else None,
                    "error": "; ".join(errors) if errors else None,
                }
            ],
        },
    }


def _execute_one(request: ToolRequest, root: Path) -> str:
    tool = request.tool
    args = request.args or {}
    if tool == "workspace.list":
        return _json(_workspace_list(root, args))
    if tool == "workspace.read":
        return _workspace_read(root, args)
    if tool == "workspace.search":
        return _json(_workspace_search(root, args))
    if tool == "workspace.write":
        return _json(_workspace_write(root, args))
    if tool == "process.run":
        return _json(_process_run(root, args))
    if tool == "project.compile":
        return _json(_project_compile(root, args))
    if tool == "source.diagnostics":
        return _json(_source_diagnostics(root, args))
    raise LocalCapabilityError(f"unsupported backend local tool: {tool}")


def _workspace_list(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    base = _safe_path(root, args.get("path") or "", create_parent=False)
    max_depth = _positive_int(args.get("max_depth"), 3, maximum=12)
    if not base.exists():
        return {"path": _rel(root, base), "exists": False, "items": []}
    items: list[dict[str, Any]] = []
    if base.is_file():
        return {"path": _rel(root, base), "exists": True, "items": [_file_item(root, base)]}
    for path in sorted(base.rglob("*")):
        if _is_ignored(path):
            continue
        rel = Path(_rel(base, path))
        if len(rel.parts) > max_depth:
            continue
        items.append(_file_item(root, path))
        if len(items) >= _positive_int(args.get("max_items"), 500, maximum=5000):
            break
    return {"path": _rel(root, base), "exists": True, "items": items}


def _workspace_read(root: Path, args: dict[str, Any]) -> str:
    path = _safe_path(root, args.get("path") or "", create_parent=False)
    if not path.exists() or not path.is_file():
        raise LocalCapabilityError(f"file not found: {_rel(root, path)}")
    start = _positive_int(args.get("start_line"), 1, maximum=1_000_000)
    end = _positive_int(args.get("end_line"), start + 220, maximum=1_000_000)
    if end < start:
        end = start
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    selected = lines[start - 1 : end]
    numbered = [f"{idx}: {line}" for idx, line in enumerate(selected, start=start)]
    return "\n".join(numbered)


def _workspace_search(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise LocalCapabilityError("workspace.search requires query")
    max_results = _positive_int(args.get("max_results"), 50, maximum=500)
    raw_paths = args.get("paths") if isinstance(args.get("paths"), list) else [""]
    bases = [_safe_path(root, item, create_parent=False) for item in raw_paths if str(item or "").strip()]
    if not bases:
        bases = [root]
    matches: list[dict[str, Any]] = []
    for base in bases:
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            if len(matches) >= max_results:
                break
            if _is_ignored(path) or not path.is_file() or _looks_binary(path):
                continue
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8-sig", errors="ignore").splitlines(), start=1):
                    if query in line:
                        matches.append({"path": _rel(root, path), "line": line_no, "text": line[:500]})
                        if len(matches) >= max_results:
                            break
            except OSError:
                continue
    return {"query": query, "matches": matches}


def _workspace_write(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    op = FileOp(
        op=str(args.get("op") or "write_file"),
        path=str(args.get("path") or ""),
        content=str(args.get("content") or "") if "content" in args else None,
    )
    changed = _apply_file_op(root, op)
    return {"ok": True, "path": changed, "op": op.op}


def _process_run(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, (list, str)) or not command:
        raise LocalCapabilityError("process.run requires command")
    cwd = _safe_path(root, args.get("cwd") or "", create_parent=False)
    cwd.mkdir(parents=True, exist_ok=True)
    timeout = _positive_int(args.get("timeout_seconds"), 120, maximum=1800)
    shell = isinstance(command, str)
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=shell,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    return {
        "command": command,
        "cwd": _rel(root, cwd),
        "exit_code": completed.returncode,
        "stdout": _truncate(completed.stdout),
        "stderr": _truncate(completed.stderr),
        "ok": completed.returncode == 0,
    }


def _project_compile(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    if command is None:
        return {
            "ok": False,
            "error": "project.compile requires an explicit command from the workflow agent or project settings.",
            "hint": "Use process.run or project.compile with args.command set to the project-specific build or test command.",
        }
    return _process_run(root, {**args, "command": command})


def _source_diagnostics(root: Path, args: dict[str, Any]) -> dict[str, Any]:
    paths = args.get("paths") if isinstance(args.get("paths"), list) else []
    files: list[dict[str, Any]] = []
    for item in paths[:200]:
        path = _safe_path(root, item, create_parent=False)
        files.append(
            {
                "path": _rel(root, path),
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() and path.is_file() else None,
            }
        )
    return {
        "ok": True,
        "provider": BACKEND_PROVIDER,
        "diagnostics": [],
        "files": files,
        "note": "Backend source.diagnostics reports file availability only; use process.run for compiler diagnostics.",
    }


def _apply_file_op(root: Path, op: FileOp) -> str | None:
    path = _safe_path(root, op.path, create_parent=True)
    kind = str(op.op or "").strip().lower()
    if kind in {"create_dir", "mkdir"}:
        path.mkdir(parents=True, exist_ok=True)
        return _rel(root, path)
    if kind in {"create_file", "update_file", "replace_file", "write_file"}:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(op.content or ""), encoding="utf-8", newline="")
        return _rel(root, path)
    if kind in {"delete_file", "delete_path", "remove"}:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return _rel(root, path)
    raise LocalCapabilityError(f"unsupported file op: {op.op}")


def _resolve_project_root(value: object, *, create: bool) -> Path:
    text = str(value or "").strip()
    if not text:
        raise LocalCapabilityError("project_root is required for backend local capabilities")
    root = Path(text).expanduser().resolve()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.exists() or not root.is_dir():
        raise LocalCapabilityError(f"project_root does not exist or is not a directory: {root}")
    return root


def _safe_path(root: Path, value: object, *, create_parent: bool) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text == ".":
        candidate = root
    else:
        raw = Path(text)
        candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LocalCapabilityError(f"path escapes project_root: {value}") from exc
    if create_parent:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _file_item(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": _rel(root, path),
        "type": "dir" if path.is_dir() else "file",
        "size": path.stat().st_size if path.is_file() else None,
    }


def _is_ignored(path: Path) -> bool:
    ignored = {".git", ".gradle", ".idea", ".devwerk", "__pycache__", "node_modules", "target", "build", "dist", ".venv", "venv"}
    return any(part in ignored for part in path.parts)


def _looks_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:2048]
    except OSError:
        return True
    return b"\0" in chunk


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _positive_int(value: object, default: int, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = parsed if parsed > 0 else default
    return min(parsed, maximum) if maximum is not None else parsed


def _truncate(value: str, limit: int = 12000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
