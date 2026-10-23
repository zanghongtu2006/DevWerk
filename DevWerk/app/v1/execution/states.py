from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Generic, TypeVar


class StringStatus(str, Enum):
    """Python 3.10-compatible string status."""

    def __str__(self) -> str:
        return self.value


class TaskStatus(StringStatus):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    RECOVERING = "recovering"
    DONE = "done"
    FAILED = "failed"


class TaskControlState(StringStatus):
    ACTIVE = "active"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"


class ColumnRunStatus(StringStatus):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AttemptStatus(StringStatus):
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AgentRunStatus(StringStatus):
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolInvocationStatus(StringStatus):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FeedbackStatus(StringStatus):
    OPEN = "open"
    VERIFYING = "verifying"
    RESOLVED = "resolved"


class MailboxStatus(StringStatus):
    PENDING = "pending"
    DELIVERED = "delivered"
    RECEIVED = "received"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    ATTENTION = "attention"


StatusT = TypeVar("StatusT", bound=StringStatus)


class StateMachine(Generic[StatusT]):
    def __init__(self, status_type: type[StatusT], transitions: dict[StatusT, set[StatusT]]):
        self.status_type = status_type
        self.transitions = MappingProxyType(
            {source: frozenset(targets) for source, targets in transitions.items()}
        )

    def parse(self, value: str | StatusT) -> StatusT:
        return value if isinstance(value, self.status_type) else self.status_type(value)

    def require(self, current: str | StatusT, target: str | StatusT) -> None:
        source_status = self.parse(current)
        target_status = self.parse(target)
        if source_status == target_status:
            return
        if target_status not in self.transitions[source_status]:
            raise ValueError(
                f"invalid {self.status_type.__name__} transition: "
                f"{source_status.value} -> {target_status.value}"
            )

    def catalog(self) -> dict[str, list[str]]:
        return {
            source.value: sorted(target.value for target in targets)
            for source, targets in self.transitions.items()
        }


TASK_STATE_MACHINE = StateMachine(
    TaskStatus,
    {
        TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.FAILED},
        TaskStatus.RUNNING: {
            TaskStatus.PENDING,
            TaskStatus.WAITING,
            TaskStatus.RECOVERING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
        },
        TaskStatus.WAITING: {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.RECOVERING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
        },
        TaskStatus.RECOVERING: {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
        },
        TaskStatus.DONE: set(),
        TaskStatus.FAILED: {TaskStatus.PENDING},
    },
)

COLUMN_RUN_STATE_MACHINE = StateMachine(
    ColumnRunStatus,
    {
        ColumnRunStatus.PENDING: {
            ColumnRunStatus.RUNNING,
            ColumnRunStatus.FAILED,
            ColumnRunStatus.INTERRUPTED,
        },
        ColumnRunStatus.RUNNING: {
            ColumnRunStatus.WAITING,
            ColumnRunStatus.SUCCEEDED,
            ColumnRunStatus.FAILED,
            ColumnRunStatus.INTERRUPTED,
        },
        ColumnRunStatus.WAITING: {
            ColumnRunStatus.RUNNING,
            ColumnRunStatus.SUCCEEDED,
            ColumnRunStatus.FAILED,
            ColumnRunStatus.INTERRUPTED,
        },
        ColumnRunStatus.SUCCEEDED: set(),
        ColumnRunStatus.FAILED: set(),
        ColumnRunStatus.INTERRUPTED: {ColumnRunStatus.RUNNING, ColumnRunStatus.FAILED},
    },
)

ATTEMPT_STATE_MACHINE = StateMachine(
    AttemptStatus,
    {
        AttemptStatus.RUNNING: {
            AttemptStatus.WAITING,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.INTERRUPTED,
        },
        AttemptStatus.WAITING: {
            AttemptStatus.RUNNING,
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.INTERRUPTED,
        },
        AttemptStatus.SUCCEEDED: set(),
        AttemptStatus.FAILED: set(),
        AttemptStatus.INTERRUPTED: set(),
    },
)

AGENT_RUN_STATE_MACHINE = StateMachine(
    AgentRunStatus,
    {
        AgentRunStatus.RUNNING: {
            AgentRunStatus.WAITING,
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
        },
        AgentRunStatus.WAITING: {
            AgentRunStatus.RUNNING,
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
        },
        AgentRunStatus.SUCCEEDED: set(),
        AgentRunStatus.FAILED: set(),
    },
)

FEEDBACK_STATE_MACHINE = StateMachine(
    FeedbackStatus,
    {
        FeedbackStatus.OPEN: {FeedbackStatus.VERIFYING},
        FeedbackStatus.VERIFYING: {FeedbackStatus.RESOLVED},
        FeedbackStatus.RESOLVED: set(),
    },
)

MAILBOX_STATE_MACHINE = StateMachine(
    MailboxStatus,
    {
        # Addressed input can expire before consumption when its Assignment ends.
        MailboxStatus.PENDING: {MailboxStatus.DELIVERED, MailboxStatus.FAILED},
        MailboxStatus.DELIVERED: {
            MailboxStatus.RECEIVED,
            MailboxStatus.FAILED,
        },
        MailboxStatus.RECEIVED: {
            MailboxStatus.ACKNOWLEDGED,
            MailboxStatus.FAILED,
            MailboxStatus.ATTENTION,
        },
        MailboxStatus.ACKNOWLEDGED: set(),
        MailboxStatus.FAILED: {MailboxStatus.PENDING},
        MailboxStatus.ATTENTION: {MailboxStatus.PENDING},
    },
)


def runtime_status_catalog() -> dict[str, dict[str, object]]:
    from app.v1.protocols.workflow.state_catalog_contracts import RuntimeStatusCatalog, StateCatalogItem

    def entry(machine: StateMachine) -> StateCatalogItem:
        return StateCatalogItem(values=[status.value for status in machine.status_type],
            transitions=machine.catalog())

    return RuntimeStatusCatalog(task=entry(TASK_STATE_MACHINE),
        column_run=entry(COLUMN_RUN_STATE_MACHINE), attempt=entry(ATTEMPT_STATE_MACHINE),
        agent_run=entry(AGENT_RUN_STATE_MACHINE), feedback=entry(FEEDBACK_STATE_MACHINE),
        tool_invocation=StateCatalogItem(values=[status.value for status in ToolInvocationStatus],
            transitions={}), mailbox=entry(MAILBOX_STATE_MACHINE)).model_dump(mode="json")
