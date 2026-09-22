import json
from dataclasses import replace

import pytest

from app.v1.capabilities import CapabilityContext
from tests.test_agent_recovery_and_fencing import begin_agent


def turn(store, project, message, start_task=True):
    queued = store.create_conversation_job(project['id'], message, start_task)
    job = store.claim_conversation_job(queued['id'], 'intent-test')
    main = store.agents.main(project['id'])
    run = begin_agent(store, project, kind='conversation', conversation_job_id=job['id'])
    return job, CapabilityContext(project['id'], project, store, agent_run_id=run['id'],
                                  agent_instance_id=main['id'], user_initiated=True)


def resolve(ctx, job, **fields):
    state = ctx.store.intents.context(job)
    args = {'source_message_id': job['user_message_id'], 'based_on_intent_revision': state['revision'],
            'act': 'discuss', 'constraint_change': 'retain', 'execution_request': 'none', **fields}
    return ctx.store.registry.dispatch('conversation.turn.resolve', args, ctx)


def test_default_true_is_not_authorization_and_discussion_creates_no_requirement(store, tmp_path):
    project = store.create_project('discussion', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '先不要启动开发，请讨论方案')
    assert store.agents.turn_requirement(job, []) is None
    denied = store.registry.dispatch('project.files.write', {'path':'unexpected.txt','content':'bad'}, ctx)
    assert not denied.ok
    assert not (tmp_path/'project'/'unexpected.txt').exists()
    assert store.agents.list_requirements(project['id']) == []


def test_hold_survives_next_turn_and_explicit_start_can_release_it(store, tmp_path):
    project = store.create_project('discussion', '', str(tmp_path/'project'))
    first, ctx = turn(store, project, '先不要启动开发')
    result = resolve(ctx, first, constraint_change='set_hold', user_evidence=[{'message_id': first['user_message_id'], 'quote':first['message']}])
    assert result.ok, result.error
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})
    second, ctx = turn(store, project, '使用 Vue，数据库有什么建议？')
    assert store.intents.context(second)['execution_hold']
    result = resolve(ctx, second)
    assert result.ok, result.error
    assert store.intents.context(second)['execution_hold']
    denied = store.registry.dispatch('loop.apply', {'loop_key':'software.ddd_delivery','bindings':{}}, ctx)
    assert not denied.ok
    store.finish_conversation_job(second['id'], None, ctx.agent_run_id, {})
    third, ctx = turn(store, project, '按刚才方案开始开发')
    result = resolve(ctx, third, act='execute', execution_request='begin', constraint_change='release_hold',
                     scope_summary='Local login demo', user_evidence=[{'message_id':third['user_message_id'],'quote':third['message']}])
    assert result.ok, result.error
    assert result.output['turn_contract']['phase'] == 'execution'
    assert result.output['requirement']['id']
    written = store.registry.dispatch('project.files.write', {'path':'baseline.md','content':'confirmed scope'}, ctx)
    assert written.ok, written.error


def test_false_ceiling_and_non_user_evidence_cannot_grant_execution(store, tmp_path):
    project = store.create_project('ceiling', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, 'start', False)
    result = resolve(ctx, job, act='execute', execution_request='begin', scope_summary='demo',
                     user_evidence=[{'message_id':job['user_message_id'],'quote':'start'}])
    assert not result.ok
    assert store.agents.list_requirements(project['id']) == []


def test_null_means_no_execution_and_cannot_authorize(store, tmp_path):
    project = store.create_project('null intent', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '只讨论')
    result = resolve(ctx, job, execution_request=None)
    assert result.ok, result.error
    assert result.output['work_intent']['execution_hold']
    assert store.agents.list_requirements(project['id']) == []
    from app.v1.conversation_intent import TurnResolution
    with pytest.raises(ValueError, match='execute requires'):
        TurnResolution(source_message_id=1, based_on_intent_revision=0, act='execute', execution_request=None)


