from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from app.v1.contracts import canonicalize_contract_value, validate_contract
from app.v1.execution_ledger import evidence_entries, execution_progress


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
        return {
            "outcome": self.outcome,
            "output": self.output,
            "summary": self.summary,
            "evidence_ids": list(self.evidence_ids),
            "failure_resolutions": [
                {
                    "failed_evidence_id": item.failed_evidence_id,
                    "resolved_by_evidence_id": item.resolved_by_evidence_id,
                    "reason": item.reason,
                }
                for item in self.failure_resolutions
            ],
        }


@dataclass
class CompletionAttemptGuard:
    last_rejection: tuple[str, str, tuple[str, ...]] | None = None
    rejections: int = 0

    def observe_rejection(
        self,
        arguments: dict[str, Any],
        error: Exception,
        ledger: list[dict[str, Any]],
        *,
        completion_tool_name: str,
    ) -> None:
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

    def observe_progress(self) -> None:
        self.last_rejection = None


def parse_completion_submission(
    arguments: dict[str, Any],
    contract: CompletionContract,
    ledger: list[dict[str, Any]],
) -> CompletionSubmission:
    schema = completion_arguments_schema(contract)
    # Older sessions may still emit receipt IDs. They are never authoritative.
    arguments = dict(arguments)
    if contract.evidence_collection == "runtime":
        arguments.pop("evidence_ids", None)
    normalized = canonicalize_contract_value(arguments, schema)
    validate_contract(normalized, schema, label=contract.tool_name)
    outcome = str(normalized["outcome"])
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
        else tuple(str(item) for item in normalized.get("evidence_ids") or [])
    )
    resolutions = tuple(
        FailureResolution(
            failed_evidence_id=str(item["failed_evidence_id"]),
            resolved_by_evidence_id=str(item["resolved_by_evidence_id"]),
            reason=str(item["reason"]),
        )
        for item in normalized.get("failure_resolutions") or []
    )
    return CompletionSubmission(
        outcome=outcome,
        output=dict(normalized["output"]),
        summary=str(normalized["summary"]),
        evidence_ids=evidence_ids,
        failure_resolutions=resolutions,
    )


def completion_tool_schema(contract: CompletionContract) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": contract.tool_name,
            "description": (
                "Submit one declared assignment outcome with structured output and auditable evidence. "
                "An alternative repair for an executed failure must be declared in failure_resolutions."
            ),
            "parameters": completion_arguments_schema(contract),
        },
    }


def completion_arguments_schema(contract: CompletionContract) -> dict[str, Any]:
    required = ["outcome", "output", "summary"]
    properties: dict[str, Any] = {
        "outcome": {"type": "string", "enum": sorted(contract.outcomes)},
        "output": contract.output_schema or {"type": "object"},
        "summary": {"type": "string", "maxLength": 4000},
        "failure_resolutions": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "required": ["failed_evidence_id", "resolved_by_evidence_id", "reason"],
                "properties": {
                    "failed_evidence_id": {"type": "string", "minLength": 1},
                    "resolved_by_evidence_id": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                "additionalProperties": False,
            },
        },
    }
    if contract.evidence_collection == "model":
        required.append("evidence_ids")
        properties["evidence_ids"] = {
            "type": "array",
            "maxItems": 500,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "minLength": 1,
                "description": "Evidence ID from an executed capability result in this Agent Run.",
            },
        }
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }
