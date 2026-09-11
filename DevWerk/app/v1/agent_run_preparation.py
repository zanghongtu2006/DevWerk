from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.v1.agent_models import AgentRunSpec
from app.v1.agent_prompt import (
    build_conversation_system_envelope,
    build_run_envelope,
    stable_json,
)
from app.v1.capabilities import CapabilityContext, CapabilityRegistry
from app.v1.completion_protocol import CompletionContract, completion_tool_schema
from app.v1.policy import PlatformPolicySnapshot, V1RuntimePolicy
from app.v1.session_replay import replayable_session_messages
from app.v1.tool_protocol import await_tool_schema


@dataclass(frozen=True)
class PreparedAgentRun:
    run: dict[str, Any]
    completion_contract: CompletionContract | None
    completion_tool_name: str
    allowed: list[str]
    effect_kinds: dict[str, str]
    tools: list[dict[str, Any]]
    capability_context: CapabilityContext
    messages: list[dict[str, Any]]
    logical_ledger: list[dict[str, Any]]


class AgentRunPreparer:
    """Resolve capabilities, persist the Run, and assemble its initial context."""

    def __init__(
        self,
        *,
        store: Any,
        registry: CapabilityRegistry,
        runtime_policy: V1RuntimePolicy,
        platform_policy: PlatformPolicySnapshot | None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.runtime_policy = runtime_policy
        self.platform_policy = platform_policy

    def prepare(self, spec: AgentRunSpec) -> PreparedAgentRun:
        if spec.kind == "column" and spec.completion_contract is None:
            raise ValueError("Column Agent requires an explicit CompletionContract")
        completion_contract = spec.completion_contract
        completion_tool_name = (
            completion_contract.tool_name
            if completion_contract is not None
            else "column.complete"
        )
        platform_policy = self.platform_policy or self.store.latest_platform_policy()
        pre_run_context = self._capability_context(spec)
        allowed = list(dict.fromkeys(spec.capability_ids))
        if spec.assignment and self.registry.contains('agent.context.read') and 'agent.context.read' not in allowed:
            allowed.append('agent.context.read')
        capabilities = self.registry.resolve(allowed, pre_run_context)
        effect_kinds = {item.id: item.side_effect_kind for item in capabilities}
        tools = [item.tool_schema() for item in capabilities]
        if spec.kind == "column":
            assert completion_contract is not None
            tools.append(completion_tool_schema(completion_contract))
            if spec.wait_config:
                tools.append(await_tool_schema(allowed, spec.wait_config))

        envelope = build_run_envelope(spec, platform_policy)
        run = self.store.begin_agent_run(
            project_id=spec.project["id"],
            kind=spec.kind,
            instruction_revision=spec.instruction_revision,
            instruction_snapshot=spec.instruction,
            context_snapshot=envelope,
            capabilities=allowed + (
                [completion_tool_name] if spec.kind == "column" else []
            ),
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            column_attempt_id=spec.column_attempt_id,
            platform_policy=platform_policy,
            runtime_policy=self.runtime_policy,
            conversation_job_id=spec.conversation_job_id,
            agent_session_id=spec.agent_session_id,
        )
        capability_context = self._capability_context(spec, agent_run_id=run["id"])
        with self.store.tx(immediate=True) as db:
            db.execute('UPDATE v1_agent_runs SET operation_protocol=1 WHERE id=?', (run['id'],))
        if spec.assignment or spec.agent_instance_id:
            with self.store.tx(immediate=True) as db:
                db.execute("UPDATE v1_agent_runs SET assignment_id=?,agent_instance_id=? WHERE id=?",
                           ((spec.assignment or {}).get("id"), spec.agent_instance_id, run["id"]))
        provider_envelope = (
            build_conversation_system_envelope(spec, platform_policy)
            if spec.kind == "conversation"
            else envelope
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": stable_json(provider_envelope)}
        ]
        self.store.add_agent_message(run["id"], "system", messages[0]["content"], [])
        current_request = (
            spec.context.get("current_request")
            if isinstance(spec.context, dict)
            else None
        )
        self._append_session_history(spec, run["id"], messages, current_request)
        self._append_explicit_history(spec, run["id"], messages)
        self._append_current_request(
            spec,
            run["id"],
            messages,
            current_request,
        )
        if spec.assignment:
            current = stable_json({'current_assignment': {'id': spec.assignment['id'], 'task_id': spec.task_id,
                                   'requirement_id': spec.requirement_id, 'generation': spec.assignment['generation'],
                                   'input': spec.context},
                                   'instruction': 'This is the current work contract. Earlier session messages are context, not new assignments or authority to change this contract.'})
            messages.append({'role': 'user', 'content': current})
            self.store.add_agent_message(run['id'], 'user', current, [], emit_progress=False)
        logical_ledger = [
            dict(item)
            for item in (spec.context.get("action_ledger") or [])
            if isinstance(item, dict)
        ]
        return PreparedAgentRun(
            run=run,
            completion_contract=completion_contract,
            completion_tool_name=completion_tool_name,
            allowed=allowed,
            effect_kinds=effect_kinds,
            tools=tools,
            capability_context=capability_context,
            messages=messages,
            logical_ledger=logical_ledger,
        )

    def _capability_context(
        self,
        spec: AgentRunSpec,
        *,
        agent_run_id: str | None = None,
    ) -> CapabilityContext:
        return CapabilityContext(
            project_id=spec.project["id"],
            project=spec.project,
            store=self.store,
            agent_run_id=agent_run_id,
            task_id=spec.task_id,
            column_run_id=spec.column_run_id,
            column_attempt_id=spec.column_attempt_id,
            start_task=spec.start_task,
            writable_paths=spec.writable_paths,
            user_initiated=spec.user_initiated,
            execution_control=spec.execution_control,
            assignment=spec.assignment,
            agent_instance_id=spec.agent_instance_id,
            requirement_id=spec.requirement_id,
        )

    def _append_session_history(
        self,
        spec: AgentRunSpec,
        run_id: str,
        messages: list[dict[str, Any]],
        current_request: Any,
    ) -> None:
        if not spec.agent_session_id:
            return
        if spec.kind == "conversation":
            history = replayable_session_messages(
                self.store.conversation_session_messages(
                    spec.project["id"],
                    spec.agent_session_id,
                    before_message_id=(
                        current_request.get("message_id")
                        if isinstance(current_request, dict)
                        else None
                    ),
                )
            )
            messages.extend(history)
            return
        if spec.assignment:
            history = replayable_session_messages(self.store.agents.session_history(
                spec.project["id"], spec.agent_session_id, before_run_id=run_id))
            messages.extend(history)
            return
        history = self.store.agent_session_messages(
            spec.project["id"], spec.agent_session_id
        )
        if not history:
            return
        item = {
            "role": "user",
            "content": stable_json({
                "logical_agent_session_history": history,
                "instruction": (
                    "Resume the same logical assignment; current context and review feedback "
                    "are authoritative."
                ),
            }),
        }
        messages.append(item)
        self.store.add_agent_message(
            run_id, "user", item["content"], [], emit_progress=False
        )

    def _append_explicit_history(
        self,
        spec: AgentRunSpec,
        run_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        for message in spec.history:
            if message.get("role") not in {"user", "assistant"}:
                continue
            item = {
                "role": message["role"],
                "content": str(message.get("content") or ""),
            }
            messages.append(item)
            self.store.add_agent_message(
                run_id,
                item["role"],
                item["content"],
                [],
                emit_progress=False,
            )

    def _append_current_request(
        self,
        spec: AgentRunSpec,
        run_id: str,
        messages: list[dict[str, Any]],
        current_request: Any,
    ) -> None:
        if not isinstance(current_request, dict):
            return
        turn_context = {
            key: value for key, value in spec.context.items() if key != "current_request"
        }
        turn_payload: dict[str, Any] = {
            "authoritative_current_request": current_request,
            "authoritative_project_state": {
                "project": spec.project,
                **turn_context,
            },
            "instruction": (
                "This immutable request created the current Conversation Job. It is authoritative "
                "over historical conversation instructions. The accompanying Project state is "
                "the current Turn projection; use tools for any new inspection or state change."
            ),
        }
        if messages and messages[-1].get("role") == "user":
            prior = messages.pop()
            turn_payload["unanswered_prior_user_message"] = str(
                prior.get("content") or ""
            )
        item = {"role": "user", "content": stable_json(turn_payload)}
        messages.append(item)
        self.store.add_agent_message(run_id, "user", item["content"], [])
