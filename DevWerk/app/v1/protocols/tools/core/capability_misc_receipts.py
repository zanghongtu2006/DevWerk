"""Successful output contracts for generic event and governance capabilities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.v1.protocols.tools.core.capability_protocol import GovernanceDecisionKind


class NoopReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: Literal[True] = Field(description="The deterministic no-op finished")


class EventReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1, description="Durable event cursor")
    project_id: str = Field(description="Owning Project ID")
    task_id: str | None = Field(description="Related Task ID, if any")
    run_id: str | None = Field(description="Related Run ID, if any")
    type: str = Field(description="Event type")
    data: dict[str, JsonValue] = Field(description="Event-specific JSON payload")
    created_at: str = Field(description="Event creation timestamp")


class EventListReceipt(RootModel[list[EventReceipt]]):
    pass


class SupervisionScheduleReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Scheduled review ID")
    project_id: str = Field(description="Owning Project ID")
    reason: str = Field(description="Review reason")
    due_at: str = Field(description="Scheduled time")
    state: Literal["pending"] = Field(description="Initial schedule state")
    created_at: str = Field(description="Creation timestamp")


class GovernanceDecisionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Decision ID")
    project_id: str = Field(description="Owning Project ID")
    kind: GovernanceDecisionKind = Field(description="Decision category")
    subject_id: str | None = Field(description="Related entity ID")
    decision: str = Field(description="Decision name")
    data: dict[str, JsonValue] = Field(description="Category-specific JSON payload")
    created_at: str = Field(description="Creation timestamp")
