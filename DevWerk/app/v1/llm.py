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
    require_tool: bool = False,
    required_tool_name: str | None = None,
) -> AgentModelResponse:
    client = get_llm_client(agent)
    result = client.complete(
        messages,
        tools,
        project_id=project_id,
        task_id=task_id,
        require_tool=require_tool,
        required_tool_name=required_tool_name,
    )
    return AgentModelResponse.model_validate(result)
