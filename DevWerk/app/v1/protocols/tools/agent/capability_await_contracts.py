"""Typed Column wait Tool declarations and submissions."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.v1.contracts import ContractError


class AwaitMode(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: Literal["poll", "event", "timer"] = "poll"


class AwaitSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=200)
    token: str | None = Field(default=None, max_length=4000)
    checkpoint: dict[str, Any] = Field(default=None)


class PollAwaitSubmission(AwaitSubmission):
    poll_capability: str
    poll_arguments: dict[str, Any]
    next_check_seconds: int = Field(default=None, ge=1)


class AwaitFunction(BaseModel):
    name: Literal["column.await"] = "column.await"
    description: str
    parameters: dict[str, Any]


class AwaitTool(BaseModel):
    type: Literal["function"] = "function"
    function: AwaitFunction


def await_submission_model(allowed: list[str], wait_config: dict[str, Any]) -> type[AwaitSubmission]:
    mode = AwaitMode.model_validate(wait_config)
    if mode.kind != "poll":
        return AwaitSubmission
    choices = tuple(sorted(set(allowed)))
    if not choices:
        raise ContractError("column.await poll policy has no allowed capabilities")
    permitted = Literal.__getitem__(choices)
    return create_model(
        "BoundPollAwaitSubmission", __base__=PollAwaitSubmission,
        poll_capability=(permitted, Field(description="Capability ID allowed by this Column")),
    )


def await_tool(allowed: list[str], wait_config: dict[str, Any]) -> AwaitTool:
    kind = AwaitMode.model_validate(wait_config).kind
    return AwaitTool(function=AwaitFunction(
        description=f"Suspend this Column Run using its declared {kind} wait policy instead of keeping an Agent alive.",
        parameters=await_submission_model(allowed, wait_config).model_json_schema(),
    ))
