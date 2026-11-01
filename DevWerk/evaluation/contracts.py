"""Versioned input and result contracts for external evaluation runs."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EvaluationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNSCORED = "unscored"
    INCONCLUSIVE = "inconclusive"


class CheckKind(StrEnum):
    FILE_EXISTS = "file_exists"
    FILE_CONTAINS = "file_contains"
    FILE_EQUALS = "file_equals"
    TASK_DONE = "task_done"
    WORKFLOW_CREATED = "workflow_created"
    PYTEST_PASS = "pytest_pass"
    MANUAL_REVIEW = "manual_review"


class CaseTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=30_000)
    wait_for_tasks: bool = False
    wait_for_tasks_if_created: bool = False


class CaseCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CheckKind
    path: str | None = None
    text: str | None = None
    grader: str | None = None

    @field_validator("path", "grader")
    @classmethod
    def safe_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in path.parts or "\\" in value or ":" in value or not value.strip():
            raise ValueError("check path must stay inside the project")
        return value


class SourceFile(BaseModel):
    """Pinned source material copied into a benchmark Project without JSON encoding."""

    model_config = ConfigDict(extra="forbid")
    relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in path.parts
                or "\\" in value or ":" in value or not value.strip()):
            raise ValueError("source path must stay inside the benchmark task folder")
        return value


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    title: str
    source: str = "devwerk-local"
    source_revision: str = "1"
    focus: Literal["independent", "orchestration", "delivery"]
    turns: list[CaseTurn] = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    source_root: str | None = None
    source_files: list[SourceFile] = Field(default_factory=list)
    checks: list[CaseCheck] = Field(default_factory=list)
    jobbench_task_id: str | None = None
    jobbench_rubric_sha256: str | None = None

    @field_validator("files")
    @classmethod
    def safe_files(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            path = PurePosixPath(name)
            if path.is_absolute() or PureWindowsPath(name).is_absolute() or ".." in path.parts or "\\" in name or ":" in name or not name.strip():
                raise ValueError("case files must stay inside the project")
        return value


class TurnEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    status: str
    error: str | None = None
    agent_run_id: str | None = None
    user_message_id: int
    created_at: str | None = None
    finished_at: str | None = None


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CheckKind
    status: EvaluationStatus
    evidence: str


class RoleMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runs: int = 0
    iterations: int = 0
    tool_calls: int = 0
    rejected_tools: int = 0
    first_tool_accepted: int = 0
    first_tool_rejected: int = 0
    request_input_tokens: int | None = None
    request_output_tokens: int | None = None


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    case_revision: str
    case_sha256: str | None = None
    project_id: str
    status: EvaluationStatus = EvaluationStatus.INCONCLUSIVE
    turns: list[TurnEvidence] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    conversation: RoleMetrics = Field(default_factory=RoleMetrics)
    column: RoleMetrics = Field(default_factory=RoleMetrics)
    workflow_created: bool | None = None
    task_count: int | None = None
    done_count: int | None = None
    error: str | None = None
    service_revision: str | None = None
    model_profile: str | None = None
    started_at: str
    finished_at: str | None = None


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: EvaluationCase
    project_id: str
    pending_job_id: str | None = None
    waiting_for_tasks: bool = False
    stop_reason: str | None = None
    next_turn: int = Field(default=0, ge=0)
    turns: list[TurnEvidence] = Field(default_factory=list)
    service_revision: str | None = None
    model_profile: str | None = None
    started_at: str

