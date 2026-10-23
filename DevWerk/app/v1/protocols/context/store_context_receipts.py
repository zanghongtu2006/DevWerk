"""Fixed context frames projected from Conversation history and earlier runs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationHistoryFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str


class SessionToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str
    capability: str
    ok: bool
    error_type: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=300)


class SessionRunIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_run_id: str
    conversation_job_id: str | None
    status: str
    error: str = Field(max_length=400)
    recent_receipts: list[SessionToolReceipt]
