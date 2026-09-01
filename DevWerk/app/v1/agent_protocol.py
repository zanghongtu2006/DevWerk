from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConversationProtocolStalled(RuntimeError):
    """The model repeated an unsupported final claim without executing progress."""


@dataclass
class ConversationTurnProtocol:
    """Validate final mutation claims against durable tool receipts.

    A correction opens another model step only when the execution ledger has
    advanced since the previous correction. This gives the main Agent room to
    perform a multi-step repair without using an arbitrary retry count, while
    stopping a prose-only correction loop immediately.
    """

    correction_progress: tuple[str, ...] | None = None

    def correction_for(
        self,
        unsupported_claims: list[str],
        ledger: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not unsupported_claims:
            return None

        progress = _successful_mutation_evidence(ledger)
        if self.correction_progress == progress:
            raise ConversationProtocolStalled(
                "Conversation Agent repeated unsupported mutation claims "
                "without a new successful state-changing tool receipt: "
                + ", ".join(sorted(unsupported_claims))
            )

        self.correction_progress = progress
        return {
            "unsupported_mutation_claims": unsupported_claims,
            "successful_mutation_evidence": list(progress),
            "instruction": (
                "The final response reports state changes that have no successful execution receipts. "
                "Call the required capabilities now, or return a concise corrected reply that clearly says "
                "the changes were not executed. A further correction is available only after a new successful "
                "state-changing tool receipt; read-only calls and prose do not count as execution progress."
            ),
        }


def _successful_mutation_evidence(
    ledger: list[dict[str, Any]],
) -> tuple[str, ...]:
    return tuple(sorted(
        str(item.get("evidence_id") or "")
        for item in ledger
        if item.get("ok")
        and item.get("status") == "completed"
        and item.get("effect_kind") in {"write", "process", "control"}
        and item.get("evidence_id")
    ))
