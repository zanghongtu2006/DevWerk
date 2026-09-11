from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from app.v1.completion_protocol import CompletionContract
from app.v1.domain import AgentModelResponse
from app.v1.execution_control import ExecutionControl


ModelComplete = Callable[..., AgentModelResponse]


@dataclass(frozen=True)
class AgentRunSpec:
    kind: Literal["conversation", "column"]
    project: dict[str, Any]
    instruction: str
    instruction_revision: int
    context: dict[str, Any]
    capability_ids: list[str]
    history: list[dict[str, Any]] = field(default_factory=list)
    task_id: str | None = None
    column_run_id: str | None = None
    column_attempt_id: str | None = None
    start_task: bool = True
    completion_contract: CompletionContract | None = None
    wait_config: dict[str, Any] = field(default_factory=dict)
    conversation_job_id: str | None = None
    agent_session_id: str | None = None
    writable_paths: tuple[str, ...] | None = None
    user_initiated: bool = False
    execution_control: ExecutionControl | None = None
    supervision_turn: bool = False
    assignment: dict[str, Any] | None = None
    agent_instance_id: str | None = None
    requirement_id: str | None = None
    require_conversation_report: bool = False


@dataclass(frozen=True)
class AgentRunResult:
    agent_run_id: str
    status: Literal["succeeded", "waiting", "failed"]
    text: str = ""
    completion: dict[str, Any] | None = None
    tool_calls: int = 0
    iterations: int = 0
    error: str | None = None
    error_category: str | None = None
    wait_request: dict[str, Any] | None = None
    error_code: str | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)
