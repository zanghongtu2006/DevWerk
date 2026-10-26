"""Runtime-owned cancellation and execution bounds, shared by tools and agents."""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Callable


class ExecutionOwnershipLost(RuntimeError):
    error_code = "execution_ownership_lost"
    error_category = "runtime_consistency"


class ExecutionBudgetExceeded(RuntimeError):
    error_code = "execution_budget_exceeded"
    error_category = "runtime_budget"


class ExecutionReplayUncertain(RuntimeError):
    error_code = "effect_outcome_unknown"
    error_category = "runtime_consistency"


class ConversationTurnPreempted(RuntimeError):
    """A newer user message superseded a Conversation turn at a safe boundary."""

    error_code = "conversation_turn_preempted"
    error_category = "user_steering"


@dataclass
class ExecutionControl:
    deadline: float
    validate_owner: Callable[[], None] | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    preempted: threading.Event = field(default_factory=threading.Event)

    def check_preempt(self) -> None:
        if self.preempted.is_set():
            raise ConversationTurnPreempted("A newer user message is ready for this Project Session")

    def check(self) -> None:
        if self.cancelled.is_set():
            raise ExecutionOwnershipLost("Execution was cancelled or lost its lease")
        if time.monotonic() >= self.deadline:
            self.cancelled.set()
            raise ExecutionBudgetExceeded("Agent wall-clock budget exhausted")
        if self.validate_owner:
            self.validate_owner()
