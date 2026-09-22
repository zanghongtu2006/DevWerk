import pytest

from app.v1.domain import ExactTaskInputString
from tests.helpers import agent_workflow, task_plan, workflow_plan, publish_initial_workflow


def prepare(store, tmp_path):
    project = store.create_project('plan admission', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    method = workflow_plan(workflow)
    method.task_contract.identity_pointer = '/requirements_path'
    method.task_contract.input_schema = {
        'type': 'object', 'required': ['requirements_path', 'requirements_confirmed'],
        'properties': {'requirements_path': {'type': 'string'}, 'requirements_confirmed': {'const': True}},
        'additionalProperties': False,
    }
    saved = store.create_workflow_plan(project['id'], method)
    revision = publish_initial_workflow(store, project['id'], workflow, saved['id'])
    plan = task_plan(revision['id'], workflow, input_data={
        'requirements_path': 'docs/requirements.md', 'requirements_confirmed': True})
    return project, plan


@pytest.mark.parametrize('pointer', ['/input/requirements_path', '/requirements_confirmed'])
def test_save_rejects_exact_before_any_plan_is_persisted(store, tmp_path, pointer):
    project, plan = prepare(store, tmp_path)
    plan.tasks[0].exact_input_strings = [ExactTaskInputString(pointer=pointer, escaped_value='true')]
    with pytest.raises(ValueError, match='ExactInputTarget'):
        store.create_task_plan(project['id'], plan)
    assert store.list_task_plans(project['id']) == []
    assert store.list_tasks(project['id']) == []


def test_save_rejects_columns_as_same_identity_tasks(store, tmp_path):
    project, plan = prepare(store, tmp_path)
    plan.tasks.append(plan.tasks[0].model_copy(update={'proposed_task_ref': 'backend'}))
    with pytest.raises(ValueError, match='DuplicateTaskIdentity'):
        store.create_task_plan(project['id'], plan)
    assert store.list_task_plans(project['id']) == []


def test_valid_plan_can_materialize_without_input_drift(store, tmp_path):
    project, plan = prepare(store, tmp_path)
    saved = store.create_task_plan(project['id'], plan)
    task = store.materialize_task_plan(project['id'], task_plan_id=saved['id'], proposed_task_ref='primary')
    assert task['input'] == saved['plan']['tasks'][0]['input']
