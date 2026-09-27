from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.services.llm_factory import get_llm_client
from app.v1.domain import AgentModelResponse


def context_limits(agent: str) -> dict[str, Any]:
    from app.core.config import settings
    cfg = settings().get_llm_config(agent)
    window = cfg.get('context_window')
    # Known direct-provider default; other endpoints must declare their limit.
    if window is None and urlsplit(cfg.get('base_url', '')).hostname in {'api.minimax.io', 'api.minimaxi.com'} and cfg['model'] in {
        'MiniMax-M2.7', 'MiniMax-M2.7-highspeed', 'MiniMax-M2.5', 'MiniMax-M2.5-highspeed',
    }:
        window = 204800
    if window is None:
        raise ValueError('LLM model context_window must be configured for this endpoint/model')
    return {'window': window, 'output': cfg['max_tokens'], 'protocol': cfg['protocol'], 'model': cfg['model']}


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


complete.context_limits = context_limits
