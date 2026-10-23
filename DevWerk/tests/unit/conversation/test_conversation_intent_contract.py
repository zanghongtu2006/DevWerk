import json
from dataclasses import replace

import pytest

from app.v1.agent import AgentCore, AgentRunSpec
from app.v1.capabilities import CapabilityContext
from app.v1.conversations.conversation import _has_durable_governance_progress
from app.v1.conversations.conversation_failure import failure_summary
from app.v1.domain import AgentModelResponse, AgentToolCall
from app.v1.execution.runtime import WorkflowRuntime
from tests.helpers import create_planned_task, publish_planned_workflow, sequence_workflow
from tests.unit.agent.test_p0_assignment_regressions import begin_agent

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


def test_narrow_execution_authorization_binds_current_message_and_releases_hold(store, tmp_path):
    project = store.create_project('narrow authorization', '', str(tmp_path/'project'))
    discussion, ctx = turn(store, project, '先只讨论')
    assert resolve(ctx, discussion).ok
    store.finish_conversation_job(discussion['id'], None, ctx.agent_run_id, {})
    start, ctx = turn(store, project, '现在开始实现登录系统')
    authorized = store.registry.dispatch('conversation.execution.authorize',
        {'request':'begin','scope_summary':'登录系统'}, ctx)
    assert authorized.ok, authorized.error
    assert authorized.output['turn_contract']['phase'] == 'execution'
    assert not authorized.output['work_intent']['execution_hold']
    assert authorized.output['turn_contract']['source_message_id'] == start['user_message_id']
    assert store.registry.dispatch('project.files.write',
        {'path':'proof.txt','content':'authorized'}, ctx).ok


def test_committed_discussion_cannot_be_upgraded_by_execution_replay(store, tmp_path):
    project = store.create_project('discussion replay', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '先只讨论')
    assert resolve(ctx, job).ok
    denied = store.registry.dispatch('conversation.execution.authorize',
        {'request':'begin','scope_summary':'unapproved work'},
        replace(ctx, execution_key='discussion-upgrade'))
    assert not denied.ok and 'TurnAlreadyResolved' in str(denied.error)
    assert store.agents.list_requirements(project['id']) == []


def test_narrow_authorization_respects_discussion_only_transport_and_task_scope(store, tmp_path):
    from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow

    project = store.create_project('narrow denial', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '只讨论', False)
    denied = store.registry.dispatch('conversation.execution.authorize',
        {'request':'begin','scope_summary':'demo'}, ctx)
    assert not denied.ok
    assert store.agents.list_requirements(project['id']) == []
    store.finish_conversation_job(job['id'], None, ctx.agent_run_id, {})

    publish_planned_workflow(store, project['id'], agent_workflow())
    task = create_planned_task(store, project['id'], 'task control')
    control, ctx = turn(store, project, f'暂停 Task {task["id"]}')
    authorized = store.registry.dispatch('conversation.task.control.authorize',
        {'action':'task.pause','task_id':task['id']}, ctx)
    assert authorized.ok, authorized.error
    assert authorized.output['turn_contract']['source_message_id'] == control['user_message_id']
    assert store.registry.dispatch('task.pause', {'task_id':task['id']}, ctx).ok


def test_task_pause_requires_matching_authorized_turn(store, tmp_path):
    from app.v1.capabilities import _task_pause
    from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow

    project = store.create_project('pause contract','',str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],agent_workflow())
    task = create_planned_task(store,project['id'],'keep running')
    unrelated, ctx = turn(store,project,'只通知 Worker 返修，不暂停 Task')
    inconsistent = resolve(ctx,unrelated,act='execute',execution_request='continue',
        control_capability='task.pause',control_task_id=task['id'])
    assert not inconsistent.ok
    assert 'control' in inconsistent.error['message']
    with pytest.raises(PermissionError, match='TaskPauseRequiresAuthorization'):
        _task_pause({'task_id':task['id']},ctx)
    assert store.get_task(task['id'])['control_state'] == 'active'
    store.finish_conversation_job(unrelated['id'],None,ctx.agent_run_id,{})

    pause_turn, pause_ctx = turn(store,project,f'请暂停 Task {task["id"]}')
    resolved = resolve(pause_ctx,pause_turn,act='control',
        control_capability='task.pause',control_task_id=task['id'])
    assert resolved.ok, resolved.error
    paused = store.registry.dispatch('task.pause',{'task_id':task['id']},pause_ctx)
    assert paused.ok, paused.error
    assert store.get_task(task['id'])['control_state'] == 'paused'


