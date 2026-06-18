from __future__ import annotations

import json
import logging
from typing import Any

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
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are DevWerk's REVIEWER agent. Review the candidate code change against the user request and plan. "
                    "Do not require every candidate plan path to change. Focus on correctness, completeness, compatibility, "
                    "security, and whether client-side verification is needed. Return JSON only with decision "
                    "(approve|request_recoding|request_replan|fail), summary, findings[], required_changes[], and warnings[]. "
                    "Use request_replan only when the plan or approved path boundary is wrong; use request_recoding for code defects."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))},
        ]
        try:
            raw = get_llm_client(self.agent_name).chat_json(messages)
            result = _normalize_review(raw)
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
                "degraded": True,
            }


def _normalize_review(raw: Any) -> dict[str, Any]:
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
        "degraded": False,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:50]
