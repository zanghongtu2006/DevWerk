from __future__ import annotations

from typing import Any

from app.models.protocol import ToolRequest
from app.services.tool_protocol import ToolProtocolError, normalize_tool_request


def configured_post_apply_tool_requests(project_settings: object) -> list[ToolRequest]:
    if not isinstance(project_settings, dict):
        return []
    parameters = project_settings.get("parameters")
    if not isinstance(parameters, dict):
        return []
    verification = parameters.get("verification")
    raw_requests = None
    if isinstance(verification, dict):
        raw_requests = verification.get("tool_requests")
    if raw_requests is None:
        raw_requests = parameters.get("verification_tool_requests")
    if not isinstance(raw_requests, list):
        return []

    requests: list[ToolRequest] = []
    for index, raw in enumerate(raw_requests):
        if not isinstance(raw, dict):
            continue
        try:
            requests.append(ToolRequest.model_validate(normalize_tool_request(raw, index)))
        except (ToolProtocolError, ValueError):
            continue
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
