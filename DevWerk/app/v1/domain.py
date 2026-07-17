from __future__ import annotations

from enum import Enum
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


KEY_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"


class StringEnum(str, Enum):
    """Python 3.10-compatible string enum."""

    def __str__(self) -> str:
        return self.value


class TaskStatus(StringEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    RECOVERING = "recovering"
    DONE = "done"
    FAILED = "failed"


class RunStatus(StringEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(default="success", pattern=KEY_PATTERN)
    target: str = Field(pattern=KEY_PATTERN)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(default=3, ge=1, le=20)
    backoff_seconds: float = Field(default=0, ge=0, le=3600)
    retryable_errors: list[str] = Field(default_factory=lambda: ["provider_transient", "tool_transient"])
    repeated_failure_limit: int = Field(default=2, ge=1, le=20)


class WaitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    waiting_kind: Literal["external", "dependency", "timer", "input", "human"] = "external"
    timeout_seconds: int = Field(default=900, ge=10, le=86_400)
    soft_deadline_seconds: int = Field(default=300, ge=5, le=86_400)
    heartbeat_seconds: int = Field(default=30, ge=5, le=3600)
    stale_after_seconds: int = Field(default=180, ge=10, le=86_400)
    poll_capability: str | None = Field(default=None, pattern=KEY_PATTERN)
    poll_arguments: dict[str, Any] = Field(default_factory=dict)
    success_outcome: str = Field(default="success", pattern=KEY_PATTERN)
    timeout_outcome: str = Field(default="failure", pattern=KEY_PATTERN)
    resume_condition: dict[str, Any] = Field(default_factory=lambda: {"status_in": ["succeeded", "done", "complete"]})
    cancel_capability: str | None = Field(default=None, pattern=KEY_PATTERN)
    cancel_arguments: dict[str, Any] = Field(default_factory=dict)
    cleanup_capability: str | None = Field(default=None, pattern=KEY_PATTERN)
    cleanup_arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_intervals(self) -> "WaitPolicy":
        if self.stale_after_seconds <= self.heartbeat_seconds:
            raise ValueError("stale_after_seconds must exceed heartbeat_seconds")
        return self


class ContextSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_project: bool = True
    include_task: bool = True
    upstream_outputs: list[str] = Field(default_factory=list)
    artifact_globs: list[str] = Field(default_factory=list)
    max_chars: int = Field(default=60_000, ge=1_000, le=500_000)


class CapabilityStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(pattern=KEY_PATTERN)
    arguments: dict[str, Any] = Field(default_factory=dict)
    save_as: str | None = Field(default=None, pattern=KEY_PATTERN)
    continue_on_error: bool = False


class AgentExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["agent"] = "agent"
    capabilities: list[str] = Field(min_length=1)
    max_iterations: int = Field(default=12, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=1, le=500)


class CapabilitySequenceExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["capability_sequence"] = "capability_sequence"
    steps: list[CapabilityStep] = Field(min_length=1, max_length=200)
    completed_outcome: str | None = Field(default="success", pattern=KEY_PATTERN)
    outcome_from: str | None = Field(default=None, pattern=r"^/.*")

    @model_validator(mode="after")
    def select_outcome_source(self) -> "CapabilitySequenceExecutor":
        if (self.completed_outcome is None) == (self.outcome_from is None):
            raise ValueError("capability_sequence requires exactly one of completed_outcome or outcome_from")
        return self


ColumnExecutor = Annotated[
    AgentExecutor | CapabilitySequenceExecutor,
    Field(discriminator="kind"),
]


class RuntimeOutcomes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_missing: str = Field(default="failure", pattern=KEY_PATTERN)
    execution_failed: str = Field(default="failure", pattern=KEY_PATTERN)
    interrupted: str = Field(default="failure", pattern=KEY_PATTERN)
    retry_exhausted: str = Field(default="failure", pattern=KEY_PATTERN)
    max_visits_exceeded: str = Field(default="failure", pattern=KEY_PATTERN)


class ColumnDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    instruction: str = Field(default="", max_length=60_000)
    executor: ColumnExecutor | None = None
    context: ContextSelection = Field(default_factory=ContextSelection)
    input_contract: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)
    runtime_outcomes: RuntimeOutcomes = Field(default_factory=RuntimeOutcomes)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    wait_policy: WaitPolicy = Field(default_factory=WaitPolicy)
    max_visits: int = Field(default=100, ge=1, le=10_000)
    terminal: Literal["done", "failed"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution(self) -> "ColumnDefinition":
        if self.terminal:
            if self.executor is not None:
                raise ValueError(f"terminal column {self.key!r} must not define an executor")
            if self.transitions:
                raise ValueError(f"terminal column {self.key!r} must not define transitions")
        else:
            if self.executor is None:
                raise ValueError(f"non-terminal column {self.key!r} requires an executor")
            if not self.transitions:
                raise ValueError(f"non-terminal column {self.key!r} has no transition")
        return self


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    entry: str = Field(pattern=KEY_PATTERN)
    columns: list[ColumnDefinition] = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("workflow column keys must be unique")
        known = set(keys)
        if self.entry not in known:
            raise ValueError("workflow entry must reference a column")

        terminals = [column for column in self.columns if column.terminal]
        terminal_kinds = [column.terminal for column in terminals]
        if terminal_kinds.count("done") != 1 or terminal_kinds.count("failed") != 1:
            raise ValueError("workflow must define exactly one done and one failed terminal column")

        for column in self.columns:
            unknown_upstream = sorted(set(column.context.upstream_outputs) - known)
            if unknown_upstream:
                raise ValueError(f"column {column.key!r} references unknown upstream columns: {unknown_upstream}")
            outcomes: set[str] = set()
            for transition in column.transitions:
                if transition.outcome in outcomes:
                    raise ValueError(f"duplicate outcome {transition.outcome!r} in {column.key}")
                outcomes.add(transition.outcome)
                if transition.target not in known:
                    raise ValueError(f"unknown transition target {transition.target!r}")
            if isinstance(column.executor, CapabilitySequenceExecutor):
                declared = {item.outcome for item in column.transitions}
                if column.executor.completed_outcome and column.executor.completed_outcome not in declared:
                    raise ValueError(f"column {column.key!r} does not declare its sequence completed outcome")
            if not column.terminal:
                declared = {item.outcome for item in column.transitions}
                runtime_outcomes = set(column.runtime_outcomes.model_dump().values())
                if not runtime_outcomes.issubset(declared):
                    raise ValueError(f"column {column.key!r} runtime outcomes must be declared transitions")
                if column.wait_policy.success_outcome not in declared or column.wait_policy.timeout_outcome not in declared:
                    raise ValueError(f"column {column.key!r} wait outcomes must be declared transitions")

        reachable: set[str] = set()
        pending = [self.entry]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            pending.extend(item.target for item in self.column(key).transitions)
        unreachable = known - reachable
        if unreachable:
            raise ValueError(f"workflow contains unreachable columns: {sorted(unreachable)}")

        terminal_keys = {column.key for column in terminals}
        for column in self.columns:
            if column.terminal:
                continue
            seen: set[str] = set()
            frontier = [column.key]
            while frontier and not (seen & terminal_keys):
                key = frontier.pop()
                if key in seen:
                    continue
                seen.add(key)
                frontier.extend(item.target for item in self.column(key).transitions)
            if not (seen & terminal_keys):
                raise ValueError(f"column {column.key!r} has no path to a terminal column")
        return self

    def column(self, key: str) -> ColumnDefinition:
        for column in self.columns:
            if column.key == key:
                return column
        raise KeyError(key)

    def terminal_key(self, kind: Literal["done", "failed"]) -> str:
        return next(column.key for column in self.columns if column.terminal == kind)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    base_dir: str = Field(min_length=1)
    agent_instruction: str = Field(default="", max_length=60_000)


class ConversationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=30_000)
    start_task: bool = True


class WorkflowPublishRequest(BaseModel):
    workflow: WorkflowDefinition


class ReadinessDecision(BaseModel):
    decision: Literal["dispatch", "queue", "hold", "merge", "split", "cancel"]
    objective: str = Field(min_length=1, max_length=4000)
    scope: list[str] = Field(default_factory=list, max_length=200)
    non_scope: list[str] = Field(default_factory=list, max_length=200)
    deliverables: list[str] = Field(min_length=1, max_length=200)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=200)
    dependencies_checked: bool
    resource_conflicts: list[str] = Field(default_factory=list, max_length=200)
    risks: list[str] = Field(default_factory=list, max_length=200)
    reason_summary: str = Field(min_length=1, max_length=4000)
    next_review_at: str | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=30_000)
    input: dict[str, Any] = Field(default_factory=dict)
    readiness: ReadinessDecision
    pending_timeout_seconds: int = Field(default=86_400, ge=60, le=2_592_000)

    @model_validator(mode="after")
    def require_dispatch(self) -> "TaskCreate":
        if self.readiness.decision != "dispatch":
            raise ValueError("a Task may be created only from a dispatch readiness decision")
        return self


class AgentToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentModelResponse(BaseModel):
    text: str = ""
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    capability: str
    output: Any = None
    error: dict[str, Any] | None = None
