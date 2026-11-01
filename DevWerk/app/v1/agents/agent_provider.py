from __future__ import annotations

import logging
import time
from app.services.visible_stream import listen_for_visible_text
from typing import Any

from app.core.debug_trace import trace_json
from app.v1.agents.agent_models import AgentRunSpec, ModelComplete
from app.v1.domain import AgentModelResponse
from app.v1.context.context_manager import ContextManager, ContextExhausted, digest
from app.services.provider_errors import LLMProviderError
from app.v1.protocols.agent.agent_runtime_receipts import AgentTraceContext
from app.v1.tooling.capability_registry import CapabilityContext


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
        capability_context: CapabilityContext | None = None,
    ) -> None:
        self.store = store
        self.model_complete = model_complete
        self.spec = spec
        self.run_id = run_id
        self.registry = registry or store.registry
        self.capability_context = capability_context
        self.last_presented_schemas: dict[str, dict[str, Any]] = {}
        self.context = None

    def request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        iteration: int,
        require_tool: bool,
        required_tool_name: str | None,
        budget_view=None,
    ) -> AgentModelResponse:
        # Keep the Run's capability snapshot stable, but stop advertising
        # one-shot authorization tools after this Job has committed its boundary.
        # Dispatch still enforces the durable grant for every business effect.
        tools = self._visible_tools(tools)
        if self.spec.kind == 'conversation' and required_tool_name == 'conversation.reply':
            tools = [tool for tool in tools
                     if tool.get('function', {}).get('name') == 'conversation.reply']
        self.last_presented_schemas = {
            str(tool.get('function', {}).get('name') or ''):
                tool.get('function', {}).get('parameters') or {}
            for tool in tools
        }
        if self.context is None:
            self.context = ContextManager(self.store, self.spec, self.run_id, self.model_complete)
        working_messages = messages
        messages = self.context.prepare(working_messages, tools, budget_view=budget_view)
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
            response = self._complete_turn(messages, tools, iteration, kwargs)
        except LLMProviderError as exc:
            if exc.error_code != 'LLM_CONTEXT_WINDOW_EXCEEDED':
                raise
            previous = digest(messages)
            previous_size = self.context.estimate(messages, tools)
            reduced = self.context.prepare(working_messages, tools, force=True,
                                           budget_view=budget_view)
            if digest(reduced) == previous or self.context.estimate(reduced, tools) >= previous_size:
                raise ContextExhausted('Provider overflow: no smaller valid request is available') from exc
            if self.spec.execution_control:
                self.spec.execution_control.check()
            if self.spec.assignment:
                self.store.agents.charge(self.spec.assignment, models=1)
            try:
                response = self._complete_turn(reduced, tools, iteration, kwargs)
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

    def _complete_turn(self, messages, tools, iteration, kwargs):
        if self.spec.kind != "conversation" or not self.spec.user_initiated:
            return self.model_complete(messages, tools, **kwargs)
        preview = ""
        structured = False
        last_sent = None
        last_visible = ""

        def send_preview(text: str) -> None:
            nonlocal last_sent, last_visible
            now = time.monotonic()
            if text.strip() and text != last_visible and (last_sent is None or now - last_sent >= 0.4):
                if self.spec.execution_control:
                    self.spec.execution_control.check()
                self.store.record_conversation_progress(self.run_id, kind="visible_text",
                    content=text[-2400:], details={"iteration": iteration})
                last_sent = now
                last_visible = text

        def receive(text: str) -> None:
            nonlocal preview, structured
            if structured:
                return
            if not preview.strip() and text.lstrip().startswith(("{", "[", "`", "<")):
                structured = True
                return
            preview = (preview + text)[-2400:]
            send_preview(preview)

        with listen_for_visible_text(receive, public_field=("conversation.reply", "message", send_preview)):
            return self.model_complete(messages, tools, **kwargs)

    def _visible_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.spec.kind != 'conversation' or not self.spec.conversation_job_id:
            return tools
        job = self.store.get_conversation_job(self.spec.conversation_job_id)
        if self.store.intents.contract(job).get('phase') not in {'execution', 'targeted_control'}:
            return tools
        one_shot = {'conversation.execution.authorize', 'conversation.task.control.authorize'}
        return [tool for tool in tools if tool.get('function', {}).get('name') not in one_shot]

    def _trace_context(self, iteration: int) -> dict[str, Any]:
        return AgentTraceContext(agent_run_id=self.run_id,
            conversation_job_id=self.spec.conversation_job_id,
            project_id=self.spec.project["id"], task_id=self.spec.task_id,
            column_run_id=self.spec.column_run_id,
            column_attempt_id=self.spec.column_attempt_id,
            agent_kind=self.spec.kind, iteration=iteration).model_dump(mode="json")
