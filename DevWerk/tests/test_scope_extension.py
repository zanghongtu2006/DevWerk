from dataclasses import replace
import json

import pytest

from app.v1.capabilities import CapabilityContext
from app.v1.domain import WorkflowDefinition
from tests.helpers import task_plan, agent_workflow, publish_planned_workflow
from tests.test_agent_recovery_and_fencing import begin_agent
from tests.test_loop_contract import novel_task_input
from tests.test_persistent_agents import assignment


def turn(store, project, requirement=None, request=None):
    with store.connect() as db:
        prior = db.execute("SELECT id,agent_run_id FROM v1_conversation_jobs WHERE project_id=? AND status='running'", (project['id'],)).fetchall()
    for row in prior:
        store.finish_conversation_job(row['id'], None, row['agent_run_id'], {})
    queued = store.create_conversation_job(project['id'], 'Extend the novel to twelve chapters without ending it', True)
    job = store.claim_conversation_job(queued['id'], 'scope-test')
    main = store.agents.main(project['id'])
    run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
    ctx = CapabilityContext(project['id'], project, store, agent_run_id=run['id'], agent_instance_id=main['id'], user_initiated=True)
    state = store.intents.context(job)
    result = store.registry.dispatch('conversation.turn.resolve', {
        'source_message_id':job['user_message_id'], 'based_on_intent_revision':state['revision'],
        'act':'execute', 'execution_request':request or ('extend' if requirement else 'begin'),
        'requirement_id':requirement['id'] if requirement else None,
        'scope_summary':job['message'], 'user_evidence':[{'message_id':job['user_message_id'],'quote':job['message']}],
    }, ctx)
    assert result.ok, result.error
    req = result.output['requirement']
    return replace(ctx, requirement_id=req['id'], requirement_revision=req['revision']), req


def invoke(ctx, name, args):
    result = ctx.store.registry.dispatch(name, args, ctx)
    assert result.ok, result.error
    return result.output


def novel(store, tmp_path):
    project = store.create_project('Six chapters', '', str(tmp_path / 'novel'))
    ctx, req = turn(store, project)
    result = invoke(ctx, 'loop.apply', {'loop_key': 'novel.production', 'bindings': {
        'project_title': 'Six chapters', 'premise': 'The photograph remains unclaimed.', 'chapter_count': 6,
        'chapter_target_characters': 3000, 'chapter_max_characters': 4000}})
    return project, ctx, req, result['workflow']['id']


def revision_args(req, workflow):
    return {'requirement_id': req['id'], 'expected_revision': req['revision'], 'expected_workflow_revision_id': workflow,
            'objective': 'Continue chapters seven through twelve; leave the story open',
            'binding_patch': {'chapter_count': 12, 'ending_policy': 'continue'}, 'reason': 'User requested continuation'}


def save_chapters(ctx, workflow, chapters):
    definition = ctx.store.workflow_by_id(ctx.project_id, workflow)
    chapters = list(chapters)
    plans = [task_plan(workflow, definition, task_ref=f'chapter{n:02}', title=f'Chapter {n}', input_data=novel_task_input(n)) for n in chapters]
    for n, plan in zip(chapters, plans):
        plan.tasks[0].dependencies = [f'chapter{n-1:02}'] if n-1 in chapters else []
    plan = plans[0].model_copy(update={'tasks': [p.tasks[0] for p in plans]})
    return invoke(ctx, 'task.plan.save', {'plan': plan.model_dump(mode='json')})


