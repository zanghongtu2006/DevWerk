from __future__ import annotations

import json
from typing import Any

from app.v1.agent_models import AgentRunSpec
from app.v1.domain import AgentModelResponse
from app.v1.policy import PlatformPolicySnapshot


def build_run_envelope(
    spec: AgentRunSpec,
    platform_policy: PlatformPolicySnapshot,
) -> dict[str, Any]:
    return {
        "protocol_version": "devwerk.agent.v1",
        "agent": {
            "kind": spec.kind,
            "project_id": spec.project["id"],
            "instruction_revision": spec.instruction_revision,
            "task_id": spec.task_id,
            "column_run_id": spec.column_run_id,
            "column_attempt_id": spec.column_attempt_id,
            "agent_session_id": spec.agent_session_id,
            "agent_instance_id": spec.agent_instance_id,
            "assignment_id": (spec.assignment or {}).get('id'),
            "requirement_id": spec.requirement_id,
        },
        "project": {
            "name": spec.project.get("name", ""),
            "description": spec.project.get("description", ""),
            "base_dir": spec.project.get("base_dir", ""),
        },
        "instruction": spec.instruction,
        "platform_policy": {
            "revision": platform_policy.revision,
            "content_hash": platform_policy.content_hash,
            **({"content": platform_policy.content} if spec.kind == "conversation" else {}),
        },
        "context": spec.context,
        "completion_checks": list(spec.completion_contract.acceptance_checks) if spec.completion_contract else [],
    }


def build_conversation_system_envelope(
    spec: AgentRunSpec,
    platform_policy: PlatformPolicySnapshot,
) -> dict[str, Any]:
    """Return the byte-stable prefix for one logical Project Session."""
    from app.v1.conversation_report import REPORT_INSTRUCTION
    from app.v1.conversation_intent import TURN_INSTRUCTION
    return {
        "protocol_version": "devwerk.conversation-session.v1",
        "agent": {
            "kind": "conversation",
            "project_id": spec.project["id"],
            "agent_session_id": spec.agent_session_id,
            "instruction_revision": spec.instruction_revision,
        },
        "project": {"id": spec.project["id"]},
        "instruction": spec.instruction,
        "platform_policy": {
            "revision": platform_policy.revision,
            "content_hash": platform_policy.content_hash,
            "content": platform_policy.content,
        },
        "turn_protocol": {
            **({'intent_boundary': TURN_INSTRUCTION} if spec.conversation_job_id else {}),
            **({'reply_report': REPORT_INSTRUCTION} if spec.require_conversation_report else {}),
            "state": "authoritative Project state is supplied in the current user Turn",
            "execution": "state changes exist only after successful tool receipts",
            "completion": (
                "a tool-free assistant response completes the current Turn; assistant prose "
                "never changes Project state and is not an execution receipt"
            ),
        },
    }


def assistant_message(response: AgentModelResponse) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": response.text or ""}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": stable_json(call.arguments)},
            }
            for call in response.tool_calls
        ]
    return message


def run_checkpoint(
    iterations: int,
    tool_calls: int,
    direct_effect_calls: int,
    latest_text: str,
) -> dict[str, Any]:
    return {
        "iterations_completed": iterations,
        "tool_calls_completed": tool_calls,
        "direct_effect_calls": direct_effect_calls,
        "latest_text": latest_text,
    }


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
