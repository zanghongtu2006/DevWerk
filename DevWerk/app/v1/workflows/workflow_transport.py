"""Typed transport view of the dynamic Workflow fields used for normalization.

This view accepts incomplete drafts. Full graph validation belongs to WorkflowDefinition.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _TransportObject(BaseModel):
    model_config = ConfigDict(extra="allow")


class CapabilityStepTransport(_TransportObject):
    capability: str | None = None
    arguments: Any = None


class ExecutorTransport(_TransportObject):
    kind: str | None = None
    steps: list[CapabilityStepTransport] | None = None


class PollWaitTransport(_TransportObject):
    poll_capability: str | None = None
    poll_arguments: Any = None
    cancel_capability: str | None = None
    cancel_arguments: Any = None
    cleanup_capability: str | None = None
    cleanup_arguments: Any = None


class ColumnTransport(_TransportObject):
    executor: ExecutorTransport | None = None
    wait_policy: PollWaitTransport | None = None


class WorkflowTransport(_TransportObject):
    columns: list[ColumnTransport] | None = None


class LegacyCheckTransport(_TransportObject):
    key: str | None = None


class LegacyRequiredCheckTransport(_TransportObject):
    column: str | None = None
    check_key: str | None = None


class LegacyEdgeTransport(_TransportObject):
    target: Any = None
    allows_unresolved_failures: bool = False
    required_checks: list[LegacyRequiredCheckTransport] | None = None


class LegacyColumnTransport(_TransportObject):
    key: str | None = None
    acceptance_checks: list[LegacyCheckTransport] = Field(default_factory=list)
    transitions: list[LegacyEdgeTransport] = Field(default_factory=list)


class LegacyTerminalTransport(_TransportObject):
    failure: str = "failed"


class LegacyWorkflowTransport(_TransportObject):
    terminals: LegacyTerminalTransport = Field(default_factory=LegacyTerminalTransport)
    columns: list[Any] = Field(default_factory=list)

    def migrate_column_checks(self) -> dict[str, Any]:
        migrated: list[Any] = []
        for original in self.columns:
            if not isinstance(original, dict):
                migrated.append(original)
                continue
            column = LegacyColumnTransport.model_validate(original)
            check_keys = [check.key for check in column.acceptance_checks if check.key]
            for edge in column.transitions:
                if ("required_checks" not in edge.model_fields_set and check_keys
                        and edge.target != self.terminals.failure
                        and not edge.allows_unresolved_failures):
                    edge.required_checks = [
                        LegacyRequiredCheckTransport(column=column.key, check_key=key)
                        for key in check_keys
                    ]
            migrated.append(column.model_dump(mode="json", exclude_unset=True))
        self.columns = migrated
        return self.model_dump(mode="json", exclude_unset=True)
