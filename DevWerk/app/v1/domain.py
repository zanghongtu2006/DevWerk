from __future__ import annotations

from datetime import datetime
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.v1.states import (
    AgentRunStatus,
    AttemptStatus,
    ColumnRunStatus,
    TaskStatus,
    ToolInvocationStatus,
)

KEY_PATTERN = r"^[a-z][a-z0-9_.-]{0,127}$"


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(default="success", pattern=KEY_PATTERN)
    target: str = Field(pattern=KEY_PATTERN)


class WaitPolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success_outcome: str = Field(default="success", pattern=KEY_PATTERN)


class PollWaitPolicy(WaitPolicyBase):
    kind: Literal["poll"] = "poll"
    poll_capability: str = Field(pattern=KEY_PATTERN)
    poll_arguments: dict[str, Any] = Field(default_factory=dict)
    poll_interval_seconds: int = Field(default=30, ge=1)
    resume_condition: dict[str, Any] = Field(default_factory=lambda: {"status_in": ["succeeded", "done", "complete"]})
    cancel_capability: str | None = Field(default=None, pattern=KEY_PATTERN)
    cancel_arguments: dict[str, Any] = Field(default_factory=dict)
    cleanup_capability: str | None = Field(default=None, pattern=KEY_PATTERN)
    cleanup_arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=500)

class EventWaitPolicy(WaitPolicyBase):
    kind: Literal["event"] = "event"
    event_type: str = Field(min_length=1, max_length=200)
    correlation_key: str = Field(min_length=1, max_length=500)
    check_interval_seconds: int = Field(ge=1)


class TimerWaitPolicy(WaitPolicyBase):
    kind: Literal["timer"] = "timer"
    resume_at: str | None = None
    delay_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def select_timer(self) -> "TimerWaitPolicy":
        if (self.resume_at is None) == (self.delay_seconds is None):
            raise ValueError("timer wait requires exactly one of resume_at or delay_seconds")
        if self.resume_at is not None:
            parsed = datetime.fromisoformat(self.resume_at)
            if parsed.tzinfo is None:
                raise ValueError("timer resume_at must include a timezone")
        return self


WaitPolicy = Annotated[
    PollWaitPolicy | EventWaitPolicy | TimerWaitPolicy,
    Field(discriminator="kind"),
]


class ContextSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_project: bool = True
    include_task: bool = True
    upstream_outputs: list[str] = Field(default_factory=list)
    artifact_globs: list[str] = Field(default_factory=list)


class CapabilityStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(pattern=KEY_PATTERN)
    arguments: dict[str, Any] = Field(default_factory=dict)
    save_as: str | None = Field(default=None, pattern=KEY_PATTERN)


class AgentExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["agent"] = "agent"
    capabilities: list[str] = Field(min_length=1)


class CapabilitySequenceExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["capability_sequence"] = "capability_sequence"
    steps: list[CapabilityStep] = Field(min_length=1, max_length=200)
    completed_outcome: str | None = Field(
        default=None,
        pattern=KEY_PATTERN,
        description=(
            "Optional fixed outcome after all steps complete. Omit this field when outcome_from "
            "is supplied. When both fields are omitted, DevWerk normalizes this value to success."
        ),
    )
    outcome_from: str | None = Field(
        default=None,
        pattern=r"^/.*",
        description=(
            "Optional absolute JSON Pointer selecting an outcome from saved step evidence. "
            "Supply this field without completed_outcome; omit the other field rather than sending null."
        ),
    )

    @model_validator(mode="after")
    def select_outcome_source(self) -> "CapabilitySequenceExecutor":
        if self.completed_outcome is None and self.outcome_from is None:
            self.completed_outcome = "success"
        elif self.completed_outcome is not None and self.outcome_from is not None:
            raise ValueError("capability_sequence requires exactly one of completed_outcome or outcome_from")
        return self


