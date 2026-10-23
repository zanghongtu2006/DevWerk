"""Fixed scheduler startup, decision, and dependency context receipts."""

from typing import Literal

from pydantic import BaseModel, Field


class SchedulerStartupReceipt(BaseModel):
    auto_resume_previous_tasks: bool
    paused_tasks: int = Field(ge=0)
    dependency_tasks_released: int = Field(ge=0)
    projects: list[str]


class SchedulingDecisionReceipt(BaseModel):
    project_id: str
    task_id: str
    state: Literal["admitted", "queued", "hold", "cancelled"]
    priority: int
    wip_group: str | None
    wip_limit: int | None
    dependencies: list[str]
    resources: list[str]
    updated_at: str


class DependencyTerminalArtifact(BaseModel):
    id: str
    kind: str
    path: str
    sha256: str | None
    size: int = Field(ge=0)


class DependencyContextRecord(BaseModel):
    task_id: str
    task_ref: str | None
    title: str
    status: str
    finished_at: str | None
    terminal_artifact: DependencyTerminalArtifact | None
