from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    ExactTaskInputString,
    OrchestrationPlan,
    OrchestrationTaskPlan,
    Transition,
    WorkflowDefinition,
)
from tests.helpers import orchestration_plan, sequence_workflow
from app.v1.states import (
    TASK_STATE_MACHINE,
    TaskStatus,
    runtime_status_catalog,
)


def test_runtime_status_catalog_is_the_single_public_status_definition():
    catalog = runtime_status_catalog()

    assert catalog["task"]["values"] == [
        "pending", "running", "waiting", "recovering", "done", "failed"
    ]
    assert "recovering" in catalog["task"]["transitions"]["running"]
    assert set(catalog) == {
        "task", "column_run", "attempt", "agent_run", "tool_invocation"
    }


def test_task_state_machine_rejects_terminal_drift():
    TASK_STATE_MACHINE.require(TaskStatus.RUNNING, TaskStatus.RECOVERING)
    TASK_STATE_MACHINE.require(TaskStatus.FAILED, TaskStatus.PENDING)

    with pytest.raises(ValueError, match="done -> running"):
        TASK_STATE_MACHINE.require(TaskStatus.DONE, TaskStatus.RUNNING)


def test_workflow_is_domain_agnostic_and_serializes_declarative_executor():
    workflow = sequence_workflow(name="任意未知领域", path="deliverable.any", content="value")
    payload = workflow.model_dump(mode="json")

    assert payload["columns"][0]["executor"]["kind"] == "capability_sequence"
    assert payload["columns"][0]["executor"]["steps"][0]["capability"] == "project.files.write"
    assert "operation" not in payload["columns"][0]
    assert "prompt" not in payload["columns"][0]


def test_workflow_reserves_done_and_failed_as_terminal_sentinels():
    with pytest.raises(ValidationError, match="reserved terminal sentinel"):
        WorkflowDefinition(
            name="invalid",
            entry="work",
            columns=[
                ColumnDefinition(
                    key="work",
                    name="Work",
                    executor=CapabilitySequenceExecutor(steps=[CapabilityStep(capability="system.noop")]),
                    transitions=[Transition(outcome="success", target="done"), Transition(outcome="failure", target="done")],
                ),
                ColumnDefinition(
                    key="done", name="Done",
                    executor=CapabilitySequenceExecutor(steps=[CapabilityStep(capability="system.noop")]),
                    transitions=[Transition(outcome="success", target="done"), Transition(outcome="failure", target="failed")],
                ),
            ],
        )


def test_sequence_needs_only_its_declared_completion_outcome():
    workflow = WorkflowDefinition(
        name="valid",
        entry="work",
        columns=[
            ColumnDefinition(
                key="work",
                name="Work",
                executor=CapabilitySequenceExecutor(steps=[CapabilityStep(capability="system.noop")]),
                transitions=[Transition(outcome="success", target="done")],
            ),
        ],
    )
    assert workflow.column("work").transitions[0].target == "done"


def test_workflow_accepts_business_self_transition_without_platform_visit_limit():
    workflow = WorkflowDefinition(
        name="bounded iteration",
        entry="work",
        columns=[
            ColumnDefinition(
                key="work",
                name="Work",
                executor=CapabilitySequenceExecutor(
                    steps=[CapabilityStep(capability="system.noop")],
                    outcome_from="/steps/decision/output/outcome",
                ),
                transitions=[
                    Transition(outcome="continue", target="work"),
                    Transition(outcome="complete", target="done"),
                    Transition(outcome="failure", target="failed"),
                ],
            )
        ],
    )

    assert workflow.column("work").transitions[0].target == "work"


