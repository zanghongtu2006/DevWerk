"""Bounded Task inspection projections and their successful Tool receipt."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.protocols.tools.planning.capability_contracts import TaskMaterializationReceipt
from app.v1.execution.states import AttemptStatus, ColumnRunStatus, TaskControlState, TaskStatus


class TaskProjection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(description="Task ID")
    project_id: str = Field(description="Owning Project ID")
    workflow_revision_id: str = Field(description="Frozen Workflow revision")
    task_plan_id: str | None = Field(description="Owning Task Plan")
    proposed_task_ref: str | None = Field(description="Reference in the Task Plan")
    logical_task_key: str | None = Field(description="Stable logical Task identity")
    title: str = Field(description="Task title")
    status: TaskStatus = Field(description="Workflow lifecycle status")
    control_state: TaskControlState = Field(description="Human/runtime control state")
    current_column: str = Field(description="Current process Column")
    attempt: int = Field(ge=0, description="Task attempt count")
    error: str | None = Field(description="Last error summary")
    created_at: str = Field(description="Creation timestamp")
    updated_at: str = Field(description="Last update timestamp")
    finished_at: str | None = Field(description="Terminal timestamp")
    state_version: int = Field(ge=0, description="Optimistic state version")
    terminal_artifact_id: str | None = Field(description="Terminal artifact ID")
    terminal_event_id: int | None = Field(description="Terminal event ID")
    notified_at: str | None = Field(description="Notification timestamp")
    observed_at: str | None = Field(description="Observation timestamp")
    supervision_action: str | None = Field(description="Pending supervision action")
    next_retry_at: str | None = Field(description="Scheduled retry timestamp")
    rerun_of_task_id: str | None = Field(description="Predecessor Task ID when rerun")
    resolved_by_task_id: str | None = Field(description="Successor Task ID when superseded")


class TaskRecord(TaskProjection):
    """Complete decoded v1_tasks row returned by Task control capabilities."""

    model_config = ConfigDict(extra="forbid")
    brief: str
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    readiness: dict[str, JsonValue]
    conflict_domains: list[str]
    lease_owner: str | None
    lease_until: str | None
    failure_origin: str | None
    failure_code: str | None
    failure_disposition: str | None
    requirement_id: str | None
    requirement_revision: int | None


class TaskListReceipt(RootModel[list[TaskRecord]]):
    pass


class TaskSuccessorReceipt(TaskRecord):
    materialization: TaskMaterializationReceipt


class ColumnRunProjection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(description="Column Run ID")
    task_id: str = Field(description="Task ID")
    column_key: str = Field(description="Process Column key")
    sequence: int = Field(ge=1, description="Visit sequence")
    status: ColumnRunStatus = Field(description="Column Run status")
    attempt: int = Field(ge=0, description="Attempt count")
    error: str | None = Field(description="Last error")
    error_category: str | None = Field(description="Error category")
    failure_fingerprint: str | None = Field(description="Failure fingerprint")
    started_at: str | None = Field(description="Start timestamp")
    finished_at: str | None = Field(description="Finish timestamp")
    created_at: str = Field(description="Creation timestamp")


class ColumnAttemptProjection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(description="Column Attempt ID")
    task_id: str = Field(description="Task ID")
    column_run_id: str = Field(description="Owning Column Run ID")
    attempt_no: int = Field(ge=1, description="Attempt number")
    status: AttemptStatus = Field(description="Attempt status")
    error: str | None = Field(description="Last error")
    error_category: str | None = Field(description="Error category")
    failure_fingerprint: str | None = Field(description="Failure fingerprint")
    agent_run_id: str | None = Field(description="Executing Agent Run ID")
    started_at: str | None = Field(description="Start timestamp")
    finished_at: str | None = Field(description="Finish timestamp")
    created_at: str = Field(description="Creation timestamp")


class DependencyFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(description="Dependency reference")
    task_ref: str | None = Field(default=None, description="Plan-local Task reference")
    resolved_task_id: str | None = Field(default=None, description="Resolved Task ID")
    required_terminal: Literal["done"] = Field(description="Required predecessor terminal")
    status: TaskStatus | Literal["unmaterialized", "missing"] = Field(description="Latest dependency status")
    satisfied: bool = Field(description="Whether this dependency is satisfied")
    plan_id: str | None = Field(default=None, description="Unmaterialized Plan ID")
    latest_task_id: str | None = Field(default=None, description="Latest materialized Task ID")
    resolved_by_task_id: str | None = Field(default=None, description="Successor Task ID")


class SchedulingProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(description="Task ID")
    project_id: str = Field(description="Owning Project ID")
    state: Literal["admitted", "queued", "hold", "cancelled"] = Field(description="Scheduling state")
    priority: int = Field(description="Relative priority")
    wip_group: str | None = Field(description="WIP group")
    wip_limit: int | None = Field(description="WIP limit")
    auto_admit: bool = Field(description="Whether Runtime may admit when ready")
    created_at: str = Field(description="Creation timestamp")
    updated_at: str = Field(description="Last update timestamp")
    dependencies: list[DependencyFact] = Field(description="Dependency facts")
    resources: list[str] = Field(description="Resource conflict domains")
    dispatch_eligible: bool = Field(description="Whether dispatch currently satisfies scheduling gates")
    pending_reason: str = Field(description="Current scheduling reason")
    blocked_by: list[DependencyFact] = Field(description="Unsatisfied dependencies")


class ArtifactProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Artifact version ID")
    project_id: str = Field(description="Owning Project ID")
    task_id: str | None = Field(description="Owning Task ID")
    run_id: str | None = Field(description="Producing Run ID")
    kind: str = Field(description="Artifact category")
    path: str = Field(description="Project-relative artifact path")
    sha256: str | None = Field(description="Content digest")
    size: int = Field(ge=0, description="Byte count")
    meta: dict[str, JsonValue] = Field(description="Artifact metadata")
    created_at: str = Field(description="Creation timestamp")


class TaskInspectReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: TaskProjection = Field(description="Bounded Task status projection")
    scheduling: SchedulingProjection = Field(description="Scheduling and dependency status")
    runs: list[ColumnRunProjection] = Field(description="Column visit summaries")
    attempts: list[ColumnAttemptProjection] = Field(description="Column attempt summaries")
    artifacts: list[ArtifactProjection] = Field(description="Registered Task artifacts")
