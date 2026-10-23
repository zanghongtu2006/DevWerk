"""Typed context envelopes sent to Agent model sessions."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from app.v1.domain import AcceptanceCheck


class AgentPromptIdentity(BaseModel):
    kind: str
    project_id: str
    instruction_revision: int
    task_id: str | None = None
    column_run_id: str | None = None
    column_attempt_id: str | None = None
    agent_session_id: str | None = None
    agent_instance_id: str | None = None
    assignment_id: str | None = None
    requirement_id: str | None = None


class AgentPromptProject(BaseModel):
    name: str
    description: str
    base_dir: str


class PlatformPolicyPrompt(BaseModel):
    revision: int
    content_hash: str
    content: str | None = None


class CompletionEvidencePrompt(BaseModel):
    evidence_requirement: str
    required_checks: list[str]


class AgentRunEnvelope(BaseModel):
    protocol_version: Literal["devwerk.agent.v1"] = "devwerk.agent.v1"
    agent: AgentPromptIdentity
    project: AgentPromptProject
    instruction: str
    platform_policy: PlatformPolicyPrompt
    context: JsonValue
    available_checks: list[AcceptanceCheck] = Field(default_factory=list)
    completion_evidence: dict[str, CompletionEvidencePrompt] = Field(default_factory=dict)

    def wire(self, *, include_context: bool = True) -> dict[str, Any]:
        excluded = set() if include_context else {"context"}
        return self.model_dump(mode="json", exclude_unset=True, exclude=excluded)


class ConversationPromptIdentity(BaseModel):
    kind: Literal["conversation"] = "conversation"
    project_id: str
    agent_session_id: str | None
    instruction_revision: int


class ConversationPromptProject(BaseModel):
    id: str


class TurnProtocolPrompt(BaseModel):
    intent_boundary: str | None = None
    reply_report: str | None = None
    state: str = "authoritative Project state is supplied in the current user Turn"
    execution: str = "state changes exist only after successful tool receipts"
    completion: str = (
        "a tool-free assistant response completes the current Turn; assistant prose "
        "never changes Project state and is not an execution receipt"
    )

    def wire(self) -> dict[str, str]:
        return self.model_dump(mode="json", exclude_none=True)


class ConversationSystemEnvelope(BaseModel):
    protocol_version: Literal["devwerk.conversation-session.v1"] = "devwerk.conversation-session.v1"
    agent: ConversationPromptIdentity
    project: ConversationPromptProject
    instruction: str
    platform_policy: PlatformPolicyPrompt
    turn_protocol: dict[str, str]


class AuthoritativeProjectState(BaseModel):
    model_config = ConfigDict(extra="allow")
    project: dict[str, JsonValue]

    @classmethod
    def from_context(cls, project: dict[str, Any], context: dict[str, Any]) -> dict[str, JsonValue]:
        result = cls(project=TypeAdapter(dict[str, JsonValue]).validate_python(project))
        for name, value in TypeAdapter(dict[str, JsonValue]).validate_python(context).items():
            setattr(result, name, value)
        return result.model_dump(mode="json")


class CurrentTurnPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    authoritative_current_request: dict[str, JsonValue]
    authoritative_project_state: dict[str, JsonValue]
    instruction: str = (
        "This immutable request created the current Conversation Job. Interpret it together with "
        "the user's unrevoked constraints in the accompanying Project state. A continuation does "
        "not revoke those constraints; only an explicit user change does. Assistant promises "
        "never broaden the user's authorization. Use tools for inspection or state changes."
    )
    unanswered_prior_user_message: str | None = None
    current_turn_boundary: JsonValue = None
