from __future__ import annotations

import sys

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.agent_prompt import build_column_system_envelope
from app.v1.capabilities import CapabilityContext, build_core_registry
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
from app.v1.domain import (
    AcceptanceCheck,
    AgentModelResponse,
    AgentToolCall,
    EdgeCheck,
    ToolResult,
    Transition,
    WorkflowDefinition,
)
from app.v1.execution_ledger import ledger_entry
from app.v1.policy import PlatformPolicySnapshot
from app.v1.runtime import WorkflowRuntime, _column_completion_contract
from app.v1.services.completion_admission import CompletionAdmissionService
from app.v1.states import FEEDBACK_STATE_MACHINE, FeedbackStatus
from app.v1.tool_protocol import parse_await_submission
from tests.helpers import (
    agent_workflow,
    create_planned_task,
    publish_planned_workflow,
    sequence_workflow,
    workflow_plan,
)

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


# ---------------------------------------------------------------------------
# Completion evidence guidance
# ---------------------------------------------------------------------------

def _completion_contract():
    workflow = agent_workflow()
    return _column_completion_contract(workflow, workflow.column('work'))


def _command_receipt(argv):
    return ledger_entry(
        'agent', 'command', 'project.command.run', 'process',
        ToolResult(ok=True, capability='project.command.run', output={'exit_code': 0}),
        arguments={'argv': argv},
    )






def test_unresolved_foreground_timeout_rejection_keeps_repair_hint_visible():
    contract = _completion_contract()
    failed = ledger_entry(
        'agent', 'server-timeout', 'project.command.run', 'process',
        ToolResult(ok=False, capability='project.command.run', status='failed',
                   error={'type': 'CommandFailed',
                          'message': 'command timed out: ' + 'STARTUP_LOG_' * 1000}),
        arguments={'argv': ['java', '-jar', 'backend/target/demo.jar']},
    )
    built = _command_receipt(['mvn.cmd', '-f', 'backend/pom.xml', 'package', '-DskipTests'])
    ledger = [failed, built]
    submission = parse_completion_submission(
        {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'built'},
        contract, ledger,
    )
    decision = CompletionAdmissionService().evaluate(submission, contract, ledger)
    assert not decision.accepted
    assert decision.rejection.code == 'unresolved_execution_failure'
    assert 'command timed out' in decision.rejection.message
    assert 'failure_resolutions' in decision.rejection.message
    assert "failed_evidence_id='agent:server-timeout'" in decision.rejection.message
    assert len(decision.rejection.message) < 1000






def test_alternative_success_requires_explicit_failure_resolution():
    contract = _completion_contract()
    failed = ledger_entry(
        'agent', 'failed-build', 'project.command.run', 'process',
        ToolResult(ok=False, capability='project.command.run', status='failed',
                   error={'type': 'CommandFailed', 'message': 'npm cache EPERM'}),
        arguments={'argv': ['npx.cmd', 'vite', 'build']},
    )
    repaired = ledger_entry(
        'agent', 'vite-build', 'project.command.run', 'process',
        ToolResult(ok=True, capability='project.command.run', output={'exit_code': 0}),
        arguments={'argv': ['cmd', '/c', 'cd /d frontend && node_modules\\.bin\\vite.cmd build']},
    )
    ledger = [failed, repaired]
    submission = parse_completion_submission(
        {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'built'},
        contract, ledger,
    )
    unresolved = CompletionAdmissionService().evaluate(submission, contract, ledger).rejection
    assert unresolved.code == 'unresolved_execution_failure'
    assert f"failed_evidence_id='{failed['evidence_id']}'" in unresolved.message
    submission = parse_completion_submission({
        'outcome': 'success', 'output': {'delivered': True}, 'summary': 'built',
        'failure_resolutions': [{
            'failed_evidence_id': failed['evidence_id'],
            'resolved_by_evidence_id': repaired['evidence_id'],
            'reason': 'The successful verification command covers the failed attempt',
        }],
    }, contract, ledger)
    assert CompletionAdmissionService().evaluate(submission, contract, ledger).accepted


# ---------------------------------------------------------------------------
# Worker-selected acceptance
# ---------------------------------------------------------------------------

