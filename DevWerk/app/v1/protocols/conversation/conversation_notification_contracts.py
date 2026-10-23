"""Durable Conversation reply and supervision notification records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class ConversationNotificationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["failed", "succeeded"]
    kind: Literal["reply", "notification"]
    job_id: str
    agent_run_id: str | None = None
    error_code: str | None = None
    durable_progress: bool | None = None
    subject_status: str | None = None
    mailbox_ids: list[int] | None = None
    llm_used: bool | None = None


class ConversationNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    meta: ConversationNotificationMeta


class ConversationFailureResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: Literal["conversation_processing_failed"] = "conversation_processing_failed"
    action_ledger: list[dict[str, JsonValue]]
    durable_progress: bool


class ConversationObservedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str
    llm_used: Literal[False] = False
    business_outcome: Literal["observed"] = "observed"
    action_ledger: list[dict[str, JsonValue]]
    task_ids: list[str]
    mailbox_ids: list[int]
