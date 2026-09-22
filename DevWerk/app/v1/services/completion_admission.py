from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from app.v1.completion_protocol import CompletionContract, CompletionSubmission
from app.v1.execution_ledger import (
    evidence_entries,
    unresolved_execution_failures,
)


@dataclass(frozen=True)
class CompletionRejection:
    code: str
    message: str


@dataclass(frozen=True)
class CompletionDecision:
    accepted: bool
    submission: CompletionSubmission | None = None
    rejection: CompletionRejection | None = None


class CompletionAdmissionService:
    """Evaluate whether a protocol-valid submission may advance Runtime state."""

    def evaluate(
        self,
        submission: CompletionSubmission,
        contract: CompletionContract,
        ledger: list[dict[str, Any]],
    ) -> CompletionDecision:
        rule = contract.rule(submission.outcome)
        excluded = {contract.tool_name, "column.await"}
        evidence = evidence_entries(ledger, excluded_capabilities=excluded)
        if any(item.get('status') == 'awaiting' for item in evidence):
            return self._reject('execution_pending', 'An external operation is still pending; completion requires its current terminal receipt')
        by_evidence = {
            str(item.get("evidence_id")): item
            for item in evidence
            if item.get("evidence_id")
        }
        referenced: list[dict[str, Any]] = []
        for item_id in submission.evidence_ids:
            item = by_evidence.get(item_id)
            if item is None:
                return self._reject("unknown_evidence", f"unknown Column evidence: {item_id!r}")
            referenced.append(item)

        referenced_ids = set(submission.evidence_ids)
        unresolved = unresolved_execution_failures(
            ledger,
            excluded_capabilities=excluded,
        )
        if contract.acceptance_checks and not rule.allows_unresolved_failures:
            # Ordinary failed exploration is not an acceptance obligation. Only
            # Runtime-executed checks from the frozen contract can discharge it.
            for check in contract.acceptance_checks:
                verified = next((item for item in reversed(ledger)
                                 if item.get('acceptance_check') == check['key']), None)
                if not verified or not verified.get('ok') or verified.get('status') != 'completed':
                    return self._reject('acceptance_check_failed', f"Required acceptance check has not passed: {check['key']}")
            unresolved = {}
        unresolved_by_evidence = {
            str(item.get("evidence_id")): item
            for item in unresolved.values()
            if item.get("evidence_id")
        }
        resolved_failure_ids: set[str] = set()
        for resolution in submission.failure_resolutions:
            # A valid explicit pair remains valid after an identical retry has
            # already discharged that failure automatically. Never require the
            # model to infer whether the Runtime removed it from `unresolved`.
            failed = by_evidence.get(resolution.failed_evidence_id)
            repaired = by_evidence.get(resolution.resolved_by_evidence_id)
            if failed is None or failed.get('ok') or failed.get('status') == 'awaiting':
                return self._reject(
                    "unknown_failed_evidence",
                    f"failure resolution references a failed receipt that does not exist: "
                    f"{resolution.failed_evidence_id!r}",
                )
            if repaired is None or not repaired.get("ok") or repaired.get("status") != "completed":
                return self._reject(
                    "invalid_resolution_evidence",
                    f"failure resolution must reference successful evidence: "
                    f"{resolution.resolved_by_evidence_id!r}",
                )
            if ledger.index(repaired) <= ledger.index(failed):
                return self._reject("resolution_precedes_failure", "repair evidence must be executed after the failure")
            if (
                failed.get("capability") != repaired.get("capability")
                or failed.get("effect_kind") != repaired.get("effect_kind")
                or failed.get("operation_sha256") != repaired.get("operation_sha256")
            ):
                return self._reject(
                    "unrelated_resolution_evidence",
                    "Unrelated commands cannot discharge a failed operation; use frozen acceptance checks for alternative repairs",
                )
            if resolution.resolved_by_evidence_id not in referenced_ids:
                return self._reject(
                    "unreferenced_resolution_evidence",
                    "failure resolution evidence must also be included in evidence_ids",
                )
            resolved_failure_ids.add(resolution.failed_evidence_id)

        remaining_failures = {
            evidence: item
            for evidence, item in unresolved_by_evidence.items()
            if evidence not in resolved_failure_ids
        }
        if remaining_failures and not rule.allows_unresolved_failures:
            failed = next(iter(remaining_failures.values()))
            message = str(
                ((((failed.get("facts") or {}).get("result") or {}).get("error")) or {}).get(
                    "message"
                )
                or "a failed Column action has not been repaired"
            )
            operation = {"capability": failed.get("capability"), "arguments": (failed.get("facts") or {}).get("arguments") or {}}
            return self._reject("unresolved_execution_failure", message +
                                "; unresolved operation=" + json.dumps(operation, ensure_ascii=False) +
                                ". Retry this operation successfully, or submit a declared failure/rework outcome. "
                                "Unrelated commands/noops do not repair this failure; Runtime collects receipt IDs automatically.")

        if rule.allows_unresolved_failures and not set(remaining_failures).issubset(
            referenced_ids
        ):
            return self._reject(
                "omitted_failure_evidence",
                "failure or rework completion omitted unresolved execution evidence",
            )

        if rule.evidence_requirement == "forbidden" and submission.evidence_ids:
            return self._reject(
                "evidence_forbidden",
                f"outcome {submission.outcome!r} does not accept execution evidence",
            )
        if rule.evidence_requirement == "required":
            if rule.allows_unresolved_failures:
                if not referenced:
                    return self._reject(
                        "evidence_required",
                        f"outcome {submission.outcome!r} requires execution evidence",
                    )
            elif not any(
                item.get("ok") and item.get("status") == "completed"
                for item in referenced
            ):
                return self._reject(
                    "successful_evidence_required",
                    f"outcome {submission.outcome!r} requires at least one successful action evidence",
                )

        return CompletionDecision(accepted=True, submission=submission)

    @staticmethod
    def _reject(code: str, message: str) -> CompletionDecision:
        return CompletionDecision(
            accepted=False,
            rejection=CompletionRejection(code=code, message=message),
        )
