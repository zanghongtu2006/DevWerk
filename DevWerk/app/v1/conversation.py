from __future__ import annotations

import asyncio
import logging
import threading
import time
from uuid import uuid4
from collections import defaultdict, deque
from typing import Any, Callable

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.agent_protocol import ConversationProtocolStalled
from app.v1.execution_control import ExecutionControl
from app.v1.capabilities import CapabilityRegistry
from app.v1.domain import ToolResult
from app.v1.execution_ledger import ledger_entry
from app.v1.store import V1Store
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, PlatformPolicySnapshot, V1RuntimePolicy


log = logging.getLogger("devwerk.v1.conversation")


class ConversationGateway:
    """Run one durable logical Conversation Session per Project."""

    def __init__(
        self,
        store: V1Store,
        registry: CapabilityRegistry,
        on_task_created: Callable[[], None] | None = None,
        agent_core: AgentCore | None = None,
        policy: V1RuntimePolicy | None = None,
        platform_policy: PlatformPolicySnapshot | None = None,
        global_settings: dict[str, Any] | None = None,
    ):
        self.store = store
        self.registry = registry
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self.platform_policy = platform_policy or store.latest_platform_policy()
        self.global_settings = dict(global_settings or {})
        self.agent_core = agent_core or AgentCore(store, registry, policy=self.policy, platform_policy=self.platform_policy)
        self._on_task_created = on_task_created
        self._locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        self._pending: defaultdict[str, deque[str]] = defaultdict(deque)
        self._pending_ids: set[str] = set()
        self._session_tasks: dict[str, asyncio.Task[None]] = {}
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._wake_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._stopping = False

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._started = True
        self._stopping = False
        self._wake_event = asyncio.Event()
        for job in await asyncio.to_thread(self.store.startup_conversation_jobs):
            self._enqueue_job(job)
        self._dispatcher_task = asyncio.create_task(
            self._dispatch_governance(),
            name="conversation-gateway-dispatcher",
        )

    async def submit(self, project_id: str, message: str, start_task: bool = True) -> dict[str, Any]:
        if not self._started:
            await self.start()
        job = await asyncio.to_thread(
            self.store.create_conversation_job,
            project_id,
            message,
            start_task,
        )
        self._enqueue_job(job)
        return {
            "status": "accepted",
            "job": job,
            "message": self.store.messages(project_id, 1)[0],
            "agent_run": None,
            "workflow": None,
            "tasks": [],
        }

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
        session_tasks = list(self._session_tasks.values())
        for task in session_tasks:
            task.cancel()
        tasks = [task for task in [self._dispatcher_task, *session_tasks] if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._session_tasks.clear()
        self._pending.clear()
        self._pending_ids.clear()
        self._dispatcher_task = None
        self._wake_event = None
        self._loop = None
        self._started = False

    def wake(self) -> None:
        """Wake governance from either Runtime threads or the Web event loop."""
        if (
            not self._started
            or self._stopping
            or self._loop is None
            or self._wake_event is None
        ):
            return
        self._loop.call_soon_threadsafe(self._wake_event.set)

    async def wake_async(self) -> None:
        if not self._started:
            await self.start()
        await self._enqueue_governance_jobs()

    async def _dispatch_governance(self) -> None:
        while not self._stopping:
            assert self._wake_event is not None
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self.policy.service_limits.event_poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()
            try:
                await self._enqueue_governance_jobs()
            except Exception:
                log.exception("conversation dispatcher tick failed; retrying")

    async def _enqueue_governance_jobs(self) -> None:
        for job_id in await asyncio.to_thread(self.store.enqueue_governance_jobs):
            job = await asyncio.to_thread(self.store.get_conversation_job, job_id)
            self._enqueue_job(job)
        # The database is the queue. A failed claim or lost process-local wakeup
        # must not orphan an accepted request until the next server restart.
        for job in await asyncio.to_thread(self.store.queued_conversation_jobs):
            self._enqueue_job(job)

    def _enqueue_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        project_id = str(job["project_id"])
        if job_id not in self._pending_ids:
            self._pending_ids.add(job_id)
            self._pending[project_id].append(job_id)
        task = self._session_tasks.get(project_id)
        if task is None or task.done():
            task = asyncio.create_task(
                self._drain_session(project_id),
                name=f"conversation-session-{project_id}",
            )
            self._session_tasks[project_id] = task
            task.add_done_callback(
                lambda completed, key=project_id: self._session_finished(key, completed)
            )

    def _session_finished(self, project_id: str, task: asyncio.Task[None]) -> None:
        if self._session_tasks.get(project_id) is task:
            self._session_tasks.pop(project_id, None)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            assert error is not None
            log.error(
                "conversation session task failed project_id=%s",
                project_id,
                exc_info=(type(error), error, error.__traceback__),
            )
        if self._pending.get(project_id) and not self._stopping and not task.cancelled() and task.exception() is None:
            next_task = asyncio.create_task(
                self._drain_session(project_id),
                name=f"conversation-session-{project_id}",
            )
            self._session_tasks[project_id] = next_task
            next_task.add_done_callback(
                lambda completed, key=project_id: self._session_finished(key, completed)
            )

    async def _drain_session(self, project_id: str) -> None:
        identity = await asyncio.to_thread(self.store.conversation_agent, project_id)
        session_owner = f"conversation-session:{identity['logical_id']}:{uuid4().hex}"
        while self._pending[project_id] and not self._stopping:
            job_id = self._pending[project_id][0]
            settled = False
            try:
                job = await asyncio.to_thread(
                    self.store.claim_conversation_job,
                    job_id,
                    session_owner,
                )
                if job is None:
                    queued = await asyncio.to_thread(
                        self.store.get_conversation_job,
                        job_id,
                    )
                    if queued["status"] == "queued":
                        settled = False
                        await asyncio.sleep(
                            self.policy.service_limits.event_poll_interval_seconds
                        )
                        continue
                    settled = True
                else:
                    settled = True
                    try:
                        await self._run_turn(job, session_owner)
                    except Exception:  # noqa: BLE001
                        # _process persisted and logged the failed Turn. The
                        # durable Project Session continues with its next Job.
                        pass
            finally:
                if settled:
                    self._pending[project_id].popleft()
                    self._pending_ids.discard(job_id)
        if not self._pending[project_id]:
            self._pending.pop(project_id, None)

    async def _run_turn(self, job: dict[str, Any], session_owner: str) -> None:
        control = ExecutionControl(time.monotonic()+self.policy.execution.agent_wall_seconds,
                                   validate_owner=lambda: self.store.assert_conversation_owner(job))
        lease_task = asyncio.create_task(
            self._renew_session_lease(str(job["project_id"]), session_owner, control),
            name=f"conversation-lease-{job['project_id']}",
        )
        try:
            await asyncio.to_thread(self._process, job, control)
        finally:
            control.cancelled.set()
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)

    async def _renew_session_lease(
        self,
        project_id: str,
        session_owner: str,
        control: ExecutionControl | None = None,
    ) -> None:
        while True:
            await asyncio.sleep(
                self.policy.scheduling.conversation_lease_renew_seconds
            )
            try:
                renewed = await asyncio.to_thread(self.store.renew_conversation_lease, project_id, session_owner)
            except Exception:
                if control:
                    control.cancelled.set()
                log.exception("conversation lease renewal failed project=%s", project_id)
                return
            if not renewed:
                if control:
                    control.cancelled.set()
                return

    async def wait_for_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            active = any(not task.done() for task in self._session_tasks.values())
            if not active and not self._pending_ids:
                return True
            await asyncio.sleep(0.01)
        return False

    def status(self) -> dict[str, Any]:
        return {
            "status": "running" if self._started and not self._stopping and self._dispatcher_task and not self._dispatcher_task.done() else "stopped",
            "active_sessions": sum(
                1 for task in self._session_tasks.values() if not task.done()
            ),
            "pending_jobs": len(self._pending_ids),
        }

    def _process(self, job: dict[str, Any], control: ExecutionControl | None = None) -> None:
        job_id = job["id"]
        project_id = job["project_id"]
        action_ledger: list[dict[str, Any]] = []
        mailbox: list[dict[str, Any]] = []
        try:
            with self._locks[project_id]:
                project = self.store.get_project(project_id)
                identity = self.store.conversation_agent(project_id)
                captured_ids = set(job.get("mailbox_ids") or [])
                mailbox = [
                    item
                    for item in self.store.mailbox(project_id, state="received", limit=self.policy.context.mailbox_limit)
                    if item["id"] in captured_ids
                ]
                mailbox_requires_user_update = _mailbox_requires_user_update(mailbox)
                try:
                    workflow = self.store.get_workflow(project_id)
                except KeyError:
                    workflow = None
                # A Project Session keeps one stable model-visible capability
                # surface. Turn-specific authority is enforced at dispatch;
                # changing tool schemas between turns destroys prompt-prefix
                # reuse and makes the same logical Agent appear to lose tools.
                capabilities = self.registry.all_ids()
                trigger_kind = str(job.get("trigger_kind") or "user")
                is_user_turn = trigger_kind == "user"
                main = self.store.agents.main(project_id)
                requirement = self.store.agents.turn_requirement(job, mailbox)
                graph_mutation_allowed = bool(requirement and requirement['status'] == 'active' and (job['start_task'] or not is_user_turn))
                if requirement and not is_user_turn:
                    current_requirement = self.store.agents.get_requirement(project_id, requirement['id'])
                    graph_mutation_allowed = graph_mutation_allowed and current_requirement['status'] == 'active' and current_requirement['revision'] == requirement['revision']
                context = {
                    "requirement": requirement,
                    "workers": self.store.agents.list_workers(project_id),
                    "active_workflow": workflow,
                    "global_settings": self.global_settings,
                    "memory": self.store.memory.build_context(project),
                    "loops": self.store.list_loops(limit=20),
                    "workflow_plans": self.store.list_workflow_plans(project_id),
                    "task_plans": self.store.list_task_plans(project_id),
                    "tasks": self.store.task_summaries(project_id, self.policy.context.task_summary_limit),
                    "mailbox": mailbox,
                    "conversation_job": {
                        "id": job_id,
                        "start_task": bool(job["start_task"]),
                        "trigger_kind": job.get("trigger_kind", "user"),
                        "trigger": job.get("trigger", {}),
                        "task_graph_mutation_allowed": graph_mutation_allowed,
                    },
                    "turn_authority": {
                        "task_graph_mutation_allowed": graph_mutation_allowed,
                        "instruction": (
                            "You are this Project's sole persistent Conversation Agent, equivalent to its main agent. There is no higher System Main Agent. Within the active Requirement, use "
                            "Worker results to continue planning, create repair work and deliver the goal. "
                            "Use existing plan/task references before creating work; repeated facts do not "
                            "require duplicate Tasks. Closed Requirements cannot be revived by late events. "
                            "For a user-requested scope extension use project.scope.inspect then project.scope.revise, never close/create the old scope. "
                            "Save a new TaskPlan against the returned Workflow and create only additional Tasks. Scope revision itself creates no Tasks. "
                            "If a frozen method must change, inspect its current Loop digest and pass loop_digest explicitly. "
                            "For continued novels use ending_policy=continue; do not raise chapter length limits without the user's request. "
                            "Columns execute one leaf Worker each; collaboration belongs in the workflow."
                        ),
                    },
                    "current_request": {
                        "message_id": job.get("user_message_id"),
                        "content": str(job.get("message") or ""),
                        "trigger_kind": job.get("trigger_kind", "user"),
                        "trigger": job.get("trigger", {}),
                        "job_id": job_id,
                    },
                }
                session_id = str(identity["logical_id"])
                result = self.agent_core.run(AgentRunSpec(
                    kind="conversation",
                    project=project,
                    instruction=str(identity.get("instruction") or ""),
                    instruction_revision=int(identity.get("instruction_revision") or 1),
                    context=context,
                    capability_ids=capabilities,
                    start_task=graph_mutation_allowed,
                    agent_instance_id=main['id'],
                    requirement_id=requirement['id'] if requirement else None,
                    conversation_job_id=job_id,
                    agent_session_id=session_id,
                    user_initiated=is_user_turn,
                    execution_control=control,
                    supervision_turn=not is_user_turn,
                    require_conversation_report=True,
                ))
                run_ids = [result.agent_run_id]
                invocations = self.store.tool_invocations(
                    project_id,
                    result.agent_run_id,
                    hydrate_payloads=True,
                )
                all_invocations = list(invocations)
                action_ledger = [
                    ledger_entry(
                        result.agent_run_id,
                        str(item["tool_call_id"]),
                        str(item["capability"]),
                        self.registry.side_effect_kind(str(item["capability"])),
                        ToolResult.model_validate(item["result"]),
                        arguments=dict(item.get("arguments") or {}),
                    )
                    for item in invocations
                ]
                runnable_mutation = any(
                    item["ok"]
                    and item["capability"] in {
                        "task.create", "task.reopen", "task.rerun", "task.retry", "task.resume", "scheduling.decide"
                    }
                    for item in invocations
                )
                if runnable_mutation and self._on_task_created:
                    self._on_task_created()
                if result.status != "succeeded":
                    raise RuntimeError(result.error or "Conversation Agent failed")

                tasks = [
                    item["result"]["output"]
                    for item in all_invocations
                    if item["capability"] in {"task.create", "task.rerun"}
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                ]
                created_plan_ids = {item.get('task_plan_id') for item in tasks if item.get('task_plan_id')}
                tasks.extend(item for item in self.store.list_tasks(project_id) if item.get('task_plan_id') in created_plan_ids)
                tasks = list({item["id"]: item for item in tasks if item.get("id")}.values())
                workflow_publications = [
                    item["result"]["output"]
                    for item in all_invocations
                    if item["capability"] == "workflow.publish"
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                ]
                workflow_publications.extend(
                    item["result"]["output"]["workflow"]
                    for item in all_invocations
                    if item["capability"] == "loop.apply"
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                    and isinstance(item["result"]["output"].get("workflow"), dict)
                )
                workflow_publications.extend(
                    self.store.get_workflow_revision(project_id, item['result']['output']['workflow_revision_id'])
                    for item in all_invocations if item['capability'] == 'project.scope.revise' and item['ok']
                )
                direct_artifact_ids = [
                    item["result"]["output"]["artifact"]["id"]
                    for item in all_invocations
                    if item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                    and isinstance(item["result"]["output"].get("artifact"), dict)
                    and item["result"]["output"]["artifact"].get("id")
                ]
                conversation_reply = result.text.strip()
                publish_reply = trigger_kind == "user" or mailbox_requires_user_update
                notification = (
                    {
                        "content": conversation_reply,
                        "meta": {
                            "status": "succeeded",
                            "kind": "reply" if trigger_kind == "user" else "notification",
                            "job_id": job_id,
                            "agent_run_id": result.agent_run_id,
                            "agent_run_ids": run_ids,
                            "task_ids": [task["id"] for task in tasks],
                            "workflow_revision_ids": [item["id"] for item in workflow_publications],
                            "mailbox_ids": [item["id"] for item in mailbox],
                            "subject_status": _mailbox_subject_status(mailbox),
                        },
                    }
                    if publish_reply
                    else None
                )
                first_task_id = tasks[0]["id"] if tasks else None
                self.store.finish_conversation_job(
                    job_id,
                    first_task_id,
                    result.agent_run_id,
                    {
                        "reply": conversation_reply,
                        "completion": result.completion or {},
                        "agent_run_ids": run_ids,
                        "action_ledger": action_ledger,
                        "workflow_revision_ids": [item["id"] for item in workflow_publications],
                        "task_ids": [task["id"] for task in tasks],
                        "direct_artifact_ids": direct_artifact_ids,
                    },
                    notification=notification,
                )
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            log.exception("conversation turn failed project_id=%s job_id=%s", project_id, job_id)
            failed_run = next(
                (
                    item
                    for item in self.store.agent_runs(project_id=project_id)
                    if str(item.get("conversation_job_id") or "") == str(job_id)
                ),
                None,
            )
            if failed_run is not None:
                invocations = self.store.tool_invocations(
                    project_id,
                    str(failed_run["id"]),
                    hydrate_payloads=True,
                )
                action_ledger = [
                    ledger_entry(
                        str(failed_run["id"]),
                        str(item["tool_call_id"]),
                        str(item["capability"]),
                        self.registry.side_effect_kind(str(item["capability"])),
                        ToolResult.model_validate(item["result"]),
                        arguments=dict(item.get("arguments") or {}),
                    )
                    for item in invocations
                ]
            durable_progress = _has_durable_governance_progress(action_ledger)
            notification = None
            if isinstance(exc, ConversationProtocolStalled) and str(job.get("trigger_kind") or "user") == "user":
                notification = {
                    "content": (
                        (
                            "本轮已停止：Conversation Agent 重复提交相同且失败的工具操作；"
                            "此前成功操作及其回执已保留。"
                        )
                        if durable_progress
                        else (
                            "本轮未完成：Conversation Agent 重复提交相同且失败的工具操作，"
                            "Runtime 已停止重复调用；项目状态未改变。"
                        )
                    ),
                    "meta": {
                        "status": "failed",
                        "kind": "reply",
                        "job_id": job_id,
                        "agent_run_id": failed_run["id"] if failed_run else None,
                        "error_code": "conversation_protocol_stalled",
                        "durable_progress": durable_progress,
                    },
                }
            elif str(job.get("trigger_kind") or "user") == "user":
                notification = {
                    "content": f"本轮未完成：{error}",
                    "meta": {
                        "status": "failed",
                        "kind": "reply",
                        "job_id": job_id,
                        "agent_run_id": failed_run["id"] if failed_run else None,
                        "error_code": "conversation_processing_failed",
                        "durable_progress": durable_progress,
                    },
                }
            self.store.fail_conversation_job(
                job_id,
                error,
                agent_run_id=(str(failed_run["id"]) if failed_run else None),
                result={
                    "error_code": "conversation_processing_failed",
                    "action_ledger": action_ledger,
                    "durable_progress": durable_progress,
                },
                notification=notification,
            )
            raise

