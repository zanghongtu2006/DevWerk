import hashlib
import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.v1.agent import AgentCore
from app.v1.agent_models import AgentRunSpec
from app.v1.capabilities import CapabilityContext
from app.v1.context_manager import ContextExhausted, ContextManager
from app.v1.domain import AcceptanceCheck, AcceptanceObligation, AgentModelResponse, AgentToolCall
from app.v1.runtime import WorkflowRuntime
from app.v1.services.acceptance_execution import run_acceptance_check
from tests.helpers import agent_workflow, publish_planned_workflow, create_planned_task


def manager(store, project, model=None, **kwargs):
    spec = AgentRunSpec(kind='column', project=project, instruction='', instruction_revision=1,
                        context={}, capability_ids=[], **kwargs)
    return ContextManager(store, spec, 'test', model or (lambda *a, **kw: None))


def batch(identity, content, source_id=0):
    return [{'role':'assistant','content':'','_source_message_id':source_id,
             'tool_calls':[{'id':identity,'type':'function','function':{'name':'project.files.write','arguments':content}}]},
            {'role':'tool','tool_call_id':identity,'content':'{"ok":true}', '_source_message_id':source_id+1 if source_id else 0}]


def test_project_text_search_skips_compiled_binary_and_keeps_later_matches(store, tmp_path):
    project = store.create_project('binary-search', '', str(tmp_path/'project'))
    root = tmp_path/'project'
    root.mkdir(exist_ok=True)
    (root/'aaa.class').write_bytes(b'\xca\xfe\xba\xbe')
    (root/'zzz.txt').write_text('find this requirement', encoding='utf-8')
    result = store.registry.dispatch('project.files.search', {'pattern':'requirement'},
                                     CapabilityContext(project['id'], project, store))
    assert result.ok
    assert result.output['matches'] == [{'path':'zzz.txt','line':1,'text':'find this requirement'}]


def test_large_project_file_result_projects_contiguous_page_and_cursor(store, tmp_path):
    from app.v1.context_manager import packed, project_result
    from app.v1.files import ProjectFiles

    project = store.create_project('file-page', '', str(tmp_path/'project'))
    ProjectFiles(project['base_dir']).write_text('source.txt', '甲乙丙丁' * 1500)
    ctx = CapabilityContext(project['id'], project, store)
    full = store.registry.dispatch('project.files.read', {'path':'source.txt'}, ctx)
    assert full.ok
    projected = json.loads(project_result(packed(full.model_dump(mode='json')), 4000))
    first = projected['output']
    assert first['content']
    assert 0 < first['next_offset'] < first['total_characters']
    assert not first['content_complete']
    next_page = store.registry.dispatch('project.files.read',
        {'path':'source.txt', 'offset':first['next_offset'], 'max_chars':100}, ctx)
    assert next_page.ok
    assert first['content'] + next_page.output['content'] == full.output['content'][:first['next_offset']+100]


def test_invalid_search_regex_is_a_tool_error_not_an_agent_crash(store, tmp_path):
    from app.v1.files import SystemFiles
    project = store.create_project('invalid-search', '', str(tmp_path/'project'))
    result = store.registry.dispatch('project.files.search', {'pattern':'*'},
                                     CapabilityContext(project['id'], project, store, agent_run_id='test-run'))
    assert not result.ok
    assert result.error['type'] == 'ValueError'
    assert 'invalid search pattern' in result.error['message']
    with pytest.raises(ValueError, match='invalid search pattern'):
        SystemFiles(tmp_path).search_text('.', '*', '**/*', limit=10)


def test_workflow_projection_keeps_acceptance_configuration_with_large_graph():
    from app.v1.context_manager import packed, project_result
    keys = ['backend_development', 'frontend_development', 'system_test', 'delivery', 'accept']
    columns = [{'key': key, 'transitions': [{'when': 'x'*3000}],
                'metadata': {'writable_paths': ['y'*2000]},
                'acceptance_checks': [{'key': key+'_check', 'scenario_ids': ['S-01'],
                                       'report_path': 'docs/'+key+'.json'}]} for key in keys]
    source = {'ok': True, 'capability': 'workflow.inspect',
              'output': {'id': 'revision-4', 'definition_hash': 'digest',
                         'workflow_plan_id': 'plan', 'definition': {'columns': columns,
                         'acceptance_obligations': [{'column': key, 'check_key': key+'_check'}
                                                    for key in keys]}}}
    projected = json.loads(project_result(packed(source), 4000))
    assert projected['output']['id'] == 'revision-4'
    assert projected['output']['definition_hash'] == 'digest'
    assert [item['key'] for item in projected['output']['column_contracts']] == keys
    assert len(projected['output']['acceptance_obligations']) == 5
    assert 'transitions' not in projected['output']['column_contracts'][0]
    assert projected['projection']['retrieve'] == 'agent.result.read'