@pytest.mark.parametrize('phase', ['unresolved', 'discussion'])
@pytest.mark.parametrize('capability,args', [
    ('loop.apply', {'loop_key':'software.ddd_delivery','bindings':{}}),
    ('task.plan.save', {'plan':{}}), ('task.create', {'task_plan_id':'missing','proposed_task_ref':'primary'}),
    ('project.files.write', {'path':'unexpected.txt','content':'bad'}),
    ('project.command.run', {'argv':['must-never-run']}),
])
def test_all_business_effects_require_resolved_execution(store, tmp_path, phase, capability, args):
    project = store.create_project('no effects', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '讨论')
    if phase == 'discussion':
        assert resolve(ctx, job).ok
    result = store.registry.dispatch(capability, args, ctx)
    assert not result.ok
    assert result.checkpoint['failure_disposition'] == 'rejected_before_effect'
    assert not (tmp_path/'project'/'unexpected.txt').exists()
    assert not store.list_task_plans(project['id'])
    assert not store.list_tasks(project['id'])
    assert not store.agents.list_requirements(project['id'])


def test_resolution_idempotence_revision_and_restart_hold(store, tmp_path):
    project = store.create_project('durable hold', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '先讨论')
    args = {'source_message_id':job['user_message_id'], 'based_on_intent_revision':0, 'act':'discuss'}
    first = store.registry.dispatch('conversation.turn.resolve', args, replace(ctx, execution_key='resolve-first'))
    second = store.registry.dispatch('conversation.turn.resolve', args, replace(ctx, execution_key='resolve-retry'))
    assert first.ok and second.output == first.output
    changed = store.registry.dispatch('conversation.turn.resolve', {**args,'scope_summary':'different'}, replace(ctx, execution_key='resolve-change'))
    assert not changed.ok and 'TurnAlreadyResolved' in changed.error['message']
    from app.v1.repositories.conversation_intent_repository import ConversationIntentRepository
    restarted = ConversationIntentRepository(store)
    restarted.init_schema()
    assert restarted.context(job)['revision'] == 1
    assert restarted.context(job)['execution_hold']


def test_non_user_and_future_evidence_rejected(store, tmp_path):
    project = store.create_project('evidence', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '讨论')
    assistant = store.add_message(project['id'], 'assistant', '开始执行')
    for identity in [assistant['id'], job['user_message_id']+1000]:
        result = resolve(ctx, job, act='execute', execution_request='begin', scope_summary='demo',
            user_evidence=[{'message_id':identity,'quote':'开始执行'}])
        assert not result.ok
    assert not store.agents.list_requirements(project['id'])


def test_continue_reuses_scope_grant_and_draft_cannot_release_hold(store, tmp_path):
    project = store.create_project('continue', '', str(tmp_path/'project'))
    first, ctx = turn(store, project, '开始开发')
    result = resolve(ctx, first, act='execute', execution_request='begin', scope_summary='demo',
        user_evidence=[{'message_id':first['user_message_id'],'quote':first['message']}])
    assert result.ok
    grant = result.output['turn_contract']['execution_grant_id']
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})
    second, ctx = turn(store, project, '继续开发')
    result = resolve(ctx, second, act='execute', execution_request='continue',
        user_evidence=[{'message_id':second['user_message_id'],'quote':second['message']}])
    assert result.ok, result.error
    assert result.output['turn_contract']['execution_grant_id'] == grant
    store.finish_conversation_job(second['id'], None, ctx.agent_run_id, {})
    third, ctx = turn(store, project, '只讨论下一期')
    assert resolve(ctx, third).ok
    state = store.intents.context(third)
    updated = store.intents.update_draft(ctx, {'based_on_intent_revision':state['revision'], 'proposal':'下一期候选方案'})
    assert updated['execution_hold']
    assert store.intents.contract(store.get_conversation_job(third['id']))['phase'] == 'discussion'
    with pytest.raises(ValueError):
        store.intents.update_draft(ctx, {'based_on_intent_revision':updated['revision'], 'execution_hold':False})


