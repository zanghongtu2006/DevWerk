from __future__ import annotations

import json
from typing import Any

from app.v1.agents.agent_models import AgentRunSpec
from app.v1.domain import AgentModelResponse
from app.v1.policy import PlatformPolicySnapshot
from app.v1.protocols.agent.agent_prompt_contracts import (
    AgentPromptIdentity, AgentPromptProject, AgentRunEnvelope,
    CompletionEvidencePrompt, ConversationPromptIdentity,
    ConversationPromptProject, ConversationSystemEnvelope,
    PlatformPolicyPrompt, TurnProtocolPrompt,
)
from app.v1.protocols.agent.agent_runtime_receipts import RunCheckpoint


def build_run_envelope(
    spec: AgentRunSpec,
    platform_policy: PlatformPolicySnapshot,
) -> dict[str, Any]:
    policy = PlatformPolicyPrompt(
        revision=platform_policy.revision, content_hash=platform_policy.content_hash,
    )
    if spec.kind == "conversation":
        policy.content = platform_policy.content
    return AgentRunEnvelope(
        protocol_version="devwerk.agent.v1",
        agent=AgentPromptIdentity(
            kind=spec.kind, project_id=spec.project["id"],
            instruction_revision=spec.instruction_revision,
            task_id=spec.task_id, column_run_id=spec.column_run_id,
            column_attempt_id=spec.column_attempt_id,
            agent_session_id=spec.agent_session_id,
            agent_instance_id=spec.agent_instance_id,
            assignment_id=(spec.assignment or {}).get('id'),
            requirement_id=spec.requirement_id,
        ),
        project=AgentPromptProject(
            name=spec.project.get("name", ""),
            description=spec.project.get("description", ""),
            base_dir=spec.project.get("base_dir", ""),
        ),
        instruction=spec.instruction, platform_policy=policy,
        context=spec.context,
        available_checks=list(spec.completion_contract.acceptance_checks) if spec.completion_contract else [],
        completion_evidence={
            outcome: CompletionEvidencePrompt(
                evidence_requirement=rule.evidence_requirement,
                required_checks=list(rule.required_checks),
            ) for outcome, rule in spec.completion_contract.outcomes.items()
        } if spec.completion_contract else {},
    ).wire()


def build_column_system_envelope(
    spec: AgentRunSpec,
    platform_policy: PlatformPolicySnapshot,
) -> dict[str, Any]:
    """Keep the column's stable instructions separate from its changing work input."""
    envelope = AgentRunEnvelope.model_validate(build_run_envelope(spec, platform_policy))
    return envelope.wire(include_context=False)


def build_conversation_system_envelope(
    spec: AgentRunSpec,
    platform_policy: PlatformPolicySnapshot,
) -> dict[str, Any]:
    """Return the byte-stable prefix for one logical Project Session."""
    from app.v1.conversations.conversation_report import REPORT_INSTRUCTION
    from app.v1.conversations.conversation_intent import TURN_INSTRUCTION
    protocol = TurnProtocolPrompt()
    if spec.conversation_job_id:
        protocol.intent_boundary = TURN_INSTRUCTION
    if spec.require_conversation_report:
        protocol.reply_report = REPORT_INSTRUCTION
    return ConversationSystemEnvelope(
        protocol_version="devwerk.conversation-session.v1",
        agent=ConversationPromptIdentity(
            kind="conversation", project_id=spec.project["id"], agent_session_id=spec.agent_session_id,
            instruction_revision=spec.instruction_revision,
        ),
        project=ConversationPromptProject(id=spec.project["id"]),
        instruction=spec.instruction,
        platform_policy=PlatformPolicyPrompt(
            revision=platform_policy.revision, content_hash=platform_policy.content_hash,
            content=platform_policy.content,
        ),
        turn_protocol=protocol.wire(),
    ).model_dump(mode="json", exclude_unset=True)


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
    return RunCheckpoint(iterations_completed=iterations,
        tool_calls_completed=tool_calls, direct_effect_calls=direct_effect_calls,
        latest_text=latest_text).model_dump(mode="json")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
