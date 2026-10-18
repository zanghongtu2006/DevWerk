import json
import sys
from dataclasses import replace

import pytest

from app.v1.domain import (
    AcceptanceCheck,
    AcceptanceObligation,
    ExactTaskInputString,
    WorkflowDefinition,
)
from tests.helpers import agent_workflow, publish_initial_workflow, task_plan, workflow_plan
from tests.test_conversation_intent_contract import resolve, turn

def software_plan(store, tmp_path):
    project = store.create_project('software entry', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '按方案开始开发；不要增加登出功能。')
    resolution = resolve(ctx, job, act='execute', execution_request='begin', scope_summary='local login demo',
        user_evidence=[{'message_id':job['user_message_id'],'quote':job['message']}])
    assert resolution.ok, resolution.error
    applied = store.registry.dispatch('loop.apply', {'loop_key':'software.ddd_delivery',
        'bindings':{'product_name':'login demo','delivery_target':'local'}}, ctx)
    assert applied.ok, applied.error
    workflow = store.get_workflow(project['id'])
    definition = WorkflowDefinition.model_validate(workflow['definition'])
    for key in ('backend_development','frontend_development','system_test','delivery','accept'):
        definition.column(key).acceptance_checks = [AcceptanceCheck(key='verify_fixture',capability='project.command.run',
            arguments={'argv':[sys.executable,'-c',"from pathlib import Path; assert Path('docs/baseline.md').exists()"]},
            purpose='Verify this test fixture exists')]
        definition.acceptance_obligations.append(AcceptanceObligation(column=key,check_key='verify_fixture',
            invalidated_by=[key] if key.endswith('_development') else ['backend_development','frontend_development']))
    workflow = store.publish_workflow(project['id'],definition,workflow['workflow_plan_id'])
    execution_key = ctx.agent_run_id + ':baseline'
    receipt = store.registry.dispatch('project.files.write', {'path':'docs/baseline.md','content':'accepted login requirements'},
        replace(ctx, execution_key=execution_key))
    assert receipt.ok, receipt.error
    plan = task_plan(workflow['id'], WorkflowDefinition.model_validate(workflow['definition']),
        input_data={'requirements_path':'docs/baseline.md','requirements_confirmed':True,'acceptance_scenarios':['entry-fixture']})
    plan.tasks[0].entry_evidence = {'confirmed_scope':resolution.output['turn_contract']['execution_grant_id'],
                                  'requirements_file':execution_key}
    return project, plan, ctx


