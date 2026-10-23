from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import TypeAdapter

from app.v1.contracts import ContractError, canonicalize_contract_value, validate_contract
from app.v1.protocols.tools.agent.capability_completion_contracts import (
    CompletionFunction, CompletionTool, ObjectOutputSchema, completion_input_model,
)
from app.v1.execution.execution_ledger import evidence_entries, execution_progress, unresolved_execution_failures


EvidenceCollection = Literal["model", "runtime"]
EvidenceRequirement = Literal["required", "optional", "forbidden"]


class CompletionProtocolStalled(RuntimeError):
    error_code = "column_completion_protocol_stalled"
    error_category = "protocol_permanent"


@dataclass(frozen=True)
class CompletionOutcomeRule:
    target: str
    evidence_requirement: EvidenceRequirement = "required"
    allows_unresolved_failures: bool = False
    required_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionContract:
    tool_name: str
    outcomes: dict[str, CompletionOutcomeRule]
    output_schema: dict[str, Any] = field(default_factory=dict)
    evidence_collection: EvidenceCollection = "model"
    acceptance_checks: tuple[dict[str, Any], ...] = ()

    def rule(self, outcome: str) -> CompletionOutcomeRule:
        try:
            return self.outcomes[outcome]
        except KeyError as exc:
            raise ValueError(f"undeclared Column outcome: {outcome!r}") from exc


@dataclass(frozen=True)
class FailureResolution:
    failed_evidence_id: str
    resolved_by_evidence_id: str
    reason: str


@dataclass(frozen=True)
class CompletionSubmission:
    outcome: str
    output: dict[str, Any]
    summary: str
    evidence_ids: tuple[str, ...]
    failure_resolutions: tuple[FailureResolution, ...] = ()

    def model_dump(self) -> dict[str, Any]:
        return TypeAdapter(CompletionSubmission).dump_python(self, mode="json")


@dataclass
class CompletionAttemptGuard:
    last_rejection: tuple[str, str, tuple[str, ...]] | None = None
    last_unresolved_operations: frozenset[str] = frozenset()
    rejections: int = 0

    def observe_rejection(
        self,
        arguments: dict[str, Any],
        error: Exception,
        ledger: list[dict[str, Any]],
        *,
        completion_tool_name: str,
    ) -> None:
        unresolved = frozenset(unresolved_execution_failures(ledger))
        if self.last_unresolved_operations - unresolved:
            # An exact successful retry repaired a previously rejected action.
            # Unrelated successful commands must not reset the loop ceiling.
            self.rejections = 0
        self.rejections += 1
        if self.rejections >= 5:
            raise CompletionProtocolStalled(
                f"{completion_tool_name} rejected five completion submissions; review the contract"
            )
        fingerprint = (
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str),
            f"{type(error).__name__}: {error}",
            execution_progress(
                ledger,
                excluded_capabilities={completion_tool_name, "column.await"},
            ),
        )
        if fingerprint == self.last_rejection:
            raise CompletionProtocolStalled(
                f"{completion_tool_name} repeated the same rejected completion "
                "without new execution progress"
            )
        self.last_rejection = fingerprint
        self.last_unresolved_operations = unresolved

    def observe_progress(self) -> None:
        self.last_rejection = None
        self.last_unresolved_operations = frozenset()
        self.rejections = 0


def parse_completion_submission(
    arguments: dict[str, Any],
    contract: CompletionContract,
    ledger: list[dict[str, Any]],
) -> CompletionSubmission:
    model = completion_input_model(list(contract.outcomes), contract.evidence_collection, contract.output_schema)
    schema = model.model_json_schema()
    # Older sessions may still emit receipt IDs. They are never authoritative.
    arguments = dict(arguments)
    if contract.evidence_collection == "runtime":
        arguments.pop("evidence_ids", None)
    if isinstance(arguments.get("output"), str):
        try:
            decoded_output = json.loads(arguments["output"])
        except (TypeError, ValueError):
            decoded_output = None
        if isinstance(decoded_output, dict):
            arguments["output"] = decoded_output
    if "output" not in arguments:
        try:
            validate_contract({}, ObjectOutputSchema.model_validate(contract.output_schema).model_dump(mode="json"), label="Column output")
        except ValueError:
            pass
        else:
            arguments["output"] = {}
    normalized = canonicalize_contract_value(arguments, schema)
    missing = [name for name, field in model.model_fields.items()
               if field.is_required() and name not in normalized]
    if missing:
        raise ContractError(f"{missing[0]} is required")
    validate_contract(normalized, schema, label=contract.tool_name)
    request = model.model_validate(normalized)
    outcome = request.outcome
    contract.rule(outcome)
    evidence_ids = () if contract.rule(outcome).evidence_requirement == "forbidden" else (
        tuple(
            str(item["evidence_id"])
            for item in evidence_entries(
                ledger,
                excluded_capabilities={contract.tool_name, "column.await"},
            )
            if item.get("evidence_id")
        )
        if contract.evidence_collection == "runtime"
        else tuple(request.evidence_ids)
    )
    resolutions = tuple(
        FailureResolution(
            failed_evidence_id=item.failed_evidence_id,
            resolved_by_evidence_id=item.resolved_by_evidence_id,
            reason=item.reason,
        )
        for item in request.failure_resolutions
    )
    return CompletionSubmission(
        outcome=outcome,
        output=request.output,
        summary=request.summary,
        evidence_ids=evidence_ids,
        failure_resolutions=resolutions,
    )


def completion_tool_schema(contract: CompletionContract) -> dict[str, Any]:
    return CompletionTool(function=CompletionFunction(
            name=contract.tool_name,
            description=(
                "Submit one declared assignment outcome with structured output and auditable evidence. "
                "Runtime collects evidence automatically. Frozen acceptance checks and successful identical retries "
                "discharge failures. For an alternative repair, pass failure_resolutions linking the failed receipt "
                "to later successful evidence from the same capability, with a concrete reason. "
                "An unrelated or non-verifying action cannot resolve a failure."
            ),
            parameters=completion_arguments_schema(contract),
        )).model_dump(mode="json")


def completion_arguments_schema(contract: CompletionContract) -> dict[str, Any]:
    return completion_input_model(
        list(contract.outcomes), contract.evidence_collection, contract.output_schema
    ).model_json_schema()
