import pytest

from app.v1.agent_models import AgentRunSpec
from app.v1.agent_prompt import build_column_system_envelope
from app.v1.completion_protocol import parse_completion_submission
from app.v1.domain import ToolResult
from app.v1.execution_ledger import ledger_entry
from app.v1.policy import PlatformPolicySnapshot
from app.v1.runtime import _column_completion_contract
from app.v1.services.completion_admission import CompletionAdmissionService
from tests.helpers import agent_workflow


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
