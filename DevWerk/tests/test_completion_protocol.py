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
    completion_tool_schema,
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
    arguments: dict | None = None,
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
        arguments=arguments if arguments is not None else {"call": call_id},
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


def test_acceptance_rejection_exposes_bounded_check_failure() -> None:
    contract = CompletionContract(
        tool_name='column.complete',
        outcomes={'advance': CompletionOutcomeRule(target='next', required_checks=('backend_unit_tests',))},
        acceptance_checks=({'key': 'backend_unit_tests'},),
    )
    submission = CompletionSubmission(outcome='advance', output={}, summary='ready', evidence_ids=())
    missing = CompletionAdmissionService().evaluate(submission, contract, [])
    assert missing.rejection.message == 'Required acceptance check has not passed: backend_unit_tests'
    failed = ledger_entry('run-1', 'check', 'project.command.run', 'process',
        ToolResult(ok=False, capability='project.command.run',
                   error={'type': 'AcceptanceEvidenceInvalid',
                          'message': 'Scenario assertion mismatch: S-07' + 'x'*1000}))
    failed['acceptance_check'] = 'backend_unit_tests'
    decision = CompletionAdmissionService().evaluate(submission, contract, [failed])
    assert decision.rejection.code == 'acceptance_check_failed'
    assert 'AcceptanceEvidenceInvalid: Scenario assertion mismatch: S-07' in decision.rejection.message
    assert len(decision.rejection.message) < 450


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


def test_explicit_resolution_can_reference_failure_already_cleared_by_identical_retry():
    arguments = {'argv':['test-runner','verify']}
    failed = _entry('failed', 'project.command.run', 'process', ok=False, arguments=arguments)
    passed = _entry('passed', 'project.command.run', 'process', ok=True, arguments=arguments)
    contract = CompletionContract(tool_name='column.complete', outcomes={'ready':CompletionOutcomeRule(target='done')})
    submission = CompletionSubmission(outcome='ready',output={},summary='verified',
        evidence_ids=(failed['evidence_id'],passed['evidence_id']),
        failure_resolutions=(FailureResolution(failed['evidence_id'],passed['evidence_id'],'same check passed'),))
    assert CompletionAdmissionService().evaluate(submission,contract,[failed,passed]).accepted


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


def test_exact_retry_resets_completion_rejection_ceiling() -> None:
    guard = CompletionAttemptGuard()
    command = {'argv': ['mvn', 'test']}
    failed = _entry('failed', 'project.command.run', 'process', ok=False, arguments=command)
    for index in range(4):
        guard.observe_rejection({'attempt': index}, ValueError('failed command'), [failed],
                                completion_tool_name='column.complete')
    repaired = _entry('repaired', 'project.command.run', 'process', ok=True, arguments=command)
    guard.observe_rejection({'attempt': 4}, ValueError('another issue'), [failed, repaired],
                            completion_tool_name='column.complete')
    assert guard.rejections == 1




def test_completion_rejection_discloses_all_pending_failed_receipt_ids() -> None:
    first = _entry('first-failure', 'project.command.run', 'process', ok=False,
                   arguments={'argv': ['mvn', 'test']})
    second = _entry('second-failure', 'project.command.run', 'process', ok=False,
                    arguments={'argv': ['java', '-jar', 'app.jar']})
    contract = CompletionContract('column.complete',
        {'ready': CompletionOutcomeRule(target='next')}, evidence_collection='runtime')
    submission = CompletionSubmission('ready', {}, 'verified',
                                      (first['evidence_id'], second['evidence_id']))

    result = CompletionAdmissionService().evaluate(submission, contract, [first, second])

    assert result.rejection.code == 'unresolved_execution_failure'
    assert 'unresolved_failure_count=2' in result.rejection.message
    assert f'"{first["evidence_id"]}"' in result.rejection.message
    assert f'"{second["evidence_id"]}"' in result.rejection.message