ColumnExecutor = Annotated[
    AgentExecutor | CapabilitySequenceExecutor,
    Field(discriminator="kind"),
]


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
    wait_policy: WaitPolicy | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_execution(self) -> "ColumnDefinition":
        if self.executor is None:
            raise ValueError(f"column {self.key!r} requires an executor")
        if not self.transitions:
            raise ValueError(f"column {self.key!r} has no transition")
        if isinstance(self.executor, CapabilitySequenceExecutor) and self.output_contract:
            schema_type = self.output_contract.get("type")
            if schema_type not in {None, "object"}:
                raise ValueError(
                    f"column {self.key!r} capability_sequence output_contract must accept "
                    "the Runtime object envelope"
                )
            runtime_fields = {"summary", "steps"}
            required = set(self.output_contract.get("required") or [])
            unsupported_required = sorted(required - runtime_fields)
            if unsupported_required:
                raise ValueError(
                    f"column {self.key!r} capability_sequence output_contract requires "
                    f"fields the Runtime does not produce: {unsupported_required}"
                )
            if self.output_contract.get("additionalProperties") is False:
                declared = set((self.output_contract.get("properties") or {}).keys())
                missing = sorted(runtime_fields - declared)
                if missing:
                    raise ValueError(
                        f"column {self.key!r} capability_sequence output_contract rejects "
                        f"Runtime envelope fields: {missing}"
                    )
        required_roots = self.input_contract.get("required")
        if isinstance(required_roots, list):
            available_roots = {"column", "planning"}
            if self.context.include_project:
                available_roots.add("project")
            if self.context.include_task:
                available_roots.add("task")
            if self.context.upstream_outputs:
                available_roots.add("upstream_outputs")
            if self.context.artifact_globs:
                available_roots.add("artifacts")
            impossible = sorted(
                str(item)
                for item in required_roots
                if str(item) not in available_roots
            )
            if impossible:
                raise ValueError(
                    f"column {self.key!r} input_contract requires unavailable Runtime "
                    f"root keys {impossible}; available roots are {sorted(available_roots)} "
                    "and Task-owned values are nested below task.input"
                )
        return self


class TerminalSentinels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: Literal["done"] = "done"
    failure: Literal["failed"] = "failed"


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["devwerk.workflow.v1"] = "devwerk.workflow.v1"
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    entry: str = Field(pattern=KEY_PATTERN)
    terminals: TerminalSentinels = Field(default_factory=TerminalSentinels)
    columns: list[ColumnDefinition] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("workflow column keys must be unique")
        known = set(keys)
        if self.entry not in known:
            raise ValueError("workflow entry must reference a column")

        terminal_keys = {self.terminals.success, self.terminals.failure}
        if known & terminal_keys:
            raise ValueError("done and failed are reserved terminal sentinel keys")

        for column in self.columns:
            unknown_upstream = sorted(set(column.context.upstream_outputs) - known)
            if unknown_upstream:
                raise ValueError(f"column {column.key!r} references unknown upstream columns: {unknown_upstream}")
            outcomes: set[str] = set()
            for transition in column.transitions:
                if transition.outcome in outcomes:
                    raise ValueError(f"duplicate outcome {transition.outcome!r} in {column.key}")
                outcomes.add(transition.outcome)
                if transition.target not in known | terminal_keys:
                    raise ValueError(f"unknown transition target {transition.target!r}")
            declared = {item.outcome for item in column.transitions}
            if isinstance(column.executor, CapabilitySequenceExecutor):
                if column.executor.completed_outcome and column.executor.completed_outcome not in declared:
                    raise ValueError(f"column {column.key!r} does not declare its sequence completed outcome")
            transition_targets = {item.outcome: item.target for item in column.transitions}
            if column.wait_policy and column.wait_policy.success_outcome not in declared:
                raise ValueError(f"column {column.key!r} wait success outcome must be a declared transition")

        reachable: set[str] = set()
        pending = [self.entry]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            if key in terminal_keys:
                continue
            pending.extend(item.target for item in self.column(key).transitions)
        unreachable = known - reachable
        if unreachable:
            raise ValueError(f"workflow contains unreachable columns: {sorted(unreachable)}")

        for column in self.columns:
            seen: set[str] = set()
            frontier = [column.key]
            while frontier and not (seen & terminal_keys):
                key = frontier.pop()
                if key in seen:
                    continue
                seen.add(key)
                if key in terminal_keys:
                    continue
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
        return self.terminals.success if kind == "done" else self.terminals.failure

    def terminal_kind(self, key: str) -> Literal["done", "failed"] | None:
        if key == self.terminals.success:
            return "done"
        if key == self.terminals.failure:
            return "failed"
        return None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    base_dir: str = Field(min_length=1)
    agent_instruction: str = Field(default="", max_length=60_000)


class ConversationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=30_000)
    start_task: bool = True


class ExternalEventSignal(BaseModel):
    event_type: str = Field(min_length=1, max_length=200)
    correlation_key: str = Field(min_length=1, max_length=500)
    output: dict[str, Any] = Field(default_factory=dict)


class WorkflowColumnPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=KEY_PATTERN)
    responsibility: str = Field(min_length=1, max_length=4_000)
    execution_mode: Literal["agent", "capability_sequence"] = Field(
        description=(
            "How this reusable process stage executes when a Task enters it: create one "
            "ephemeral Agent or run a deterministic capability sequence."
        ),
    )
    entry_evidence: list[str] = Field(min_length=1, max_length=200)
    exit_evidence: list[str] = Field(min_length=1, max_length=200)
    context_boundary: str = Field(min_length=1, max_length=4_000)
    review_or_rework_role: str = Field(min_length=1, max_length=4_000)


class ConflictDomain(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["workspace_path", "database", "process_environment", "external_resource", "logical"]
    identity: str = Field(min_length=1, max_length=1_000)

    @property
    def canonical_key(self) -> str:
        identity = self.identity.strip().replace("\\", "/")
        if self.kind == "workspace_path":
            parts = [part for part in identity.split("/") if part not in {"", "."}]
            if not parts or identity.startswith("/") or any(part == ".." for part in parts):
                raise ValueError("workspace_path conflict domains must be stable Project-relative paths")
            identity = "/".join(parts).casefold()
        else:
            identity = identity.casefold()
        return f"{self.kind}:{identity}"


class ReadinessDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["dispatch", "queue", "hold", "merge", "split", "cancel"]
    objective: str = Field(min_length=1, max_length=4000)
    scope: list[str] = Field(default_factory=list, max_length=200)
    non_scope: list[str] = Field(default_factory=list, max_length=200)
    deliverables: list[str] = Field(min_length=1, max_length=200)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=200)
    dependencies_checked: bool
    dependencies: list[str] = Field(default_factory=list, max_length=200)
    conflict_domains: list[ConflictDomain] = Field(default_factory=list, max_length=200)
    risks: list[str] = Field(default_factory=list, max_length=200)
    reason_summary: str = Field(min_length=1, max_length=4000)
    next_review_at: str | None = None


class TaskPlanReadiness(BaseModel):
    """Readiness facts authored once in a Task Plan and materialized into a Task.

    ``queue`` is an automatically managed dependency/WIP queue. Use an explicit
    scheduling ``hold`` after materialization when human intervention is required.
    """

    model_config = ConfigDict(extra="forbid")
    decision: Literal["dispatch", "queue"] = Field(
        description=(
            "dispatch marks work eligible immediately; queue marks planned work that DevWerk "
            "automatically admits when its dependencies and WIP constraints allow."
        ),
    )
    scope: list[str] = Field(default_factory=list, max_length=200)
    non_scope: list[str] = Field(default_factory=list, max_length=200)
    deliverables: list[str] = Field(min_length=1, max_length=200)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=200)
    dependencies_checked: bool
    risks: list[str] = Field(default_factory=list, max_length=200)
    reason_summary: str = Field(min_length=1, max_length=4_000)
    next_review_at: str | None = None