def test_user_authorized_requirement_turn_can_pause_its_task(store, tmp_path):
    from tests.helpers import agent_workflow, publish_planned_workflow

    project = store.create_project('workflow change', '', str(tmp_path / 'project'))
    first, ctx = turn(store, project, 'Start the work')
    authorized = store.registry.dispatch('conversation.execution.authorize',
        {'request': 'begin', 'scope_summary': 'Start the work'}, ctx)
    assert authorized.ok, authorized.error
    requirement = authorized.output['requirement']
    _, workflow = publish_planned_workflow(store, project['id'], agent_workflow())
    store.scopes.bind_workflow(workflow['id'], None, requirement['id'], requirement['revision'])
    task = create_planned_task(store, project['id'], 'work')
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})

    change, ctx = turn(store, project, 'Change this Workflow and pause its Task')
    continued = store.registry.dispatch('conversation.execution.authorize',
        {'request': 'continue', 'requirement_id': requirement['id']}, ctx)
    assert continued.ok, continued.error
    ctx = replace(ctx, requirement_id=requirement['id'],
                  requirement_revision=requirement['revision'])
    paused = store.registry.dispatch('task.pause', {'task_id': task['id']}, ctx)
    assert paused.ok, paused.error
    assert paused.output['control_state'] == 'paused'
    with pytest.raises(PermissionError, match='TaskPauseRequiresAuthorization'):
        from app.v1.capabilities import _task_pause
        _task_pause({'task_id': task['id']}, replace(ctx, requirement_id='another'))


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
    from app.v1.conversations.conversation_intent import TurnResolution
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


def test_provider_catalog_stays_stable_across_durable_phase(store, tmp_path):
    from app.v1.agent import AgentRunSpec
    from app.v1.agents.agent_provider import ProviderTurnRequester
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
    assert seen[-1] == {'conversation.turn.resolve', 'conversation.turn.inspect', 'project.files.write'}
    assert resolve(ctx, job, act='execute', execution_request='begin', scope_summary='demo',
        user_evidence=[{'message_id':job['user_message_id'],'quote':job['message']}]).ok
    provider.request([], tools, iteration=2, require_tool=False, required_tool_name=None)
    assert seen[-1] == seen[0]


def test_status_turn_exposes_reads_and_reply_after_resolution(store, tmp_path):
    from app.v1.agent import AgentRunSpec
    from app.v1.agents.agent_provider import ProviderTurnRequester
    from app.v1.domain import AgentModelResponse
    project = store.create_project('status catalog', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '为什么失败？')
    assert resolve(ctx, job, act='status').ok
    seen = []
    def model(messages, tools, **kwargs):
        seen.append({tool['function']['name'] for tool in tools})
        return AgentModelResponse(text='状态说明')
    spec = AgentRunSpec(kind='conversation', project=project, instruction='', instruction_revision=1,
        context={}, capability_ids=[], conversation_job_id=job['id'])
    provider = ProviderTurnRequester(store=store, model_complete=model, spec=spec, run_id=ctx.agent_run_id)
    tools = store.registry.schemas([
        'conversation.turn.resolve', 'conversation.reply', 'workflow.inspect', 'project.files.write'], ctx)
    provider.request([], tools, iteration=1, require_tool=False, required_tool_name=None)
    assert seen == [{'conversation.turn.resolve', 'conversation.reply', 'workflow.inspect', 'project.files.write'}]