def test_compaction_archives_large_arguments_but_never_partial_batches(store, tmp_path):
    project = store.create_project('projection','',str(tmp_path/'project'))
    ctx = manager(store, project)
    original = [{'role':'system','content':'current contract'}, *batch('complete', 'x'*200000),
                *batch('pending','small')[:1]]
    projected = ctx.prepare(original, [])
    assert projected[0]['content'] == 'current contract'
    assert len(projected) == 3 and projected[-1]['tool_calls'][0]['id'] == 'pending'
    assert 'context_checkpoint' in projected[1]['content']
    assert all(not any(k.startswith('_') for k in m) for m in projected)


def test_fixed_input_and_tools_overflow_rejects_without_model_call(store, tmp_path):
    project = store.create_project('oversize','',str(tmp_path/'project'))
    ctx = manager(store, project, lambda *a, **kw: pytest.fail('Must not submit oversized fixed input'))
    with pytest.raises(ContextExhausted):
        ctx.prepare([{'role':'system','content':'x'*300000}], [])
    with pytest.raises(ContextExhausted):
        ctx.prepare([{'role':'system','content':'small'}], [{'description':'x'*300000}])


def test_summary_failure_falls_back_without_recursive_calls(store, tmp_path):
    project = store.create_project('fallback','',str(tmp_path/'project'))
    calls = []
    def model(messages, tools, **kwargs):
        calls.append(messages)
        assert tools == []
        raise ValueError('summary provider failed')
    model.context_limits = lambda _: {'window':32000,'output':2000,'protocol':'anthropic','model':'test'}
    ctx = manager(store, project, model)
    result = ctx.prepare([{'role':'system','content':'keep'},*batch('tool','x'*100000)], [])
    assert len(calls) == 1
    assert 'deterministic' in result[1]['content']


def test_cancelled_checkpoint_does_not_commit(store, tmp_path):
    from app.v1.execution_control import ExecutionOwnershipLost
    project = store.create_project('fence','',str(tmp_path/'project'))
    control = SimpleNamespace(check=lambda: (_ for _ in ()).throw(ExecutionOwnershipLost('cancelled')))
    ctx = manager(store, project, execution_control=control)
    with pytest.raises(ExecutionOwnershipLost):
        ctx._save(batch('x','data',10), {'summary':'late'}, 100, 50)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v1_context_checkpoints').fetchone()[0] == 0


