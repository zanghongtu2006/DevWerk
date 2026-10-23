"""Runtime-bound Workflow Tool models; schemas are generated from model fields."""

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue, WithJsonSchema, create_model

from app.v1.protocols.tools.planning.capability_contracts import WorkflowCreateInput, WorkflowPublishInput
from app.v1.protocols.tools.core.provider_schema_contracts import ProviderSchemaNode, provider_view
from app.v1.domain import (
    AcceptanceCheck, AgentExecutor, CapabilitySequenceExecutor, CapabilityStep, ColumnDefinition,
    WorkflowAuthoringColumn, WorkflowAuthoringRequest, WorkflowDefinition,
)


@dataclass(frozen=True)
class WorkflowToolModels:
    create: type[WorkflowCreateInput]
    create_runtime: type[WorkflowCreateInput]
    publish: type[WorkflowPublishInput]


@dataclass(frozen=True)
class WorkflowCapabilityContract:
    id: str
    input_schema: dict[str, Any]
    supports_acceptance_check: bool
    inline_provider_arguments: bool = True


class RuntimeArgumentReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pointer: str = Field(alias="$ref", pattern=r"^/")


class WorkflowToolId(str, Enum):
    CREATE = "workflow.create"
    PUBLISH = "workflow.publish"


def _template_schema(node: ProviderSchemaNode | bool, reference: ProviderSchemaNode) -> ProviderSchemaNode:
    """Present exact Tool types and the existing Runtime $ref alternative."""
    source = node.clone() if isinstance(node, ProviderSchemaNode) else node
    if isinstance(source, ProviderSchemaNode):
        if source.properties is not None:
            source.properties = {key: _template_schema(child, reference) for key, child in source.properties.items()}
        if source.items is not None:
            source.items = _template_schema(source.items, reference)
        if source.any_of is not None:
            source.any_of = [_template_schema(choice, reference) for choice in source.any_of]
        if source.one_of is not None:
            source.one_of = [_template_schema(choice, reference) for choice in source.one_of]
    return ProviderSchemaNode(any_of=[source, reference.clone()])


def _compact_argument_schema(node: ProviderSchemaNode | bool) -> ProviderSchemaNode | bool:
    """The direct Tool retains prose; nested variants need only enforceable facts."""
    if isinstance(node, bool):
        return node
    result = node.clone()
    result.title = None
    result.description = None
    if result.properties is not None:
        result.properties = {key: _compact_argument_schema(value) for key, value in result.properties.items()}
    if result.items is not None:
        result.items = _compact_argument_schema(result.items)
    if result.any_of is not None:
        result.any_of = [_compact_argument_schema(value) for value in result.any_of]
    if result.one_of is not None:
        result.one_of = [_compact_argument_schema(value) for value in result.one_of]
    if isinstance(result.additional_properties, ProviderSchemaNode):
        result.additional_properties = _compact_argument_schema(result.additional_properties)
    return result


def _argument_field(contract: WorkflowCapabilityContract, *, allow_references: bool) -> Any:
    schema = _compact_argument_schema(ProviderSchemaNode.model_validate(provider_view(contract.input_schema)))
    assert isinstance(schema, ProviderSchemaNode)
    if allow_references:
        reference = ProviderSchemaNode.model_validate(provider_view(RuntimeArgumentReference.model_json_schema()))
        schema = _template_schema(schema, reference)
    return Annotated[dict[str, JsonValue], WithJsonSchema(schema.wire())]


def _selected_variants(models: list[type[BaseModel]]) -> Any:
    if len(models) == 1:
        return models[0]
    return Annotated[Union[tuple(models)], Field(discriminator="capability")]


