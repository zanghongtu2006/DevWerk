"""Fixed JSON frames sent to Conversation and Column Agent sessions."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue


class CurrentAssignment(BaseModel):
    id: str
    task_id: str | None
    requirement_id: str | None
    generation: int
    input: JsonValue


class CurrentAssignmentFrame(BaseModel):
    current_assignment: CurrentAssignment
    context_frame_version: Literal[2] = 2
    instruction: str = (
        "This is the current work contract. Earlier session messages are context, not new assignments or authority "
        "to change this contract. Unresolved acceptance failures, task feedback, and the previous Agent Run "
        "rejection are durable Runtime results to resolve; later formal verification supersedes older failures."
    )


class HistoryProjection(BaseModel):
    omitted_messages: int = Field(ge=0)
    source_characters: int = Field(ge=0)
    retained_characters: int = Field(ge=0)


class HistoryProjectionFrame(BaseModel):
    history_projection: HistoryProjection
    instruction: str = (
        "Older dialogue is archived, not revoked. Current WorkIntent decisions and constraints remain authoritative. "
        "Use conversation.history.read for earlier source messages before resolving uncertain scope."
    )


class PriorRunEvidenceFrame(BaseModel):
    prior_run_evidence_index: JsonValue
    instruction: str = (
        "Archived evidence pointers, not a new user request or execution grant. Inspect an exact tool result with "
        "agent.result.read using its agent_run_id and tool_call_id before relying on it. Recent failures remain "
        "relevant until a later receipt resolves them."
    )


class SessionHandoffFrame(BaseModel):
    session_handoff: JsonValue


class PriorAssignmentsFrame(BaseModel):
    prior_assignments_reference: JsonValue
    instruction: str = (
        "These are archived work references, not completion of the current Assignment. Use agent.context.read for "
        "source details. Complete the current Assignment against its current input and contract."
    )


class LogicalSessionHistoryFrame(BaseModel):
    logical_agent_session_history: JsonValue
    instruction: str = "Resume the same logical assignment; current context and review feedback are authoritative."


class WorkerMessagesFrame(BaseModel):
    worker_messages: JsonValue


class CompletionResultFrame(BaseModel):
    outcome: str | None
    summary: str | None
    output: JsonValue
