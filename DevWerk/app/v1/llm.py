from __future__ import annotations

from typing import Any

from app.services.llm_factory import get_llm_client
from app.v1.domain import AgentModelResponse


def complete(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    project_id: str,
    task_id: str | None = None,
    agent: str = "project",
    timeout_seconds: float | None = None,
) -> AgentModelResponse:
    client = get_llm_client(agent, timeout_seconds)
    result = client.complete(messages, tools, project_id=project_id, task_id=task_id)
    return AgentModelResponse.model_validate(result)
