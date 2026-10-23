"""Persisted Conversation intent and turn-boundary wire contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from app.v1.conversations.conversation_intent import WorkIntentState


class UserTurnContract(BaseModel):
    model_config = ConfigDict(extra="allow")  # Read older supervision contracts without discarding their fields.
    phase: Literal["unresolved", "discussion", "execution", "targeted_control", "supervision"] = "unresolved"
    act: Literal["discuss", "execute", "status", "control", "clarify", "supervise"] | None = None
    source_message_id: int | None = Field(default=None, ge=1)
    intent_revision: int | None = Field(default=None, ge=0)
    execution_grant_id: str | None = None
    control_capability: Literal["task.pause", "task.resume", "task.cancel"] | None = None
    control_task_id: str | None = None
    supervised_task_id: str | None = None


class RequirementSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    project_id: str
    scope_key: str
    objective: str
    revision: int = Field(ge=1)
    status: str
    created_at: str
    updated_at: str


class RequirementListReceipt(RootModel[list[RequirementSnapshot]]):
    pass


class TurnResolutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_contract: UserTurnContract
    work_intent: WorkIntentState
    requirement: RequirementSnapshot | None
    next_reply_contract: str