def test_scope_extension_closed_six_to_twelve_same_conversation_and_frozen_history(store, tmp_path):
    project, ctx, req, old_workflow = novel(store, tmp_path)
    old_plan = save_chapters(ctx, old_workflow, range(1, 7))
    invoke(ctx, 'task.create', {'task_plan_id': old_plan['id'], 'proposed_task_ref': 'chapter01'})
    old_tasks = store.list_tasks(project['id'])
    assert len(old_tasks) == 6
    assert all(t['requirement_revision'] == 1 for t in old_tasks)
    # Historical terminal fixture: this test verifies planning/ownership, not prose acceptance.
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET status='done' WHERE project_id=?", (project['id'],))
    store.agents.close_requirement(project['id'], req['id'], 'cancelled')
    rejected = store.registry.dispatch('requirement.create', {'scope_key': 'default', 'objective': 'Twelve'}, ctx)
    assert not rejected.ok
    assert 'Main Agent' not in rejected.error['message']
    ctx, _ = turn(store, project, req)
    revised = invoke(replace(ctx, start_task=False), 'project.scope.revise', revision_args(req, old_workflow))
    assert revised['requirement']['id'] == req['id'] and revised['requirement']['revision'] == 2
    assert revised['bindings']['chapter_max_characters'] == 4000
    assert revised['task_ids'] == [] and len(store.list_tasks(project['id'])) == 6
    # The original cached context is deliberately reused after the revision.
    new_plan = save_chapters(replace(ctx, start_task=False), revised['workflow_revision_id'], range(7, 13))
    invoke(ctx, 'task.create', {'task_plan_id': new_plan['id'], 'proposed_task_ref': 'chapter07'})
    tasks = store.list_tasks(project['id'])
    assert len(tasks) == 12
    new_tasks = [t for t in tasks if t['requirement_revision'] == 2]
    assert len(new_tasks) == 6 and all(t['requirement_id'] == req['id'] for t in tasks)
    seven = next(t for t in tasks if t['input']['chapter_number'] == 7)
    six = next(t for t in tasks if t['input']['chapter_number'] == 6)
    with store.connect() as db:
        assert db.execute('SELECT 1 FROM v1_task_dependencies WHERE task_id=? AND depends_on_task_id=?', (seven['id'], six['id'])).fetchone()
    assert store.get_project_loop_binding(project['id'], old_workflow)['bindings']['chapter_count'] == 6
    assert store.get_project_loop_binding(project['id'])['bindings']['ending_policy'] == 'continue'
    assert all(store.get_task(t['id'])['workflow_revision_id'] == old_workflow for t in old_tasks)
    assert [w['id'] for w in store.agents.list_workers(project['id']) if w['role'] == 'main'] == [ctx.agent_instance_id]
    old_rejected = store.registry.dispatch('task.create', {'task_plan_id': old_plan['id'], 'proposed_task_ref': 'chapter02'}, ctx)
    assert not old_rejected.ok


def test_scope_revision_atomic_rollback_and_compare_and_swap(store, tmp_path, monkeypatch):
    project, ctx, req, workflow = novel(store, tmp_path)
    ctx, _ = turn(store, project, req)
    original = store.publish_workflow
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError('Injected failure after publish')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'publish_workflow', fail)
        rejected = store.registry.dispatch('project.scope.revise', revision_args(req, workflow), ctx)
        assert not rejected.ok
    assert store.get_workflow(project['id'])['id'] == workflow
    assert store.agents.get_requirement(project['id'], req['id'])['revision'] == 1
    revised = invoke(ctx, 'project.scope.revise', revision_args(req, workflow))
    # A distinct operation with stale expected version conflicts rather than overwriting.
    rejected = store.registry.dispatch('project.scope.revise', revision_args(req, workflow), replace(ctx, execution_key='another-operation'))
    assert not rejected.ok and rejected.error['code'] == 'scope_revision_conflict'
    assert store.get_workflow(project['id'])['id'] == revised['workflow_revision_id']


def test_old_assignment_finishes_with_old_contract_after_scope_revision(store, tmp_path):
    project, ctx, req, workflow = novel(store, tmp_path)
    plan = save_chapters(ctx, workflow, [1])
    task = invoke(ctx, 'task.create', {'task_plan_id': plan['id'], 'proposed_task_ref': 'chapter01'})
    job = store.agents.conversation_job(ctx)
    store.finish_conversation_job(job['id'], task['id'], ctx.agent_run_id, {})
    task = store.claim_task(task['id'], 'old-worker')
    run = store.begin_run(task, {})
    definition = store.workflow_by_id(project['id'], workflow)
    active = store.agents.assign(task, run, definition.column(task['current_column']))
    ctx, _ = turn(store, project, req)
    invoke(ctx, 'project.scope.revise', revision_args(req, workflow))
    store.agents.assert_owner(active)
    store.agents.finish(active, 'completed', {'summary': 'Original six chapter scope'})
    assert active['requirement_revision'] == 1
    assert store.get_project_loop_binding(project['id'], task['workflow_revision_id'])['bindings']['chapter_count'] == 6


