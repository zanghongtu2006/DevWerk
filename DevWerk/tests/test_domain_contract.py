from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    Transition,
    WorkflowDefinition,
)
from tests.helpers import sequence_workflow, terminals


def test_workflow_is_domain_agnostic_and_serializes_declarative_executor():
    workflow = sequence_workflow(name="任意未知领域", path="deliverable.any", content="value")
    payload = workflow.model_dump(mode="json")

    assert payload["columns"][0]["executor"]["kind"] == "capability_sequence"
    assert payload["columns"][0]["executor"]["steps"][0]["capability"] == "project.files.write"
    assert "operation" not in payload["columns"][0]
    assert "prompt" not in payload["columns"][0]


def test_workflow_requires_reachable_done_and_failed_terminals():
    with pytest.raises(ValidationError, match="exactly one done and one failed"):
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
                ColumnDefinition(key="done", name="Done", terminal="done"),
                ColumnDefinition(key="unused", name="Unused", terminal="done"),
            ],
        )


def test_sequence_outcomes_must_be_declared():
    with pytest.raises(ValidationError, match="runtime outcomes"):
        WorkflowDefinition(
            name="invalid",
            entry="work",
            columns=[
                ColumnDefinition(
                    key="work",
                    name="Work",
                    executor=CapabilitySequenceExecutor(steps=[CapabilityStep(capability="system.noop")]),
                    transitions=[Transition(outcome="success", target="done")],
                ),
                *terminals(),
            ],
        )


def test_agent_executor_requires_explicit_non_empty_capability_allowlist():
    with pytest.raises(ValidationError, match="capabilities"):
        AgentExecutor()
    with pytest.raises(ValidationError, match="at least 1 item"):
        AgentExecutor(capabilities=[])
