from __future__ import annotations

import logging
from typing import Any

from app.core.debug_trace import trace_json
from app.v1.agent_models import AgentRunSpec, ModelComplete
from app.v1.domain import AgentModelResponse
from app.v1.context_manager import ContextManager, ContextExhausted, digest
from app.services.provider_errors import LLMProviderError


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
        registry: Any = None,
    ) -> None:
        self.store = store
        self.model_complete = model_complete
        self.spec = spec
        self.run_id = run_id
        self.registry = registry or store.registry
        self.context = None

    def request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        iteration: int,
        require_tool: bool,
        required_tool_name: str | None,
    ) -> AgentModelResponse:
        # Keep one stable tool catalog throughout the Project Agent's session.
        # Dispatch checks the durable turn grant before business mutations;
        # discussion, inspection and memory reads need no preliminary tool call.
        if self.context is None:
            self.context = ContextManager(self.store, self.spec, self.run_id, self.model_complete)
        working_messages = messages
        messages = self.context.prepare(working_messages, tools)
        if self.spec.kind == "conversation":
            self.store.record_conversation_progress(
                self.run_id,
                kind="provider_wait",
                content=f"第 {iteration} 轮：已向 LLM Provider 提交本阶段上下文，正在等待响应。",
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
        kwargs = dict(project_id=self.spec.project['id'], task_id=self.spec.task_id,
                      agent=self.spec.kind, require_tool=require_tool, required_tool_name=required_tool_name)
        try:
            response = self.model_complete(messages, tools, **kwargs)
        except LLMProviderError as exc:
            if exc.error_code != 'LLM_CONTEXT_WINDOW_EXCEEDED':
                raise
            previous = digest(messages)
            previous_size = self.context.estimate(messages, tools)
            reduced = self.context.prepare(working_messages, tools, force=True)
            if digest(reduced) == previous or self.context.estimate(reduced, tools) >= previous_size:
                raise ContextExhausted('Provider overflow: no smaller valid request is available') from exc
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if self.spec.assignment:
                self.store.agents.charge(self.spec.assignment, models=1)
            try:
                response = self.model_complete(reduced, tools, **kwargs)
            except LLMProviderError as retry_error:
                if retry_error.error_code == 'LLM_CONTEXT_WINDOW_EXCEEDED':
                    raise ContextExhausted('Provider overflow after one reduced-context retry') from retry_error
                raise
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
        self.context.observe(response)
        trace_json(trace_log, 'agent.context_budget', **self._trace_context(iteration), **self.context.last_metrics)
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
