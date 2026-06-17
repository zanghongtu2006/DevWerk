from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from app.models.ide import ToolRequest


def infer_post_apply_tool_requests(workspace: object) -> list[ToolRequest]:
    paths = _workspace_paths(workspace)
    if not paths:
        return []

    requests: list[ToolRequest] = []
    maven_roots = _manifest_dirs(paths, "pom.xml")
    gradle_roots = _manifest_dirs(paths, "build.gradle") | _manifest_dirs(paths, "build.gradle.kts")

    for root in sorted(maven_roots):
        command = ["./mvnw", "test"] if _has_any(paths, root, {"mvnw", "mvnw.cmd"}) else ["mvn", "test"]
        requests.append(
            _run_command_request(
                request_id=_tool_id("compile", root),
                command=command,
                cwd=root,
                reason="Maven project manifest detected.",
            )
        )

    for root in sorted(gradle_roots):
        command = ["./gradlew", "test"] if _has_any(paths, root, {"gradlew", "gradlew.bat"}) else ["gradle", "test"]
        requests.append(
            _run_command_request(
                request_id=_tool_id("compile", root),
                command=command,
                cwd=root,
                reason="Gradle project manifest detected.",
            )
        )

    return _dedupe_requests(requests)


def verification_failed(verification: object) -> bool:
    if not isinstance(verification, dict):
        return False
    required = verification.get("required")
    results = verification.get("results")
    if not isinstance(required, list) or not isinstance(results, dict):
        return False
    return any(str(results.get(str(item))).lower() != "passed" for item in required)


def verification_has_policy(verification: object) -> bool:
    if not isinstance(verification, dict):
        return False
    return isinstance(verification.get("required"), list) and isinstance(verification.get("results"), dict)


def verification_feedback_summary(verification: object) -> str:
    if not isinstance(verification, dict):
        return "Post-apply verification failed."
    details = verification.get("tool_results")
    if not isinstance(details, list):
        return "Post-apply verification failed."
    lines = []
    for item in details[:5]:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or item.get("tool") or "tool")
        status = "passed" if item.get("ok") is True else "failed"
        text = str(item.get("error") or item.get("content") or "")
        lines.append(f"{tool_id}: {status}\n{text[-4000:]}")
    return "\n\n".join(lines) if lines else "Post-apply verification failed."


def _run_command_request(*, request_id: str, command: list[str], cwd: str, reason: str) -> ToolRequest:
    args: dict[str, Any] = {"command": command, "timeout_seconds": 180, "reason": reason}
    if cwd:
        args["cwd"] = cwd
    return ToolRequest(id=request_id, tool="run_command", args=args)


def _workspace_paths(workspace: object) -> set[str]:
    paths: set[str] = set()
    if not isinstance(workspace, dict):
        return paths

    source_map = workspace.get("source_map")
    if isinstance(source_map, dict):
        files = source_map.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict):
                    _add_path(paths, item.get("path"))

    for key in ("open_files", "changed_files"):
        items = workspace.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    _add_path(paths, item.get("path"))
                else:
                    _add_path(paths, item)

    tree_preview = workspace.get("tree_preview")
    if isinstance(tree_preview, str):
        for line in tree_preview.splitlines():
            text = line.strip()
            if text and not text.endswith("/"):
                _add_path(paths, text)
    return paths


def _add_path(paths: set[str], value: object) -> None:
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or ".." in PurePosixPath(text).parts:
        return
    paths.add(text.strip("/"))


def _manifest_dirs(paths: set[str], manifest: str) -> set[str]:
    out = set()
    for path in paths:
        if path.endswith("/" + manifest) or path == manifest:
            parent = path.removesuffix("/" + manifest) if path != manifest else ""
            out.add(parent)
    return out


def _has_any(paths: set[str], root: str, names: set[str]) -> bool:
    prefix = f"{root}/" if root else ""
    return any(prefix + name in paths for name in names)


def _tool_id(prefix: str, root: str) -> str:
    suffix = root.replace("/", "_").replace("-", "_").strip("_")
    return f"{prefix}_{suffix}" if suffix else prefix


def _dedupe_requests(requests: list[ToolRequest]) -> list[ToolRequest]:
    seen: set[tuple[str, str]] = set()
    out: list[ToolRequest] = []
    for request in requests:
        command = " ".join(str(part) for part in request.args.get("command") or [])
        cwd = str(request.args.get("cwd") or "")
        key = (cwd, command)
        if key in seen:
            continue
        seen.add(key)
        out.append(request)
    return out