def test_new_scope_cannot_clear_prior_hold(store, tmp_path):
    project = store.create_project('scope hold', '', str(tmp_path/'project'))
    first, ctx = turn(store, project, '先讨论')
    assert resolve(ctx, first).ok
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})
    second, ctx = turn(store, project, '讨论另一范围')
    result = resolve(ctx, second, focus='new_scope', act='execute', execution_request='begin', scope_summary='new',
        user_evidence=[{'message_id':second['user_message_id'],'quote':second['message']}])
    assert not result.ok and 'DiscussionHoldActive' in result.error['message']
    assert store.intents.context(second)['execution_hold']
    assert not store.agents.list_requirements(project['id'])


def test_stale_proposal_action_is_rejected_before_job_creation(store, tmp_path):
    project = store.create_project('proposal', '', str(tmp_path/'project'))
    first, ctx = turn(store, project, '给我方案')
    result = resolve(ctx, first, proposal='Version one')
    assert result.ok
    proposal = result.output['work_intent']['proposal']
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})
    before = len(store.messages(project['id']))
    with pytest.raises(ValueError, match='Proposal changed'):
        store.create_conversation_job(project['id'], '开始', True, user_action={
            'kind':'start_proposal','proposal_id':proposal['id'],'proposal_hash':'stale'})
    assert len(store.messages(project['id'])) == before
    accepted = store.create_conversation_job(project['id'], '开始', True, user_action={
        'kind':'start_proposal','proposal_id':proposal['id'],'proposal_hash':proposal['hash']})
    assert json.loads(accepted['user_action_json'])['proposal_id'] == proposal['id']


def test_provider_catalog_follows_durable_phase(store, tmp_path):
    from app.v1.agent import AgentRunSpec
    from app.v1.agent_provider import ProviderTurnRequester
    from app.v1.domain import AgentModelResponse
    project = store.create_project('catalog', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '开始开发')
    seen = []
    def model(messages, tools, **kwargs):
        seen.append({t['function']['name'] for t in tools})
        return AgentModelResponse(text='{}')
    spec = AgentRunSpec(kind='conversation', project=project, instruction='', instruction_revision=1,
        context={}, capability_ids=[], conversation_job_id=job['id'])
    provider = ProviderTurnRequester(store=store, model_complete=model, spec=spec, run_id=ctx.agent_run_id)
    tools = store.registry.schemas(['conversation.turn.resolve','conversation.turn.inspect','project.files.write'],ctx)
    provider.request([], tools, iteration=1, require_tool=False, required_tool_name=None)
    assert seen[-1] == {'conversation.turn.resolve'}
    assert resolve(ctx, job, act='execute', execution_request='begin', scope_summary='demo',
        user_evidence=[{'message_id':job['user_message_id'],'quote':job['message']}]).ok
    provider.request([], tools, iteration=2, require_tool=False, required_tool_name=None)
    assert 'project.files.write' in seen[-1]


@pytest.mark.parametrize('act', ['unresolved','discuss','status','execute'])
def test_natural_reply_only_for_committed_discussion(store, tmp_path, act):
    from app.v1.conversation_report import render_report
    project = store.create_project('reply boundary', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '用户请求')
    if act != 'unresolved':
        fields = {'act':act}
        if act == 'execute':
            fields.update(execution_request='begin', scope_summary='demo', user_evidence=[
                {'message_id':job['user_message_id'],'quote':job['message']}])
        assert resolve(ctx, job, **fields).ok
    if act == 'discuss':
        text, evidence = render_report(store, project['id'],ctx.agent_run_id,'建议先确定接口，再开始开发。')
        assert text.startswith('本轮仅讨论，未新建任务。')
        assert evidence['discussion_prose']
        assert not store.agents.list_requirements(project['id'])
    else:
        with pytest.raises(ValueError):
            render_report(store, project['id'],ctx.agent_run_id,'任务已经完成。')


def test_discussion_draft_tool_available_under_explicit_discuss_ceiling(store, tmp_path):
    project = store.create_project('draft tool', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '只讨论方案', False)
    assert resolve(ctx, job).ok
    state = store.intents.context(job)
    result = store.registry.dispatch('conversation.draft.update', {'based_on_intent_revision':state['revision'],
        'proposal':'待用户接受的最小方案'},ctx)
    assert result.ok, result.error
    assert result.output['execution_hold']
    assert not store.agents.list_requirements(project['id'])


