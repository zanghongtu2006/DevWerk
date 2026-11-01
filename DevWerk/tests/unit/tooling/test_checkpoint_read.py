import hashlib
import json
from types import SimpleNamespace

from app.v1.context.context_manager import project_result
from app.v1.protocols.context.context_checkpoint_contracts import ContextSummaryDocument, SummaryEntry
from app.v1.tooling.checkpoint_handlers import read_checkpoint


def test_checkpoint_read_pages_full_document_without_losing_cursor(store, tmp_path):
    project = store.create_project('checkpoint read', '', str(tmp_path / 'project'))
    document = ContextSummaryDocument(entries=[SummaryEntry(
        entry_id='middle', kind='observation', text='中间事实' * 2000)])
    with store.tx(immediate=True) as db:
        db.execute('''INSERT INTO v1_context_checkpoints
            (id,project_id,scope_id,agent_run_id,revision,through_message_id,
             summary_json,source_hash,metrics_json,created_at,coverage_json,document_json)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
             ('cp-1', project['id'], 'scope', 'run', 1, 1, '{}', 'hash', '{}',
              '2026-10-30T00:00:00Z', None, document.model_dump_json()))
    ctx = SimpleNamespace(store=store, project_id=project['id'], column_run_id=None)
    first = read_checkpoint({'checkpoint_id': 'cp-1', 'entry_id': 'middle', 'limit': 200}, ctx)
    assert first['content'] and first['next_offset'] == 200
    second = read_checkpoint({'checkpoint_id': 'cp-1', 'entry_id': 'middle',
                              'offset': first['next_offset'], 'limit': 200}, ctx)
    assert second['offset'] == first['next_offset']
    assert second['sha256'] == first['sha256']
    assert hashlib.sha256((first['content'] + second['content']).encode()).hexdigest() != first['sha256']
    projected = project_result(json.dumps({'capability': 'agent.checkpoint.read', 'output': first}), 600)
    assert 'checkpoint_id' in projected
