from app.v1.context.context_summary import build_document, project_summary
from app.v1.context.context_manager import ContextManager
from app.v1.agents.agent_models import AgentRunSpec
from app.v1.domain import AgentModelResponse


def test_full_document_retains_middle_fact_when_view_omits_it():
    messages = [
        {'role': 'tool', '_source_message_id': index, 'tool_call_id': f'call-{index}',
         'content': f'fact {index} ' + ('MIDDLE_FACT' if index == 4 else 'x' * 700)}
        for index in range(1, 9)
    ]
    document = build_document(messages, run_id='run-1', previous=None,
                              previous_checkpoint_id=None, max_bytes=65_536)
    view = project_summary(document, 'checkpoint-1', 1500)
    assert any('MIDDLE_FACT' in entry.text for entry in document.entries)
    assert view.omitted_entry_count > 0
    assert all(entry.text in [source.text for source in document.entries]
               for entry in view.entries)
    assert view.checkpoint_id == 'checkpoint-1'


def test_maintenance_accepts_only_sourced_structured_entries(store, tmp_path):
    project = store.create_project('summary', '', str(tmp_path / 'project'))
    spec = AgentRunSpec(kind='conversation', project=project, instruction='',
        instruction_revision=1, context={}, capability_ids=[])
    messages = [{'role': 'tool', '_source_message_id': 12, 'agent_run_id': 'original-run',
                 'tool_call_id': 'call-12', 'content': 'The middle fact remains important.'}]
    document = build_document(messages, run_id='current-run', previous=None,
        previous_checkpoint_id=None, max_bytes=65_536)

    def complete(_messages, _tools, **_kwargs):
        return AgentModelResponse(text=(
            '{"entries":[{"entry_id":"draft","kind":"open_question",'
            '"text":"Check the middle fact","source_refs":[{'
            '"agent_run_id":"original-run","message_id":12,'
            '"tool_call_id":"call-12"}]}]}'))
    complete.context_limits = lambda _kind: {'window': 160_000, 'output': 16_000,
                                              'protocol': 'openai', 'model': 'test'}
    manager = ContextManager(store, spec, 'current-run', complete)
    maintained = manager._maintain_document(document, 'checkpoint-1')
    assert maintained.entries[-1].kind == 'open_question'
    assert maintained.entries[-1].source_refs[0].agent_run_id == 'original-run'
    assert maintained.method == 'same_agent_summary'

    def invented(_messages, _tools, **_kwargs):
        return AgentModelResponse(text=(
            '{"entries":[{"entry_id":"draft","kind":"decision",'
            '"text":"Finished","source_refs":[{'
            '"agent_run_id":"invented-run","message_id":999}]}]}'))
    invented.context_limits = complete.context_limits
    rejected = ContextManager(store, spec, 'current-run', invented)._maintain_document(
        document, 'checkpoint-2')
    assert rejected.entries == document.entries
    assert rejected.method == 'deterministic'
