from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.v1.workspace.files import ProjectFiles


def test_work_record_continues_into_new_user_job_without_reviving_old_one(store, tmp_path, monkeypatch):
    project = store.create_project('work continuation', '', str(tmp_path / 'project'))
    first = store.create_conversation_job(project['id'], 'Create a file', True)
    second = store.create_conversation_job(project['id'], 'Continue the unfinished work', True)
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_conversation_jobs SET status='running' WHERE id IN (?,?)",
                   (first['id'], second['id']))
    current = first['id']
    monkeypatch.setattr(store.agents, 'assert_conversation',
                        lambda _ctx: store.get_conversation_job(current))
    files = ProjectFiles(project['base_dir'])
    ctx = SimpleNamespace(project_id=project['id'], files=files,
                          effect_guard=nullcontext, execution_key='first-work',
                          agent_run_id='run-1')
    saved = store.work_progress.update(ctx, {'base_revision': 0, 'snapshot': {
        'objective': 'Produce a report', 'items': [
            {'item_id': 'draft', 'requirement': 'Draft report', 'resource_ids': ['report']},
            {'item_id': 'verify', 'requirement': 'Verify report', 'depends_on': ['draft']}],
        'resources': [{'resource_id': 'report', 'relative_path': 'report.txt'}],
    }})
    assert saved.revision == 1
    assert not saved.resources[0].observation.exists
    claimed_ready = saved.model_copy(deep=True)
    claimed_ready.items[0].agent_status = 'ready'
    assert 'draft' in store.work_progress.current_readiness(claimed_ready)[0]
    current = second['id']
    ctx.execution_key = 'second-work'
    continued = store.work_progress.update(ctx, {'base_revision': 0, 'resume_from': {
        'source_job_id': first['id'], 'source_revision': 1,
        'item_ids': ['verify'], 'mode': 'continue', 'reason': 'User requested continuation'}})
    assert continued.job_id == second['id']
    assert {item.item_id for item in continued.items} == {'draft', 'verify'}
    assert store.work_progress.current_view(project['id'], first['id']).revision == 1
    ctx.execution_key = 'third-work'
    revised = store.work_progress.update(ctx, {'base_revision': 1, 'snapshot': {
        'objective': 'Produce a report', 'items': [
            {'item_id': 'draft', 'requirement': 'Draft report', 'resource_ids': ['report']},
            {'item_id': 'verify', 'requirement': 'Verify report', 'depends_on': ['draft']}],
        'resources': [{'resource_id': 'report', 'relative_path': 'report.txt'}],
    }})
    assert revised.resume_from == continued.resume_from
    replayed = store.work_progress.update(ctx, {'base_revision': 1, 'snapshot': {
        'objective': 'Produce a report', 'items': [
            {'item_id': 'draft', 'requirement': 'Draft report', 'resource_ids': ['report']},
            {'item_id': 'verify', 'requirement': 'Verify report', 'depends_on': ['draft']}],
        'resources': [{'resource_id': 'report', 'relative_path': 'report.txt'}],
    }})
    assert replayed.revision == revised.revision
    with pytest.raises(ValueError, match='payload changed'):
        store.work_progress.update(ctx, {'base_revision': 1, 'snapshot': {
            'objective': 'Different objective', 'items': [
                {'item_id': 'draft', 'requirement': 'Draft report', 'resource_ids': ['report']},
                {'item_id': 'verify', 'requirement': 'Verify report', 'depends_on': ['draft']}],
            'resources': [{'resource_id': 'report', 'relative_path': 'report.txt'}],
        }})
    ctx.execution_key = 'fourth-work'
    with pytest.raises(ValueError, match='cannot silently drop'):
        store.work_progress.update(ctx, {'base_revision': 2, 'snapshot': {
            'objective': 'Produce a report', 'items': [
                {'item_id': 'draft', 'requirement': 'Draft report', 'resource_ids': ['report']}],
            'resources': [{'resource_id': 'report', 'relative_path': 'report.txt'}],
        }})
    ctx.execution_key = 'fifth-work'
    with pytest.raises(ValueError, match='only allowed for the first'):
        store.work_progress.update(ctx, {'base_revision': 2, 'resume_from': {
            'source_job_id': first['id'], 'source_revision': 1,
            'item_ids': ['draft'], 'mode': 'continue', 'reason': 'again'}})


def test_later_step_is_reported_without_becoming_completed_work(store, tmp_path, monkeypatch):
    from pathlib import Path
    from app.v1.protocols.conversation.conversation_work_contracts import ConversationWorkUpdateInput

    project = store.create_project('handoff', '', str(tmp_path / 'project'))
    job = store.create_conversation_job(project['id'], 'Write a note. Plan rollout later.', True)
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_conversation_jobs SET status='running' WHERE id=?", (job['id'],))
    monkeypatch.setattr(store.agents, 'assert_conversation', lambda ctx: job)
    ctx = SimpleNamespace(project_id=project['id'], files=ProjectFiles(project['base_dir']),
                          effect_guard=nullcontext, execution_key='work', agent_run_id='run')
    Path(project['base_dir'], 'note.txt').write_text('note', encoding='utf-8')
    current = {'item_id': 'note', 'requirement': 'Write note', 'agent_status': 'ready', 'resource_ids': ['note']}
    future = {'item_id': 'plan', 'requirement': 'Plan rollout', 'agent_status': 'deferred',
              'deferral_quote': 'Plan rollout later.'}
    snapshot = {'objective': 'Write note', 'items': [current, future],
                'resources': [{'resource_id': 'note', 'relative_path': 'note.txt'}]}
    saved = store.work_progress.update(ctx, {'base_revision': 0, 'snapshot': snapshot})
    assert saved.items[1].agent_status == 'deferred'
    assert store.work_progress.current_readiness(saved) == ([], set())
    Path(project['base_dir'], 'note.txt').unlink()
    assert store.work_progress.current_readiness(saved)[0] == ['note']
    ctx.execution_key = 'revise'
    future['deferral_quote'] = 'Made up request'
    with pytest.raises(ValueError, match='quote the current user request'):
        store.work_progress.update(ctx, {'base_revision': 1, 'snapshot': snapshot})
    current['depends_on'] = ['plan']
    with pytest.raises(ValueError, match='depend on deferred work'):
        ConversationWorkUpdateInput.model_validate({'base_revision': 1, 'snapshot': snapshot})
