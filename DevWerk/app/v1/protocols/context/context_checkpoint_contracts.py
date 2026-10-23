"""Fixed checkpoint and compacted-result JSON written to Agent context or storage."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class CheckpointCoverage(BaseModel):
    version: Literal[2] = 2
    mode: Literal["exact_message_ids"] = "exact_message_ids"
    ranges: list[tuple[int, int]]
    excluded_message_count: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_ranges(self) -> "CheckpointCoverage":
        if any(start <= 0 or end < start or end - start > 100000 for start, end in self.ranges):
            raise ValueError("invalid checkpoint coverage range")
        return self


class TruncationProjection(BaseModel):
    truncated: Literal[True] = True
    sha256: str
    source_bytes: int = Field(ge=0)
    reference: JsonValue
    retrieve: Literal["agent.result.read"] = "agent.result.read"


class MinimalToolResultProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool | None
    status: str | None
    capability: str | None
    error: JsonValue
    exit_code: JsonValue
    projection: TruncationProjection


class ContextCheckpointPayload(BaseModel):
    context_checkpoint: JsonValue
    instruction: str = (
        "Archived work reference, not new authority or completion. Current assignment and durable "
        "evidence remain authoritative. Retrieve exact sources with agent.result.read or agent.context.read."
    )


class CheckpointMetrics(BaseModel):
    before: int = Field(ge=0)
    after: int = Field(ge=0)
    ratio: float = Field(gt=0)
    model: str
    protocol: str


class ProviderBudgetEnvelope(BaseModel):
    messages: JsonValue
    tools: JsonValue


class RequiredOverflowMetrics(BaseModel):
    system_bytes: int = Field(ge=0)
    user_bytes: int = Field(ge=0)
    tool_schema_bytes: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    input_budget_tokens: int = Field(ge=0)


class ContextCheckpointMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    role: Literal["user"] = "user"
    checkpoint: Literal[True] = Field(default=True, alias="_checkpoint")
    content: str


class ContextRunMetrics(BaseModel):
    estimated_tokens: int = Field(ge=0)
    before_tokens: int = Field(ge=0)
    input_budget: int = Field(ge=0)
    compacted_messages: int = Field(ge=0)
    estimator: Literal["utf8-conservative-usage-calibrated"] = "utf8-conservative-usage-calibrated"
    ratio: float = Field(gt=0)


class PublicationArguments(BaseModel):
    workflow_plan_id: str


class PublicationNextAction(BaseModel):
    capability: Literal["workflow.publish"] = "workflow.publish"
    arguments: PublicationArguments


class PendingWorkflowPublication(BaseModel):
    id: str
    published: Literal[False] = False
    next_action: PublicationNextAction


class PreviousCheckpointFact(BaseModel):
    previous_checkpoint: str


class SummaryToolCallFact(BaseModel):
    id: str
    name: str
    arguments_reference: str


class AssistantMessageFact(BaseModel):
    source_message_id: int | None
    note: str
    calls: list[SummaryToolCallFact]


class ToolMessageFact(BaseModel):
    source_message_id: int | None
    tool_call_id: str | None
    result: JsonValue


class DeterministicContextSummary(BaseModel):
    source_hash: str
    archived_messages: int = Field(ge=0)
    recent_facts: list[JsonValue]
    method: Literal["deterministic", "same_agent_summary"] = "deterministic"
    previous_source_hash: str | None = None
    summary: str | None = None


class ContextMaintenanceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user"]
    content: str