def test_column_plain_text_is_corrected_before_declared_completion(store, tmp_path):
    project = store.create_project('column-text-correction', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    saved = store.create_workflow_plan(project['id'], workflow_plan(workflow))
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'complete after reminder')
    replies = iter([
        AgentModelResponse(text='Here is a diagnostic summary, but no completion.'),
        AgentModelResponse(tool_calls=[AgentToolCall(id='done', name='column.complete',
            arguments={'outcome': 'success', 'output': {'delivered': True},
                       'summary': 'completed after reminder'})]),
    ])
    seen = []
    def model(messages, tools, **kwargs):
        seen.append(messages[-1]['content'])
        return next(replies)
    runtime = WorkflowRuntime(store, store.registry, 'test-worker',
                              AgentCore(store, store.registry, model))
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    assert 'plain-text reply channel' in seen[-1]


def test_repeated_column_plain_text_never_completes_task(store, tmp_path):
    project = store.create_project('column-text-only', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    saved = store.create_workflow_plan(project['id'], workflow_plan(workflow))
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'no fake completion')
    runtime = WorkflowRuntime(store, store.registry, 'test-worker',
                              AgentCore(store, store.registry,
                                        lambda *_a, **_kw: AgentModelResponse(text='still just a summary')))
    with pytest.raises(RuntimeError, match='without calling column.complete'):
        runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] != 'done'














def test_qualitative_feedback_does_not_override_declared_done_edge(store, tmp_path):
    project = store.create_project('market research', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    research = workflow.column('work')
    research.executor.capabilities = ['project.files.write']
    research.transitions = [Transition(outcome='success', target='review')]
    review = research.model_copy(deep=True)
    review.key, review.name = 'review', 'Independent review'
    review.executor.capabilities = ['project.files.read', 'task.feedback.record', 'task.feedback.resolve']
    review.transitions = [Transition(outcome='success', target='done'),
                          Transition(outcome='rework', target='work', allows_unresolved_failures=True)]
    workflow.columns.append(review)
    method = workflow_plan(workflow)
    method.task_contract.feedback_columns = ['review']
    saved = store.create_workflow_plan(project['id'], method)
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'source-backed market report')
    calls = iter([
        ('project.files.write', {'path': 'market.md', 'content': 'Draft findings'}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'draft ready'}),
        ('project.files.read', {'path': 'market.md'}),
        ('task.feedback.record', {'task_id': task['id'], 'dedupe_key': 'missing-sources',
            'responsible_column': 'work', 'description': 'The findings lack source citations'}),
        ('column.complete', {'outcome': 'rework', 'output': {'delivered': False}, 'summary': 'add sources'}),
        ('project.files.write', {'path': 'market.md', 'content': 'Findings with cited sources'}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'sources added'}),
        ('project.files.read', {'path': 'market.md'}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'premature acceptance'}),
        ('task.feedback.resolve', {'task_id': task['id'], 'feedback_id': 'pending',
            'verification_note': 'Read the revised report and confirmed source citations'}),
        ('column.complete', {'outcome': 'success', 'output': {'delivered': True}, 'summary': 'reviewed'}),
    ])
    counter = iter(range(20))

    def model(*_args, **_kwargs):
        name, arguments = next(calls)
        if name == 'task.feedback.resolve':
            arguments['feedback_id'] = store.feedback.list(project['id'], task['id'])[0]['id']
        return AgentModelResponse(tool_calls=[AgentToolCall(
            id=f'call-{next(counter)}', name=name, arguments=arguments)])

    runtime = WorkflowRuntime(store, store.registry, 'test-worker', AgentCore(store, store.registry, model))
    for _ in range(3):
        runtime.step(task['id'])
    assert store.feedback.list(project['id'], task['id'])[0]['state'] == 'verifying'
    runtime.step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    feedback = store.feedback.list(project['id'], task['id'])
    assert len(feedback) == 1 and feedback[0]['state'] == 'verifying'
    with store.connect() as db:
        rejected = db.execute("SELECT COUNT(*) FROM v1_tool_invocations WHERE project_id=? AND capability='column.complete' AND ok=0",
                              (project['id'],)).fetchone()[0]
    assert rejected == 0


def test_qualitative_feedback_can_notify_without_review_column(store, tmp_path):
    project = store.create_project('research without reviewer', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    workflow.column('work').executor.capabilities.append('task.feedback.record')
    saved = store.create_workflow_plan(project['id'], workflow_plan(workflow))
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'research findings')
    ctx = CapabilityContext(project_id=project['id'], project=project, store=store)
    feedback = store.feedback.record(ctx, {'task_id': task['id'], 'dedupe_key': 'sources',
        'responsible_column': 'work', 'description': 'Missing source citations'})
    assert feedback['state'] == 'open'


