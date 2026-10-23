"""Bounded, fixed feedback and failure facts for Agent context."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from app.v1.protocols.tools.planning.capability_task_contracts import FeedbackCheckReference
from app.v1.protocols.workflow.runtime_json_contracts import FeedbackVerificationPayload


class FailedAcceptanceCheck(BaseModel):
    check_key: str
    acceptance_result_id: str
    error_type: str = Field(max_length=80)
    error_message: str = Field(max_length=300)


class AgentFailureFact(BaseModel):
    agent_run_id: str
    runtime_error: str = Field(max_length=300)
    completion_rejection: str | None = Field(default=None, max_length=600)


class TaskFailureDetail(BaseModel):
    column_key: str | None
    column_run_id: str | None
    reported_reason: str | None = Field(default=None, max_length=400)
    runtime_error: str = Field(max_length=300)
    completion_rejection: str | None = Field(default=None, max_length=600)


class EmptyVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskFeedbackRecord(BaseModel):
    id: str
    project_id: str
    task_id: str
    dedupe_key: str
    responsible_column: str
    description: str
    checks: list[FeedbackCheckReference]
    source_run_id: str | None
    source_job_id: str | None
    reported_sequence: int
    state: str
    repair_run_id: str | None
    verification: FeedbackVerificationPayload | EmptyVerification | list[str]
    created_at: str
    resolved_at: str | None
    observed_check_rowid: int


class TaskFeedbackList(RootModel[list[TaskFeedbackRecord]]):
    pass


class PendingFeedback(BaseModel):
    feedback_id: str
    state: str
    responsible_column: str
    description: str
    verification_mode: Literal["fixed_checks", "manual_review"]
    verification_checks: list[FeedbackCheckReference]