def test_software_receipts_save_and_materialize_one_task(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    saved = store.create_task_plan(project['id'], plan)
    created = store.registry.dispatch('task.create', {'task_plan_id':saved['id'],'proposed_task_ref':'primary'}, ctx)
    assert created.ok, created.error
    assert len(store.list_tasks(project['id'])) == 1
    assert created.output['materialization']['created_task_ids'] == [created.output['id']]
    replay = store.materialize_task_plan(project['id'], task_plan_id=saved['id'], proposed_task_ref='primary')
    assert replay['materialization'] == {'created_task_ids':[], 'reused_task_ids':[created.output['id']]}


def test_plan_contract_exposes_only_real_entry_evidence(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    result = store.registry.dispatch('task.plan.contract', {}, ctx)
    assert result.ok, result.error
    guide = result.output
    assert guide['workflow_revision_id'] == plan.workflow_revision_id
    assert set(guide['input_schema']['required']) == {
        'requirements_path', 'requirements_confirmed', 'acceptance_scenarios'}
    assert guide['entry_evidence']['confirmed_scope']['candidate_ids'] == [
        plan.tasks[0].entry_evidence['confirmed_scope']]
    assert guide['authorized_scope'][0] == {
        'grant_id': plan.tasks[0].entry_evidence['confirmed_scope'],
        'user_request': '按方案开始开发；不要增加登出功能。',
    }
    assert guide['entry_evidence']['requirements_file']['candidates'] == [{
        'execution_key': plan.tasks[0].entry_evidence['requirements_file'],
        'path': 'docs/baseline.md',
        'execution_grant_id': plan.tasks[0].entry_evidence['confirmed_scope']}]
    assert not store.list_task_plans(project['id']) and not store.list_tasks(project['id'])
    with store.tx(immediate=True) as db:
        db.execute('''INSERT INTO v1_execution_grants
            (id,project_id,source_job_id,requirement_id,requirement_revision,kind,state,snapshot_json,snapshot_hash,created_at)
            SELECT 'grant_newer',project_id,source_job_id,requirement_id,requirement_revision,
                   kind,state,snapshot_json,snapshot_hash,'9999-01-01T00:00:00+00:00'
            FROM v1_execution_grants WHERE id=?''',
            (plan.tasks[0].entry_evidence['confirmed_scope'],))
    paired = store.registry.dispatch('task.plan.contract', {}, replace(ctx, execution_key='read-paired-contract'))
    assert paired.ok
    assert paired.output['entry_evidence']['confirmed_scope']['candidate_ids'][0] == 'grant_newer'
    assert paired.output['entry_evidence']['requirements_file']['candidates'][0]['execution_grant_id'] == (
        plan.tasks[0].entry_evidence['confirmed_scope'])
    (tmp_path/'project'/'docs'/'baseline.md').write_text('changed', encoding='utf-8')
    stale = store.registry.dispatch('task.plan.contract', {}, replace(ctx, execution_key='read-stale-contract'))
    assert stale.ok and stale.output['entry_evidence']['requirements_file']['candidates'] == []


def test_each_column_receives_original_authorized_scope_even_when_plan_omits_exclusion(store, tmp_path):
    from app.v1.runtime import WorkflowRuntime

    project, plan, _ = software_plan(store, tmp_path)
    assert plan.tasks[0].readiness.non_scope == []
    saved = store.create_task_plan(project['id'], plan)
    task = store.create_task(project['id'], task_plan_id=saved['id'], proposed_task_ref='primary')
    workflow = store.workflow_by_id(project['id'], task['workflow_revision_id'])
    runtime = WorkflowRuntime(store, store.registry, 'scope-worker')

    for key in ('backend_development', 'frontend_development', 'accept'):
        column = workflow.column(key)
        context = runtime._input_for(task, workflow, column)
        assert context['authorized_scope'] == {
            'grant_id': plan.tasks[0].entry_evidence['confirmed_scope'],
            'user_request': '按方案开始开发；不要增加登出功能。',
        }
        assert 'explicit exclusions' in context['context_manifest']['consumption_contract']


def test_repair_task_receives_current_request_and_initial_grant_request(store, tmp_path):
    from app.v1.runtime import WorkflowRuntime

    project, plan, ctx = software_plan(store, tmp_path)
    prior_job = store.intents.job_for_run(ctx.agent_run_id, project['id'])
    store.finish_conversation_job(prior_job['id'], None, ctx.agent_run_id, {})
    followup, followup_ctx = turn(store, project, '修复交付：仍然不要增加登出功能。')
    authorized = store.registry.dispatch('conversation.execution.authorize',
        {'request': 'continue'}, followup_ctx)
    assert authorized.ok, authorized.error
    assert authorized.output['turn_contract']['execution_grant_id'] == (
        plan.tasks[0].entry_evidence['confirmed_scope'])
    guide = store.registry.dispatch('task.plan.contract', {}, followup_ctx)
    assert guide.ok, guide.error
    assert guide.output['authorized_scope'][0] == {
        'grant_id': plan.tasks[0].entry_evidence['confirmed_scope'],
        'user_request': followup['message'],
        'original_user_request': '按方案开始开发；不要增加登出功能。',
    }

    saved = store.create_task_plan(project['id'], plan)
    task = store.create_task(project['id'], task_plan_id=saved['id'], proposed_task_ref='primary')
    workflow = store.workflow_by_id(project['id'], task['workflow_revision_id'])
    context = WorkflowRuntime(store, store.registry, 'scope-worker')._input_for(
        task, workflow, workflow.column('accept'))
    assert context['authorized_scope'] == {
        'grant_id': plan.tasks[0].entry_evidence['confirmed_scope'],
        'user_request': followup['message'],
        'original_user_request': '按方案开始开发；不要增加登出功能。',
    }


def test_unsaved_plan_is_not_misdiagnosed_as_requirement_conflict(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    before = store.get_project(project['id'])
    validation = store.registry.dispatch('task.plan.validate',{'plan':plan.model_dump(mode='json')},ctx)
    assert validation.ok and validation.output['valid'], validation.error
    result = store.registry.dispatch('task.create',{'task_plan_id':'tpln_invented','proposed_task_ref':'primary'},ctx)
    assert not result.ok
    assert 'TaskPlanNotFound' in str(result.error) and 'task.plan.save' in str(result.error)
    assert 'different Requirement' not in str(result.error)
    assert not store.list_tasks(project['id']) and not store.list_task_plans(project['id'])
    assert store.get_project(project['id']) == before
    saved = store.registry.dispatch('task.plan.save',{'plan':plan.model_dump(mode='json')},ctx)
    assert saved.ok, saved.error
    created = store.registry.dispatch('task.create',{'task_plan_id':saved.output['id'],'proposed_task_ref':'primary'},ctx)
    assert created.ok, created.error
    assert len(store.list_tasks(project['id'])) == 1


def test_json_object_plan_transport_is_validated_and_saved(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    encoded = json.dumps(plan.model_dump(mode='json'), ensure_ascii=False)
    validation = store.registry.dispatch('task.plan.validate', {'plan': encoded}, ctx)
    assert validation.ok and validation.output['valid'], validation.error
    saved = store.registry.dispatch('task.plan.save', {'plan': encoded}, ctx)
    assert saved.ok, saved.error
    assert saved.output['next_action'] == {'capability': 'task.create', 'arguments': {
        'task_plan_id': saved.output['id'], 'proposed_task_ref': 'primary'}}
    invalid = store.registry.dispatch('task.plan.validate', {'plan': '[1,2]'}, ctx)
    assert not invalid.ok and invalid.error['type'] == 'ContractError'


def test_entry_evidence_error_finds_matching_receipt_after_many_other_writes(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    baseline_key = plan.tasks[0].entry_evidence['requirements_file']
    for index in range(25):
        receipt = store.registry.dispatch('project.files.write', {
            'path': f'docs/other-{index}.md', 'content': str(index)},
            replace(ctx, execution_key=f'{ctx.agent_run_id}:other-{index}'))
        assert receipt.ok, receipt.error
    plan.tasks[0].entry_evidence['requirements_file'] = 'wrong-id'
    rejected = store.registry.dispatch('task.plan.save', {'plan': plan.model_dump(mode='json')}, ctx)
    assert not rejected.ok
    assert "Task input path 'docs/baseline.md'" in rejected.error['message']
    assert baseline_key in rejected.error['message']


def test_plan_validate_rejects_stale_terminal_repair_predecessor(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    first_plan = store.create_task_plan(project['id'], plan)
    first = store.materialize_task_plan(project['id'],task_plan_id=first_plan['id'],
                                        proposed_task_ref='primary')
    store.route_task_to_failed(first['id'],'first attempt failed')
    second_plan = plan.model_copy(deep=True)
    second_plan.objective = 'Corrected second attempt'
    second_plan.repair_of_task_id = first['id']
    second_saved = store.create_task_plan(project['id'],second_plan)
    second = store.materialize_task_plan(project['id'],task_plan_id=second_saved['id'],
        proposed_task_ref='primary',rerun_of_task_id=first['id'])
    store.route_task_to_failed(second['id'],'second attempt failed')
    candidate = plan.model_copy(deep=True)
    candidate.objective = 'Third attempt with correct predecessor'
    candidate.repair_of_task_id = first['id']
    stale = store.registry.dispatch('task.plan.validate',{'plan':candidate.model_dump(mode='json')},ctx)
    assert stale.ok and not stale.output['valid']
    assert any('DuplicateTaskIdentity' in issue['message'] and second['id'] in issue['message']
               for issue in stale.output['diagnostics'])
    rejected = store.registry.dispatch('task.plan.save',{'plan':candidate.model_dump(mode='json')},ctx)
    assert not rejected.ok and 'DuplicateTaskIdentity' in str(rejected.error)
    candidate.repair_of_task_id = second['id']
    valid = store.registry.dispatch('task.plan.validate',{'plan':candidate.model_dump(mode='json')},ctx)
    assert valid.ok and valid.output['valid'], valid.output
    accepted = store.registry.dispatch('task.plan.save',{'plan':candidate.model_dump(mode='json')},ctx)
    assert accepted.ok, accepted.error


@pytest.mark.parametrize('invalid', ['missing','wrong_grant','foreign_receipt','changed_file'])
def test_software_boolean_is_not_sufficient_entry_evidence(store, tmp_path, invalid):
    project, plan, ctx = software_plan(store, tmp_path)
    if invalid == 'missing':
        plan.tasks[0].entry_evidence = {}
    elif invalid == 'wrong_grant':
        plan.tasks[0].entry_evidence['confirmed_scope'] = 'grant_missing'
    elif invalid == 'foreign_receipt':
        receipt = store.registry.dispatch('project.files.write', {'path':'docs/baseline.md','content':'accepted login requirements'},
            replace(ctx, execution_key='unbound-write'))
        assert receipt.ok
        plan.tasks[0].entry_evidence['requirements_file'] = 'unbound-write'
    else:
        (tmp_path/'project'/'docs'/'baseline.md').write_text('changed after receipt', encoding='utf-8')
    with pytest.raises(ValueError, match='EntryEvidence'):
        store.create_task_plan(project['id'], plan)
    assert not store.list_task_plans(project['id'])
    assert not store.list_tasks(project['id'])


def test_materialization_rechecks_baseline_changed_after_save(store, tmp_path):
    project, plan, ctx = software_plan(store, tmp_path)
    saved = store.create_task_plan(project['id'], plan)
    (tmp_path/'project'/'docs'/'baseline.md').write_text('external edit', encoding='utf-8')
    with pytest.raises(ValueError, match='EntryEvidenceFileChanged'):
        store.materialize_task_plan(project['id'], task_plan_id=saved['id'], proposed_task_ref='primary')
    assert not store.list_tasks(project['id'])


# ---------------------------------------------------------------------------
# Task Plan compiler
# ---------------------------------------------------------------------------

def prepare(store, tmp_path, *, identity_pointer='/requirements_path'):
    project = store.create_project('plan admission', '', str(tmp_path / 'project'))
    workflow = agent_workflow()
    method = workflow_plan(workflow)
    method.task_contract.identity_pointer = identity_pointer
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


def test_runtime_form_task_input_pointer_saves_and_materializes(store, tmp_path):
    project, plan = prepare(store, tmp_path, identity_pointer='/input/task/input/requirements_path')
    saved = store.create_task_plan(project['id'], plan)
    task = store.materialize_task_plan(project['id'], task_plan_id=saved['id'], proposed_task_ref='primary')
    assert task['input']['requirements_path'] == 'docs/requirements.md'


def test_runtime_form_task_input_contract_pointers_are_canonical():
    from app.v1.domain import OrderedTaskDependencyContract, TaskContract, TaskEntryRequirement

    contract = TaskContract.model_validate({
        'identity_pointer': '/input/task/input/country',
        'dependency_contract': {'order_pointer': '/input/task/input/country'},
        'entry_requirements': [{
            'key': 'source', 'kind': 'input_file_snapshot',
            'input_pointer': '/input/task/input/source_path', 'scope_evidence_key': 'scope',
        }, {'key': 'scope', 'kind': 'accepted_scope_snapshot'}],
        'expected_outputs': ['result'], 'acceptance_contract': ['result recorded'],
    })
    assert contract.identity_pointer == '/country'
    assert contract.dependency_contract == OrderedTaskDependencyContract(order_pointer='/country')
    assert contract.entry_requirements[0] == TaskEntryRequirement(
        key='source', kind='input_file_snapshot', input_pointer='/source_path', scope_evidence_key='scope')


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
