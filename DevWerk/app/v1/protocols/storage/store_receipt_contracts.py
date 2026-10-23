"""Fixed Store return records crossing API, Tool, or Agent boundaries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.v1.conversations.conversation_intent import WorkIntentState


class ConversationMessageRecord(BaseModel):
    id: int
    project_id: str
    role: str
    content: str
    meta: dict[str, JsonValue]
    created_at: str


class ConversationJobSummary(BaseModel):
    id: str
    status: str
    trigger_kind: str
    created_at: str
    updated_at: str
    finished_at: str | None
    has_error: bool


class ConversationStateRecord(BaseModel):
    project_id: str
    agent_state: str
    job: ConversationJobSummary | None
    work_intent: WorkIntentState


class BacklogRecord(BaseModel):
    id: str
    project_id: str
    title: str
    brief: str
    readiness: dict[str, JsonValue]
    state: str
    created_at: str
    updated_at: str


class ColumnRunStartRecord(BaseModel):
    id: str
    attempt_id: str
    attempt_no: int = Field(ge=1)
    sequence: int = Field(ge=1)
    column_key: str
    status: Literal["running"] = "running"
    started_at: str


class AgentMessageRecord(BaseModel):
    id: int
    agent_run_id: str
    sequence: int
    role: str
    content: str
    tool_calls: list[dict[str, JsonValue]]
    tool_call_id: str | None
    created_at: str


class ToolInvocationRecord(BaseModel):
    id: int
    agent_run_id: str
    sequence: int
    tool_call_id: str
    capability: str
    arguments: dict[str, JsonValue]
    result: JsonValue
    ok: bool
    status: str
    created_at: str


class ScheduledReviewRecord(BaseModel):
    id: str
    project_id: str
    reason: str
    due_at: str
    state: Literal["pending"] = "pending"
    created_at: str


class GovernanceDecisionRecord(BaseModel):
    id: str
    project_id: str
    kind: str
    subject_id: str | None
    decision: str
    data: dict[str, JsonValue]
    created_at: str


class ExecutionReceiptView(BaseModel):
    model_config = ConfigDict(extra="allow")  # DB columns vary across old additive migrations.
    id: str
    project_id: str
    execution_key: str
    capability: str
    status: str
    arguments: dict[str, JsonValue]
    claimed: bool = False


class KanbanProjectionRecord(BaseModel):
    project_id: str
    version: int
    projection: dict[str, JsonValue]
    updated_at: str


class UnresolvedTaskFailure(BaseModel):
    id: str
    title: str
    error: str | None
    rerun_of_task_id: str | None
    finished_at: str | None


class UnresolvedConversationFailure(BaseModel):
    id: str
    trigger_kind: str
    error: str | None
    finished_at: str | None


class UnresolvedFailures(BaseModel):
    tasks: list[UnresolvedTaskFailure]
    conversation_jobs: list[UnresolvedConversationFailure]


class UnresolvedFailureCounts(BaseModel):
    tasks: int = Field(ge=0)
    conversation_jobs: int = Field(ge=0)


class QuiescenceBlockers(BaseModel):
    conversation_jobs: int = Field(ge=0)
    nonterminal_tasks: int = Field(ge=0)
    unacknowledged_mailbox: int = Field(ge=0)
    due_scheduled_reviews: int = Field(ge=0)
    stability_window_remaining_seconds: float = Field(ge=0)


class ProjectQuiescenceRecord(BaseModel):
    project_id: str
    quiescent: bool
    observed_at: str
    latest_activity_at: str
    required_stability_seconds: float
    governance_outcome: Literal["attention_required", "settled", "active"]
    blockers: QuiescenceBlockers
    unresolved_failures: UnresolvedFailureCounts
