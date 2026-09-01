from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_CAPABILITY_PREREQUISITES: dict[str, set[str]] = {
    "task.create": {
        "loop.list",
        "loop.inspect",
        "loop.apply",
        "project.inspect",
        "workflow.inspect",
        "task.list",
        "task.plan.list",
        "task.plan.save",
    },
}

_REQUIRED_MUTATION_PREREQUISITE: dict[str, str] = {
    "task.create": "task.plan.save",
}


class ConversationProtocolStalled(RuntimeError):
    """The model repeated an unsupported final claim without executing progress."""

    error_code = "conversation_protocol_stalled"
    error_category = "protocol_permanent"


@dataclass
class ConversationTurnProtocol:
    """Validate final mutation claims against durable tool receipts.

    A correction requires a real tool call on the next model step. Further
    repair steps are allowed only when the execution ledger advances. This
    gives the main Agent room to perform a multi-step repair without using an
    arbitrary retry count, while stopping a prose-only correction loop.
    """

    correction_progress: tuple[str, ...] | None = None
    required_capabilities: tuple[str, ...] = ()
    selected_capabilities: tuple[str, ...] = ()
    forced_tool_name: str | None = None

    @property
    def requires_tool(self) -> bool:
        return bool(self.required_capabilities)

    def select_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expose only the capabilities required to repair an unsupported claim."""
        if not self.required_capabilities:
            self.selected_capabilities = ()
            return tools
        if self.forced_tool_name:
            selected = [
                item
                for item in tools
                if str(item.get("function", {}).get("name") or "")
                == self.forced_tool_name
            ]
            if not selected:
                raise ConversationProtocolStalled(
                    "Conversation Agent cannot continue the execution correction "
                    "because the selected capability is not available: "
                    + self.forced_tool_name
                )
            self.selected_capabilities = (self.forced_tool_name,)
            return selected
        required = set(self.required_capabilities)
        permitted = set(required)
        for capability in required:
            permitted.update(_CAPABILITY_PREREQUISITES.get(capability, set()))
        selected = [
            item
            for item in tools
            if str(item.get("function", {}).get("name") or "") in permitted
        ]
        if not selected:
            raise ConversationProtocolStalled(
                "Conversation Agent cannot repair its execution claim because the "
                "required capabilities are not available: "
                + ", ".join(self.required_capabilities)
            )
        self.selected_capabilities = tuple(sorted(
            str(item.get("function", {}).get("name") or "")
            for item in selected
        ))
        return selected

    def validate_forced_response(self, capability_names: list[str]) -> None:
        """Reject a Provider that ignores a required tool-choice continuation."""
        if not self.required_capabilities:
            return
        if self.forced_tool_name:
            if self.forced_tool_name not in capability_names:
                raise ConversationProtocolStalled(
                    "Conversation Agent did not call the selected capability after an "
                    "execution correction: " + self.forced_tool_name
                )
            return
        permitted = set(self.selected_capabilities or self.required_capabilities)
        if not any(name in permitted for name in capability_names):
            raise ConversationProtocolStalled(
                "Conversation Agent did not call any permitted capability after an "
                "execution correction: " + ", ".join(self.required_capabilities)
            )

    def select_forced_tool(self, capability_names: list[str]) -> str | None:
        """Turn a Provider's prose-only tool selection into one exact next call."""
        permitted = set(self.selected_capabilities or self.required_capabilities)
        for required in self.required_capabilities:
            prerequisite = _REQUIRED_MUTATION_PREREQUISITE.get(required)
            if prerequisite and prerequisite in permitted:
                if self.forced_tool_name == prerequisite:
                    return None
                self.forced_tool_name = prerequisite
                return prerequisite
        candidates = permitted.intersection(capability_names)
        if not candidates:
            return None
        prerequisites = sorted(candidates - set(self.required_capabilities))
        selected = prerequisites[0] if prerequisites else sorted(candidates)[0]
        if self.forced_tool_name == selected:
            return None
        self.forced_tool_name = selected
        return selected

    def observe_results(self, ledger: list[dict[str, Any]]) -> None:
        """Release or advance a forced continuation after real tool receipts arrive."""
        if not self.required_capabilities:
            return
        required = set(self.required_capabilities)
        permitted = set(self.selected_capabilities or self.required_capabilities)
        relevant = [
            item
            for item in ledger
            if str(item.get("capability") or "") in permitted
        ]
        if not relevant:
            return
        forced_results = [
            item for item in relevant
            if self.forced_tool_name
            and str(item.get("capability") or "") == self.forced_tool_name
        ]
        if forced_results and any(
            not item.get("ok") or item.get("status") != "completed"
            for item in forced_results
        ):
            # The model now has an observable failure receipt. Let it repair the
            # arguments or select another prerequisite. A failed required
            # capability may be reported as the real blocker.
            if self.forced_tool_name in required:
                self.required_capabilities = ()
            self.forced_tool_name = None
            return
        completed = {
            str(item.get("capability") or "")
            for item in relevant
            if item.get("ok") and item.get("status") == "completed"
        }
        self.required_capabilities = tuple(sorted(required - completed))
        if forced_results:
            self.forced_tool_name = (
                self.required_capabilities[0]
                if len(self.required_capabilities) == 1
                else None
            )
        if not self.required_capabilities:
            self.selected_capabilities = ()
            self.forced_tool_name = None

    def correction_for(
        self,
        unsupported_claims: list[str],
        ledger: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not unsupported_claims:
            return None

        progress = _mutation_attempt_progress(ledger)
        if self.correction_progress == progress:
            raise ConversationProtocolStalled(
                "Conversation Agent repeated unsupported mutation claims "
                "without a new successful state-changing tool receipt: "
                + ", ".join(sorted(unsupported_claims))
            )

        self.correction_progress = progress
        self.required_capabilities = tuple(sorted(set(unsupported_claims)))
        return {
            "unsupported_mutation_claims": unsupported_claims,
            "successful_mutation_evidence": list(
                _successful_mutation_evidence(ledger)
            ),
            "execution_attempt_progress": list(progress),
            "instruction": (
                "The final response reports state changes that have no successful execution receipts. "
                "The next Provider request requires one of the listed capabilities and exposes only those "
                "capabilities. Call the required capabilities now. If a capability rejects the operation, "
                "use its receipt to correct the arguments or report the real blocker; prose does not count "
                "as execution progress."
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


def _mutation_attempt_progress(
    ledger: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Return stable progress for successful mutations and distinct failures.

    A corrected argument set is meaningful progress even when the capability
    rejects it. Repeating the same operation and receiving the same result is
    not progress and therefore cannot open another prose-only correction loop.
    """
    progress: set[str] = set(_successful_mutation_evidence(ledger))
    for item in ledger:
        if item.get("effect_kind") not in {"write", "process", "control"}:
            continue
        if item.get("ok") and item.get("status") == "completed":
            continue
        operation_hash = str(item.get("operation_sha256") or "")
        result_hash = str(item.get("result_sha256") or "")
        if operation_hash or result_hash:
            progress.add(
                "failed:"
                + str(item.get("capability") or "")
                + ":"
                + operation_hash
                + ":"
                + result_hash
            )
    return tuple(sorted(progress))
