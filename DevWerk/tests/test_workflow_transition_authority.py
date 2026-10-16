import pytest

from app.v1.capabilities import CapabilityContext
from app.v1.domain import AcceptanceCheck, EdgeCheck, Transition, WorkflowDefinition
from app.v1.states import FEEDBACK_STATE_MACHINE, FeedbackStatus
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow, sequence_workflow


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