_TASK_TERMINAL_EVENTS = {"task.done", "task.failed"}
_USER_UPDATE_EVENTS = _TASK_TERMINAL_EVENTS | {"conversation.planning_failed"}


def _mailbox_requires_user_update(mailbox: list[dict[str, Any]]) -> bool:
    return any(item.get("event_type") in _USER_UPDATE_EVENTS for item in mailbox)


def _mailbox_subject_status(mailbox: list[dict[str, Any]]) -> str | None:
    event_types = {str(item.get("event_type") or "") for item in mailbox}
    if "task.failed" in event_types:
        return "failed"
    if "task.done" in event_types:
        return "done"
    if "conversation.planning_failed" in event_types:
        return "supervision_failed"
    return None


def _has_durable_governance_progress(
    action_ledger: list[dict[str, Any]],
) -> bool:
    """Recognize persisted delivery progress without trusting assistant prose."""
    task_progress_controls = {
        "task.cancel",
        "task.create",
        "task.fail",
        "task.rerun",
        "task.reopen",
        "task.resume",
        "task.retry",
        "task.plan.save",
        "workflow.plan.save",
        "workflow.publish",
        "loop.apply",
    }
    return any(
        item.get("ok")
        and item.get("status") == "completed"
        and (
            item.get("effect_kind") in {"write", "process"}
            or item.get("capability") in task_progress_controls
        )
        for item in action_ledger
    )
