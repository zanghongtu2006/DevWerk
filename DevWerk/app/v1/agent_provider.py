from __future__ import annotations

import logging
from typing import Any

from app.core.debug_trace import trace_json
from app.v1.agent_models import AgentRunSpec, ModelComplete
from app.v1.domain import AgentModelResponse


trace_log = logging.getLogger("devwerk.agent.trace")


class ProviderTurnRequester:
    """Issue and audit one normalized Provider turn."""

    def __init__(
        self,
        *,
        store: Any,
        model_complete: ModelComplete,
        spec: AgentRunSpec,
        run_id: str,
    ) -> None:
        self.store = store
        self.model_complete = model_complete
        self.spec = spec
        self.run_id = run_id

    def request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        iteration: int,
        require_tool: bool,
        required_tool_name: str | None,
    ) -> AgentModelResponse:
        if self.spec.kind == "conversation":
            self.store.record_conversation_progress(
                self.run_id,
                kind="provider_wait",
                content=f"第 {iteration} 轮：已向 LLM Provider 提交完整上下文，正在等待响应。",
                details={"iteration": iteration},
            )
        trace_json(
            trace_log,
            "agent.model_input",
            **self._trace_context(iteration),
            messages=messages,
            tools=tools,
            require_tool=require_tool,
            required_tool_name=required_tool_name,
        )
        response = self.model_complete(
            messages,
            tools,
            project_id=self.spec.project["id"],
            task_id=self.spec.task_id,
            agent="conversation" if self.spec.kind == "conversation" else "column",
            require_tool=require_tool,
            required_tool_name=required_tool_name,
        )
        trace_json(
            trace_log,
            "agent.model_output",
            **self._trace_context(iteration),
            response=(
                response.model_dump(mode="json")
                if isinstance(response, AgentModelResponse)
                else response
            ),
        )
        if not isinstance(response, AgentModelResponse):
            response = AgentModelResponse.model_validate(response)
        return response

    def _trace_context(self, iteration: int) -> dict[str, Any]:
        return {
            "agent_run_id": self.run_id,
            "conversation_job_id": self.spec.conversation_job_id,
            "project_id": self.spec.project["id"],
            "task_id": self.spec.task_id,
            "column_run_id": self.spec.column_run_id,
            "column_attempt_id": self.spec.column_attempt_id,
            "agent_kind": self.spec.kind,
            "iteration": iteration,
        }