class ExactTaskInputString(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pointer: str = Field(
        pattern=r"^/.*",
        max_length=2_000,
        description=(
            "JSON Pointer to a string inside Task input. Prefer the Task-input-relative form "
            "/contract/content; the full runtime form /input/task/input/contract/content is "
            "accepted and normalized to the same pointer."
        ),
    )
    escaped_value: str = Field(
        max_length=100_000,
        description=(
            "Exact Unicode string in a single Provider-safe field. Backslash escapes are decoded "
            "once by DevWerk: use \\n for LF, \\r for CR, \\t for tab, \\u0020 for a significant "
            "space, and \\\\ for a literal backslash. Only string-valued deterministic Task-input "
            "references belong here; numbers and booleans remain ordinary Task input."
        ),
    )

    @field_validator("pointer", mode="before")
    @classmethod
    def normalize_runtime_pointer(cls, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("/input/task/input/"):
            return value[len("/input/task/input"):]
        return value

    @model_validator(mode="after")
    def validate_pointer(self) -> "ExactTaskInputString":
        if any(character in self.pointer for character in {"\r", "\n", "\t"}):
            raise ValueError("exact input pointer cannot contain control characters")
        index = 0
        while index < len(self.pointer):
            if self.pointer[index] != "~":
                index += 1
                continue
            if index + 1 >= len(self.pointer) or self.pointer[index + 1] not in {"0", "1"}:
                raise ValueError("exact input pointer contains an invalid JSON Pointer escape")
            index += 2
        _decode_exact_escaped_value(self.escaped_value)
        return self

    @property
    def value(self) -> str:
        return _decode_exact_escaped_value(self.escaped_value)


def _decode_exact_escaped_value(value: str) -> str:
    """Decode a small JSON-compatible escape alphabet without touching Unicode text."""
    decoded: list[str] = []
    simple = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(value):
            raise ValueError("exact input escaped_value ends with an incomplete escape")
        marker = value[index + 1]
        if marker in simple:
            decoded.append(simple[marker])
            index += 2
            continue
        if marker != "u" or index + 6 > len(value):
            raise ValueError(f"exact input escaped_value contains unsupported escape: \\{marker}")
        digits = value[index + 2:index + 6]
        try:
            codepoint = int(digits, 16)
        except ValueError as exc:
            raise ValueError(f"exact input escaped_value contains invalid Unicode escape: \\u{digits}") from exc
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("exact input escaped_value cannot contain an isolated UTF-16 surrogate")
        decoded.append(chr(codepoint))
        index += 6
    return "".join(decoded)


class TaskPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposed_task_ref: str = Field(pattern=KEY_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default="", max_length=30_000)
    input: dict[str, Any] = Field(default_factory=dict)
    objective: str = Field(min_length=1, max_length=4_000)
    workflow_fit: str = Field(min_length=1, max_length=4_000)
    agent_execution: Literal["forbidden", "required", "allowed"] = Field(
        description=(
            "Executable Task policy for ephemeral Agent use. forbidden means zero Task-associated "
            "Agent Runs; required means done requires at least one; allowed permits either."
        ),
    )
    dependencies: list[str] = Field(default_factory=list, max_length=200)
    conflict_domains: list[ConflictDomain] = Field(default_factory=list, max_length=200)
    exact_input_strings: list[ExactTaskInputString] = Field(
        default_factory=list,
        max_length=10_000,
        description=(
            "Authoritative exact string values for deterministic Task-input references. "
            "Declare each value with one escaped_value string; Task creation decodes and "
            "materializes it instead of trusting a lossy Provider copy."
        ),
    )
    review_scope: str = Field(min_length=1, max_length=4_000)
    retry_scope: str = Field(min_length=1, max_length=4_000)
    readiness: TaskPlanReadiness

    @model_validator(mode="after")
    def require_unique_exact_input_pointers(self) -> "TaskPlanItem":
        pointers = [item.pointer for item in self.exact_input_strings]
        if len(pointers) != len(set(pointers)):
            raise ValueError("exact_input_strings pointers must be unique within a planned Task")
        return self

    def validate_agent_execution_workflow(self, workflow: WorkflowDefinition) -> None:
        agent_columns = [
            column.key
            for column in workflow.columns
            if isinstance(column.executor, AgentExecutor)
        ]
        if self.agent_execution == "forbidden" and agent_columns:
            raise ValueError(
                f"Task {self.proposed_task_ref!r} forbids Task Agent Runs but Workflow "
                f"contains Agent executor columns: {agent_columns}"
            )
        if self.agent_execution == "required" and not agent_columns:
            raise ValueError(
                f"Task {self.proposed_task_ref!r} requires a Task Agent Run but Workflow "
                "contains no Agent executor column"
            )

    def validate_exact_input_workflow(self, workflow: WorkflowDefinition) -> None:
        """Require deterministic Tasks to bind every declared exact string by reference."""
        if self.agent_execution != "forbidden" or not self.exact_input_strings:
            return
        referenced: set[str] = set()
        for column in workflow.columns:
            if not isinstance(column.executor, CapabilitySequenceExecutor):
                continue
            for step in column.executor.steps:
                referenced.update(_workflow_task_input_references(step.arguments))
            if isinstance(column.wait_policy, PollWaitPolicy):
                for arguments in (
                    column.wait_policy.poll_arguments,
                    column.wait_policy.cancel_arguments,
                    column.wait_policy.cleanup_arguments,
                ):
                    referenced.update(_workflow_task_input_references(arguments))
        required = {
            f"/input/task/input{item.pointer}"
            for item in self.exact_input_strings
        }
        missing = sorted(required - referenced)
        if missing:
            raise ValueError(
                f"Task {self.proposed_task_ref!r} declares exact_input_strings that the "
                f"deterministic Workflow does not consume through Task-input $ref: {missing}"
            )


def _workflow_task_input_references(value: Any) -> set[str]:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            pointer = str(value["$ref"])
            if pointer == "/input/task/input" or pointer.startswith("/input/task/input/"):
                return {pointer}
            return set()
        references: set[str] = set()
        for item in value.values():
            references.update(_workflow_task_input_references(item))
        return references
    if isinstance(value, list):
        references = set()
        for item in value:
            references.update(_workflow_task_input_references(item))
        return references
    return set()


class WorkflowPlanSelfCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every_task_can_start_at_entry: Literal[True]
    every_column_applies_to_every_task: Literal[True]
    columns_are_process_stages_not_work_slices: Literal[True]
    tasks_are_independently_reviewable: Literal[True]
    context_handoffs_are_explicit: Literal[True]
    concurrency_conflicts_are_declared: Literal[True]
    terminal_and_rework_paths_are_explicit: Literal[True]


class WorkflowWalkthroughStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column_key: str = Field(pattern=KEY_PATTERN)
    receives: list[str] = Field(
        min_length=1,
        max_length=200,
        description="Task facts, upstream outputs, artifacts, or evidence available on entry.",
    )
    action: str = Field(
        min_length=1,
        max_length=4_000,
        description="Concrete stage action in the reusable lifecycle example.",
    )
    produces: list[str] = Field(
        min_length=1,
        max_length=200,
        description="Outputs, artifacts, or evidence handed to the next stage or terminal.",
    )
    completion_evidence: list[str] = Field(
        min_length=1,
        max_length=200,
        description="Observable facts that allow this stage to emit the selected outcome.",
    )
    outcome: str = Field(
        min_length=1,
        max_length=200,
        description="The declared Workflow transition outcome selected by this simulated stage result.",
    )


class LinearTaskDependencyContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["linear_by_integer_input"] = "linear_by_integer_input"
    order_pointer: str = Field(pattern=r"^/.*", max_length=2_000)
    first_value: int = 1


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_schema: dict[str, Any] = Field(default_factory=dict)
    dependency_contract: LinearTaskDependencyContract | None = None
    required_context: list[str] = Field(default_factory=list, max_length=200)
    expected_outputs: list[str] = Field(min_length=1, max_length=200)
    acceptance_contract: list[str] = Field(min_length=1, max_length=200)


class WorkflowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["devwerk.workflow-plan.v1"] = "devwerk.workflow-plan.v1"
    intent_summary: str = Field(min_length=1, max_length=10_000)
    completion_definition: str = Field(min_length=1, max_length=10_000)
    flow_unit: str = Field(
        min_length=1,
        max_length=4_000,
        description=(
            "The reusable work item or state progression that moves through every Column. "
            "It is not a batch, numbered range, deliverable list, or Column list."
        ),
    )
    lifecycle_summary: str = Field(min_length=1, max_length=10_000)
    entry_meaning: str = Field(min_length=1, max_length=4_000)
    terminal_meaning: str = Field(min_length=1, max_length=4_000)
    task_contract: TaskContract
    columns: list[WorkflowColumnPlan] = Field(min_length=1, max_length=200)
    lifecycle_walkthrough: list[WorkflowWalkthroughStep] = Field(
        min_length=1,
        max_length=1_000,
        description=(
            "Ordered entry-to-terminal lifecycle simulation. Each step states "
            "what a Column receives, does, produces, proves, and which transition outcome it emits."
        ),
    )
    wip_group: str = Field(
        min_length=1,
        max_length=200,
        description="Default scheduling group materialized for every Task in this plan.",
    )
    wip_limit: int = Field(
        ge=1,
        le=100,
        description="Maximum concurrently running Tasks in the plan's default scheduling group.",
    )
    wip_decision: str = Field(min_length=1, max_length=4_000)
    concurrency_groups: list[list[str]] = Field(default_factory=list, max_length=200)
    serialization_reasons: list[str] = Field(default_factory=list, max_length=200)
    progress_evidence: list[str] = Field(min_length=1, max_length=200)
    review_points: list[str] = Field(default_factory=list, max_length=200)
    intervention_conditions: list[str] = Field(min_length=1, max_length=200)
    self_check: WorkflowPlanSelfCheck

    @model_validator(mode="after")
    def validate_references(self) -> "WorkflowPlan":
        column_keys = [item.key for item in self.columns]
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("workflow plan column keys must be unique")
        known_columns = set(column_keys)
        unknown_walkthrough_columns = sorted({
            step.column_key
            for step in self.lifecycle_walkthrough
            if step.column_key not in known_columns
        })
        if unknown_walkthrough_columns:
            raise ValueError(
                "lifecycle walkthrough references unknown columns: "
                f"{unknown_walkthrough_columns}"
            )
        return self


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["devwerk.task-plan.v1"] = "devwerk.task-plan.v1"
    objective: str = Field(min_length=1, max_length=10_000)
    workflow_revision_id: str = Field(min_length=1)
    tasks: list[TaskPlanItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "TaskPlan":
        task_refs = [item.proposed_task_ref for item in self.tasks]
        if len(task_refs) != len(set(task_refs)):
            raise ValueError("task plan references must be unique")
        known_tasks = set(task_refs)
        for task in self.tasks:
            if len(task.dependencies) != len(set(task.dependencies)):
                raise ValueError(f"task {task.proposed_task_ref!r} has duplicate dependencies")
            if task.proposed_task_ref in task.dependencies:
                raise ValueError(f"task {task.proposed_task_ref!r} cannot depend on itself")
            unknown = sorted(set(task.dependencies) - known_tasks)
            if unknown:
                raise ValueError(f"task {task.proposed_task_ref!r} has unknown dependencies: {unknown}")
        dependency_graph = {task.proposed_task_ref: set(task.dependencies) for task in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_ref: str, trail: tuple[str, ...]) -> None:
            if task_ref in visiting:
                cycle_start = trail.index(task_ref)
                cycle = (*trail[cycle_start:], task_ref)
                raise ValueError(f"task plan dependencies contain a cycle: {' -> '.join(cycle)}")
            if task_ref in visited:
                return
            visiting.add(task_ref)
            for dependency in dependency_graph[task_ref]:
                visit(dependency, (*trail, task_ref))
            visiting.remove(task_ref)
            visited.add(task_ref)

        for task_ref in task_refs:
            visit(task_ref, ())
        return self


class WorkflowPlanCreate(BaseModel):
    plan: WorkflowPlan


class TaskPlanCreate(BaseModel):
    plan: TaskPlan


class WorkflowRevisionPublishRequest(BaseModel):
    workflow_plan_id: str = Field(min_length=1)
    workflow: WorkflowDefinition


class LoopApplyRequest(BaseModel):
    loop_key: str = Field(pattern=KEY_PATTERN)
    bindings: dict[str, Any] = Field(default_factory=dict)


class TaskCreate(BaseModel):
    task_plan_id: str = Field(min_length=1)
    proposed_task_ref: str = Field(pattern=KEY_PATTERN)


class AgentToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentModelResponse(BaseModel):
    text: str = ""
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    capability: str
    status: Literal["completed", "awaiting", "failed"] | None = None
    output: Any = None
    error: dict[str, Any] | None = None
    await_handle_draft: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_protocol(self) -> "ToolResult":
        inferred = "completed" if self.ok else "failed"
        if self.status is None:
            self.status = inferred
        if self.status == "completed" and not self.ok:
            raise ValueError("completed CapabilityResult must be ok")
        if self.status == "failed" and self.ok:
            raise ValueError("failed CapabilityResult cannot be ok")
        if self.status == "awaiting":
            if not self.ok:
                raise ValueError("awaiting CapabilityResult must represent an accepted operation")
            if self.await_handle_draft is None or self.checkpoint is None:
                raise ValueError("awaiting CapabilityResult requires await_handle_draft and checkpoint")
        return self