@pytest.mark.parametrize('act', ['unresolved','discuss','status','execute'])
def test_natural_reply_for_non_execution_turns(store, tmp_path, act):
    from app.v1.conversations.conversation_report import render_report
    project = store.create_project('reply boundary', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '用户请求')
    if act != 'unresolved':
        fields = {'act':act}
        if act == 'execute':
            fields.update(execution_request='begin', scope_summary='demo', user_evidence=[
                {'message_id':job['user_message_id'],'quote':job['message']}])
        assert resolve(ctx, job, **fields).ok
    if act in {'unresolved', 'discuss', 'status'}:
        text, evidence = render_report(store, project['id'],ctx.agent_run_id,'建议先确定接口，再开始开发。')
        assert text == '建议先确定接口，再开始开发。'
        assert evidence['discussion_prose']
        assert not store.agents.list_requirements(project['id'])
        if act == 'unresolved':
            assert store.intents.contract(store.get_conversation_job(job['id']))['phase'] == 'discussion'
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


def test_resolving_decided_topic_without_prior_question_is_idempotent(store, tmp_path):
    project = store.create_project('decided topic', '', str(tmp_path/'project'))
    first, ctx = turn(store, project, '成功后清零计数')
    message_id = first['user_message_id']
    result = resolve(ctx, first, decision_updates=[{'key':'count-reset', 'value':'成功后清零',
        'status':'user_decided', 'source_message_ids':[message_id]}],
        open_question_updates=[{'key':'count-reset', 'state':'resolved', 'source_message_ids':[message_id]}])
    assert result.ok, result.error
    assert result.output['work_intent']['open_questions'] == []
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})
    second, ctx = turn(store, project, '继续确认计数重置')
    result = resolve(ctx, second, open_question_updates=[{'key':'count-reset', 'state':'resolved',
        'source_message_ids':[second['user_message_id']]}])
    assert result.ok, result.error
    assert result.output['work_intent']['open_questions'] == []


def test_resolving_undecided_topic_without_question_still_rejected(store, tmp_path):
    project = store.create_project('undecided topic', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '讨论计数重置')
    result = resolve(ctx, job, decision_updates=[{'key':'count-reset', 'value':'建议清零',
        'status':'agent_proposed'}], open_question_updates=[{'key':'count-reset', 'state':'resolved'}])
    assert not result.ok and 'NewQuestionMissing' in result.error['message']


def test_agent_question_note_does_not_override_later_user_execution_request(store, tmp_path):
    project = store.create_project('question is advisory', '', str(tmp_path/'project'))
    first, ctx = turn(store, project, '先讨论部署选择')
    assert resolve(ctx, first, open_question_updates=[{'key':'deployment',
        'question':'部署到哪里？', 'blocking_for_start':True,
        'source_message_ids':[first['user_message_id']]}]).ok
    store.finish_conversation_job(first['id'], None, ctx.agent_run_id, {})
    second, ctx = turn(store, project, '现在开始实现，部署形式由你决定')
    result = resolve(ctx, second, act='execute', execution_request='begin',
        constraint_change='release_hold', scope_summary='可运行演示项目',
        user_evidence=[{'message_id':second['user_message_id']}])
    assert result.ok, result.error
    assert result.output['turn_contract']['phase'] == 'execution'
    assert result.output['work_intent']['open_questions'][0]['state'] == 'open'


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
    from app.v1.conversations.conversation_intent import DiscussionDraftUpdate
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


# ---------------------------------------------------------------------------
# Partial-effect recovery
# ---------------------------------------------------------------------------

