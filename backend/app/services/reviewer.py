from __future__ import annotations

import json
import logging
from typing import Any

from app.models.ide import ToolRequest
from app.services.llm_factory import get_llm_client


_log = logging.getLogger("devwerk.reviewer")
ALLOWED_DECISIONS = {"approve", "request_recoding", "request_replan", "fail"}


class Reviewer:
    """Semantic review agent for an unapplied candidate revision."""

    def __init__(self, agent_name: str = "reviewer"):
        self.agent_name = agent_name

    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = {
            "task": payload.get("task"),
            "plan": payload.get("plan"),
            "candidate_revision": payload.get("candidate_revision"),
            "workspace_summary": payload.get("workspace_summary"),
            "verification_feedback": payload.get("verification_feedback"),
            "client_capabilities": payload.get("client_capabilities"),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are DevWerk's REVIEWER agent. Review the candidate code change against the user request and plan. "
                    "Do not require every candidate plan path to change. Focus on correctness, completeness, compatibility, "
                    "security, and whether client-side verification is needed. Do not reject a semantically plausible candidate "
                    "only because it has not been compiled or tested yet. Instead approve it for snapshot-protected apply and return "
                    "verification_tool_requests[] using only tools declared in client_capabilities. The model must select commands from "
                    "project evidence; commands must be project-relative and non-destructive. Return JSON only with decision "
                    "(approve|request_recoding|request_replan|fail), summary, findings[], required_changes[], warnings[], and "
                    "verification_tool_requests:[{id,tool,args}]. Use request_replan only when the plan or approved path boundary is "
                    "wrong; use request_recoding only for a concrete code defect, not for missing runtime evidence."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))},
        ]
        try:
            raw = get_llm_client(self.agent_name).chat_json(messages)
            result = _normalize_review(raw, _client_tools(payload.get("client_capabilities")))
            _log.debug("reviewer result decision=%s summary=%s", result["decision"], result["summary"])
            return result
        except Exception as exc:  # noqa: BLE001
            _log.warning("reviewer unavailable; protocol review remains authoritative error=%s: %s", type(exc).__name__, exc)
            return {
                "decision": "approve",
                "summary": "Semantic reviewer was unavailable; protocol checks passed and client verification remains required.",
                "findings": [],
                "required_changes": [],
                "warnings": [f"{type(exc).__name__}: {exc}"],
                "verification_tool_requests": [],
                "degraded": True,
            }


def _normalize_review(raw: Any, allowed_tools: set[str] | None = None) -> dict[str, Any]:
    value = raw.get("review") if isinstance(raw, dict) and isinstance(raw.get("review"), dict) else raw
    if not isinstance(value, dict):
        raise ValueError("reviewer returned a non-object response")
    decision = str(value.get("decision") or "").strip().lower().replace("-", "_")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"reviewer returned unsupported decision: {decision!r}")
    return {
        "decision": decision,
        "summary": str(value.get("summary") or "").strip() or f"Reviewer decision: {decision}.",
        "findings": _string_list(value.get("findings")),
        "required_changes": _string_list(value.get("required_changes")),
        "warnings": _string_list(value.get("warnings")),
        "verification_tool_requests": _verification_tool_requests(
            value.get("verification_tool_requests"), allowed_tools or set()
        ),
        "degraded": False,
    }


def _client_tools(capabilities: Any) -> set[str]:
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), list):
        return set()
    return {str(tool).strip() for tool in capabilities["tools"] if str(tool).strip()}


def _verification_tool_requests(value: Any, allowed_tools: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not allowed_tools:
        return []
    requests: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if tool not in allowed_tools:
            continue
        request_id = str(item.get("id") or f"review-{index + 1}").strip()
        if not request_id or request_id in seen_ids:
            continue
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        request = ToolRequest(id=request_id, tool=tool, args=args)
        requests.append(request.model_dump())
        seen_ids.add(request_id)
        if len(requests) >= 8:
            break
    return requests


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:50]