def workflow_tool_models(contracts: list[WorkflowCapabilityContract]) -> WorkflowToolModels:
    """Bind live Tool input types to Workflow fields through Pydantic models."""
    choices = tuple(sorted({contract.id for contract in contracts}))
    if not choices:
        raise ValueError("Workflow Tool models require at least one Column capability")
    by_id = {contract.id: contract for contract in contracts}
    step_variants: list[type[BaseModel]] = []
    check_variants: list[type[BaseModel]] = []
    external_contracts: list[WorkflowCapabilityContract] = []
    for index, capability_id in enumerate(choices):
        contract = by_id[capability_id]
        if not contract.inline_provider_arguments:
            external_contracts.append(contract)
            continue
        selected = Literal[capability_id]
        step_variants.append(create_model(
            f"WorkflowCapabilityStep{index}", __base__=CapabilityStep,
            capability=(selected, Field(description="Selected registered Column Tool")),
            arguments=(_argument_field(contract, allow_references=True), Field(
                default_factory=dict,
                description=(f"Arguments for {capability_id}; use its declared types. "
                             "Exact Task values may use a $ref such as /input/task/input/contract/content; "
                             "do not inline exact Task strings."),
            )),
        ))
        if contract.supports_acceptance_check:
            check_variants.append(create_model(
                f"WorkflowAcceptanceCheck{index}", __base__=AcceptanceCheck,
                capability=(selected, Field(description="Selected registered acceptance Tool")),
                arguments=(_argument_field(contract, allow_references=False), Field(
                    description=(f"Arguments for {capability_id}; "
                                 f"{AcceptanceCheck.model_fields['arguments'].description}"),
                )),
            ))
    if external_contracts:
        external_ids = tuple(contract.id for contract in external_contracts)
        external_type = Literal.__getitem__(external_ids)
        dynamic_arguments = (
            dict[str, JsonValue], Field(default_factory=dict, description=(
                "Arguments for the selected external Tool. Its direct Tool definition declares "
                "the exact input schema; DevWerk validates that schema before publishing."
            )),
        )
        step_variants.append(create_model(
            "WorkflowExternalCapabilityStep", __base__=CapabilityStep,
            capability=(external_type, Field(description="Selected registered external Tool")),
            arguments=dynamic_arguments,
        ))
        check_ids = tuple(contract.id for contract in external_contracts
                          if contract.supports_acceptance_check)
        if check_ids:
            check_variants.append(create_model(
                "WorkflowExternalAcceptanceCheck", __base__=AcceptanceCheck,
                capability=(Literal.__getitem__(check_ids), Field(
                    description="Selected registered external acceptance Tool")),
                arguments=(dict[str, JsonValue], Field(description=(
                    "Arguments for the selected external Tool. Its direct Tool definition declares "
                    "the exact input schema; DevWerk validates that schema before publishing."
                ))),
            ))
    check_type = _selected_variants(check_variants) if check_variants else AcceptanceCheck
    step_type = _selected_variants(step_variants)
    capability = Literal.__getitem__(choices)
    agent = create_model(
        "AgentExecutor", __base__=AgentExecutor,
        capabilities=(list[capability], Field(min_length=1, description=(
            "Explicit non-empty allowlist for this Column Agent; IDs come from the live Capability Registry."
        ))),
    )
    basic_step = create_model(
        "CapabilityStep", __base__=CapabilityStep,
        capability=(capability, Field(description="Exact capability ID from the live Registry")),
    )
    basic_sequence = create_model(
        "CapabilitySequenceExecutor", __base__=CapabilitySequenceExecutor,
        steps=(list[basic_step], Field(min_length=1, max_length=200)),
    )
    author_sequence = create_model(
        "TypedCapabilitySequenceExecutor", __base__=CapabilitySequenceExecutor,
        steps=(list[step_type], Field(min_length=1, max_length=200)),
    )
    frozen_executor = Annotated[agent | basic_sequence, Field(discriminator="kind")]
    author_executor = Annotated[agent | author_sequence, Field(discriminator="kind")]
    frozen_column = create_model(
        "ColumnDefinition", __base__=ColumnDefinition,
        executor=(frozen_executor, Field(description="Required Column execution strategy")),
    )
    author_column = create_model(
        "WorkflowAuthoringColumn", __base__=WorkflowAuthoringColumn,
        executor=(author_executor, Field(description="Required Column execution strategy")),
        acceptance_checks=(list[check_type], Field(default_factory=list)),
    )
    author_runtime_column = create_model(
        "WorkflowAuthoringRuntimeColumn", __base__=WorkflowAuthoringColumn,
        executor=(frozen_executor, Field(description="Required Column execution strategy")),
    )
    frozen_workflow = create_model(
        "WorkflowDefinition", __base__=WorkflowDefinition,
        columns=(list[frozen_column], Field(min_length=1, max_length=200,
            description=WorkflowDefinition.model_fields["columns"].description)),
    )
    author_workflow = create_model(
        "WorkflowAuthoringRequest", __base__=WorkflowAuthoringRequest,
        columns=(list[author_column], Field(min_length=1, max_length=200,
            description=WorkflowAuthoringRequest.model_fields["columns"].description)),
    )
    author_runtime_workflow = create_model(
        "WorkflowAuthoringRuntimeRequest", __base__=WorkflowAuthoringRequest,
        columns=(list[author_runtime_column], Field(min_length=1, max_length=200,
            description=WorkflowAuthoringRequest.model_fields["columns"].description)),
    )
    create = create_model(
        "WorkflowCreateInput", __base__=WorkflowCreateInput,
        workflow=(author_workflow, Field(description="Complete author-owned Workflow graph")),
    )
    create_runtime = create_model(
        "WorkflowCreateRuntimeInput", __base__=WorkflowCreateInput,
        workflow=(author_runtime_workflow, Field(description="Complete author-owned Workflow graph")),
    )
    publish = create_model(
        "WorkflowPublishInput", __base__=WorkflowPublishInput,
        workflow=(frozen_workflow, Field(description="Complete frozen Workflow graph")),
    )
    return WorkflowToolModels(create=create, create_runtime=create_runtime, publish=publish)
