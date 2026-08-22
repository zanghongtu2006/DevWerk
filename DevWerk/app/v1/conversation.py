from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict
from typing import Any, Callable

from app.v1.agent import AgentCore, AgentRunSpec, ConversationEvidenceRequiredError, _ledger_entry
from app.v1.capabilities import CapabilityRegistry
from app.v1.domain import ToolResult
from app.v1.store import V1Store
from app.v1.policy import DEFAULT_V1_RUNTIME_POLICY, PlatformPolicySnapshot, V1RuntimePolicy


log = logging.getLogger("devwerk.v1.conversation")


class ConversationAgent:
    """One durable logical Agent per Project, without platform execution budgets."""

    def __init__(
        self,
        store: V1Store,
        registry: CapabilityRegistry,
        on_task_created: Callable[[], None] | None = None,
        workers: int | None = None,
        agent_core: AgentCore | None = None,
        policy: V1RuntimePolicy | None = None,
        platform_policy: PlatformPolicySnapshot | None = None,
    ):
        self.store = store
        self.registry = registry
        self.policy = policy or DEFAULT_V1_RUNTIME_POLICY
        self.platform_policy = platform_policy or store.latest_platform_policy()
        self.agent_core = agent_core or AgentCore(store, registry, policy=self.policy, platform_policy=self.platform_policy)
        self._on_task_created = on_task_created
        self._locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._workers = [
            threading.Thread(target=self._worker, name=f"conversation-agent-{index + 1}", daemon=True)
            for index in range(max(1, workers or self.policy.scheduling.conversation_workers))
        ]
        for worker in self._workers:
            worker.start()
        self._dispatcher = threading.Thread(target=self._dispatch_governance, name="conversation-governance-dispatcher", daemon=True)
        self._dispatcher.start()
        for job in self.store.startup_conversation_jobs():
            self._queue.put(job["id"])

    def submit(self, project_id: str, message: str, start_task: bool = True) -> dict[str, Any]:
        job = self.store.create_conversation_job(project_id, message, start_task)
        self._queue.put(job["id"])
        return {
            "status": "accepted",
            "job": job,
            "message": self.store.messages(project_id, 1)[0],
            "agent_run": None,
            "workflow": None,
            "tasks": [],
        }

    def stop(self) -> None:
        self._stop.set()
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=1)
        self._dispatcher.join(timeout=1)

    def wake(self) -> None:
        for job_id in self.store.enqueue_governance_jobs():
            self._queue.put(job_id)

    def _dispatch_governance(self) -> None:
        while not self._stop.wait(self.policy.service_limits.event_poll_interval_seconds):
            self.wake()

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False

    def _worker(self) -> None:
        worker_id = threading.current_thread().name
        while not self._stop.is_set():
            job_id = self._queue.get()
            if job_id is None:
                self._queue.task_done()
                return
            queued = self.store.get_conversation_job(job_id)
            claim_deferred = False
            with self._locks[queued["project_id"]]:
                job = self.store.claim_conversation_job(job_id, worker_id)
                if job:
                    self._process(job)
                elif self.store.get_conversation_job(job_id)["status"] == "queued":
                    claim_deferred = True
            if claim_deferred and not self._stop.wait(
                self.policy.service_limits.event_poll_interval_seconds
            ):
                self._queue.put(job_id)
            self._queue.task_done()

    def _process(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        project_id = job["project_id"]
        action_ledger: list[dict[str, Any]] = []
        mailbox: list[dict[str, Any]] = []
        lease_keeper = GovernanceLeaseKeeper(self.store, project_id, str(job.get("worker_id") or threading.current_thread().name))
        lease_keeper.start()
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
                if workflow is None:
                    capabilities = [
                        item for item in capabilities
                        if item not in {
                            "orchestration.plan.save",
                            "workflow.publish",
                            "task.create",
                        }
                    ]
                else:
                    capabilities = [item for item in capabilities if item != "loop.apply"]
                if not job["start_task"]:
                    capabilities = [
                        item
                        for item in capabilities
                        if self.registry.side_effect_kind(item) in {"none", "read"}
                    ]
                context = {
                    "active_workflow": workflow,
                    "loops": self.store.list_loops(limit=20),
                    "orchestration_plans": self.store.list_orchestration_plans(project_id),
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
                        "task.create", "task.reopen", "task.rerun", "task.retry", "task.resume", "scheduling.decide", "loop.apply"
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
                for item in all_invocations:
                    if (
                        item["capability"] == "loop.apply"
                        and item["ok"]
                        and isinstance(item.get("result", {}).get("output"), dict)
                    ):
                        tasks.extend(item["result"]["output"].get("tasks") or [])
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
                direct_artifact_ids = [
                    item["result"]["output"]["artifact"]["id"]
                    for item in all_invocations
                    if item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                    and isinstance(item["result"]["output"].get("artifact"), dict)
                    and item["result"]["output"]["artifact"].get("id")
                ]
                conversation_reply = result.text.strip()
                trigger_kind = str(job.get("trigger_kind") or "user")
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
        finally:
            lease_keeper.stop()

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


class GovernanceLeaseKeeper:
    def __init__(self, store: V1Store, project_id: str, worker_id: str):
        self.store, self.project_id, self.worker_id = store, project_id, worker_id
        self.policy = store.policy
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"governance-lease-{project_id[-8:]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(self.policy.scheduling.conversation_lease_renew_seconds):
            if not self.store.renew_conversation_lease(self.project_id, self.worker_id):
                return
