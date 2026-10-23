"""Inputs for Worker inspection, steering, and archived evidence reads."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.v1.protocols.tools.core.capability_protocol import READ_LIMITS


class AgentCapabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkerInput(AgentCapabilityInput):
    worker_id: str = Field(description="Existing Worker ID in the current Project")


class WorkerSendInput(WorkerInput):
    content: str = Field(min_length=1, description="Steering input for the selected Worker")
    assignment_id: str = Field(default=None, description="Current Assignment; omit for the next visit")
    dedupe_key: str = Field(default=None, description="Optional idempotency key")


class WorkerLifecycleInput(WorkerInput):
    state: Literal["available", "suspended", "retired"] = Field(description="Requested idle Worker lifecycle state")


class WorkerReplaceInput(WorkerInput):
    summary: str = Field(min_length=1, description="Handoff summary preserved for the successor")


class AgentResultReadInput(AgentCapabilityInput):
    agent_run_id: str = Field(description="Source Agent Run ID")
    tool_call_id: str = Field(description="Source Tool call ID")
    field: Literal["result", "stdout", "stderr"] = Field(default="result", description="Result field to read")
    offset: int = Field(default=0, ge=0, description="Character offset in the archived result")
    limit: int = Field(default=READ_LIMITS.archived_result_default_characters, ge=1,
                       le=READ_LIMITS.archived_result_max_characters, description="Maximum returned characters")


class AgentEvidenceListInput(AgentCapabilityInput):
    column_run_id: str = Field(default=None, description="Column Run whose receipts are listed")
    after_invocation_id: int = Field(default=0, ge=0, description="Exclusive invocation cursor")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum receipt references")


class AgentContextReadInput(AgentCapabilityInput):
    worker_id: str = Field(default=None, description="Worker to inspect; omit for the current Worker")
    after_message_id: int = Field(default=0, ge=0, description="Exclusive message cursor")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum context entries")
    snapshot_id: str = Field(default=None, description="Exact snapshot to inspect")
    source_message_id: int = Field(default=None, ge=1, description="Exact source message to page")
    offset: int = Field(default=0, ge=0, description="Character offset")
    max_characters: int = Field(default=4000, ge=1, le=4000, description="Maximum excerpt characters")
    expected_sha256: str = Field(default=None, max_length=128, description="Optional source hash assertion")


class AgentContextCompactInput(WorkerInput):
    through_message_id: int = Field(ge=1, description="Last source message covered")
    summary: dict[str, Any] = Field(description="Explicit coverage summary")


class ArchivedCommandOutput(BaseModel):
    model_config = ConfigDict(extra="allow")
    stdout: str = ""
    stderr: str = ""


class ArchivedToolResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    output: dict[str, Any] | None = None


class AvailableToolCall(BaseModel):
    tool_call_id: str
    capability: str


class AgentResultReadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_run_id: str
    tool_call_id: str
    found: bool = True
    available: list[AvailableToolCall] | None = None
    field: Literal["result", "stdout", "stderr"] | None = None
    offset: int | None = None
    content: str
    total_characters: int | None = None
    sha256: str | None = None
    next_offset: int | None = None
