"""Decoded Runtime rows exposed through Project HTTP endpoints."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.v1.execution.states import AgentRunStatus, AttemptStatus, ColumnRunStatus


class ColumnRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    project_id: str
    task_id: str
    column_key: str
    sequence: int = Field(ge=1)
    status: ColumnRunStatus
    attempt: int = Field(ge=0)
    input: dict[str, JsonValue]
    output: dict[str, JsonValue]
    agent_run_id: str | None
    error: str | None
    failure_fingerprint: str | None
    heartbeat_at: str | None
    last_progress_at: str | None
    started_at: str | None
    finished_at: str | None
    created_at: str
    error_category: str | None
    runtime_policy_revision: int | None
    runtime_policy_hash: str | None


class ColumnAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    project_id: str
    task_id: str
    column_run_id: str
    attempt_no: int = Field(ge=1)
    status: AttemptStatus
    input: dict[str, JsonValue]
    output: dict[str, JsonValue]
    checkpoint: dict[str, JsonValue]
    error: str | None
    error_category: str | None
    failure_fingerprint: str | None
    runtime_policy_revision: int
    runtime_policy_hash: str
    started_at: str
    finished_at: str | None
    created_at: str
    error_code: str | None
    agent_run_id: str | None
    partial_artifacts_json: str


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    project_id: str
    task_id: str | None
    column_run_id: str | None
    conversation_job_id: str | None
    kind: str
    status: AgentRunStatus
    instruction_revision: int = Field(ge=1)
    instruction_snapshot: str
    context: dict[str, JsonValue]
    capabilities: list[str]
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    final_text: str
    error: str | None
    error_category: str | None
    created_at: str
    started_at: str
    finished_at: str | None
    platform_policy_revision: int | None
    platform_policy_hash: str | None
    runtime_policy_revision: int | None
    runtime_policy_hash: str | None
    checkpoint: dict[str, JsonValue]
    agent_session_id: str | None
    error_code: str | None
    duration_seconds: float | None
    column_attempt_id: str | None
    operation_protocol: int = Field(ge=0)
    assignment_id: str | None
    agent_instance_id: str | None
