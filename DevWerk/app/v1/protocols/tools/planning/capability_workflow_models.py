"""Typed Workflow publication schema bound to the live Column capability IDs.

Workflow creation uses the static authoring command. Dynamic Tool arguments remain
validated against their own registered contracts before a graph is published.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, create_model

from app.v1.domain import (
    AgentExecutor,
    CapabilitySequenceExecutor,
    CapabilityStep,
    ColumnDefinition,
    WorkflowDefinition,
)
from app.v1.protocols.tools.planning.capability_contracts import WorkflowPublishInput


@dataclass(frozen=True)
class WorkflowToolModels:
    publish: type[WorkflowPublishInput]


@dataclass(frozen=True)
class WorkflowCapabilityContract:
    id: str


class WorkflowToolId(str, Enum):
    CREATE = "workflow.create"
    PUBLISH = "workflow.publish"


def workflow_tool_models(contracts: list[WorkflowCapabilityContract]) -> WorkflowToolModels:
    """Constrain frozen graph capability IDs without inlining each Tool's schema."""
    choices = tuple(sorted({contract.id for contract in contracts}))
    if not choices:
        raise ValueError("Workflow Tool models require at least one Column capability")
    capability = Literal.__getitem__(choices)
    agent = create_model(
        "AgentExecutor", __base__=AgentExecutor,
        capabilities=(list[capability], Field(min_length=1, description=(
            "Explicit non-empty allowlist for this Column Agent; IDs come from the live Capability Registry."
        ))),
    )
    step = create_model(
        "CapabilityStep", __base__=CapabilityStep,
        capability=(capability, Field(description="Exact capability ID from the live Registry")),
    )
    sequence = create_model(
        "CapabilitySequenceExecutor", __base__=CapabilitySequenceExecutor,
        steps=(list[step], Field(min_length=1, max_length=200)),
    )
    executor = Annotated[agent | sequence, Field(discriminator="kind")]
    column = create_model(
        "ColumnDefinition", __base__=ColumnDefinition,
        executor=(executor, Field(description=ColumnDefinition.model_fields["executor"].description)),
    )
    workflow = create_model(
        "WorkflowDefinition", __base__=WorkflowDefinition,
        columns=(list[column], Field(min_length=1, max_length=200,
            description=WorkflowDefinition.model_fields["columns"].description)),
    )
    publish = create_model(
        "WorkflowPublishInput", __base__=WorkflowPublishInput,
        workflow=(workflow, Field(description="Complete frozen Workflow graph")),
    )
    return WorkflowToolModels(publish=publish)
