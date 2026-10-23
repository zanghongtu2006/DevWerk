"""Class-owned wire contract for Column completion submissions."""

from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, create_model, field_validator

from app.v1.contracts import ContractError


class CompletionFailureResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failed_evidence_id: str = Field(min_length=1)
    resolved_by_evidence_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


class CompletionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    output: dict[str, Any] = Field(description="Column output object; Runtime validates the frozen output_contract")
    summary: str = Field(max_length=4000)
    failure_resolutions: list[CompletionFailureResolutionInput] = Field(default_factory=list, max_length=100)


class ModelEvidenceCompletionInput(CompletionInput):
    evidence_ids: list[str] = Field(max_length=500, json_schema_extra={"uniqueItems": True})

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item for item in value):
            raise ValueError("evidence_ids must contain distinct non-empty IDs")
        return value


class CompletionFunction(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class CompletionTool(BaseModel):
    type: Literal["function"] = "function"
    function: CompletionFunction


class ObjectOutputSchema(BaseModel):
    """An author-supplied object contract with class-owned root defaults."""

    model_config = ConfigDict(extra="allow")
    type: Literal["object"] = "object"
    properties: dict[str, Any] = Field(default_factory=dict)


def completion_input_model(
    outcomes: list[str], evidence_collection: str, output_contract: dict[str, Any]
) -> type[CompletionInput]:
    choices = tuple(sorted(set(outcomes)))
    if not choices:
        raise ContractError("Column completion requires at least one declared outcome")
    permitted = Literal.__getitem__(choices)
    base = ModelEvidenceCompletionInput if evidence_collection == "model" else CompletionInput
    output_schema = ObjectOutputSchema.model_validate(output_contract).model_dump(mode="json")
    output_type = Annotated[dict[str, Any], WithJsonSchema(output_schema)]
    return create_model(
        "BoundCompletionInput", __base__=base,
        outcome=(permitted, Field(description="Declared outcome of this Column")),
        output=(output_type, Field(description="Frozen Column output contract")),
    )