def test_existing_question_patch_preserves_text_flags_and_sources(store, tmp_path):
    project = store.create_project('question patch', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '讨论数据库')
    assert resolve(ctx,job,open_question_updates=[{'key':'storage','question':'用哪种数据库？',
        'blocking_for_start':False,'source_message_ids':[job['user_message_id']]}]).ok
    revision = store.intents.context(job)['revision']
    state = store.intents.update_draft(ctx, {'based_on_intent_revision':revision,
        'open_question_updates':[{'key':'storage','state':'delegated'}]})
    assert state['open_questions'] == [{'key':'storage','question':'用哪种数据库？','blocking_for_start':False,
        'source_message_ids':[job['user_message_id']], 'state':'delegated'}]


def test_reference_only_evidence_can_start_but_new_question_needs_text(store, tmp_path):
    project = store.create_project('source reference', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '开始实施')
    rejected = resolve(ctx,job,open_question_updates=[{'key':'new','state':'resolved'}])
    assert not rejected.ok and 'NewQuestionMissing' in rejected.error['message']
    result = resolve(ctx,job,act='execute',execution_request='begin',scope_summary='demo',
        user_evidence=[{'message_id':job['user_message_id']}])
    assert result.ok, result.error


def test_continued_discussion_keeps_original_hold_source(store, tmp_path):
    project = store.create_project('hold provenance', '', str(tmp_path/'project'))
    first, ctx = turn(store,project,'先不要开发，讨论方案')
    assert resolve(ctx,first).ok
    store.finish_conversation_job(first['id'],None,ctx.agent_run_id,{})
    second, ctx = turn(store,project,'继续讨论')
    assert resolve(ctx,second).ok
    assert store.intents.context(second)['hold_source_id'] == first['user_message_id']


def test_multiple_draft_updates_are_versioned_and_idempotent(store, tmp_path):
    project = store.create_project('draft revisions', '', str(tmp_path/'project'))
    job, ctx = turn(store,project,'只讨论方案')
    assert resolve(ctx,job).ok
    first_args = {'based_on_intent_revision':1,'proposal':'方案一'}
    first = store.intents.update_draft(ctx, first_args)
    second = store.intents.update_draft(ctx, {'based_on_intent_revision':2,'proposal':'完善的方案'})
    assert (first['revision'],second['revision']) == (2,3)
    assert store.intents.update_draft(ctx,first_args) == first
    with pytest.raises(ValueError,match='DraftRevisionConflict'):
        store.intents.update_draft(ctx,{'based_on_intent_revision':1,'proposal':'不同内容'})
    assert store.intents.context(job) == second
    assert second['execution_hold']
    assert not store.agents.list_requirements(project['id'])


def test_legacy_draft_records_and_unique_job_snapshot_schema_migrate(store, tmp_path):
    from app.v1.conversation_intent import DiscussionDraftUpdate
    from app.v1.repositories.conversation_intent_repository import stable
    project = store.create_project('legacy draft', '', str(tmp_path/'project'))
    job, ctx = turn(store,project,'讨论')
    assert resolve(ctx,job).ok
    args = {'based_on_intent_revision':1,'proposal':'旧方案'}
    state = store.intents.context(job)
    state['revision'] = 2
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_work_intents SET revision=2,snapshot_json=? WHERE job_id=?', (stable(state),job['id']))
        db.execute('CREATE UNIQUE INDEX legacy_job_unique ON v1_work_intents(job_id)')
        db.execute('INSERT INTO v1_discussion_draft_updates VALUES(?,?,?)',
            (job['id'],stable(DiscussionDraftUpdate.model_validate(args).model_dump(mode='json')),stable(state)))
    store.intents.init_schema()
    store.intents.init_schema()
    assert store.intents.context(job) == state
    assert store.intents.update_draft(ctx,args) == state
    updated = store.intents.update_draft(ctx,{'based_on_intent_revision':state['revision'],'proposal':'新方案'})
    assert updated['revision'] == state['revision']+1
