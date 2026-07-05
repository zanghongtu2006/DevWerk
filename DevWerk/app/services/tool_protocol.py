from __future__ import annotations

from typing import Any


LOCAL_CAPABILITIES = {"workspace.list", "workspace.read", "workspace.search", "workspace.write"}
BROWSER_CAPABILITIES = {"browser.cdp", "browser.playwright"}
NETWORK_CAPABILITIES = {"network.http", "network.web"}
REMOTE_CAPABILITIES = {
    "process.run",
    "project.compile",
    "source.diagnostics",
    *BROWSER_CAPABILITIES,
    *NETWORK_CAPABILITIES,
}
ALL_CAPABILITIES = LOCAL_CAPABILITIES | REMOTE_CAPABILITIES


class ToolProtocolError(ValueError):
    pass


def normalize_tool_request(raw: dict[str, Any], index: int = 0) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ToolProtocolError(f"tool_requests[{index}] must be object")

    tool = str(raw.get("tool") or raw.get("name") or raw.get("capability") or "").strip()
    if not tool:
        raise ToolProtocolError(f"tool_requests[{index}].tool invalid")

    request_id = str(raw.get("id") or f"tool-{index + 1}").strip()
    if not request_id:
        raise ToolProtocolError(f"tool_requests[{index}].id invalid")

    args_obj = raw.get("args") if isinstance(raw.get("args"), dict) else raw.get("arguments")
    if not isinstance(args_obj, dict):
        args_obj = raw.get("params") if isinstance(raw.get("params"), dict) else None
    args = dict(args_obj) if isinstance(args_obj, dict) else {}

    if tool in {"workspace.mkdir", "workspace.makedirs"}:
        tool = "workspace.write"
        args["op"] = "create_dir"
        paths = args.get("paths") if isinstance(args.get("paths"), list) else []
        if paths:
            args["path"] = str(paths[0] or "")
    elif tool == "workspace.write" and isinstance(args.get("files"), list):
        first = next((item for item in args["files"] if isinstance(item, dict)), None)
        if first is not None:
            args = {
                "op": first.get("op") or first.get("operation") or "write_file",
                "path": first.get("path") or first.get("file") or first.get("name"),
                "content": first.get("content"),
            }

    if tool == "workspace.read":
        _copy_alias(args, "file_path", "path")
        _require_path(args, index)
        args.setdefault("start_line", 1)
        args.setdefault("end_line", 220)
    elif tool == "workspace.list":
        _copy_alias(args, "dir", "path")
        _copy_alias(args, "directory", "path")
        args["path"] = str(args.get("path") or "")
        args.setdefault("max_depth", 3)
    elif tool == "workspace.search":
        _copy_alias(args, "pattern", "query")
        _copy_alias(args, "term", "query")
        _copy_alias(args, "text", "query")
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolProtocolError(f"tool_requests[{index}].args.query missing")
        args["query"] = query
        if "path" in args and "paths" not in args:
            args["paths"] = [args["path"]]
        if not isinstance(args.get("paths"), list):
            args["paths"] = []
        args.setdefault("max_results", 50)
    elif tool == "workspace.write":
        _require_path(args, index)
        op = str(args.get("op") or args.get("operation") or "write_file").strip()
        args["op"] = op or "write_file"
        if args["op"] not in {"create_file", "update_file", "replace_file", "write_file", "create_dir", "mkdir", "delete_file", "delete_path", "remove"}:
            raise ToolProtocolError(f"tool_requests[{index}].args.op invalid")
        if args["op"] not in {"create_dir", "mkdir", "delete_file", "delete_path", "remove"} and "content" not in args:
            raise ToolProtocolError(f"tool_requests[{index}].args.content missing")
    elif tool == "process.run":
        command = args.get("command")
        if not isinstance(command, (list, str)) or not command:
            raise ToolProtocolError(f"tool_requests[{index}].args.command missing")
        args.setdefault("timeout_seconds", 120)
    elif tool == "source.diagnostics":
        paths = args.get("paths")
        if "path" in args and not isinstance(paths, list):
            paths = [args["path"]]
        if not isinstance(paths, list):
            paths = []
        args["paths"] = paths
        args.setdefault("max_errors", 100)
    elif tool == "project.compile":
        args.setdefault("timeout_seconds", 300)
        args.setdefault("max_errors", 200)
    elif tool == "browser.cdp":
        _copy_alias(args, "command", "action")
        _copy_alias(args, "method", "action")
        args["action"] = str(args.get("action") or "inspect").strip()
        args.setdefault("timeout_seconds", 60)
        if not args["action"]:
            raise ToolProtocolError(f"tool_requests[{index}].args.action missing")
    elif tool == "browser.playwright":
        _copy_alias(args, "command", "action")
        args["action"] = str(args.get("action") or "run").strip()
        args.setdefault("timeout_seconds", 120)
        if not args["action"]:
            raise ToolProtocolError(f"tool_requests[{index}].args.action missing")
    elif tool == "network.http":
        _copy_alias(args, "uri", "url")
        url = str(args.get("url") or "").strip()
        if not url:
            raise ToolProtocolError(f"tool_requests[{index}].args.url missing")
        args["url"] = url
        args["method"] = str(args.get("method") or "GET").strip().upper()
        args.setdefault("timeout_seconds", 60)
    elif tool == "network.web":
        _copy_alias(args, "q", "query")
        _copy_alias(args, "url", "query")
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolProtocolError(f"tool_requests[{index}].args.query missing")
        args["query"] = query
        args.setdefault("max_results", 5)
        args.setdefault("timeout_seconds", 60)

    return {"id": request_id, "tool": tool, "args": args}


def normalize_tool_requests(raw_requests: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw_requests or []):
        if not isinstance(item, dict):
            raise ToolProtocolError(f"tool_requests[{index}] must be object")
        out.append(normalize_tool_request(item, index))
    return out


def _copy_alias(args: dict[str, Any], source: str, target: str) -> None:
    if target not in args and source in args:
        args[target] = args[source]


def _require_path(args: dict[str, Any], index: int) -> None:
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ToolProtocolError(f"tool_requests[{index}].args.path missing")
