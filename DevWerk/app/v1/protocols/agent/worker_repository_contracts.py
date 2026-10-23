"""Fixed Worker, Assignment, and archived-context repository views."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.protocols.workflow.runtime_json_contracts import WorkerMessage


class ConsumedWorkerMessages(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: list[WorkerMessage]
    source_message_id: int = Field(ge=1)


class WorkerRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    project_id: str
    requirement_id: str | None
    parent_id: str | None
    role: str
    reuse_key: str
    lifecycle: str
    active_session_id: str | None
    active_assignment_id: str | None
    created_at: str
    updated_at: str


class WorkerListReceipt(RootModel[list[WorkerRecord]]):
    pass


class WorkerInputRecord(BaseModel):
    id: str
    project_id: str
    recipient_agent_id: str
    assignment_id: str | None
    payload_json: str
    dedupe_key: str | None
    state: str
    created_at: str
    consumed_by_run_id: str | None
    acknowledged_at: str | None
    failed_at: str | None
    last_error: str | None


class WorkerAssignmentRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    project_id: str
    requirement_id: str
    requirement_revision: int
    task_id: str
    column_run_id: str
    agent_instance_id: str
    session_id: str
    generation: int
    task_generation: int
    lease_owner: str | None
    status: str
    input: JsonValue
    contract: JsonValue
    result: JsonValue
    model_calls: int
    tool_calls: int
    resumes: int
    no_progress_awaits: int
    progress_marker: str
    budget: JsonValue
    created_at: str
    updated_at: str


class LegacyMailboxInput(BaseModel):
    id: int
    state: str
    payload_json: str
    assignment_id: str | None
    consumed_by_run_id: str | None


class WorkerInspectReceipt(BaseModel):
    worker: WorkerRecord
    assignments: list[WorkerAssignmentRecord]
    messages: list[WorkerInputRecord]
    legacy_mailbox_inputs_read_only: list[LegacyMailboxInput]


class WorkerReplaceReceipt(BaseModel):
    worker: WorkerRecord
    predecessor_worker_id: str
    worker_key: str
    snapshot_id: str


class WorkerSessionHandoff(BaseModel):
    id: str
    revision: int = Field(ge=1)
    through_message_id: int = Field(ge=0)
    summary: JsonValue
    instance_id: str
    requirement_id: str | None


class WorkerContextSourcePage(BaseModel):
    source_kind: str
    source_id: int | str
    sha256: str
    offset: int = Field(ge=0)
    content: str
    next_offset: int | None = None
    complete: bool
    total_characters: int = Field(ge=0)


class WorkerContextMessagePage(BaseModel):
    id: int
    project_id: str
    agent_run_id: str
    sequence: int
    role: str
    content: str
    tool_calls: JsonValue
    tool_call_id: str | None
    created_at: str
    source_message_id: int


class WorkerContextReadReceipt(RootModel[list[WorkerContextSourcePage | WorkerContextMessagePage]]):
    pass


class WorkerEvidencePage(BaseModel):
    entries: list[JsonValue]
    next_cursor: int | None
    has_more: bool


class WorkerSnapshotReceipt(BaseModel):
    snapshot_id: str
    revision: int = Field(ge=1)
    through_message_id: int = Field(ge=0)
