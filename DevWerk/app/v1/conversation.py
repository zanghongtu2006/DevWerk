from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict
from typing import Any, Callable

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import CapabilityRegistry
from app.v1.store import V1Store


log = logging.getLogger("devwerk.v1.conversation")


class ConversationAgent:
    """One durable logical Agent per Project, executed as serialized bounded turns."""

    def __init__(
        self,
        store: V1Store,
        registry: CapabilityRegistry,
        on_task_created: Callable[[], None] | None = None,
        workers: int = 4,
        agent_core: AgentCore | None = None,
    ):
        self.store = store
        self.registry = registry
        self.agent_core = agent_core or AgentCore(store, registry)
        self._on_task_created = on_task_created
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._workers = [
            threading.Thread(target=self._worker, name=f"conversation-agent-{index + 1}", daemon=True)
            for index in range(max(1, workers))
        ]
        for worker in self._workers:
            worker.start()
        self._dispatcher = threading.Thread(target=self._dispatch_governance, name="conversation-governance-dispatcher", daemon=True)
        self._dispatcher.start()
        for job in self.store.recover_conversation_jobs():
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
        while not self._stop.wait(1.0):
            try:
                self.wake()
            except Exception:  # noqa: BLE001
                log.exception("conversation governance dispatch failed")

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
            try:
                if job_id is None:
                    return
                job = self.store.claim_conversation_job(job_id, worker_id)
                if job:
                    self._process(job)
            except Exception:  # noqa: BLE001
                log.exception("conversation worker failed job_id=%s", job_id)
            finally:
                self._queue.task_done()

    def _process(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        project_id = job["project_id"]
        lease_keeper = GovernanceLeaseKeeper(self.store, project_id, str(job.get("worker_id") or threading.current_thread().name))
        lease_keeper.start()
        try:
            with self._locks[project_id]:
                project = self.store.get_project(project_id)
                identity = self.store.conversation_agent(project_id)
                captured_ids = set(job.get("mailbox_ids") or [])
                mailbox = [item for item in self.store.mailbox(project_id, state="pending", limit=100) if not captured_ids or item["id"] in captured_ids]
                try:
                    workflow = self.store.get_workflow(project_id)
                except KeyError:
                    workflow = None
                capabilities = self.registry.all_ids()
                if not job["start_task"]:
                    capabilities = [item for item in capabilities if item not in {"workflow.publish", "task.create"}]
                result = self.agent_core.run(
                    AgentRunSpec(
                        kind="conversation",
                        project=project,
                        instruction=str(identity.get("instruction") or ""),
                        instruction_revision=int(identity.get("instruction_revision") or 1),
                        context={
                            "active_workflow": workflow,
                            "tasks": self.store.task_summaries(project_id, 100),
                            "mailbox": mailbox,
                            "conversation_job": {"id": job_id, "start_task": bool(job["start_task"]), "trigger_kind": job.get("trigger_kind", "user"), "trigger": job.get("trigger", {})},
                        },
                        capability_ids=capabilities,
                        history=_bounded_history(self.store.messages(project_id, 80)),
                        start_task=bool(job["start_task"]),
                        max_iterations=16,
                        max_tool_calls=80,
                        timeout_seconds=600,
                    )
                )
                if result.status != "succeeded":
                    raise RuntimeError(result.error or "Conversation Agent failed")

                invocations = self.store.tool_invocations(project_id, result.agent_run_id)
                tasks = [
                    item["result"]["output"]
                    for item in invocations
                    if item["capability"] == "task.create"
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                ]
                workflow_publications = [
                    item["result"]["output"]
                    for item in invocations
                    if item["capability"] == "workflow.publish"
                    and item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                ]
                direct_artifact_ids = [
                    item["result"]["output"]["artifact"]["id"]
                    for item in invocations
                    if item["ok"]
                    and isinstance(item.get("result", {}).get("output"), dict)
                    and isinstance(item["result"]["output"].get("artifact"), dict)
                    and item["result"]["output"]["artifact"].get("id")
                ]
                self.store.add_message(
                    project_id,
                    "assistant",
                    result.text,
                    {
                        "status": "succeeded",
                        "job_id": job_id,
                        "agent_run_id": result.agent_run_id,
                        "task_ids": [task["id"] for task in tasks],
                        "workflow_revision_ids": [item["id"] for item in workflow_publications],
                    },
                )
                first_task_id = tasks[0]["id"] if tasks else None
                self.store.finish_conversation_job(
                    job_id,
                    first_task_id,
                    result.agent_run_id,
                    {
                        "reply": result.text,
                        "workflow_revision_ids": [item["id"] for item in workflow_publications],
                        "task_ids": [task["id"] for task in tasks],
                        "direct_artifact_ids": direct_artifact_ids,
                    },
                )
                if tasks and self._on_task_created:
                    self._on_task_created()
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"[:2000]
            log.exception("conversation turn failed project_id=%s job_id=%s", project_id, job_id)
            self.store.fail_conversation_job(job_id, error)
        finally:
            lease_keeper.stop()


def _bounded_history(messages: list[dict[str, Any]], max_bytes: int = 120_000) -> list[dict[str, Any]]:
    """Keep newest complete messages under a byte budget; DB remains the source of truth."""
    selected: list[dict[str, Any]] = []
    used = 0
    for message in reversed(messages):
        size = len(str(message.get("content") or "").encode("utf-8")) + 512
        if selected and used + size > max_bytes:
            break
        selected.append(message)
        used += size
    return list(reversed(selected))


class GovernanceLeaseKeeper:
    def __init__(self, store: V1Store, project_id: str, worker_id: str):
        self.store, self.project_id, self.worker_id = store, project_id, worker_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"governance-lease-{project_id[-8:]}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.wait(60):
            if not self.store.renew_conversation_lease(self.project_id, self.worker_id):
                return