def test_late_event_cannot_select_or_revise_new_scope(store, tmp_path):
    project, ctx, req, workflow = novel(store, tmp_path)
    ctx, _ = turn(store, project, req)
    invoke(ctx, 'project.scope.revise', revision_args(req, workflow))
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_conversation_jobs SET trigger_kind='mailbox',requirement_revision=1 WHERE id=(SELECT conversation_job_id FROM v1_agent_runs WHERE id=?)", (ctx.agent_run_id,))
    result = store.registry.dispatch('requirement.select', {'requirement_id': req['id']}, ctx)
    assert not result.ok
    result = store.registry.dispatch('project.scope.revise', revision_args(req, workflow), replace(ctx, execution_key='late-event'))
    assert not result.ok


def test_retired_worker_successor_keeps_old_history_and_new_private_context(store, tmp_path):
    project = store.create_project('Worker continuity', '', str(tmp_path / 'worker'))
    wf = agent_workflow()
    publish_planned_workflow(store, project['id'], wf)
    task, run, old = assignment(store, project, wf)
    req = store.agents.get_requirement(project['id'], old['requirement_id'])
    evidence = store.prepare_terminal_evidence(task, run['id'], 'done', {}, None)
    store.finish_run(task, run['id'], {}, 'success', 'done', terminal='done', terminal_artifact=evidence)
    store.agents.set_lifecycle(project['id'], old['agent_instance_id'], 'retired')
    ctx, _ = turn(store, project, req, request='continue')
    result = invoke(ctx, 'agent.worker.replace', {'worker_id': old['agent_instance_id'], 'summary': 'Chapter six ends with the unopened letter.'})
    store.finish_conversation_job(store.agents.conversation_job(ctx)['id'], None, ctx.agent_run_id, {})
    _, _, new = assignment(store, project, wf, requirement=req)
    assert new['agent_instance_id'] == result['worker']['id'] != old['agent_instance_id']
    assert new['session_id'] != old['session_id']
    assert store.agents.get_worker(project['id'], old['agent_instance_id'])['lifecycle'] == 'retired'
    assert 'unopened letter' in json.dumps(store.agents.session_history(project['id'], new['session_id']))


@pytest.mark.parametrize('text', [
    'Chapters 7–12 are queued and chapter 7 is running.',
    '{"mode":"work_result","task_ids":["tsk_invented"]}',
    '{"mode":"work_result","tool_call_ids":["invented-create"]}',
])
def test_gateway_never_publishes_unverified_execution_reply(store, tmp_path, text):
    from app.v1.agent import AgentCore
    from app.v1.domain import AgentModelResponse
    from app.v1.conversation import ConversationGateway
    from tests.test_conversation_contract import run_turn
    project = store.create_project('Unverified reply', '', str(tmp_path / 'reply'))
    calls = []
    def model(*args, **kwargs):
        calls.append(True)
        return AgentModelResponse(text=text)
    gateway = ConversationGateway(store, store.registry, agent_core=AgentCore(store, store.registry, model))
    queued = run_turn(gateway, project['id'], 'Continue with chapters 7–12', True)
    job = store.get_conversation_job(queued['job']['id'])
    assert job['status'] == 'failed' and len(calls) == 3
    assert store.list_tasks(project['id']) == []
    assert all(m['content'] != text for m in store.messages(project['id']) if m['role'] == 'assistant')


def test_report_uses_actual_pending_state_not_model_claim(store, tmp_path):
    from app.v1.conversation_report import render_report
    project, ctx, req, workflow = novel(store, tmp_path)
    plan = save_chapters(ctx, workflow, [1])
    task = invoke(ctx, 'task.create', {'task_plan_id': plan['id'], 'proposed_task_ref': 'chapter01'})
    text, evidence = render_report(store, project['id'], ctx.agent_run_id, json.dumps({
        'mode': 'work_result', 'message': 'All chapters are done and delivered.', 'task_ids': [task['id']]}))
    assert '待执行' in text and 'done and delivered' not in text
    assert evidence['tasks'][0]['status'] == 'pending'


