"""Object-level conversion from compact Workflow authoring to frozen graph and plan."""

import copy
from typing import Any, Protocol

from app.v1.contracts import canonicalize_contract_value
from app.v1.domain import (
    CapabilitySequenceExecutor,
    ColumnDefinition,
    ContextSelection,
    PollWaitPolicy,
    TaskContract,
    Transition,
    WorkflowAuthoringRequest,
    WorkflowColumnPlan,
    WorkflowColumnTarget,
    WorkflowDefinition,
    WorkflowPlan,
)
from app.v1.workflows.workflow_transport import WorkflowTransport


DIAGNOSTIC_COLUMN_LIMIT = 20
DEFAULT_WIP_GROUP = "default"


class CapabilitySchemaLookup(Protocol):
    def contains(self, capability_id: str) -> bool: ...
    def input_schema(self, capability_id: str) -> dict[str, Any]: ...


def canonicalize_workflow_model(
    workflow: WorkflowDefinition, registry: CapabilitySchemaLookup
) -> WorkflowDefinition:
    """Normalize dynamic arguments without converting the Workflow to JSON."""
    normalized = workflow.model_copy(deep=True)
    for column in normalized.columns:
        if isinstance(column.executor, CapabilitySequenceExecutor):
            for step in column.executor.steps:
                step.arguments = _canonical_arguments(step.capability, step.arguments, registry)
        wait = column.wait_policy
        if isinstance(wait, PollWaitPolicy):
            wait.poll_arguments = _canonical_arguments(wait.poll_capability, wait.poll_arguments, registry)
            if wait.cancel_capability:
                wait.cancel_arguments = _canonical_arguments(wait.cancel_capability, wait.cancel_arguments, registry)
            if wait.cleanup_capability:
                wait.cleanup_arguments = _canonical_arguments(wait.cleanup_capability, wait.cleanup_arguments, registry)
    return normalized


def canonicalize_workflow_capability_arguments(
    value: Any, registry: CapabilitySchemaLookup
) -> Any:
    """Normalize dynamic tool arguments on typed Workflow objects at the boundary."""
    if isinstance(value, WorkflowDefinition):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        return value
    if not isinstance(value.get("columns"), list):
        return copy.deepcopy(value)
    workflow = WorkflowTransport.model_validate(value)
    for column in workflow.columns or []:
        if column.executor and column.executor.kind == "capability_sequence":
            for step in column.executor.steps or []:
                if step.capability and isinstance(step.arguments, dict):
                    step.arguments = _canonical_arguments(step.capability, step.arguments, registry)
        wait = column.wait_policy
        if wait:
            if wait.poll_capability and isinstance(wait.poll_arguments, dict):
                wait.poll_arguments = _canonical_arguments(wait.poll_capability, wait.poll_arguments, registry)
            if wait.cancel_capability and isinstance(wait.cancel_arguments, dict):
                wait.cancel_arguments = _canonical_arguments(wait.cancel_capability, wait.cancel_arguments, registry)
            if wait.cleanup_capability and isinstance(wait.cleanup_arguments, dict):
                wait.cleanup_arguments = _canonical_arguments(wait.cleanup_capability, wait.cleanup_arguments, registry)
    return workflow.model_dump(mode="json", exclude_unset=True)


def _canonical_arguments(
    capability_id: str, arguments: dict[str, Any], registry: CapabilitySchemaLookup
) -> dict[str, Any]:
    if not registry.contains(capability_id):
        return arguments
    canonical = canonicalize_contract_value(arguments, registry.input_schema(capability_id))
    if _contains_tagged_runtime_reference(canonical):
        raise ValueError("tagged $ref string is invalid")
    return canonical


def _contains_tagged_runtime_reference(value: Any) -> bool:
    if isinstance(value, str):
        return "<$ref>" in value or "</$ref>" in value
    if isinstance(value, dict):
        return any(_contains_tagged_runtime_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tagged_runtime_reference(item) for item in value)
    return False


def materialize_workflow(draft: WorkflowAuthoringRequest) -> tuple[WorkflowDefinition, WorkflowPlan]:
    """Preserve the author's edges while deriving only predecessor and plan facts."""
    predecessors: dict[str, set[str]] = {column.key: set() for column in draft.columns}
    for column in draft.columns:
        for transition in column.transitions:
            target = transition.target
            if isinstance(target, WorkflowColumnTarget):
                if target.key not in predecessors:
                    known = sorted(predecessors)
                    raise ValueError(
                        f"column {column.key!r} outcome {transition.outcome!r} targets unknown Column "
                        f"{target.key!r}; declared Column keys: "
                        f"{known[:DIAGNOSTIC_COLUMN_LIMIT]} (total {len(known)}). "
                        "To finish, use kind=terminal with status=done or failed."
                    )
                if target.key != column.key:
                    predecessors[target.key].add(column.key)

    columns: list[ColumnDefinition] = []
    for item in draft.columns:
        context = item.context.model_copy(deep=True) if item.context else ContextSelection()
        if not context.upstream_outputs:
            context.upstream_outputs = sorted(predecessors[item.key])
        transitions = [
            Transition(
                outcome=edge.outcome,
                target=edge.target.key if isinstance(edge.target, WorkflowColumnTarget) else edge.target.status,
                evidence_requirement=edge.evidence_requirement,
                allows_unresolved_failures=edge.allows_unresolved_failures,
                required_checks=edge.required_checks,
            )
            for edge in item.transitions
        ]
        columns.append(ColumnDefinition(
            key=item.key,
            name=item.name,
            instruction=item.instruction,
            executor=item.executor,
            transitions=transitions,
            context=context,
            input_contract=item.input_contract,
            output_contract=item.output_contract,
            acceptance_checks=item.acceptance_checks,
            wait_policy=item.wait_policy,
        ))

    workflow = WorkflowDefinition(
        name=draft.name,
        description=draft.description,
        entry=draft.entry,
        columns=columns,
    )
    plan = WorkflowPlan(
        intent_summary=draft.name,
        completion_definition="; ".join(draft.completion_conditions),
        flow_unit=draft.flow_unit,
        lifecycle_summary=draft.description or draft.name,
        entry_meaning=f"Task enters {draft.entry}",
        terminal_meaning="Follow declared transitions to done or failed",
        task_contract=TaskContract(
            input_schema=draft.task_input_schema,
            expected_outputs=draft.expected_outputs,
            acceptance_contract=draft.completion_conditions,
        ),
        columns=[WorkflowColumnPlan(
            key=item.key,
            responsibility=item.instruction,
            execution_mode=item.executor.kind,
            entry_evidence=["Task input and selected predecessor outputs"],
            exit_evidence=["Column output and transition receipt"],
            context_boundary="Selected Task and predecessor context",
            review_or_rework_role="Follow explicitly declared graph transitions",
        ) for item in draft.columns],
        wip_group=DEFAULT_WIP_GROUP,
        wip_limit=draft.wip_limit,
        wip_decision="Use the declared WIP limit",
        progress_evidence=["Column transition receipts"],
        intervention_conditions=["Task reaches failed or requires user input"],
    )
    return workflow, plan
