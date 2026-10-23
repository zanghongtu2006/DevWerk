"""Fixed JSON records written to runtime persistence and agent context."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class WorkResultNotice(BaseModel):
    mode: Literal["work_result"] = "work_result"
    task_ids: list[str]
    tool_call_ids: list[str]


class TaskFingerprint(BaseModel):
    workflow: str
    task: dict[str, JsonValue]


class WorkerSuccessionSnapshot(BaseModel):
    summary: str
    predecessor_worker_id: str


class ArchivedAgentMessage(BaseModel):
    role: str
    content: str
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)


class WorkerInputPayload(BaseModel):
    content: str
    worker_id: str


class WorkerMessage(BaseModel):
    message_id: str
    input: WorkerInputPayload


class WorkerMessagesPayload(BaseModel):
    worker_messages: list[WorkerMessage]


class ContextCheckpointPayload(BaseModel):
    context_checkpoint: JsonValue
    covered_message_count: int | None = None
    covers_through_message_id: int | None = None


class QueuedConversationMeta(BaseModel):
    status: Literal["queued"] = "queued"
    job_id: str
    kind: Literal["message"] = "message"


class ReadinessDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    unresolved_dependencies: list[str] = Field(default_factory=list)


class PriorRunContext(BaseModel):
    prior_agent_run_id: str
    status: str
    final_text: str | None = None
    error: str | None = None
    instruction: str = "Continue the same logical assignment using current Task artifacts and review feedback."


class FailedTerminalArtifactMeta(BaseModel):
    terminal: Literal["failed"] = "failed"
    failure_code: str


class FeedbackVerificationPayload(BaseModel):
    review_run_id: str
    note: str


class TaskTerminalEvidence(BaseModel):
    schema_version: Literal["devwerk.task-terminal.v1"] = Field(
        default="devwerk.task-terminal.v1", alias="schema"
    )
    project_id: str
    task_id: str
    column_run_id: str
    terminal: str
    output: dict[str, JsonValue]
    error: str | None = None
    recorded_at: str


class TaskTerminalEvidenceMeta(BaseModel):
    terminal: str
    schema_version: Literal["devwerk.task-terminal.v1"] = Field(
        default="devwerk.task-terminal.v1", alias="schema"
    )


class PreparedTerminalEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: Literal["task_terminal"] = "task_terminal"
    meta: TaskTerminalEvidenceMeta | None = None

    @classmethod
    def from_file(cls, file_info: dict[str, Any], terminal: str) -> dict[str, JsonValue]:
        result = cls.model_validate(file_info)
        result.meta = TaskTerminalEvidenceMeta(terminal=terminal)
        return result.model_dump(mode="json", by_alias=True)


class ConversationProgressDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    sequence: int
    tool_call_id: str | None = None

    @classmethod
    def with_extra(cls, sequence: int, details: dict[str, Any] | None, tool_call_id: str | None = None) -> dict[str, JsonValue]:
        result = cls(sequence=sequence)
        if tool_call_id is not None:
            result.tool_call_id = tool_call_id
        for name, value in TypeAdapter(dict[str, JsonValue]).validate_python(details or {}).items():
            setattr(result, name, value)
        return result.model_dump(mode="json", exclude_none=True)


class ConversationProgressEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    agent_run_id: str
    conversation_job_id: str | None = None
    kind: str
    chunk_index: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    content: str

    @classmethod
    def with_details(cls, details: dict[str, Any] | None, **fields: Any) -> dict[str, JsonValue]:
        result = cls.model_validate(fields)
        for name, value in TypeAdapter(dict[str, JsonValue]).validate_python(details or {}).items():
            setattr(result, name, value)
        return result.model_dump(mode="json")


class OperationFingerprintPayload(BaseModel):
    capability: str
    arguments: dict[str, JsonValue]


class TriggerConversationMeta(BaseModel):
    trigger: str
    job_id: str
    reason: str | None = None


class SupervisionTrigger(BaseModel):
    mailbox_ids: list[int] = Field(default_factory=list)
    review_reason: str | None = None


class SupervisionTurnContract(BaseModel):
    phase: Literal["supervision"] = "supervision"
    act: Literal["supervise"] = "supervise"
    source_message_id: int
    execution_grant_id: str
    supervised_task_id: str


class TaskInitialPlanningContext(BaseModel):
    task_plan_id: str
    workflow_plan_id: str
    task_ref: str
    workflow_fit: str
    review_scope: str
    retry_scope: str


class TaskInitialContext(BaseModel):
    planning: TaskInitialPlanningContext


class ProjectionWorkflow(BaseModel):
    id: str
    revision: int
    definition: dict[str, JsonValue]


class KanbanProjectionPayload(BaseModel):
    workflow: ProjectionWorkflow | None = None
    tasks: list[dict[str, JsonValue]] = Field(default_factory=list)


class WorkerContextWindow(BaseModel):
    older_messages_omitted: int = Field(ge=0)
    first_retained_message_id: int | None = None
    prior_assignments: list[dict[str, JsonValue]] = Field(default_factory=list)
    retrieve: str = "Use agent.context.read to inspect older source messages; no source history has been deleted."


class WorkerContextWindowPayload(BaseModel):
    context_window: WorkerContextWindow


class FailedOperationSummary(BaseModel):
    capability: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class FailedTaskTerminalPayload(BaseModel):
    schema_version: Literal["devwerk.task-terminal.v1"] = Field(
        default="devwerk.task-terminal.v1", alias="schema"
    )
    project_id: str
    task_id: str
    column_run_id: str | None = None
    terminal: Literal["failed"] = "failed"
    failure_code: str
    error: str
    recorded_at: str