def test_assignment_checkpoint_is_reloaded_after_store_restart(store, tmp_path):
    from tests.test_persistent_agents import assignment
    from app.v1.store import V1Store
    from app.v1.session_replay import replayable_session_messages
    project = store.create_project('resume','',str(tmp_path/'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    task, run, owned = assignment(store, project, workflow)
    agent = store.begin_agent_run(project_id=project['id'], kind='column', instruction_revision=1,
        instruction_snapshot='', context_snapshot={}, capabilities=[], task_id=task['id'], column_run_id=run['id'],
        agent_session_id=owned['session_id'], platform_policy=store.latest_platform_policy(), runtime_policy=store.policy)
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_agent_runs SET assignment_id=? WHERE id=?',(owned['id'],agent['id']))
    group = batch('old','x'*200000)
    for m in group:
        saved = store.add_agent_message(agent['id'], m['role'], m['content'],m.get('tool_calls',[]),m.get('tool_call_id'))
        m['_source_message_id'] = saved['id']
    ctx = manager(store, project, assignment=owned)
    ctx.run_id = agent['id']
    ctx.prepare([{'role':'system','content':'fixed'},*group], [])
    tail = store.add_agent_message(agent['id'],'user','unresolved 401 versus 429',[])
    reopened = V1Store(str(store.path),registry=store.registry)
    history = replayable_session_messages(reopened.agents.session_history(project['id'],owned['session_id'],assignment_id=owned['id']))
    assert history[0]['_checkpoint'] and 'context_checkpoint' in history[0]['content']
    assert history[-1]['content'] == 'unresolved 401 versus 429'
    assert history[-1]['_source_message_id'] == tail['id']
    assert not any(m.get('tool_calls') for m in history)


@pytest.mark.parametrize('variant',['valid','mismatch','skipped','missing','stale','source_changed','malformed'])
def test_runtime_scenario_evidence_checks_real_report(store, tmp_path, variant):
    project = store.create_project('reports','',str(tmp_path/'project'))
    root = tmp_path/'project'
    source = root/'test_behavior.py'
    source.write_text('assert 401 == 401\n',encoding='utf-8')
    report = {'schema_version':'devwerk.acceptance-report.v1','scenarios':[{'id':'login-failure','status':'passed',
        'assertions':[{'expected':401,'actual':401}],
        'test_source':{'path':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}}]}
    if variant == 'mismatch': report['scenarios'][0]['assertions'][0]['actual'] = 429
    if variant == 'skipped': report['scenarios'][0]['status'] = 'skipped'
    if variant == 'missing': report['scenarios'] = []
    if variant == 'source_changed': source.write_text('assert True\n')
    if variant == 'malformed': report = []
    script = "from pathlib import Path; Path('report.json').write_text("+repr(json.dumps(report))+",encoding='utf-8')"
    if variant == 'stale':
        (root/'report.json').write_text(json.dumps(report))
        script = 'print("no tests executed")'
    ctx = CapabilityContext(project['id'],project,store,execution_key='report-check')
    result = run_acceptance_check(store.registry,ctx,{'capability':'project.command.run',
        'arguments':{'argv':[sys.executable,'-c',script]},'report_path':'report.json','scenario_ids':['login-failure']})
    assert result.ok == (variant == 'valid'), result
    if result.ok:
        assert result.output['scenario_evidence']['execution_key'] == 'report-check'
    else:
        assert result.error['type'] == 'AcceptanceEvidenceInvalid'


def test_same_visit_feedback_resolves_only_after_new_acceptance(store, tmp_path):
    workflow = agent_workflow()
    work = workflow.column('work')
    work.executor.capabilities += ['task.feedback.record','project.command.run']
    work.acceptance_checks = [AcceptanceCheck(key='behavior',capability='project.command.run',
        arguments={'argv':[sys.executable,'-c',"from pathlib import Path; assert Path('value.txt').read_text()=='fixed'"]})]
    workflow.acceptance_obligations = [AcceptanceObligation(column='work',check_key='behavior')]
    project = store.create_project('same visit','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'repair in this visit')
    calls = iter([
        ('task.feedback.record',{'task_id':task['id'],'dedupe_key':'observed','responsible_column':'work','description':'value wrong',
            'checks':[{'column':'work','check_key':'behavior'}]}),
        ('project.files.write',{'path':'value.txt','content':'fixed'}),
        ('column.complete',{'outcome':'success','output':{'delivered':True},'summary':'fixed and verified'})])
    def model(*a, **kw):
        name, arguments = next(calls)
        return AgentModelResponse(tool_calls=[AgentToolCall(id=name,name=name,arguments=arguments)])
    WorkflowRuntime(store,store.registry,'owner',AgentCore(store,store.registry,model)).step(task['id'])
    assert store.get_task(task['id'])['status'] == 'done'
    feedback = store.feedback.list(project['id'],task['id'])[0]
    assert feedback['state'] == 'resolved' and feedback['source_run_id'] == feedback['repair_run_id']
    assert len(store.runs(project['id'],task['id'])) == 1


def test_partial_acceptance_configure_preserves_graph_and_rejects_stale_base(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    project, plan, ctx = software_plan(store,tmp_path)
    before = store.get_workflow(project['id'])
    args = {'base_revision_id':before['id'],'base_digest':before['definition_hash'],
            'columns':[{'key':'accept','acceptance_checks':before['definition']['columns'][-1]['acceptance_checks']}],
            'acceptance_obligations':before['definition']['acceptance_obligations']}
    result = store.registry.dispatch('workflow.acceptance.configure',args,ctx)
    assert result.ok, result.error
    after = store.get_workflow(project['id'])
    assert before['definition'] == after['definition']
    assert len(json.dumps(result.output)) < 600
    stale = store.registry.dispatch('workflow.acceptance.configure',args,replace(ctx,execution_key='new-stale-operation'))
    assert not stale.ok and 'WorkflowRevisionChanged' in str(stale.error)
    assert store.get_workflow(project['id'])['id'] == after['id']


def test_acceptance_can_be_configured_one_column_at_a_time_without_weaker_task_admission(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    from app.v1.domain import WorkflowDefinition

    project, plan, ctx = software_plan(store, tmp_path)
    complete = store.get_workflow(project['id'])
    checks = {column['key']: column['acceptance_checks']
              for column in complete['definition']['columns']}
    empty = WorkflowDefinition.model_validate(complete['definition'])
    for column in empty.columns:
        column.acceptance_checks = []
    empty.acceptance_obligations = []
    current = store.publish_workflow(project['id'], empty, complete['workflow_plan_id'])
    required = ['backend_development', 'frontend_development', 'system_test', 'delivery', 'accept']
    for index, key in enumerate(required):
        configured = store.registry.dispatch('workflow.acceptance.configure', {
            'base_revision_id': current['id'], 'base_digest': current['definition_hash'],
            'columns': [{'key': key, 'acceptance_checks': checks[key]}],
        }, replace(ctx, execution_key=f'configure-stage-{index}'))
        assert configured.ok, configured.error
        assert configured.output['ready_for_task_plan'] is (index == len(required) - 1)
        assert bool(configured.output['remaining_diagnostics']) is (index < len(required) - 1)
        current = store.get_workflow(project['id'])
        assert current['id'] == configured.output['id']
        obligations = {(item['column'], item['check_key']): item['invalidated_by']
                       for item in current['definition']['acceptance_obligations']}
        assert set(column for column, _ in obligations) == set(required[:index + 1])
        expected_invalidators = ([key] if key.endswith('_development')
                                 else ['backend_development', 'frontend_development'])
        assert obligations[(key, 'verify_fixture')] == expected_invalidators
        plan.workflow_revision_id = current['id']
        validation = store.registry.dispatch('task.plan.validate',
            {'plan': plan.model_dump(mode='json')},
            replace(ctx, execution_key=f'validate-stage-{index}'))
        assert validation.ok, validation.error
        assert validation.output['valid'] is (index == len(required) - 1)


def test_simple_behavior_runners_freeze_checks_and_derive_obligations(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    from app.v1.domain import WorkflowDefinition

    project, plan, ctx = software_plan(store, tmp_path)
    complete = store.get_workflow(project['id'])
    empty = WorkflowDefinition.model_validate(complete['definition'])
    for column in empty.columns:
        column.acceptance_checks = []
    empty.acceptance_obligations = []
    current = store.publish_workflow(project['id'], empty, complete['workflow_plan_id'])
    required = ['backend_development', 'frontend_development', 'system_test', 'delivery', 'accept']

    def runner(key):
        report = f'reports/{key}.json'
        return {'column':key, 'argv':['python', f'scripts/{key}.py', '--report', report],
            'report_path':report, 'scenario_ids':['entry-fixture'],
            'purpose':f'Run real {key} assertions and write the fresh report'}

    for index, batch in enumerate((required[:2], required[2:])):
        result = store.registry.dispatch('workflow.acceptance.runners', {
            'base_revision_id':current['id'], 'base_digest':current['definition_hash'],
            'runners':[runner(key) for key in batch]},
            replace(ctx, execution_key=f'runner-batch-{index}'))
        assert result.ok, result.error
        assert result.output['ready_for_task_plan'] is (index == 1)
        current = store.get_workflow(project['id'])
    definition = WorkflowDefinition.model_validate(current['definition'])
    for key in required:
        check = definition.column(key).acceptance_checks[0]
        assert check.evidence_kind == 'behavior' and check.launch_validation == 'deferred'
        assert check.report_path in check.arguments['argv']
        obligation = next(item for item in definition.acceptance_obligations
            if item.column == key and item.check_key == check.key)
        assert obligation.invalidated_by == ([key] if key.endswith('_development')
            else ['backend_development','frontend_development'])
    plan.workflow_revision_id = current['id']
    validation = store.registry.dispatch('task.plan.validate',
        {'plan':plan.model_dump(mode='json')}, replace(ctx, execution_key='runner-validate'))
    assert validation.ok and validation.output['valid'], validation.error

    stale = store.registry.dispatch('workflow.acceptance.runners', {
        'base_revision_id':complete['id'], 'base_digest':complete['definition_hash'],
        'runners':[runner('accept')]}, replace(ctx, execution_key='runner-stale'))
    assert not stale.ok and 'WorkflowRevisionChanged' in str(stale.error)


def test_simple_behavior_runner_rejects_missing_report_argument_and_fake_check(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    project, _, ctx = software_plan(store, tmp_path)
    current = store.get_workflow(project['id'])
    report = 'reports/backend.json'
    base = {'base_revision_id':current['id'], 'base_digest':current['definition_hash']}
    runner = {'column':'backend_development', 'argv':['mvn','compile'],
        'report_path':report, 'scenario_ids':['entry-fixture'], 'purpose':'Compile backend'}
    missing = store.registry.dispatch('workflow.acceptance.runners',
        {**base,'runners':[runner]}, replace(ctx, execution_key='runner-no-report'))
    assert not missing.ok and 'BehaviorRunnerReportArgumentMissing' in str(missing.error)
    fake = store.registry.dispatch('workflow.acceptance.runners',
        {**base,'runners':[{**runner,'argv':['echo',report]}]},
        replace(ctx, execution_key='runner-echo'))
    assert not fake.ok and 'NonBehaviorCheck' in str(fake.error)
    no_scenarios = store.registry.dispatch('workflow.acceptance.runners',
        {**base,'runners':[{**runner,'argv':['python','backend/acceptance.py',report],
            'scenario_ids':[]}]}, replace(ctx, execution_key='runner-no-scenarios'))
    assert not no_scenarios.ok
    assert store.get_workflow(project['id'])['id'] == current['id']


def test_rejects_one_backend_runner_as_distinct_frontend_and_delivery_proof(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    from app.v1.domain import WorkflowDefinition, WorkflowPlan
    from app.v1.services.task_plan_compiler import acceptance_diagnostics
    project, _, ctx = software_plan(store, tmp_path)
    current = store.get_workflow(project['id'])
    definition = WorkflowDefinition.model_validate(current['definition'])
    definition.column('frontend_development').acceptance_checks[0].scenario_ids = ['frontend-ui']
    definition.column('delivery').acceptance_checks[0].scenario_ids = ['delivery-cleanup']
    method = WorkflowPlan.model_validate(store.get_workflow_plan(
        project['id'], current['workflow_plan_id'])['plan'])
    errors = acceptance_diagnostics(definition, method)
    assert sum('AcceptanceEvidenceScopeCollision' in error['message'] for error in errors) == 2
    args = {'base_revision_id':current['id'], 'base_digest':current['definition_hash'],
            'columns':[{'key':column.key, 'acceptance_checks':[check.model_dump(mode='json')
                        for check in column.acceptance_checks]} for column in definition.columns
                       if column.key in {'frontend_development','delivery'}],
            'acceptance_obligations':[item.model_dump(mode='json')
                                      for item in definition.acceptance_obligations]}
    rejected = store.registry.dispatch('workflow.acceptance.configure', args, ctx)
    assert not rejected.ok and 'AcceptanceEvidenceScopeCollision' in str(rejected.error)
    assert store.get_workflow(project['id'])['id'] == current['id']
    published = store.registry.dispatch('workflow.publish', {
        'workflow_plan_id': current['workflow_plan_id'],
        'workflow': definition.model_dump(mode='json')},
        replace(ctx, execution_key='direct-collision-publication'))
    assert not published.ok and 'AcceptanceEvidenceScopeCollision' in str(published.error)
    assert store.get_workflow(project['id'])['id'] == current['id']
    definition.column('frontend_development').acceptance_checks[0].arguments['argv'].append('frontend-test')
    definition.column('delivery').acceptance_checks[0].arguments['argv'].append('delivery-test')
    assert not acceptance_diagnostics(definition, method)
    for key in ('backend_development', 'frontend_development', 'delivery'):
        definition.column(key).acceptance_checks[0].arguments['argv'] = ['python', '-m', 'pytest', 'all_tests.py']
        definition.column(key).acceptance_checks[0].scenario_ids = ['one', 'two']
    definition.column('frontend_development').acceptance_checks[0].scenario_ids.reverse()
    assert not acceptance_diagnostics(definition, method)


def test_task_plan_validation_collects_all_columns_without_writes(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    from app.v1.domain import WorkflowDefinition
    project, plan, ctx = software_plan(store,tmp_path)
    before = store.get_workflow(project['id'])
    definition = WorkflowDefinition.model_validate(before['definition'])
    for column in definition.columns: column.acceptance_checks = []
    definition.acceptance_obligations = []
    revision = store.publish_workflow(project['id'],definition,before['workflow_plan_id'])
    plan.workflow_revision_id = revision['id']
    result = store.registry.dispatch('task.plan.validate',{'plan':plan.model_dump(mode='json')},ctx)
    assert result.ok and not result.output['valid']
    assert len(result.output['diagnostics']) >= 5
    assert not store.list_task_plans(project['id'])


def test_planning_projection_preserves_required_schema_and_revision(store, tmp_path):
    from app.v1.context_manager import packed, project_result
    project = store.create_project('planning projection','',str(tmp_path/'project'))
    loop = store.get_loop('software.ddd_delivery')
    view = json.loads(project_result(packed({'ok':True,'capability':'loop.inspect','output':loop}),6900))
    assert view['output']['parameter_schema'] == loop['parameter_schema']
    assert view['output']['task_contract'] == loop['bundle']['workflow_plan']['task_contract']
    assert 'acceptance-report.md' in view['output']['assets']
    repeated = json.loads(project_result(packed(view), max(3000,len(packed(view).encode('utf8'))-50)))
    assert repeated['ok'] is True
    applied = store.apply_loop(project['id'],'software.ddd_delivery',{'product_name':'demo','delivery_target':'local'})
    view = json.loads(project_result(packed({'ok':True,'capability':'loop.apply','output':applied}),6900))
    assert view['output']['workflow']['definition_hash'] == applied['workflow']['definition_hash']
    assert len(packed(view)) < 5000


def test_old_receipt_cannot_resolve_new_feedback_in_same_visit(store, tmp_path):
    from tests.test_persistent_agents import assignment
    from app.v1.domain import ToolResult
    project = store.create_project('receipt boundary','',str(tmp_path/'project'))
    workflow = agent_workflow()
    workflow.column('work').acceptance_checks = [AcceptanceCheck(key='check',capability='project.files.read',arguments={'path':'proof.txt'})]
    publish_planned_workflow(store,project['id'],workflow)
    task, run, owned = assignment(store,project,workflow)
    ctx = CapabilityContext(project['id'],project,store,task_id=task['id'],column_run_id=run['id'])
    spec = AgentRunSpec(kind='column',project=project,instruction='',instruction_revision=1,context={},capability_ids=[],
        task_id=task['id'],column_run_id=run['id'],column_attempt_id=run['attempt_id'],assignment=owned)
    result = ToolResult(ok=True,capability='project.files.read',output={'content':'verified'})
    store.feedback.record_check(spec,'check','before-feedback',result)
    feedback = store.feedback.record(ctx,{'task_id':task['id'],'dedupe_key':'later','responsible_column':'work',
        'description':'new defect observed after old passing check','checks':[{'column':'work','check_key':'check'}]})
    with store.connect() as db:
        assert not store.feedback.verified(db,task,'work','check',current_run=run['id'],after_check_rowid=feedback['observed_check_rowid'])
    store.feedback.record_check(spec,'check','after-feedback',result)
    with store.connect() as db:
        assert store.feedback.verified(db,task,'work','check',current_run=run['id'],after_check_rowid=feedback['observed_check_rowid'])


def test_unknown_provider_requires_explicit_context_window(monkeypatch):
    from app.core.config import reload_settings
    from app.v1.llm import context_limits
    import os
    config = json.loads(os.environ['DEVWERK_LLM_CONFIG_JSON'])
    with pytest.raises(ValueError, match='context_window'):
        context_limits('column')
    config['models']['main']['context_window'] = 128000
    monkeypatch.setenv('DEVWERK_LLM_CONFIG_JSON',json.dumps(config))
    reload_settings()
    assert context_limits('column')['window'] == 128000


@pytest.mark.parametrize('status',[200,400,413])
def test_overflow_error_works_for_provider_payload_and_http_status(status):
    from app.services.provider_errors import LLMProviderError, ProviderErrorDetails
    error = LLMProviderError(ProviderErrorDetails('anthropic','minimax',status,'LLM_BAD_REQUEST',
        'invalid params, context window exceeds limit (2013)',2013))
    assert error.error_code == 'LLM_CONTEXT_WINDOW_EXCEEDED'


def test_second_overflow_stops_without_effect_replay(store, tmp_path):
    from app.services.provider_errors import LLMProviderError, ProviderErrorDetails
    project = store.create_project('retry ceiling','',str(tmp_path/'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'write only once')
    count = 0
    def model(*a, **kw):
        nonlocal count
        count += 1
        if count == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id='write',name='project.files.write',arguments={'path':'x.txt','content':'x'*25000})])
        raise LLMProviderError(ProviderErrorDetails('anthropic','minimax',400,'LLM_BAD_REQUEST','context window exceeds limit',2013))
    with pytest.raises(ContextExhausted, match='one reduced-context retry'):
        WorkflowRuntime(store,store.registry,'owner',AgentCore(store,store.registry,model)).step(task['id'])
    assert count == 3
    current = store.get_task(task['id'])
    assert current['status'] == 'recovering' and current['control_state'] == 'paused'
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_tool_invocations WHERE capability='project.files.write'").fetchone()[0] == 1
        assert db.execute('SELECT error_code FROM v1_column_attempts').fetchone()[0] == 'LLM_CONTEXT_WINDOW_EXCEEDED'


@pytest.mark.parametrize('argv', [
    ['cmd','/c','dir','FINAL_ACCEPTANCE.md'], ['cmd.exe','/c','dir README.md'],
    ['mvn','package','-DskipTests'], ['mvn','test','-Dmaven.test.skip=true'],
    ['cmd','/c','cmd /c if exist FINAL_ACCEPTANCE.md (echo OK)'],
    ['cmd.exe','/d','/s','/c','"cmd /c if exist "folder name/report.json" (echo OK)"'],
    ['mvnw.cmd','clean','compile','-q'],
    ['cmd','/c','echo Backend JUnit T-01~T-10 passed'],
    ['cmd','/c','type README.md'],
    ['cmd','/c','findstr /C:"Windows" README.md 2>&1'],
])
def test_obviously_nonbehavior_commands_are_rejected_at_admission(argv):
    from app.v1.services.task_plan_compiler import known_nonbehavior_command
    assert known_nonbehavior_command(argv).startswith('NonBehaviorCheck:')
    assert known_nonbehavior_command(['mvn','test','-DskipTests=false']) is None
    assert known_nonbehavior_command(['cmd','/c','dir README.md && python verify.py']) is None
    assert known_nonbehavior_command(['cmd','/c','if exist report.json (echo OK) && python verify.py']) is None
    assert known_nonbehavior_command(['cmd','/c','echo preparing && python verify.py']) is None
    assert known_nonbehavior_command(['mvn','compile','test']) is None
    assert known_nonbehavior_command(['mvn','compile','-Pacceptance']) is None
    assert known_nonbehavior_command(['npm.cmd','run','build']) is None


def test_software_workflow_rejects_echo_behavior_before_publication(store, tmp_path):
    from tests.test_task_entry_admission import software_plan
    from app.v1.domain import WorkflowDefinition, WorkflowPlan
    from app.v1.services.task_plan_compiler import acceptance_diagnostics
    project, _, ctx = software_plan(store, tmp_path)
    current = store.get_workflow(project['id'])
    definition = WorkflowDefinition.model_validate(current['definition'])
    check = definition.column('backend_development').acceptance_checks[0]
    check.arguments['argv'] = ['cmd', '/c', 'echo Backend JUnit tests passed']
    method = WorkflowPlan.model_validate(store.get_workflow_plan(
        project['id'], current['workflow_plan_id'])['plan'])
    assert any('NonBehaviorCheck:' in issue['message'] for issue in acceptance_diagnostics(definition, method))
    denied = store.registry.dispatch('workflow.acceptance.configure', {
        'base_revision_id': current['id'], 'base_digest': current['definition_hash'],
        'columns': [{'key': 'backend_development', 'acceptance_checks': [check.model_dump(mode='json')]}],
        'acceptance_obligations': current['definition']['acceptance_obligations'],
    }, ctx)
    assert not denied.ok and 'NonBehaviorCheck:' in str(denied.error)
    assert store.get_workflow(project['id'])['id'] == current['id']
    direct = store.registry.dispatch('workflow.publish', {
        'workflow_plan_id': current['workflow_plan_id'],
        'workflow': definition.model_dump(mode='json')},
        replace(ctx, execution_key='echo-direct-publish'))
    assert not direct.ok and 'NonBehaviorCheck:' in str(direct.error)
    assert store.get_workflow(project['id'])['id'] == current['id']


def test_checkpoint_notes_share_the_whole_summary_budget(store, tmp_path):
    from app.v1.context_manager import packed
    from app.v1.policy import ContextPolicy
    project = store.create_project('bounded-notes','',str(tmp_path/'project'))
    store.policy = store.policy.model_copy(update={'context':ContextPolicy(summary_tokens=512)})
    ctx = manager(store, project)
    notes = [{'role':'user','content':'失败原因和待核对事实'*300,'_source_message_id':i} for i in range(1,5)]
    messages = [{'role':'system','content':'current assignment'},*notes,*batch('write','x'*200000,10)]
    ctx.prepare(messages,[])
    summary = json.loads(next(m['content'] for m in messages if m.get('_checkpoint')))['context_checkpoint']
    assert len(packed(summary).encode('utf8')) <= max(512,int(ctx.policy.summary_tokens/(ctx.ratio*1.15)))
    with store.connect() as db:
        persisted = json.loads(db.execute('SELECT summary_json FROM v1_context_checkpoints').fetchone()[0])
    assert persisted == summary


def test_command_preflight_and_deferred_entry_do_not_execute(store, tmp_path):
    from app.v1.services.task_plan_compiler import diagnose_acceptance_launches
    project = store.create_project('preflight','',str(tmp_path/'project'))
    workflow = agent_workflow()
    check = AcceptanceCheck(key='future',capability='project.command.run',arguments={'argv':['nonexistent-future-verify-273653.exe']})
    workflow.column('work').acceptance_checks = [check]
    result = diagnose_acceptance_launches(store,project['id'],workflow)
    assert not result['diagnostics'] and len(result['deferred_command_checks']) == 1
    check.launch_validation = 'preflight'
    result = diagnose_acceptance_launches(store,project['id'],workflow)
    assert len(result['diagnostics']) == 1 and not result['deferred_command_checks']
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v1_execution_receipts').fetchone()[0] == 0


def test_archive_unicode_pages_are_contiguous_and_project_scoped(store, tmp_path):
    from tests.test_agent_recovery_and_fencing import begin_agent
    project = store.create_project('archive','',str(tmp_path/'project'))
    run = begin_agent(store,project,kind='conversation')
    source = '诊断信息 expected 401 actual 429\n'*800
    store.record_tool_invocation(agent_run_id=run['id'],tool_call_id='large',capability='project.command.run',
        arguments={'argv':['unused']},result={'ok':True,'output':{'stdout':source}},ok=True)
    ctx = CapabilityContext(project['id'],project,store)
    from app.v1.capabilities import _agent_result_read
    from app.v1.context_manager import packed, project_result
    text, offset = '', 0
    while offset is not None:
        result = _agent_result_read({'agent_run_id':run['id'],'tool_call_id':'large','field':'stdout','offset':offset,'limit':8000},ctx)
        assert len(result['content'].encode('utf8')) <= 6000
        if offset == 0:
            assert len(result['content'].encode('utf8')) > 2000
        text += result['content']
        offset = result['next_offset']
    assert text == source
    projected_text, offset = '', 0
    while offset is not None:
        page = _agent_result_read({'agent_run_id':run['id'],'tool_call_id':'large',
            'field':'stdout','offset':offset,'limit':8000},ctx)
        visible = json.loads(project_result(packed({'ok':True,'capability':'agent.result.read',
            'output':page}), 1200))['output']
        assert visible['content']
        assert visible['next_offset'] == (offset+len(visible['content'])
            if offset+len(visible['content']) < len(source) else None)
        assert len(packed({'ok':True,'capability':'agent.result.read','output':visible}).encode('utf8')) <= 1200
        projected_text += visible['content']
        offset = visible['next_offset']
    assert projected_text == source
    other = store.create_project('other','',str(tmp_path/'other'))
    with pytest.raises(ValueError,match='outside'):
        _agent_result_read({'agent_run_id':run['id'],'tool_call_id':'large'},CapabilityContext(other['id'],other,store))


def test_summary_cancelled_during_provider_call_cannot_save(store, tmp_path):
    from app.v1.execution_control import ExecutionOwnershipLost
    project = store.create_project('summary race','',str(tmp_path/'project'))
    cancelled = False
    def check():
        if cancelled:
            raise ExecutionOwnershipLost('cancelled during summary')
    def model(*a, **kw):
        nonlocal cancelled
        cancelled = True
        return AgentModelResponse(text='late summary')
    model.context_limits = lambda _: {'window':24000,'output':2000,'model':'test','protocol':'anthropic'}
    ctx = manager(store,project,model,execution_control=SimpleNamespace(check=check))
    with pytest.raises(ExecutionOwnershipLost):
        ctx.prepare([{'role':'system','content':'contract'},*batch('tool','x'*200000,10)],[])
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v1_context_checkpoints').fetchone()[0] == 0


def test_accepting_current_proposal_reports_focus_conflict_without_mutation(store, tmp_path):
    from tests.test_conversation_intent_contract import turn, resolve
    project = store.create_project('proposal diagnostics','',str(tmp_path/'project'))
    first, ctx = turn(store,project,'先讨论方案')
    discussed = resolve(ctx,first,proposal='confirmed draft scope')
    proposal = discussed.output['work_intent']['proposal']
    store.finish_conversation_job(first['id'],None,ctx.agent_run_id,{})
    second, ctx = turn(store,project,'现在按刚才方案开始实现')
    args = dict(act='execute',execution_request='begin',constraint_change='release_hold',
        accepted_proposal_id=proposal['id'],user_evidence=[{'message_id':second['user_message_id']}])
    rejected = resolve(ctx,second,focus='new_scope',**args)
    assert not rejected.ok and 'focus=current' in rejected.error['message']
    assert store.intents.context(second)['execution_hold']
    assert not store.agents.list_requirements(project['id'])
    accepted = resolve(ctx,second,focus='current',**args)
    assert accepted.ok, accepted.error
    assert accepted.output['turn_contract']['phase'] == 'execution'
