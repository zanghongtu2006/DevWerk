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
            arguments={'argv':[sys.executable,'-c',"from pathlib import Path; assert Path('docs/baseline.md').exists()"]},
            evidence_kind='behavior',purpose='Test fixture for entry-admission contract, not software acceptance')]
        definition.acceptance_obligations.append(AcceptanceObligation(column=key,check_key='verify_fixture',
            invalidated_by=[key] if key.endswith('_development') else ['backend_development','frontend_development']))
    workflow = store.publish_workflow(project['id'],definition,workflow['workflow_plan_id'])
    execution_key = ctx.agent_run_id + ':baseline'
    receipt = store.registry.dispatch('project.files.write', {'path':'docs/baseline.md','content':'accepted login requirements'},
        replace(ctx, execution_key=execution_key))
    assert receipt.ok, receipt.error
    plan = task_plan(workflow['id'], WorkflowDefinition.model_validate(workflow['definition']),
        input_data={'requirements_path':'docs/baseline.md','requirements_confirmed':True})
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