def test_failed_listing_can_be_resolved_by_later_successful_listing() -> None:
    failed = _entry('bad-listing', 'project.command.run', 'process', ok=False,
                    arguments={'argv': ['cmd', '/c', 'dir', 'frontend/src', '/s', '/b']})
    repaired = _entry('good-listing', 'project.command.run', 'process', ok=True,
                      arguments={'argv': ['cmd', '/c', 'dir', '/s', '/b', 'frontend/src']})
    built = _entry('build', 'project.command.run', 'process', ok=True,
                   arguments={'argv': ['npm', 'run', 'build']})
    contract = CompletionContract('column.complete',
        {'ready': CompletionOutcomeRule(target='next')},
        evidence_collection='runtime')
    submission = CompletionSubmission('ready', {}, 'built',
        tuple(item['evidence_id'] for item in (failed, repaired, built)),
        (FailureResolution(failed['evidence_id'], repaired['evidence_id'],
                           'Corrected the directory listing arguments'),))

    assert CompletionAdmissionService().evaluate(
        submission, contract, [failed, repaired, built]).accepted




def test_successful_frontend_build_supersedes_failed_install_without_model_evidence_ids() -> None:
    failed = _entry('failed-install', 'project.command.run', 'process', ok=False,
                    arguments={'argv': ['npm', 'install']})
    built = _entry('built', 'project.command.run', 'process', ok=True,
                   arguments={'argv': ['cmd', '/c', 'cd /d frontend && pnpm run build']})
    contract = CompletionContract('column.complete',
        {'frontend_done': CompletionOutcomeRule(target='next')},
        evidence_collection='runtime')
    submission = parse_completion_submission({
        'outcome': 'frontend_done', 'output': {}, 'summary': 'build passed',
        'failure_resolutions': [{'failed_evidence_id': failed['evidence_id'],
                                 'resolved_by_evidence_id': built['evidence_id'],
                                 'reason': 'The later verification covers the failed setup'}]},
        contract, [failed, built])

    assert CompletionAdmissionService().evaluate(submission, contract, [failed, built]).accepted


def test_unfrozen_acceptance_column_can_complete_after_later_successful_behavior_check() -> None:
    failed = _entry('failed', 'project.command.run', 'process', ok=False,
                    arguments={'argv': ['mvn', 'spring-boot:run']})
    passed = _entry('passed', 'project.command.run', 'process', ok=True,
                    arguments={'argv': ['powershell', '-File', 'acceptance-test.ps1']})
    cleanup = _entry('cleanup', 'project.command.run', 'process', ok=True,
                     arguments={'argv': ['cmd', '/c', 'del', 'temporary.java']})
    contract = CompletionContract('column.complete',
        {'done': CompletionOutcomeRule(target='done')}, evidence_collection='runtime')

    def decision(ledger, *, frozen=False, resolve=False):
        selected = contract if not frozen else CompletionContract(
            'column.complete', {'done': CompletionOutcomeRule(target='done', required_checks=('http_check',))}, evidence_collection='runtime',
            acceptance_checks=({'key': 'http_check'},))
        return CompletionAdmissionService().evaluate(
            CompletionSubmission('done', {}, 'verified',
                                 tuple(item['evidence_id'] for item in ledger),
                                 (FailureResolution(failed['evidence_id'], passed['evidence_id'],
                                                    'Later command verifies the failed attempt'),) if resolve else ()),
            selected, ledger)

    assert decision([failed, passed]).rejection.code == 'unresolved_execution_failure'
    assert decision([failed, passed], resolve=True).accepted
    assert decision([passed, failed]).rejection.code == 'unresolved_execution_failure'
    assert decision([failed, cleanup]).rejection.code == 'unresolved_execution_failure'
    assert decision([failed, passed], frozen=True).rejection.code == 'acceptance_check_failed'


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


@pytest.mark.parametrize('arguments', [
    {'outcome': 'ready', 'output': '{}', 'summary': 'verified'},
    {'outcome': 'ready', 'summary': 'verified'},
])
def test_completion_accepts_empty_object_output_from_provider(arguments) -> None:
    contract = CompletionContract(
        tool_name='column.complete',
        outcomes={'ready': CompletionOutcomeRule(target='next', evidence_requirement='optional')},
        output_schema={'properties': {}},
        evidence_collection='runtime',
    )
    assert parse_completion_submission(arguments, contract, []).output == {}


def test_completion_does_not_invent_required_output_fields() -> None:
    contract = CompletionContract(
        tool_name='column.complete',
        outcomes={'ready': CompletionOutcomeRule(target='next', evidence_requirement='optional')},
        output_schema={'type': 'object', 'required': ['delivered'],
                       'properties': {'delivered': {'type': 'boolean'}}},
    )
    with pytest.raises(ValueError, match='output'):
        parse_completion_submission({'outcome': 'ready', 'summary': 'verified'}, contract, [])


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