def test_capability_sequence_output_contract_must_accept_runtime_envelope():
    with pytest.raises(ValidationError, match="rejects Runtime envelope fields"):
        ColumnDefinition(
            key="work",
            name="Work",
            executor=CapabilitySequenceExecutor(
                steps=[CapabilityStep(capability="system.noop")]
            ),
            output_contract={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            transitions=[
                Transition(outcome="success", target="done"),
                Transition(outcome="failure", target="failed"),
            ],
        )

    column = ColumnDefinition(
        key="work",
        name="Work",
        executor=CapabilitySequenceExecutor(
            steps=[CapabilityStep(capability="system.noop")]
        ),
        output_contract={
            "type": "object",
            "required": ["summary", "steps"],
            "properties": {
                "summary": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object"}},
            },
            "additionalProperties": False,
        },
        transitions=[
            Transition(outcome="success", target="done"),
            Transition(outcome="failure", target="failed"),
        ],
    )

    assert column.output_contract["required"] == ["summary", "steps"]


def test_agent_executor_requires_explicit_non_empty_capability_allowlist():
    with pytest.raises(ValidationError, match="capabilities"):
        AgentExecutor()
    with pytest.raises(ValidationError, match="at least 1 item"):
        AgentExecutor(capabilities=[])


def test_orchestration_plan_dependencies_are_an_acyclic_graph():
    base = orchestration_plan(sequence_workflow()).model_dump(mode="json")
    base["task_portfolio"][0]["dependencies"] = ["primary"]
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        OrchestrationPlan.model_validate(base)

    first = dict(base["task_portfolio"][0])
    first["proposed_task_ref"] = "first"
    first["dependencies"] = ["second"]
    second = dict(first)
    second["proposed_task_ref"] = "second"
    second["dependencies"] = ["first"]
    base["task_portfolio"] = [first, second]
    base["representative_task_ref"] = "first"
    with pytest.raises(ValidationError, match="dependencies contain a cycle"):
        OrchestrationPlan.model_validate(base)


def test_sequence_can_declare_business_outcomes_without_runtime_failure_policy():
    transitions = [
        Transition(outcome="matched", target="done"),
        Transition(outcome="mismatch", target="failed"),
        Transition(outcome="failure", target="failed"),
    ]
    fixed = WorkflowDefinition(
        name="fixed deterministic branch",
        entry="verify",
        columns=[
            ColumnDefinition(
                key="verify",
                name="Verify",
                executor=CapabilitySequenceExecutor(
                    steps=[CapabilityStep(capability="project.files.verify")],
                    completed_outcome="matched",
                ),
                transitions=transitions,
            )
        ],
    )
    assert fixed.column("verify").executor.completed_outcome == "matched"

    workflow = WorkflowDefinition(
        name="evidence selected deterministic branch",
        entry="verify",
        columns=[
            ColumnDefinition(
                key="verify",
                name="Verify",
                    executor=CapabilitySequenceExecutor(
                        steps=[
                            CapabilityStep(
                                capability="project.files.verify",
                                save_as="verification",
                            )
                        ],
                        outcome_from="/steps/verification/output/outcome",
                    ),
                transitions=transitions,
            )
        ],
    )

    assert workflow.columns[0].executor.outcome_from == "/steps/verification/output/outcome"
    assert workflow.columns[0].executor.completed_outcome is None


def test_sequence_outcome_source_defaults_are_provider_authorable():
    defaulted = CapabilitySequenceExecutor(
        steps=[CapabilityStep(capability="system.noop")]
    )
    selected = CapabilitySequenceExecutor(
        steps=[CapabilityStep(capability="system.noop", save_as="result")],
        outcome_from="/steps/result/output/outcome",
    )

    assert defaulted.completed_outcome == "success"
    assert defaulted.outcome_from is None
    assert selected.completed_outcome is None
    assert selected.outcome_from == "/steps/result/output/outcome"

    with pytest.raises(ValidationError, match="exactly one"):
        CapabilitySequenceExecutor(
            steps=[CapabilityStep(capability="system.noop")],
            completed_outcome="success",
            outcome_from="/steps/result/output/outcome",
        )


def test_exact_task_input_string_decodes_transport_safe_escapes():
    exact = ExactTaskInputString(
        pointer="/contract/content",
        escaped_value=r"\u0020payload\n\t",
    )

    assert exact.value == " payload\n\t"

    with pytest.raises(ValidationError, match="isolated UTF-16 surrogate"):
        ExactTaskInputString(pointer="/contract/content", escaped_value=r"\uD800")

    assert ExactTaskInputString(
        pointer="/input/task/input/contract/content",
        escaped_value=r"payload\n",
    ).pointer == "/contract/content"
    assert ExactTaskInputString(
        pointer="/contract/path",
        escaped_value=r'目录\\file\"name',
    ).value == '目录\\file"name'

    with pytest.raises(ValidationError, match="pointers must be unique"):
        OrchestrationTaskPlan(
            proposed_task_ref="primary",
            objective="Deliver exact input.",
            workflow_fit="Every process stage applies.",
            agent_execution="forbidden",
            exact_input_strings=[exact, exact],
            review_scope="Review exact delivery.",
            retry_scope="Retry the failed stage.",
        )


def test_column_input_contract_must_match_selected_runtime_envelope():
    with pytest.raises(ValidationError, match="unavailable Runtime root keys"):
        ColumnDefinition(
            key="entry",
            name="Entry",
            executor=CapabilitySequenceExecutor(
                steps=[CapabilityStep(capability="system.noop")]
            ),
            input_contract={
                "type": "object",
                "required": ["contract"],
                "properties": {"contract": {"type": "object"}},
            },
            transitions=[
                Transition(outcome="success", target="done"),
                Transition(outcome="failure", target="failed"),
            ],
        )

    column = ColumnDefinition(
        key="entry",
        name="Entry",
        executor=CapabilitySequenceExecutor(
            steps=[CapabilityStep(capability="system.noop")]
        ),
        input_contract={
            "type": "object",
            "required": ["task"],
            "properties": {
                "task": {
                    "type": "object",
                    "required": ["input"],
                    "properties": {
                        "input": {
                            "type": "object",
                            "required": ["contract"],
                        }
                    },
                }
            },
        },
        transitions=[
            Transition(outcome="success", target="done"),
            Transition(outcome="failure", target="failed"),
        ],
    )
    assert column.input_contract["required"] == ["task"]

    with pytest.raises(ValidationError, match="upstream_outputs"):
        ColumnDefinition(
            key="entry",
            name="Entry",
            executor=CapabilitySequenceExecutor(
                steps=[CapabilityStep(capability="system.noop")]
            ),
            context={"upstream_outputs": []},
            input_contract={
                "type": "object",
                "required": ["upstream_outputs"],
            },
            transitions=[
                Transition(outcome="success", target="done"),
                Transition(outcome="failure", target="failed"),
            ],
        )