def test_continue_policy_and_frozen_loop_assets_survive_catalog_change(store, tmp_path, monkeypatch):
    project, ctx, req, workflow = novel(store, tmp_path)
    frozen_assets = store.get_project_loop_assets(project['id'], workflow)
    ctx, _ = turn(store, project, req)
    invoke(ctx, 'project.scope.revise', revision_args(req, workflow))
    current = store.get_workflow(project['id'])
    assert current['loop_bindings']['ending_policy'] == 'continue'
    instructions = [c.get('instruction', '') for c in current['definition']['columns']]
    assert any('continue' in instruction and '不得强行完结' in instruction for instruction in instructions)
    monkeypatch.setattr(store, 'get_loop', lambda *_: (_ for _ in ()).throw(ValueError('catalog unavailable')))
    assert store.get_project_loop_assets(project['id'], workflow) == frozen_assets


def test_scope_revision_preserves_custom_method_and_rejects_conflicting_upgrade(store, tmp_path):
    from app.v1.repositories.scope_repository import _apply_delta, ScopeConflict
    assert _apply_delta({'fixed': 1, 'rule': 1}, {'fixed': 1, 'rule': 2}, {'fixed': 'custom', 'rule': 1}) == {'fixed': 'custom', 'rule': 2}
    with pytest.raises(ScopeConflict):
        _apply_delta({'rule': 1}, {'rule': 2}, {'rule': 'custom'})


def test_gateway_continues_cancelled_requirement_and_reports_six_created_tasks(store, tmp_path):
    from app.v1.agent import AgentCore
    from app.v1.domain import AgentModelResponse, AgentToolCall
    from app.v1.conversation import ConversationGateway
    from tests.test_conversation_contract import run_turn
    project, ctx, req, workflow = novel(store, tmp_path)
    planned = save_chapters(ctx, workflow, range(1, 7))
    invoke(ctx, 'task.create', {'task_plan_id': planned['id'], 'proposed_task_ref': 'chapter01'})
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET status='done' WHERE project_id=?", (project['id'],))
    store.agents.close_requirement(project['id'], req['id'], 'cancelled')
    store.finish_conversation_job(store.agents.conversation_job(ctx)['id'], None, ctx.agent_run_id, {})
    step = 0
    def call(name, args, identity):
        return AgentModelResponse(tool_calls=[AgentToolCall(id=identity, name=name, arguments=args)])
    def model(*args, **kwargs):
        nonlocal step
        step += 1
        if step == 1:
            return call('project.scope.inspect', {}, 'inspect')
        if step == 2:
            return call('project.scope.revise', revision_args(req, workflow), 'revise')
        if step == 3:
            active = store.get_workflow(project['id'])
            definition = WorkflowDefinition.model_validate(active['definition'])
            items = []
            for n in range(7, 13):
                item = task_plan(active['id'], definition, task_ref=f'chapter{n:02}', title=f'Chapter {n}', input_data=novel_task_input(n)).tasks[0]
                item.dependencies = [f'chapter{n-1:02}'] if n > 7 else []
                items.append(item)
            plan = task_plan(active['id'], definition).model_copy(update={'tasks': items})
            return call('task.plan.save', {'plan': plan.model_dump(mode='json')}, 'plan')
        if step == 4:
            plan = next(p for p in store.list_task_plans(project['id']) if p['plan']['tasks'][0]['proposed_task_ref'] == 'chapter07')
            return call('task.create', {'task_plan_id': plan['id'], 'proposed_task_ref': 'chapter07'}, 'create')
        return AgentModelResponse(text=json.dumps({'mode': 'work_result', 'message': 'Chapter seven is running', 'tool_call_ids': ['revise', 'create']}))
    from tests.helpers import resolve_execution_before
    gateway = ConversationGateway(store, store.registry, agent_core=AgentCore(store, store.registry,
        resolve_execution_before(model, request='extend', requirement_id=req['id'])))
    queued = run_turn(gateway, project['id'], 'Continue chapters seven through twelve without concluding', True)
    job = store.get_conversation_job(queued['job']['id'])
    assert job['status'] == 'succeeded' and step == 5
    assert len(job['result']['task_ids']) == 6
    assert len(job['result']['workflow_revision_ids']) == 1
    assert 'Chapter 7：待执行' in job['result']['reply'] and 'Chapter seven is running' not in job['result']['reply']
    assert job['requirement_id'] == req['id'] and job['requirement_revision'] == 2
