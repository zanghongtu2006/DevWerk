"""Conversation Tool inputs with class-owned defaults and field contracts."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.v1.conversations.conversation_intent import TurnResolution, UserEvidence, WorkIntentState
from app.v1.protocols.conversation.conversation_intent_contracts import UserTurnContract


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationTurnResolutionInput(TurnResolution):
    source_message_id: int = Field(default=None, description="Current user message ID; host binds it when omitted")
    based_on_intent_revision: int = Field(default=None, ge=0,
                                          description="Current intent revision; host binds it when omitted")

    def bind_host(self, message_id: int, revision: int) -> TurnResolution:
        bound = self.model_copy(update={
            "source_message_id": self.source_message_id if self.source_message_id is not None else message_id,
            "based_on_intent_revision": self.based_on_intent_revision if self.based_on_intent_revision is not None else revision,
            "user_evidence": self.user_evidence if "user_evidence" in self.model_fields_set else [UserEvidence(message_id=message_id)],
        })
        return TurnResolution.model_validate(bound.model_dump(mode="json"))


class ExecutionAuthorizeInput(EmptyInput):
    request: Literal["begin", "continue", "extend"] = Field(description="Requested Requirement execution scope")
    scope_summary: str = Field(default=None, max_length=30000, description="Optional updated objective")
    requirement_id: str = Field(default=None, description="Existing Requirement for continue or extend")
    new_scope: bool = Field(default=None, description="Discard the prior discussion proposal for a new scope")


class TaskControlAuthorizeInput(EmptyInput):
    action: Literal["task.pause", "task.resume", "task.cancel"] = Field(description="Requested Task control action")
    task_id: str = Field(min_length=1, description="Task ID named by the current user request")


class ConversationHistoryInput(EmptyInput):
    latest_cursor: ClassVar[int] = (1 << 63) - 1
    before_message_id: int = Field(default=None, ge=1, description="Exclusive message cursor")
    limit: int = Field(default=10, ge=1, le=30, description="Maximum archived messages")

    def before(self, current_message_id: int | None) -> int:
        return min(self.before_message_id or self.latest_cursor, current_message_id or self.latest_cursor)


class PriorTurnRevision(BaseModel):
    model_config = ConfigDict(extra="allow")
    based_on_intent_revision: int = Field(ge=0)


class ConversationReplyReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rendered: str
    conversation_report: dict[str, JsonValue]


class ConversationTurnInspectReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_message_id: int = Field(ge=1)
    turn_contract: UserTurnContract
    work_intent: WorkIntentState
    requested_mode: Literal["auto", "discuss"]


class ConversationHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str


class ConversationHistoryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ConversationHistoryMessage]
    next_before_message_id: int | None = Field(ge=1)