def test_qualitative_feedback_does_not_require_a_future_reviewer_path(store, tmp_path):
    project = store.create_project('review before repair', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    work = workflow.column('work')
    review = work.model_copy(deep=True)
    review.key, review.name = 'review', 'Review'
    review.executor.capabilities = ['task.feedback.record', 'task.feedback.resolve']
    review.transitions = [Transition(outcome='success', target='work')]
    workflow.entry = 'review'
    workflow.columns.append(review)
    method = workflow_plan(workflow)
    method.task_contract.feedback_columns = ['review']
    saved = store.create_workflow_plan(project['id'], method)
    store.publish_workflow(project['id'], workflow, saved['id'])
    task = create_planned_task(store, project['id'], 'review findings')
    ctx = CapabilityContext(project_id=project['id'], project=project, store=store)
    feedback = store.feedback.record(ctx, {'task_id': task['id'], 'dedupe_key': 'correction',
        'responsible_column': 'work', 'description': 'Correct the findings'})
    assert feedback['state'] == 'open'


# ---------------------------------------------------------------------------
# Workflow transition authority
# ---------------------------------------------------------------------------

def _started_review(store, tmp_path):
    workflow = agent_workflow()
    review = workflow.column('work')
    review.key = 'review'
    review.transitions = [
        Transition(outcome='accepted', target='system_test'),
        Transition(outcome='rework', target='repair', allows_unresolved_failures=True),
        Transition(outcome='failure', target='failed'),
    ]
    repair = review.model_copy(deep=True)
    repair.key = 'repair'
    repair.transitions = [Transition(outcome='repaired', target='review')]
    repair.acceptance_checks = [AcceptanceCheck(
        key='proof', capability='project.files.read', arguments={'path': 'proof.txt'}
    )]
    system_test = review.model_copy(deep=True)
    system_test.key = 'system_test'
    system_test.transitions = [Transition(outcome='passed', target='done')]
    workflow.entry = 'review'
    workflow.columns = [review, repair, system_test]
    project = store.create_project('transition authority', '', str(tmp_path / 'project'))
    publish_planned_workflow(store, project['id'], workflow)
    task = create_planned_task(store, project['id'], 'follow the declared graph')
    owned = store.claim_task(task['id'], 'worker')
    run = store.begin_run(owned, {})
    feedback = store.feedback.record(CapabilityContext(
        project['id'], project, store, task_id=task['id'], column_run_id=run['id']
    ), {
        'task_id': task['id'], 'dedupe_key': 'repair-proof',
        'responsible_column': 'repair', 'description': 'Repair has not supplied proof',
        'checks': [{'column': 'repair', 'check_key': 'proof'}],
    })
    return project, task, owned, run, feedback


@pytest.mark.parametrize('feedback_state', ['open', 'resolved'])
def test_accepted_follows_declared_edge_even_with_feedback(store, tmp_path, feedback_state):
    project, task, owned, run, feedback = _started_review(store, tmp_path)
    if feedback_state == 'resolved':
        with store.tx(immediate=True) as db:
            db.execute("UPDATE v1_task_feedback SET state='resolved' WHERE id=?", (feedback['id'],))

    store.finish_run(owned, run['id'], {'delivered': True}, 'accepted', 'system_test')

    assert store.get_task(task['id'])['current_column'] == 'system_test'
    assert store.feedback.list(project['id'], task['id'])[0]['state'] == feedback_state
    finished = [event for event in store.events(project['id']) if event['type'] == 'column.finished']
    assert finished[-1]['data']['outcome'] == 'accepted'
    assert finished[-1]['data']['next'] == 'system_test'
    assert not any(event['type'] == 'task.feedback_routed' for event in store.events(project['id']))


def test_explicit_rework_follows_declared_edge(store, tmp_path):
    project, task, owned, run, feedback = _started_review(store, tmp_path)
    store.finish_run(owned, run['id'], {'delivered': False}, 'rework', 'repair')
    assert store.get_task(task['id'])['current_column'] == 'repair'
    assert store.feedback.list(project['id'], task['id'])[0]['state'] == 'open'


def test_finish_run_rejects_target_other_than_declared_edge(store, tmp_path):
    project, task, owned, run, feedback = _started_review(store, tmp_path)
    with pytest.raises(ValueError, match='declared Workflow transition'):
        store.finish_run(owned, run['id'], {'delivered': True}, 'accepted', 'repair')
    assert store.get_task(task['id'])['current_column'] == 'review'
    assert store.feedback.list(project['id'], task['id'])[0]['state'] == 'open'


def test_feedback_does_not_add_an_implicit_terminal_guard(store, tmp_path):
    project, task, owned, run, feedback = _started_review(store, tmp_path)
    assert any(item['event_type'] == 'task.feedback_recorded'
               for item in store.mailbox(project['id']))
    store.finish_run(owned, run['id'], {'delivered': True}, 'accepted', 'system_test')
    owned = store.claim_task(task['id'], 'worker')
    run = store.begin_run(owned, {})
    store.finish_run(owned, run['id'], {'delivered': True}, 'passed', 'done', terminal='done')
    assert store.get_task(task['id'])['status'] == 'done'
    assert store.feedback.list(project['id'], task['id'])[0]['state'] == 'open'


def test_only_selected_edge_requires_its_declared_check(store, tmp_path):
    from app.v1.domain import ToolResult
    from app.v1.agent_models import AgentRunSpec

    workflow = agent_workflow()
    work = workflow.column('work')
    work.acceptance_checks = [AcceptanceCheck(
        key='proof', capability='project.files.read', arguments={'path': 'proof.txt'}
    )]
    work.transitions = [
        Transition(outcome='verified', target='done',
                   required_checks=[EdgeCheck(column='work', check_key='proof')]),
        Transition(outcome='plain', target='done'),
    ]
    project = store.create_project('edge checks', '', str(tmp_path / 'project'))
    publish_planned_workflow(store, project['id'], workflow)
    task = create_planned_task(store, project['id'], 'produce proof')
    owned = store.claim_task(task['id'], 'worker')
    run = store.begin_run(owned, {})
    with pytest.raises(ValueError, match='Workflow edge check missing'):
        store.finish_run(owned, run['id'], {}, 'verified', 'done', terminal='done')
    spec = AgentRunSpec(kind='column', project=project, instruction='',
                        instruction_revision=1, context={}, capability_ids=[],
                        task_id=task['id'], column_run_id=run['id'],
                        column_attempt_id=run['attempt_id'])
    store.feedback.record_check(spec, 'proof', 'edge-proof',
                                ToolResult(ok=True, capability='project.files.read', output={'content': 'ok'}),
                                task_owner=owned)
    store.finish_run(owned, run['id'], {}, 'verified', 'done', terminal='done')
    assert store.get_task(task['id'])['status'] == 'done'

    other = create_planned_task(store, project['id'], 'plain delivery', task_ref='plain')
    owned = store.claim_task(other['id'], 'worker')
    run = store.begin_run(owned, {})
    store.finish_run(owned, run['id'], {}, 'plain', 'done', terminal='done')
    assert store.get_task(other['id'])['status'] == 'done'


def test_resolved_feedback_cannot_reopen_in_runtime_state_machine():
    with pytest.raises(ValueError, match='invalid FeedbackStatus transition'):
        FEEDBACK_STATE_MACHINE.require(FeedbackStatus.RESOLVED, FeedbackStatus.VERIFYING)


def test_capability_sequence_runs_only_the_selected_edge_check(store, tmp_path):
    from app.v1.runtime import WorkflowRuntime

    workflow = sequence_workflow()
    column = workflow.column('execute')
    column.acceptance_checks = [AcceptanceCheck(
        key='written_file', capability='project.files.read', arguments={'path': 'result.txt'}
    )]
    column.transitions[0].required_checks = [EdgeCheck(column='execute', check_key='written_file')]
    project = store.create_project('sequence edge', '', str(tmp_path / 'project'))
    publish_planned_workflow(store, project['id'], workflow)
    task = create_planned_task(store, project['id'], 'write a file')
    WorkflowRuntime(store, store.registry, 'worker').step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v1_acceptance_results WHERE task_id=?',
                          (task['id'],)).fetchone()[0] == 1


def test_legacy_column_check_is_explicit_on_normalized_success_edge():
    workflow = agent_workflow()
    workflow.column('work').acceptance_checks = [AcceptanceCheck(
        key='legacy', capability='project.files.read', arguments={'path': 'proof.txt'}
    )]
    old_payload = workflow.model_dump(mode='json')
    for edge in old_payload['columns'][0]['transitions']:
        edge.pop('required_checks')
    normalized = WorkflowDefinition.model_validate(old_payload)
    assert [(item.column, item.check_key)
            for item in normalized.column('work').transitions[0].required_checks] == [('work', 'legacy')]
    assert normalized.column('work').transitions[1].required_checks == []
