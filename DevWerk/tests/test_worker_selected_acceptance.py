import sys

import pytest

from app.v1.agent import AgentCore
from app.v1.capabilities import CapabilityContext
from app.v1.domain import AcceptanceCheck, AgentModelResponse, AgentToolCall, ToolResult, Transition
from app.v1.execution_ledger import ledger_entry
from app.v1.runtime import WorkflowRuntime, _column_completion_contract
from app.v1.completion_protocol import parse_completion_submission
from app.v1.services.completion_admission import CompletionAdmissionService
from tests.helpers import agent_workflow, create_planned_task, workflow_plan




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


