from __future__ import annotations

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import build_core_registry
from app.v1.completion_protocol import (
    CompletionAttemptGuard,
    CompletionContract,
    CompletionOutcomeRule,
    CompletionProtocolStalled,
    CompletionSubmission,
    FailureResolution,
    parse_completion_submission,
)
from app.v1.domain import AgentModelResponse, AgentToolCall, ToolResult
from app.v1.execution_ledger import ledger_entry
from app.v1.services.completion_admission import CompletionAdmissionService
from app.v1.tool_protocol import parse_await_submission


def _entry(
    call_id: str,
    capability: str,
    effect_kind: str,
    *,
    ok: bool,
) -> dict:
    return ledger_entry(
        "run-1",
        call_id,
        capability,
        effect_kind,
        ToolResult(
            ok=ok,
            capability=capability,
            output={"call": call_id} if ok else None,
            error=None if ok else {"type": "RuntimeError", "message": "failed"},
        ),
        arguments={"call": call_id},
    )


def test_custom_intermediate_outcome_requires_declared_evidence() -> None:
    contract = CompletionContract(
        tool_name="column.complete",
        outcomes={"advance": CompletionOutcomeRule(target="next")},
    )
    submission = CompletionSubmission(
        outcome="advance",
        output={},
        summary="advance",
        evidence_ids=(),
    )

    decision = CompletionAdmissionService().evaluate(submission, contract, [])

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "successful_evidence_required"


def test_declared_failure_outcome_can_carry_failed_evidence() -> None:
    failed = _entry("failed", "project.command.run", "process", ok=False)
    contract = CompletionContract(
        tool_name="column.complete",
        outcomes={
            "failed": CompletionOutcomeRule(
                target="failed",
                evidence_requirement="required",
                allows_unresolved_failures=True,
            )
        },
    )
    submission = CompletionSubmission(
        outcome="failed",
        output={"reason": "command failed"},
        summary="command failed",
        evidence_ids=(failed["evidence_id"],),
    )

    decision = CompletionAdmissionService().evaluate(
        submission,
        contract,
        [failed],
    )

    assert decision.accepted is True


def test_unrelated_success_cannot_resolve_an_executed_failure() -> None:
    failed = _entry("failed", "project.command.run", "process", ok=False)
    unrelated = _entry("write", "project.files.write", "write", ok=True)
    contract = CompletionContract(
        tool_name="column.complete",
        outcomes={"ready": CompletionOutcomeRule(target="next")},
        evidence_collection="runtime",
    )
    submission = CompletionSubmission(
        outcome="ready",
        output={},
        summary="ready",
        evidence_ids=(failed["evidence_id"], unrelated["evidence_id"]),
        failure_resolutions=(FailureResolution(
            failed_evidence_id=failed["evidence_id"],
            resolved_by_evidence_id=unrelated["evidence_id"],
            reason="unrelated write",
        ),),
    )

    decision = CompletionAdmissionService().evaluate(
        submission,
        contract,
        [failed, unrelated],
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "unrelated_resolution_evidence"


def test_repeated_rejected_completion_stalls_without_execution_progress() -> None:
    guard = CompletionAttemptGuard()
    arguments = {"outcome": "ready", "output": {}, "summary": "ready"}
    error = ValueError("evidence required")
    guard.observe_rejection(
        arguments,
        error,
        [],
        completion_tool_name="column.complete",
    )

    with pytest.raises(CompletionProtocolStalled, match="without new execution progress"):
        guard.observe_rejection(
            arguments,
            error,
            [],
            completion_tool_name="column.complete",
        )


def test_runtime_evidence_collection_ignores_model_supplied_evidence_ids() -> None:
    successful = _entry("read", "project.files.read", "read", ok=True)
    contract = CompletionContract(
        tool_name="column.complete",
        outcomes={"ready": CompletionOutcomeRule(target="next")},
        evidence_collection="runtime",
    )

    submission = parse_completion_submission(
        {"outcome": "ready", "output": {}, "summary": "ready"},
        contract,
        [successful],
    )

    assert submission.evidence_ids == (successful["evidence_id"],)


def test_completion_recovers_provider_misplaced_named_parameter_without_inventing_data() -> None:
    contract = CompletionContract(
        tool_name="column.complete",
        outcomes={"accepted": CompletionOutcomeRule(target="next", evidence_requirement="optional")},
        output_schema={"type": "object", "additionalProperties": True},
        evidence_collection="runtime",
    )

    submission = parse_completion_submission(
        {
            "outcome": "accepted",
            "output": '{"path":"artifact.md"},"parameter name="summary">review passed',
        },
        contract,
        [],
    )

    assert submission.output == {"path": "artifact.md"}
    assert submission.summary == "review passed"


def test_completion_does_not_recover_undeclared_misplaced_parameter() -> None:
    contract = CompletionContract(
        tool_name="column.complete",
        outcomes={"accepted": CompletionOutcomeRule(target="next", evidence_requirement="optional")},
        output_schema={"type": "object", "additionalProperties": True},
        evidence_collection="runtime",
    )

    with pytest.raises(ValueError, match="summary.*required"):
        parse_completion_submission(
            {
                "outcome": "accepted",
                "output": '{"path":"artifact.md"},"parameter name="unknown">value',
            },
            contract,
            [],
        )


def test_await_submission_is_fully_validated_server_side() -> None:
    wait_config = {"kind": "poll"}
    with pytest.raises(ValueError, match="provider"):
        parse_await_submission(
            {"poll_capability": "system.noop", "poll_arguments": {}},
            ["system.noop"],
            wait_config,
        )
    with pytest.raises(ValueError, match="poll_capability"):
        parse_await_submission(
            {
                "provider": "test",
                "poll_capability": "project.files.read",
                "poll_arguments": {},
            },
            ["system.noop"],
            wait_config,
        )


def test_agent_core_stops_repeated_rejected_completion_without_more_model_calls(
    store,
    tmp_path,
) -> None:
    project = store.create_project("completion guard", "", str(tmp_path / "project"))
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id=f"complete-{calls}",
            name="column.complete",
            arguments={"outcome": "ready", "output": {}, "summary": "ready"},
        )])

    with pytest.raises(CompletionProtocolStalled, match="without new execution progress"):
        AgentCore(store, build_core_registry(), model).run(AgentRunSpec(
            kind="column",
            project=project,
            instruction="complete the assignment",
            instruction_revision=1,
            context={},
            capability_ids=[],
            completion_contract=CompletionContract(
                tool_name="column.complete",
                outcomes={"ready": CompletionOutcomeRule(target="done")},
            ),
        ))

    assert calls == 2


def test_agent_core_stops_repeated_invalid_await_without_more_model_calls(
    store,
    tmp_path,
) -> None:
    project = store.create_project("await guard", "", str(tmp_path / "project"))
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id=f"await-{calls}",
            name="column.await",
            arguments={"poll_capability": "system.noop", "poll_arguments": {}},
        )])

    with pytest.raises(CompletionProtocolStalled, match="without new execution progress"):
        AgentCore(store, build_core_registry(), model).run(AgentRunSpec(
            kind="column",
            project=project,
            instruction="wait for the external operation",
            instruction_revision=1,
            context={},
            capability_ids=["system.noop"],
            completion_contract=CompletionContract(
                tool_name="column.complete",
                outcomes={"ready": CompletionOutcomeRule(target="done")},
            ),
            wait_config={"kind": "poll"},
        ))

    assert calls == 2
