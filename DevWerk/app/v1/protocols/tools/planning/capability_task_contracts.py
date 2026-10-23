"""Inputs for Task control, feedback, backlog, and scheduling capabilities."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskCapabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskIdInput(TaskCapabilityInput):
    task_id: str = Field(description="Existing Task ID in the current Project")


class TaskListInput(TaskCapabilityInput):
    limit: int = Field(default=None, ge=1, le=200, description="Maximum Tasks to return")


class TaskRetryInput(TaskIdInput):
    column_key: str = Field(default=None, description="Permitted recovery Column; omit for pinned position")
    clear_context: bool = Field(default=None, description="Reset the selected Worker context when restarting")


class TaskFailureInput(TaskIdInput):
    reason: str = Field(min_length=1, max_length=4000, description="Reason recorded with the failed terminal")


class TaskSuccessorInput(TaskIdInput):
    task_plan_id: str = Field(description="Corrected immutable Task Plan ID")


class FeedbackCheckReference(TaskCapabilityInput):
    column: str = Field(description="Column containing the frozen check")
    check_key: str = Field(description="Check key in that Column")


class FeedbackRecordInput(TaskIdInput):
    dedupe_key: str = Field(min_length=1, max_length=200, description="Idempotency key for this defect")
    responsible_column: str = Field(description="Column responsible for the repair")
    description: str = Field(min_length=1, max_length=12000, description="Concrete observed defect")
    checks: list[FeedbackCheckReference] = Field(default=None, max_length=30,
                                                  description="Optional frozen check references")


class FeedbackResolveInput(TaskIdInput):
    feedback_id: str = Field(description="Existing feedback ID")
    verification_note: str = Field(min_length=1, max_length=12000,
                                   description="Concrete verification of a later repair visit")


class BacklogReadiness(BaseModel):
    model_config = ConfigDict(extra="allow")
    decision: Literal["queue", "hold", "merge", "split", "cancel"] = Field(description="Backlog disposition")
    objective: str = Field(description="Work objective")
    deliverables: list[str] = Field(description="Expected deliverables")
    acceptance_criteria: list[str] = Field(description="Planning criteria for this backlog item")
    dependencies_checked: bool = Field(description="Whether dependencies were reviewed")
    reason_summary: str = Field(description="Reason for this disposition")


class BacklogRecordInput(TaskCapabilityInput):
    title: str = Field(min_length=1, max_length=200, description="Backlog item title")
    brief: str = Field(max_length=30000, description="Work brief")
    readiness: BacklogReadiness = Field(description="Declared readiness and disposition")


class SchedulingDecisionInput(TaskIdInput):
    state: Literal["admitted", "queued", "hold", "cancelled"] = Field(description="Requested scheduling state")
    priority: int = Field(default=None, ge=-1000, le=1000, description="Relative scheduling priority")
    wip_group: str = Field(default=None, min_length=1, max_length=200, description="WIP group assertion")
    wip_limit: int = Field(default=None, ge=1, le=100, description="WIP limit assertion")
    dependencies: list[str] = Field(default=None, max_length=200, description="Exact dependency assertion")
    resources: list[str] = Field(default=None, max_length=200, description="Exact resource assertion")
