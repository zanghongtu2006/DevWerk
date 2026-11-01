"""Minimal typed views of the public DevWerk HTTP responses used by evaluation.

Unknown additive API fields are preserved as evidence. Their contents are
dynamic Provider/Project data, not fixed evaluation protocol keys.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class HTTPView(BaseModel):
    model_config = ConfigDict(extra="allow")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class HealthView(HTTPView):
    status: str
    supervisor: str
    conversation_gateway: dict[str, JsonValue]


class ProjectView(HTTPView):
    id: str
    base_dir: str


class JobView(HTTPView):
    id: str
    status: str
    user_message_id: int = Field(ge=1)
    agent_run_id: str | None = None
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None


class SubmissionView(HTTPView):
    job: JobView


class TaskView(HTTPView):
    id: str
    status: str


class AgentRunView(HTTPView):
    id: str
    kind: str
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)


class AgentMessageView(HTTPView):
    sequence: int = Field(ge=1)


class ToolInvocationView(HTTPView):
    sequence: int = Field(ge=1)
    capability: str
    ok: bool


class AgentRunDetailView(AgentRunView):
    messages: list[AgentMessageView]
    tool_invocations: list[ToolInvocationView]


class VisibleMessageView(HTTPView):
    id: int = Field(ge=1)
    role: str
    content: str


class BoardView(HTTPView):
    workflow: JsonValue | None


class QuiescenceView(HTTPView):
    quiescent: bool