def test_mixed_resolution_and_mutation_batch_has_no_business_effects(store, tmp_path):
    project = store.create_project('mixed batch', '', str(tmp_path/'project'))
    queued = store.create_conversation_job(project['id'], '开始写文件', True)
    job = store.claim_conversation_job(queued['id'], 'test')
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
                    'source_message_id':job['user_message_id'],'based_on_intent_revision':0,
                    'act':'execute','execution_request':'begin','scope_summary':'write file',
                    'user_evidence':[{'message_id':job['user_message_id'],'quote':job['message']}]}),
                AgentToolCall(id='write',name='project.files.write',arguments={'path':'bad.txt','content':'bad'}),
            ])
        return AgentModelResponse(text=json.dumps({'mode':'discussion','message':'需要单独提交回合边界。'}))
    result = AgentCore(store, store.registry, model).run(AgentRunSpec(kind='conversation',project=project,
        instruction='',instruction_revision=1,context={},conversation_job_id=job['id'],
        agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve','project.files.write'],require_conversation_report=True))
    invocations = store.tool_invocations(project['id'],result.agent_run_id,hydrate_payloads=True)
    assert len(invocations) == 2 and not any(i['ok'] for i in invocations)
    assert all(i['result']['error']['type'] == 'ConversationTurnProtocolError' for i in invocations)
    assert not (tmp_path/'project'/'bad.txt').exists()
    assert not store.agents.list_requirements(project['id'])


def test_plain_discussion_cannot_hide_successful_effect_receipt(store, tmp_path):
    from app.v1.conversations.conversation_report import render_report
    project = store.create_project('legacy recovery receipt', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '讨论')
    assert resolve(ctx, job).ok
    store.record_tool_invocation(agent_run_id=ctx.agent_run_id,tool_call_id='legacy-write',
        capability='project.files.write',arguments={'path':'legacy.txt','content':'x'},
        result={'ok':True,'capability':'project.files.write','output':{}},ok=True)
    with pytest.raises(ValueError):
        render_report(store,project['id'],ctx.agent_run_id,'只是讨论，不需要检查执行结果。')


def test_partial_failure_reports_saved_work_and_actual_started_count(store, tmp_path):
    project = store.create_project('partial effects', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],sequence_workflow())
    task = create_planned_task(store,project['id'],'first')
    WorkflowRuntime(store,store.registry,'test-worker').step(task['id'])
    def entry(capability,ok=True,output=None,error=None):
        return {'capability':capability,'ok':ok,'facts':{'result':{'output':output,'error':{'message':error}}}}
    ledger = [entry('loop.apply'),entry('task.plan.save'),
        entry('task.create',output={'materialization':{'created_task_ids':[task['id']],'reused_task_ids':[]}}),
        entry('task.create',output={'materialization':{'created_task_ids':[],'reused_task_ids':[task['id']]}}),
        entry('task.plan.save',False,error='ExactInputTargetType: /requirements_confirmed is boolean')]
    reply = failure_summary(store,project['id'],ledger,'ConversationProtocolStalled')
    assert '新建 1 个任务，其中 1 个已启动' in reply
    assert '工作流已保存' in reply and '任务计划已保存' in reply
    assert 'ExactInputTargetType' in reply and '重复失败调用已停止' in reply
    assert '复用 1' not in reply


def test_failed_final_reply_reports_queued_worker_input_as_partial_effect(store, tmp_path):
    project = store.create_project('worker input receipt', '', str(tmp_path/'project'))
    ledger = [
        {'capability': 'agent.worker.send', 'ok': True, 'status': 'completed',
         'effect_kind': 'control', 'facts': {'result': {'output': {'id': 'winput_1'}}}},
        {'capability': 'conversation.reply', 'ok': False, 'status': 'failed',
         'effect_kind': 'session_control', 'facts': {'result': {'error': {
             'message': 'The report references a tool call absent from this turn'}}}},
    ]
    assert _has_durable_governance_progress(ledger)
    reply = failure_summary(store, project['id'], ledger, 'ConversationProtocolStalled')
    assert '已向 Worker 排队 1 条输入' in reply
    assert '是否消费需另查' in reply
    assert 'tool call absent' in reply


def test_validation_failure_summary_keeps_paths_and_business_reason_without_debug_payload(store, tmp_path):
    project = store.create_project('validation failure', '', str(tmp_path/'project'))
    detail = ('2 validation errors for WorkflowDefinition\n'
              'columns.6.acceptance_checks.0\n'
              "  Value error, A scenario report requires a behavioral command [type=value_error, input_value={'arguments': {'secret': 'not-for-ui'}}, input_type=dict]\n"
              '    For further information visit https://errors.pydantic.dev/2.13/v/value_error\n'
              'columns.6.acceptance_checks.1\n'
              '  Value error, A scenario report requires a behavioral command [type=value_error, input_value={}, input_type=dict]')
    ledger = [{'capability': 'workflow.acceptance.configure', 'ok': False,
               'facts': {'result': {'error': {'message': detail}}}}]
    reply = failure_summary(store, project['id'], ledger, 'ConversationProtocolStalled')
    assert 'columns.6.acceptance_checks.0: A scenario report requires a behavioral command' in reply
    assert 'columns.6.acceptance_checks.1' in reply
    assert 'not-for-ui' not in reply and 'pydantic.dev' not in reply


def test_contract_failure_summary_omits_serialized_plan_arguments(store, tmp_path):
    project = store.create_project('contract failure', '', str(tmp_path/'project'))
    detail = "task.plan.save input rejected value at plan: '{\"secret\": \"not-for-ui\"}'"
    ledger = [{'capability': 'task.plan.save', 'ok': False,
               'facts': {'result': {'error': {'message': detail}}}}]
    reply = failure_summary(store, project['id'], ledger, 'ConversationProtocolStalled')
    assert 'task.plan.save input rejected value at plan' in reply
    assert 'JSON 字符串' in reply
    assert 'not-for-ui' not in reply


# ---------------------------------------------------------------------------
# Conversation reply tool
# ---------------------------------------------------------------------------

def test_reply_tool_finishes_same_run_with_real_task_state(store, tmp_path):
    project = store.create_project('reply tool', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],sequence_workflow())
    task = create_planned_task(store,project['id'],'Actual task')
    queued = store.create_conversation_job(project['id'],'当前进度？',True)
    job = store.claim_conversation_job(queued['id'],'test')
    calls = 0
    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert kwargs['required_tool_name'] is None
            return AgentModelResponse(tool_calls=[AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
                'source_message_id':job['user_message_id'],'based_on_intent_revision':0,'act':'status'})])
        return AgentModelResponse(tool_calls=[AgentToolCall(id='reply',name='conversation.reply',arguments={
            'mode':'work_result','task_ids':[task['id']],
            'message':'I will review the result after the queued Task runs.'})])
    result = AgentCore(store,store.registry,model).run(AgentRunSpec(kind='conversation',project=project,
        instruction='',instruction_revision=1,context={},conversation_job_id=job['id'],
        agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve'],require_conversation_report=True))
    assert 'Actual task：待执行' in result.text
    assert 'Agent 说明（未作为完成依据）' in result.text
    assert 'Actual task：待执行' in result.text.splitlines()[0]
    assert 'review the result' in result.text
    assert calls == 2
    assert len(store.agent_runs(project_id=project['id'])) == 1
    assert result.completion['conversation_report']['tasks'][0]['id'] == task['id']


def test_persistent_session_preserves_prior_tool_trace_across_resolution(store, tmp_path):
    from app.v1.agents.agent_provider import ProviderTurnRequester
    project = store.create_project('phase context', '', str(tmp_path/'project'))
    job, ctx = turn(store,project,'开始实施')
    payload = {'authoritative_current_request':{'message_id':job['user_message_id'],'content':job['message']},
        'authoritative_project_state':{}}
    messages = [{'role':'system','content':'same identity'},
        {'role':'tool','tool_call_id':'old','content':'OLD_TOOL_TRACE'},
        {'role':'user','content':json.dumps(payload)}]
    captured = []
    def model(messages, tools, **kwargs):
        captured.append(messages)
        return AgentModelResponse(text='{}')
    spec = AgentRunSpec(kind='conversation',project=project,instruction='',instruction_revision=1,
        context={},capability_ids=[],conversation_job_id=job['id'])
    provider = ProviderTurnRequester(store=store,model_complete=model,spec=spec,run_id=ctx.agent_run_id)
    tools = store.registry.schemas(['conversation.turn.resolve','project.files.read'],ctx)
    provider.request(messages,tools,iteration=1,require_tool=False,required_tool_name=None)
    assert 'OLD_TOOL_TRACE' in str(captured[-1])
    assert captured[-1][0] == messages[0]
    assert resolve(ctx,job,act='execute',execution_request='begin',scope_summary='demo',
        user_evidence=[{'message_id':job['user_message_id']}]).ok
    provider.request(messages,tools,iteration=2,require_tool=False,required_tool_name=None)
    assert 'OLD_TOOL_TRACE' in str(captured[-1])


def test_reply_cannot_cite_invented_task(store, tmp_path):
    project = store.create_project('reply provenance', '', str(tmp_path/'project'))
    job, ctx = turn(store,project,'进度？')
    assert resolve(ctx,job,act='status').ok
    reply = store.registry.dispatch('conversation.reply',{'mode':'work_result','task_ids':['tsk_invented']},ctx)
    assert not reply.ok
    assert not store.list_tasks(project['id'])


def test_execution_reply_cannot_use_old_task_status_without_current_effect(store, tmp_path):
    import json

    from app.v1.conversations.conversation_report import render_report

    project = store.create_project('execution report admission', '', str(tmp_path/'project'))
    first, first_ctx = turn(store, project, '开始交付')
    assert resolve(first_ctx, first, act='execute', execution_request='begin',
                   scope_summary='demo', user_evidence=[{
                       'message_id': first['user_message_id'], 'quote': first['message']}]).ok
    store.finish_conversation_job(first['id'], None, first_ctx.agent_run_id, {})
    publish_planned_workflow(store, project['id'], sequence_workflow())
    task = create_planned_task(store, project['id'], 'Existing task')

    job, ctx = turn(store, project, '继续执行当前交付')
    assert resolve(ctx, job, act='execute', execution_request='continue',
                   user_evidence=[{'message_id': job['user_message_id'], 'quote': job['message']}]).ok
    with pytest.raises(ValueError, match='successful write, process or control receipt'):
        render_report(store, project['id'], ctx.agent_run_id,
                      json.dumps({'mode': 'work_result', 'task_ids': [task['id']]}))

    written = store.registry.dispatch('project.files.write',
        {'path': 'current-work.txt', 'content': 'real effect'}, ctx)
    assert written.ok, written.error
    store.record_tool_invocation(agent_run_id=ctx.agent_run_id, tool_call_id='current-write',
        capability='project.files.write', arguments={'path': 'current-work.txt'},
        result=written.model_dump(mode='json'), ok=True)
    rendered, _ = render_report(store, project['id'], ctx.agent_run_id,
        json.dumps({'mode': 'work_result', 'task_ids': [task['id']],
                    'tool_call_ids': ['current-write']}))
    assert '项目文件已写入' in rendered


def test_execution_reply_cannot_present_old_done_task_as_new_work(store, tmp_path):
    from app.v1.conversations.conversation_report import render_report

    project = store.create_project('terminal provenance', '', str(tmp_path/'project'))
    first, first_ctx = turn(store, project, '开始交付')
    assert resolve(first_ctx, first, act='execute', execution_request='begin',
                   scope_summary='original', user_evidence=[{'message_id': first['user_message_id']}]).ok
    store.finish_conversation_job(first['id'], None, first_ctx.agent_run_id, {})
    publish_planned_workflow(store, project['id'], sequence_workflow())
    task = create_planned_task(store, project['id'], 'Old delivery')
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_tasks SET status='done' WHERE id=?", (task['id'],))

    job, ctx = turn(store, project, '为已交付项目建立修复任务')
    assert resolve(ctx, job, act='execute', execution_request='begin', focus='new_scope',
                   scope_summary='separate repair', user_evidence=[{'message_id': job['user_message_id']}]).ok
    store.record_tool_invocation(agent_run_id=ctx.agent_run_id, tool_call_id='scope-created',
        capability='requirement.create', arguments={}, result={'ok': True, 'output': {'id': 'new-scope'}}, ok=True)
    with pytest.raises(ValueError, match='HistoricalTerminalTaskNotCurrentWork'):
        render_report(store, project['id'], ctx.agent_run_id,
            json.dumps({'mode': 'work_result', 'task_ids': [task['id']]}))

    rendered, _ = render_report(store, project['id'], ctx.agent_run_id,
        json.dumps({'mode': 'work_result', 'tool_call_ids': ['scope-created']}))
    assert '需求已建立' in rendered and 'Old delivery' not in rendered


def test_execution_reply_reports_partial_workflow_configuration_as_incomplete(store, tmp_path):
    from app.v1.conversations.conversation_report import render_report

    project = store.create_project('partial workflow reply', '', str(tmp_path/'project'))
    job, ctx = turn(store, project, '配置工作流验收')
    assert resolve(ctx, job, act='execute', execution_request='begin', scope_summary='demo',
                   user_evidence=[{'message_id': job['user_message_id']}]).ok
    store.record_tool_invocation(agent_run_id=ctx.agent_run_id, tool_call_id='partial-check',
        capability='workflow.acceptance.configure', arguments={}, ok=True,
        result={'ok': True, 'capability': 'workflow.acceptance.configure',
                'output': {'ready_for_task_plan': False, 'remaining_diagnostic_count': 4}})
    rendered, _ = render_report(store, project['id'], ctx.agent_run_id,
        json.dumps({'mode': 'work_result', 'tool_call_ids': ['partial-check']}))
    assert 'Task 计划准入仍缺 4 项' in rendered


def test_reply_and_mutation_batch_is_rejected_before_any_effect(store, tmp_path):
    project = store.create_project('reply batch', '', str(tmp_path/'project'))
    queued = store.create_conversation_job(project['id'], '开始写文件', True)
    job = store.claim_conversation_job(queued['id'], 'test')
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id='resolve', name='conversation.turn.resolve', arguments={
                'source_message_id':job['user_message_id'],'based_on_intent_revision':0,
                'act':'execute','execution_request':'begin','scope_summary':'write file',
                'user_evidence':[{'message_id':job['user_message_id']}]} )])
        if calls == 2:
            return AgentModelResponse(tool_calls=[
                AgentToolCall(id='reply',name='conversation.reply',arguments={'mode':'blocked','message':'Finish'}),
                AgentToolCall(id='write',name='project.files.write',arguments={'path':'bad.txt','content':'bad'})])
        return AgentModelResponse(tool_calls=[AgentToolCall(id='finish',name='conversation.reply',arguments={
            'mode':'blocked','tool_call_ids':['write']})])
    result = AgentCore(store,store.registry,model).run(AgentRunSpec(kind='conversation',project=project,
        instruction='',instruction_revision=1,context={},conversation_job_id=job['id'],
        agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve','project.files.write'],require_conversation_report=True))
    receipts = store.tool_invocations(project['id'],result.agent_run_id,hydrate_payloads=True)
    mixed = [i for i in receipts if i['tool_call_id'] in {'reply','write'}]
    assert len(mixed) == 2 and not any(i['ok'] for i in mixed)
    assert all(i['result']['error']['type'] == 'ConversationTurnProtocolError' for i in mixed)
    assert not (tmp_path/'project'/'bad.txt').exists()
    assert result.completion['conversation_report']['report']['mode'] == 'blocked'


def test_reply_receipt_recovery_renders_current_task_without_column_completion(store, tmp_path, monkeypatch):
    project = store.create_project('reply recovery', '', str(tmp_path/'project'))
    publish_planned_workflow(store,project['id'],sequence_workflow())
    task = create_planned_task(store,project['id'],'Recoverable report')
    queued = store.create_conversation_job(project['id'],'进度？',True)
    job = store.claim_conversation_job(queued['id'],'test')
    responses = iter([
        AgentModelResponse(tool_calls=[AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
            'source_message_id':job['user_message_id'],'based_on_intent_revision':0,'act':'status'})]),
        AgentModelResponse(tool_calls=[AgentToolCall(id='reply',name='conversation.reply',arguments={
            'mode':'work_result','task_ids':[task['id']]})]),
    ])
    spec = AgentRunSpec(kind='conversation',project=project,instruction='',instruction_revision=1,context={},
        conversation_job_id=job['id'],agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve'],require_conversation_report=True)
    finish = store.finish_agent_run
    crashed = False
    def interrupt_finish(run_id, status, *args, **kwargs):
        nonlocal crashed
        if status == 'succeeded' and not crashed:
            crashed = True
            raise RuntimeError('injected interruption after reply receipt')
        return finish(run_id,status,*args,**kwargs)
    monkeypatch.setattr(store,'finish_agent_run',interrupt_finish)
    core = AgentCore(store,store.registry,lambda *a,**kw: next(responses))
    with pytest.raises(RuntimeError, match='injected interruption'):
        core.run(spec)
    store.pause_task(task['id'])
    recovered = core.run(spec)
    assert recovered.status == 'succeeded', recovered.error
    assert 'Recoverable report：已暂停' in recovered.text
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v1_agent_operations WHERE capability='conversation.reply'").fetchone()[0] == 1


def test_empty_discussion_response_recovers_with_reply_tool(store, tmp_path):
    project = store.create_project('empty reply', '', str(tmp_path/'project'))
    queued = store.create_conversation_job(project['id'],'只讨论',False)
    job = store.claim_conversation_job(queued['id'],'test')
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentModelResponse(tool_calls=[AgentToolCall(id='resolve',name='conversation.turn.resolve',arguments={
                'source_message_id':job['user_message_id'],'based_on_intent_revision':0,'act':'discuss'})])
        if calls == 2:
            return AgentModelResponse()
        assert kwargs['required_tool_name'] == 'conversation.reply'
        return AgentModelResponse(tool_calls=[AgentToolCall(id='reply',name='conversation.reply',arguments={
            'mode':'discussion','message':'先讨论需求与验收。'})])
    spec = AgentRunSpec(kind='conversation',project=project,instruction='',instruction_revision=1,context={},
        conversation_job_id=job['id'],agent_instance_id=store.conversation_agent(project['id'])['logical_id'],
        capability_ids=['conversation.turn.resolve'],require_conversation_report=True)
    result = AgentCore(store,store.registry,model).run(spec)
    assert result.status == 'succeeded', result.error
    assert result.text == '先讨论需求与验收。'
    assert calls == 3
    assert not store.agents.list_requirements(project['id'])


def test_persistent_empty_response_stops_after_three_attempts(store, tmp_path):
    project = store.create_project('bounded empty', '', str(tmp_path/'project'))
    calls = 0
    def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        return AgentModelResponse()
    with pytest.raises(RuntimeError, match='neither tools nor final text'):
        AgentCore(store,store.registry,model).run(AgentRunSpec(kind='conversation',project=project,
            instruction='',instruction_revision=1,context={},capability_ids=[],require_conversation_report=True))
    assert calls == 3
