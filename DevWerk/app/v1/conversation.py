from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable

from app.v1.agent import AgentCore, AgentRunSpec, ConversationEvidenceRequiredError, _ledger_entry
from app.v1.capabilities import CapabilityRegistry
from app.v1.domain import ToolResult
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
            await self._enqueue_governance_jobs()

    async def _enqueue_governance_jobs(self) -> None:
        for job_id in await asyncio.to_thread(self.store.enqueue_governance_jobs):
            job = await asyncio.to_thread(self.store.get_conversation_job, job_id)
            self._enqueue_job(job)

    def _enqueue_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        if job_id in self._pending_ids:
            return
        project_id = str(job["project_id"])
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
        if self._pending.get(project_id) and not self._stopping:
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
        session_owner = f"conversation-session:{identity['logical_id']}"
        while self._pending[project_id] and not self._stopping:
            job_id = self._pending[project_id].popleft()
            settled = True
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
                        self._pending[project_id].appendleft(job_id)
                        await asyncio.sleep(
                            self.policy.service_limits.event_poll_interval_seconds
                        )
                        continue
                else:
                    try:
                        await self._run_turn(job, session_owner)
                    except Exception:  # noqa: BLE001
                        # _process persisted and logged the failed Turn. The
                        # durable Project Session continues with its next Job.
                        pass
            finally:
                if settled:
                    self._pending_ids.discard(job_id)
        if not self._pending[project_id]:
            self._pending.pop(project_id, None)

    async def _run_turn(self, job: dict[str, Any], session_owner: str) -> None:
        lease_task = asyncio.create_task(
            self._renew_session_lease(str(job["project_id"]), session_owner),
            name=f"conversation-lease-{job['project_id']}",
        )
        try:
            await asyncio.to_thread(self._process, job)
        finally:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)

    async def _renew_session_lease(
        self,
        project_id: str,
        session_owner: str,
    ) -> None:
        while True:
            await asyncio.sleep(
                self.policy.scheduling.conversation_lease_renew_seconds
            )
            renewed = await asyncio.to_thread(
                self.store.renew_conversation_lease,
                project_id,
                session_owner,
            )
            if not renewed:
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
            "status": "running" if self._started and not self._stopping else "stopped",
            "active_sessions": sum(
                1 for task in self._session_tasks.values() if not task.done()
            ),
            "pending_jobs": len(self._pending_ids),
        }

    def _process(self, job: dict[str, Any]) -> None:
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
                    for item in self.store.mailbox(project_id, state="claimed", limit=self.policy.context.mailbox_limit)
                    if item["id"] in captured_ids
                ]
                mailbox_requires_user_update = _mailbox_requires_user_update(mailbox)
                try:
                    workflow = self.store.get_workflow(project_id)
                except KeyError:
                    workflow = None
                capabilities = self.registry.all_ids()
                if workflow is not None:
                    capabilities = [item for item in capabilities if item != "loop.apply"]
                if not job["start_task"]:
                    capabilities = [
                        item
                        for item in capabilities
                        if self.registry.side_effect_kind(item) in {"none", "read"}
                    ]
                trigger_kind = str(job.get("trigger_kind") or "user")
                is_user_turn = trigger_kind == "user"
                context = {
                    "active_workflow": workflow,
                    "global_settings": self.global_settings,
                    "memory": self.store.memory.build_context(project),
                    "loops": self.store.list_loops(limit=20),
                    "workflow_plans": self.store.list_workflow_plans(project_id) if is_user_turn else [],
                    "task_plans": self.store.list_task_plans(project_id) if is_user_turn else [],
                    "tasks": self.store.task_summaries(project_id, self.policy.context.task_summary_limit),
                    "mailbox": mailbox,
                    "conversation_job": {"id": job_id, "start_task": bool(job["start_task"]), "trigger_kind": job.get("trigger_kind", "user"), "trigger": job.get("trigger", {})},
                    "current_request": {
                        "message_id": job.get("user_message_id"),
                        "content": str(job.get("message") or ""),
                        "trigger_kind": job.get("trigger_kind", "user"),
                        "trigger": job.get("trigger", {}),
                        "job_id": job_id,
                    },
                }
                session_id = str(identity["logical_id"])
                history = []
                if not self.store.conversation_session_has_messages(
                    project_id,
                    session_id,
                ):
                    # Import an existing Project's public transcript into its
                    # first Session-bound Run. Subsequent Turns replay the
                    # canonical Session transcript without duplicating it.
                    history = [
                        item
                        for item in self.store.messages(project_id, limit=None)
                        if item.get("id") != job.get("user_message_id")
                        and not (
                            item.get("role") == "assistant"
                            and (item.get("meta") or {}).get("kind") == "notification"
                        )
                        and not (
                            item.get("role") == "assistant"
                            and (item.get("meta") or {}).get("status") == "failed"
                        )
                    ]
                result = self.agent_core.run(AgentRunSpec(
                    kind="conversation",
                    project=project,
                    instruction=str(identity.get("instruction") or ""),
                    instruction_revision=int(identity.get("instruction_revision") or 1),
                    context=context,
                    capability_ids=capabilities,
                    history=history,
                    start_task=bool(job["start_task"]),
                    conversation_job_id=job_id,
                    agent_session_id=session_id,
                    user_initiated=is_user_turn,
                ))
                run_ids = [result.agent_run_id]
                invocations = self.store.tool_invocations(
                    project_id,
                    result.agent_run_id,
                    hydrate_payloads=True,
                )
                all_invocations = list(invocations)
                action_ledger = [
                    _ledger_entry(
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
                tasks = list({item["id"]: item for item in tasks if item.get("id")}.values())
                workflow_publications = [
                    item["result"]["output"]
                    for item in all_invocations
                    if item["capability"] == "workflow.publish"
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                ] if is_user_turn else []
                workflow_publications.extend(
                    item["result"]["output"]["workflow"]
                    for item in all_invocations
                    if item["capability"] == "loop.apply"
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                    and isinstance(item["result"]["output"].get("workflow"), dict)
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
            self.store.fail_conversation_job(
                job_id,
                error,
                result={
                    "error_code": "conversation_processing_failed",
                    "action_ledger": action_ledger,
                    "durable_progress": _has_durable_governance_progress(
                        action_ledger
                    ),
                },
                notification=None,
                attention=isinstance(exc, ConversationEvidenceRequiredError),
            )
            if isinstance(exc, ConversationEvidenceRequiredError):
                return
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
