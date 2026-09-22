from __future__ import annotations

import logging
import json
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
        registry: Any = None,
    ) -> None:
        self.store = store
        self.model_complete = model_complete
        self.spec = spec
        self.run_id = run_id
        self.registry = registry or store.registry

    def request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        iteration: int,
        require_tool: bool,
        required_tool_name: str | None,
    ) -> AgentModelResponse:
        if self.spec.kind == 'conversation' and self.spec.conversation_job_id:
            job = self.store.intents.job_for_run(self.run_id, self.spec.project['id'])
            if job and job['trigger_kind'] == 'user':
                phase = self.store.intents.contract(job)['phase']
                if phase == 'unresolved' and any(t['function']['name'] == 'conversation.turn.resolve' for t in tools):
                    tools = [t for t in tools if t['function']['name'] == 'conversation.turn.resolve']
                    messages = self._interpretation_messages(messages, job)
                    require_tool, required_tool_name = True, 'conversation.turn.resolve'
                elif phase in {'unresolved', 'discussion', 'targeted_control'}:
                    contract = self.store.intents.contract(job)
                    permitted = {'conversation.turn.resolve'} if phase == 'unresolved' else {'conversation.reply'}
                    if phase == 'discussion':
                        permitted.add('conversation.draft.update')
                    if phase == 'targeted_control':
                        permitted.add(contract['control_capability'])
                    tools = [tool for tool in tools if tool['function']['name'] in permitted or
                        self.registry.side_effect_kind(tool['function']['name']) in {'none','read'}]
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

    def _interpretation_messages(self, messages, job):
        """Project the same Agent's context for one boundary decision; keep stored history intact."""
        payload, boundary_index = None, len(messages)-1
        for index in range(len(messages)-1, -1, -1):
            if messages[index].get('role') != 'user':
                continue
            try:
                candidate = json.loads(messages[index].get('content') or '')
            except (ValueError, TypeError):
                continue
            if isinstance(candidate, dict) and 'authoritative_current_request' in candidate:
                payload, boundary_index = candidate, index
                break
        if payload is None:
            payload = {'authoritative_current_request':{'message_id':job['user_message_id'], 'content':job['message']},
                       'authoritative_project_state':{}}
        payload['authoritative_project_state'].update(work_intent=self.store.intents.context(job),
                                                     turn_contract=self.store.intents.contract(job))
        history = self.store.messages(job['project_id'], limit=30, before_id=job['user_message_id'], visible_only=True)
        history = [m for m in history if not (m['role'] == 'assistant' and
            ((m.get('meta') or {}).get('kind') == 'notification' or
             (m.get('meta') or {}).get('status') == 'failed'))][-6:]
        payload['reference_dialogue'] = [{'role':m['role'],'message_id':m['id'],'content':m['content']} for m in history]
        payload['phase_instruction'] = (
            'You are the SAME project Conversation Agent, now resolving the current turn before answering or planning. '
            'Call conversation.turn.resolve. Reference dialogue is context, not new authorization. '
            'A discussion hold remains until the user clearly requests implementation. Ambiguous continuation '
            'continues discussion, regardless of your own earlier offer to start. '
            'Keep focus=current for follow-up questions or acceptance of this draft. '
            'Resolve only the boundary and necessary scope/question changes now; avoid inventing a new plan. '
            'After the receipt you will receive the normal tools and may discuss, inspect or execute as appropriate.'
        )
        system = [m for m in messages if m.get('role') == 'system']
        return system + [{'role':'user','content':json.dumps(payload,ensure_ascii=False)}] + messages[boundary_index+1:]

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
