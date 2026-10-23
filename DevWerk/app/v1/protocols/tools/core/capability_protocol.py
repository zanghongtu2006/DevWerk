"""Finite wire values and shared decisions for capability execution."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel, TypeAdapter, model_validator

from app.v1.contracts import ContractError


class SideEffectKind(str, Enum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    PROCESS = "process"
    CONTROL = "control"
    SESSION_CONTROL = "session_control"

    @property
    def uses_control_transaction(self) -> bool:
        return self in (self.CONTROL, self.SESSION_CONTROL)

    @property
    def mutates_workspace(self) -> bool:
        return self in (self.WRITE, self.PROCESS)

    @property
    def may_have_uncommitted_effect(self) -> bool:
        return self in (self.WRITE, self.PROCESS, self.CONTROL)

    @property
    def supports_acceptance_check(self) -> bool:
        return self in (self.NONE, self.READ, self.PROCESS)


class CapabilityToolset(str, Enum):
    CORE = "core"
    MCP = "mcp"


class ExecutionReceiptStatus(str, Enum):
    STARTED = "started"
    AWAITING = "awaiting"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureDisposition(str, Enum):
    REJECTED_BEFORE_EFFECT = "rejected_before_effect"
    EXECUTION_FAILED = "execution_failed"


class BuiltinCapability(str, Enum):
    PROJECT_FILE_WRITE = "project.files.write"
    PROJECT_COMMAND_RUN = "project.command.run"
    SYSTEM_COMMAND_RUN = "system.command.run"
    MEMORY_READ = "project.memory.read"
    MEMORY_SEARCH = "project.memory.search"
    MEMORY_WRITE = "project.memory.write"
    MEMORY_APPEND = "project.memory.append"
    MEMORY_SUPERSEDE = "project.memory.supersede"

    @property
    def is_memory(self) -> bool:
        return self in {
            self.MEMORY_READ, self.MEMORY_SEARCH, self.MEMORY_WRITE,
            self.MEMORY_APPEND, self.MEMORY_SUPERSEDE,
        }


MEMORY_CAPABILITIES = frozenset(item.value for item in BuiltinCapability if item.is_memory)
COMMAND_CAPABILITIES = frozenset({
    BuiltinCapability.PROJECT_COMMAND_RUN.value,
    BuiltinCapability.SYSTEM_COMMAND_RUN.value,
})


class ConversationPhase(str, Enum):
    DISCUSSION = "discussion"
    EXECUTION = "execution"
    TARGETED_CONTROL = "targeted_control"


class ConversationTrigger(str, Enum):
    USER = "user"


class MemoryScope(str, Enum):
    PROJECT = "project"
    TASK = "task"
    WORKFLOW = "workflow"
    CONVERSATION = "conversation"


class GovernanceDecisionKind(str, Enum):
    BACKLOG = "backlog"
    SCHEDULING = "scheduling"
    DIRECT_EXECUTION = "direct_execution"
    INTERVENTION = "intervention"


class ToolErrorCode(str, Enum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_IN_PROGRESS = "execution_in_progress"
    PRIOR_EXECUTION_FAILED = "prior_execution_failed"
    COMMAND_FAILED = "command_failed"
    OPERATION_FAILED = "operation_failed"


class ToolError(BaseModel):
    """Stable failure envelope; unknown legacy error types keep a generic code."""

    model_config = ConfigDict(extra="allow")
    type: str
    message: str = ""
    code: str | None = None

    @model_validator(mode="after")
    def fill_code(self) -> "ToolError":
        if self.code:
            return self
        category = {
            "ContractError": ToolErrorCode.INVALID_ARGUMENTS,
            "ValueError": ToolErrorCode.INVALID_ARGUMENTS,
            "KeyError": ToolErrorCode.NOT_FOUND,
            "FileNotFoundError": ToolErrorCode.NOT_FOUND,
            "PermissionError": ToolErrorCode.PERMISSION_DENIED,
            "LeafCapabilityDenied": ToolErrorCode.PERMISSION_DENIED,
            "ExecutionInProgress": ToolErrorCode.EXECUTION_IN_PROGRESS,
            "PriorExecutionFailed": ToolErrorCode.PRIOR_EXECUTION_FAILED,
            "CommandFailed": ToolErrorCode.COMMAND_FAILED,
            "LegacyCommandFailed": ToolErrorCode.COMMAND_FAILED,
        }
        self.code = category.get(self.type, ToolErrorCode.OPERATION_FAILED).value
        return self


@dataclass(frozen=True)
class CapabilityReadLimits:
    memory_page_characters: int = 2000
    memory_search_results: int = 8
    memory_preview_characters: int = 160
    memory_preview_bytes: int = 320
    archived_result_default_characters: int = 4000
    archived_result_max_characters: int = 8000
    archived_result_min_bytes: int = 512
    archived_result_max_bytes: int = 6000
    archived_result_byte_ratio_numerator: int = 3
    archived_result_byte_ratio_denominator: int = 2


READ_LIMITS = CapabilityReadLimits()


class DynamicToolArguments(RootModel[dict[str, JsonValue]]):
    """JSON-valued Provider arguments with controlled dynamic field replacement."""

    def replacing(self, name: str, value: Any) -> dict[str, JsonValue]:
        changed = self.model_copy(deep=True)
        changed.root[name] = TypeAdapter(JsonValue).validate_python(value)
        return changed.root


@dataclass(frozen=True)
class StructuredObjectParameter:
    """Decode a Provider's JSON-string wrapper once at the Tool input boundary."""

    name: str

    def normalize(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.name not in arguments:
            return arguments
        value = arguments[self.name]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    f"{self.name} must be an object; serialized JSON is malformed at "
                    f"line {exc.lineno}, column {exc.colno}. Pass an object value."
                ) from exc
        if not isinstance(value, dict):
            raise ContractError(f"{self.name} must be an object value")
        return DynamicToolArguments.model_validate(arguments).replacing(self.name, value)
