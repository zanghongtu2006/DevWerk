from __future__ import annotations

import json
import logging
from typing import Any

from app.models.protocol import ToolRequest
from app.services.capability_broker import CapabilityBroker
from app.services.llm_factory import get_llm_client
from app.services.tool_protocol import ToolProtocolError, normalize_tool_request


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
            "previous_revision_verification_feedback": payload.get("verification_feedback"),
            "client_capabilities": payload.get("client_capabilities"),
            "verification_required": bool(payload.get("verification_required")),
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are DevWerk's REVIEWER agent. Review the candidate code change against the user request and plan. "
                    "Do not require every candidate plan path to change. Focus on correctness, completeness, compatibility, "
                    "security, and whether client-side verification is needed. Do not reject a semantically plausible candidate "
                    "because previous_revision_verification_feedback still contains errors: those results describe the previously "
                    "applied revision and are evidence the current candidate is expected to repair, not validation of the current candidate. "
                    "Do not reject a candidate only because it has not been compiled or tested yet. Instead approve it for "
                    "snapshot-protected apply and return "
                    "verification_tool_requests[] using only tools declared in client_capabilities. The model must select commands from "
                    "project evidence; commands must be project-relative and non-destructive. Prefer one authoritative project-native "
                    "build, test, typecheck, lint, or provider diagnostic operation inferred from manifests and workspace evidence. Never use "
                    "source-printing or text-matching commands such as cat, type, grep, or findstr as verification; source content is review "
                    "evidence, not an executable check. When verification_required=true, an approve decision MUST include at least one "
                    "authoritative verification_tool_request; infer it from project evidence rather than hardcoding a framework command. "
                    "If verification is not required and no authoritative operation can be inferred, return no verification request. "
                    "Return JSON only with decision "
                    "(approve|request_recoding|request_replan|fail), summary, findings[], required_changes[], warnings[], and "
                    "verification_tool_requests:[{id,tool,args}]. Use request_replan only when the plan or approved path boundary is "
                    "wrong; use request_recoding for any concrete, repairable code defect. Use fail only when no code revision can possibly "
                    "satisfy the task, never merely because the current candidate is wrong or incomplete."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))},
        ]
        try:
            client = get_llm_client(self.agent_name)
            raw = client.chat_json(messages)
            result = _normalize_review(raw, _client_tools(payload.get("client_capabilities")))
            if _missing_required_verification(result, payload):
                repair_messages = messages + [
                    {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            "The task requires executable verification, but the review approved without a usable client tool request. "
                            "Re-emit the complete review JSON and include at least one authoritative, project-relative verification_tool_request "
                            "selected from client_capabilities and inferred from workspace manifests."
                        ),
                    },
                ]
                result = _normalize_review(
                    client.chat_json(repair_messages),
                    _client_tools(payload.get("client_capabilities")),
                )
                if _missing_required_verification(result, payload):
                    result["decision"] = "request_recoding"
                    result["summary"] = "Executable verification is required before this revision can be approved."
                    result["required_changes"] = list(result.get("required_changes") or []) + [
                        "Return an authoritative client verification request inferred from the project evidence."
                    ]
            _log.debug("reviewer result decision=%s summary=%s", result["decision"], result["summary"])
            return result
        except Exception as exc:  # noqa: BLE001
            _log.warning("reviewer unavailable; protocol review remains authoritative error=%s: %s", type(exc).__name__, exc)
            verification_required = bool(payload.get("verification_required"))
            return {
                "decision": "request_recoding" if verification_required else "approve",
                "summary": (
                    "Semantic reviewer was unavailable and mandatory executable verification could not be selected."
                    if verification_required
                    else "Semantic reviewer was unavailable; protocol checks passed."
                ),
                "findings": [],
                "required_changes": (
                    ["Select an authoritative client verification request before approval."]
                    if verification_required
                    else []
                ),
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
    return CapabilityBroker().available(capabilities)


def _missing_required_verification(result: dict[str, Any], payload: dict[str, Any]) -> bool:
    if not payload.get("verification_required") or result.get("decision") != "approve":
        return False
    client_tools = _client_tools(payload.get("client_capabilities"))
    if not client_tools.intersection({"process.run", "project.compile", "source.diagnostics"}):
        return False
    return not result.get("verification_tool_requests")


def _verification_tool_requests(value: Any, allowed_tools: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not allowed_tools:
        return []
    requests: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        try:
            normalized = normalize_tool_request(item, index)
        except ToolProtocolError:
            continue
        tool = str(normalized.get("tool") or "").strip()
        if tool not in allowed_tools:
            continue
        request_id = str(normalized.get("id") or f"review-{index + 1}").strip()
        if not request_id or request_id in seen_ids:
            continue
        args = normalized.get("args") if isinstance(normalized.get("args"), dict) else {}
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
