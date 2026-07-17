from __future__ import annotations

from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    Transition,
    WorkflowDefinition,
)


def terminals() -> list[ColumnDefinition]:
    return [
        ColumnDefinition(key="done", name="Done", terminal="done"),
        ColumnDefinition(key="failed", name="Failed", terminal="failed"),
    ]


def readiness(**overrides):
    value = {
        "decision": "dispatch",
        "objective": "Deliver the requested result",
        "scope": ["requested work"],
        "non_scope": [],
        "deliverables": ["verifiable result"],
        "acceptance_criteria": ["workflow reaches an explicit terminal"],
        "dependencies_checked": True,
        "resource_conflicts": [],
        "risks": [],
        "reason_summary": "The work benefits from tracked Workflow execution.",
        "next_review_at": None,
    }
    value.update(overrides)
    return value


def sequence_workflow(*, name: str = "deterministic", path: str = "result.txt", content: str = "done") -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name,
        entry="execute",
        columns=[
            ColumnDefinition(
                key="execute",
                name="Execute",
                executor=CapabilitySequenceExecutor(
                    steps=[
                        CapabilityStep(
                            capability="project.files.write",
                            arguments={"path": path, "content": content},
                        )
                    ]
                ),
                transitions=[
                    Transition(outcome="success", target="done"),
                    Transition(outcome="failure", target="failed"),
                ],
            ),
            *terminals(),
        ],
    )


def agent_workflow(*, instruction: str = "Use the declared capabilities and submit a contract-valid completion.") -> WorkflowDefinition:
    return WorkflowDefinition(
        name="agent work",
        entry="work",
        columns=[
            ColumnDefinition(
                key="work",
                name="Work",
                instruction=instruction,
                executor=AgentExecutor(
                    capabilities=["project.files.write", "project.files.read"],
                    max_iterations=5,
                    max_tool_calls=10,
                ),
                output_contract={
                    "type": "object",
                    "required": ["delivered"],
                    "properties": {"delivered": {"type": "boolean"}},
                    "additionalProperties": True,
                },
                transitions=[
                    Transition(outcome="success", target="done"),
                    Transition(outcome="failure", target="failed"),
                ],
            ),
            *terminals(),
        ],
    )
