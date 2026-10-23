"""Fixed web response envelopes for composed Project and runtime views."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.v1.protocols.tools.planning.capability_project_receipts import ProjectRecord, WorkflowRevisionReceipt
from app.v1.protocols.agent.agent_runtime_receipts import SupervisorHealth
from app.v1.protocols.storage.store_receipt_contracts import ConversationMessageRecord
from app.v1.protocols.storage.store_receipt_contracts import AgentMessageRecord, ToolInvocationRecord
from app.v1.protocols.tools.planning.capability_task_receipts import ArtifactProjection, TaskRecord
from app.v1.protocols.agent.run_record_contracts import AgentRunRecord, ColumnAttemptRecord, ColumnRunRecord


class ConversationGatewayHealth(BaseModel):
    status: Literal["running", "stopped"]
    active_sessions: int
    pending_jobs: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    runtime: Literal["devwerk-v1"] = "devwerk-v1"
    supervisor: Literal["running", "stopped"]
    supervisor_health: SupervisorHealth
    conversation_gateway: ConversationGatewayHealth


class ConversationSubmitReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["accepted"] = "accepted"
    job: "ConversationJobRecord"
    message: ConversationMessageRecord
    agent_run: None = None
    workflow: None = None
    tasks: list[JsonValue] = Field(default_factory=list)


class ConversationJobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    project_id: str
    conversation_session_id: str
    user_message_id: int = Field(ge=1)
    message: str
    start_task: bool
    status: str
    task_id: str | None
    agent_run_id: str | None
    error: str | None
    resolved_by_job_id: str | None
    claim_owner: str | None
    claim_token: str | None
    created_at: str
    updated_at: str
    finished_at: str | None
    trigger_kind: str
    trigger: dict[str, JsonValue]
    mailbox_ids: list[int]
    scheduled_review_id: str | None
    result: dict[str, JsonValue]
    requested_mode: str
    user_action_json: str | None
    turn_contract_json: str
    execution_grant_id: str | None
    requirement_id: str | None
    requirement_revision: int | None


class BoardResponse(BaseModel):
    project: ProjectRecord
    workflow: WorkflowRevisionReceipt | None
    tasks: list[TaskRecord]


class TaskDetailResponse(TaskRecord):
    runs: list[ColumnRunRecord] = Field(default_factory=list)
    attempts: list[ColumnAttemptRecord] = Field(default_factory=list)
    artifacts: list[ArtifactProjection] = Field(default_factory=list)
    agent_runs: list[AgentRunRecord] = Field(default_factory=list)


class AgentRunDetailResponse(AgentRunRecord):
    messages: list[AgentMessageRecord] = Field(default_factory=list)
    tool_invocations: list[ToolInvocationRecord] = Field(default_factory=list)
