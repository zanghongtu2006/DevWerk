from dataclasses import replace
import sys

import pytest

from app.v1.domain import WorkflowDefinition, AcceptanceCheck, AcceptanceObligation
from tests.helpers import task_plan
from tests.test_conversation_intent_contract import turn, resolve


def software_plan(store, tmp_path):
    project = store.create_project('software entry', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '按方案开始开发')
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
            arguments={'argv':[sys.executable,'-c',"from pathlib import Path; assert Path('docs/baseline.md').exists()", 'fixture-report.json']},
            evidence_kind='behavior',purpose='Test fixture for entry-admission contract, not software acceptance',
            scenario_ids=['entry-fixture'],report_path='fixture-report.json')]
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
