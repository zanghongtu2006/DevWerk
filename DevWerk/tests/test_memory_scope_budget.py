from __future__ import annotations

import json

import pytest

from app.services.provider_errors import LLMProviderError, ProviderErrorDetails
from app.v1.agent_models import AgentRunSpec
from app.v1.agent_provider import ProviderTurnRequester
from app.v1.capabilities import CapabilityContext, build_core_registry
from app.v1.context_manager import ContextExhausted, ContextManager, project_result
from app.v1.domain import AgentModelResponse, ContextSelection, MemorySelector
from app.v1.memory import FileMemoryStore, MemoryScopeContext
from app.v1.runtime import WorkflowRuntime
from app.v1.store import V1Store
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow

class WindowModel:
    def __init__(self, window=16_000, output=2_000):
        self.window, self.output = window, output

    def context_limits(self, _kind):
        return {'window': self.window, 'output': self.output,
                'protocol': 'test', 'model': 'bounded-memory-test'}


def large_memory(store, project, size=230_000):
    store.memory_append(project['id'], 'CONSTRAINTS.md', 'important constraint\n' + '甲乙丙丁' * (size//4),
                        source_type='test')
    return store.memory.build_context(project,
        max_bytes=store.policy.context.memory_view_tokens*2,
        record_bytes=store.policy.context.memory_record_tokens*2)


def manager(store, project, kind='conversation', window=16_000):
    spec = AgentRunSpec(kind=kind, project=project, instruction='work',
        instruction_revision=1, context={}, capability_ids=[])
    return ContextManager(store, spec, 'test-run', WindowModel(window))


@pytest.mark.parametrize('kind', ['conversation', 'column'])
def test_large_core_memory_is_bounded_in_actual_provider_frame(store, tmp_path, kind):
    project = store.create_project('large core', '', str(tmp_path/'project'))
    memory = large_memory(store, project)
    assert len(json.dumps(memory, ensure_ascii=False,separators=(',',':')).encode('utf-8')) <= 16_000
    source = next(record for record in memory['records']
                  if record['reference'] == 'CONSTRAINTS.md')
    assert source['content_complete'] is False and source['next_offset']
    assert source['sha256'] == store.memory_read(project['id'],'CONSTRAINTS.md')['sha256']
    payload = ({'authoritative_project_state':{'memory':memory},
                'authoritative_current_request':{'content':'Continue'}} if kind == 'conversation'
               else {'current_assignment':{'input':{'input':{'memory':memory},
                    'execution_evidence':{'entries':[{'id':str(n)} for n in range(20)]}}}})
    messages = [{'role':'system','content':'protocol'},
                {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
    context = manager(store, project, kind)
    view = context.prepare(messages, [{'type':'function','function':{'name':'project.memory.read'}}])
    assert context.estimate(view, []) < context.hard
    assert '甲乙丙丁' * 5000 not in json.dumps(view,ensure_ascii=False)
    projected = json.loads(view[-1]['content'])
    selected = (projected['authoritative_project_state']['memory'] if kind == 'conversation'
                else projected['current_assignment']['input']['input']['memory'])
    assert selected['records'][3]['reference'] == 'CONSTRAINTS.md'
    assert selected['records'][3]['sha256'] == source['sha256']


def test_memory_page_reassembles_chinese_after_tool_projection(store, tmp_path):
    project = store.create_project('paging','',str(tmp_path/'project'))
    written = store.memory_write(project['id'], {'kind':'fact','scope':'project',
        'content':'中文片段和约束' * 1700})
    original = store.memory_read(project['id'],written['reference'])
    ctx = CapabilityContext(project_id=project['id'],project=project,store=store)
    offset, pieces, sha = 0, [], original['sha256']
    while True:
        args = {'reference':written['reference'],'offset':offset,
                'max_characters':4000,'expected_sha256':sha}
        result = store.registry.dispatch('project.memory.read', args, ctx)
        assert result.ok, result.error
        projected = project_result(json.dumps(result.model_dump(mode='json'),ensure_ascii=False), 512)
        assert len(projected.encode('utf-8')) <= 512
        page = json.loads(projected)['output']
        assert page['sha256'] == sha and page['offset'] == offset
        pieces.append(page['content'])
        if page['next_offset'] is None:
            break
        assert page['next_offset'] > offset
        assert page['next_offset'] == offset+len(page['content'])
        offset = page['next_offset']
    assert ''.join(pieces) == original['content']
    store.memory_append(project['id'],written['reference'],'new revision',source_type='test')
    changed = store.registry.dispatch('project.memory.read',
        {'reference':written['reference'],'offset':offset,'expected_sha256':sha},ctx)
    assert changed.ok and changed.output['source_changed'] is True
    assert changed.output['sha256'] != sha


def test_required_user_input_overflow_reports_components(store, tmp_path):
    project = store.create_project('overflow','',str(tmp_path/'project'))
    context = manager(store,project,window=4096)
    messages = [{'role':'system','content':'protocol'},
                {'role':'user','content':'用户确认的必要约束' * 6000}]
    with pytest.raises(ContextExhausted, match='required_context_overflow.*user_bytes'):
        context.prepare(messages,[])


def test_memory_reprojects_against_tools_and_calibrated_request_budget(store, tmp_path):
    project = store.create_project('mixed','',str(tmp_path/'project'))
    memory = large_memory(store,project)
    payload = {'current_assignment':{'input':{'input':{
        'memory':memory, 'reference_artifacts':[{'content':'artifact facts ' * 180}],
        'execution_evidence':{'entries':[{'status':'failed','id':str(n)} for n in range(30)]}}}}}
    messages = [{'role':'system','content':'protocol'},
                {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
    tools = [{'type':'function','function':{'name':'project.memory.read',
              'description':'read memory source','parameters':{'type':'object'}}}]
    context = manager(store,project,kind='column')
    first = context.prepare(messages,tools)
    first_record = json.loads(first[-1]['content'])['current_assignment']['input']['input']['memory']['records'][3]
    context.ratio = 1.2
    second = context.prepare(messages,tools)
    second_record = json.loads(second[-1]['content'])['current_assignment']['input']['input']['memory']['records'][3]
    assert len(second_record['content']) <= len(first_record['content'])
    assert context.estimate(second,tools) <= context.hard
    assert second_record['sha256'] == first_record['sha256']


def test_read_page_uses_replaceable_memory_store_interface(tmp_path):
    class AlternateFileStore(FileMemoryStore):
        name = 'alternate'

    store = V1Store(str(tmp_path/'alternate.db'),registry=build_core_registry(),
                    memory_store=AlternateFileStore())
    project = store.create_project('alternate','',str(tmp_path/'project'))
    written = store.memory_write(project['id'], {'kind':'fact','scope':'project',
        'content':'replaceable provider content'})
    page = store.memory_read_page(project['id'],written['reference'],max_characters=8)
    assert page['content'] == 'replacea' and page['next_offset'] == 8


def test_memory_search_result_is_a_bounded_source_directory(store, tmp_path):
    project = store.create_project('search','',str(tmp_path/'project'))
    for index in range(30):
        store.memory_write(project['id'], {'kind':'fact','scope':'project',
            'content':f'MARKER_{index} shared needle '+('大' * 10_000)})
    ctx = CapabilityContext(project_id=project['id'],project=project,store=store)
    found = store.registry.dispatch('project.memory.search',
        {'query':'shared needle','limit':200},ctx)
    assert found.ok and len(found.output) == 8
    assert all(item['content_complete'] is False and item['next_offset']
               and item['sha256'] and item['reference'] for item in found.output)
    assert all(item['search_more_matches'] and item['search_limit_applied'] == 8
               for item in found.output)
    assert all(item['search_next_offset'] == 8 for item in found.output)
    assert len(json.dumps(found.output,ensure_ascii=False).encode('utf-8')) < 8_000
    projected = project_result(json.dumps(found.model_dump(mode='json'),ensure_ascii=False), 512)
    assert len(projected.encode('utf-8')) <= 512
    assert json.loads(projected)['capability'] == 'project.memory.search'
    visited = {item['reference'] for item in found.output}
    offset = found.output[0]['search_next_offset']
    while offset is not None:
        next_page = store.registry.dispatch('project.memory.search',
            {'query':'shared needle','limit':200,'offset':offset},ctx)
        assert next_page.ok
        visited.update(item['reference'] for item in next_page.output)
        offset = next_page.output[0]['search_next_offset'] if next_page.output else None
    assert len(visited) == 30


def test_memory_source_change_is_not_mixed_into_existing_frame(store, tmp_path):
    project = store.create_project('change','',str(tmp_path/'project'))
    memory = store.memory.build_context(project)
    store.memory_append(project['id'],'PROJECT.md','new version',source_type='test')
    payload = {'authoritative_project_state':{'memory':memory},
               'authoritative_current_request':{'content':'continue'}}
    messages = [{'role':'system','content':'protocol'},
                {'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
    view = manager(store,project).prepare(messages,[])
    record = json.loads(view[-1]['content'])['authoritative_project_state']['memory']['records'][0]
    assert record['source_changed_or_unavailable'] is True
    assert record['content'] == '' and record['content_complete'] is False


def test_small_window_shrinks_optional_handoff_and_ledger_before_blocking(store, tmp_path):
    project = store.create_project('references','',str(tmp_path/'project'))
    context = manager(store,project,kind='column',window=4096)
    handoff = {'session_handoff':{'snapshot_id':'snapshot-1','sha256':'a'*64,
        'summary_excerpt':'historic context ' * 900,'content_complete':False,
        'retrieve':'agent.context.read'}}
    ledger = {'current_assignment':{'input':{'execution_evidence':{
        'total_operations':80,'entries':[{'evidence_id':str(n),'status':'ok'} for n in range(80)],
        'retrieve':'agent.evidence.list'}}}}
    messages = [{'role':'system','content':'protocol'},
        {'role':'user','content':json.dumps(handoff)},
        {'role':'user','content':json.dumps(ledger)},
        {'role':'user','content':'Current Worker instruction stays intact'}]
    view = context.prepare(messages,[])
    assert context.estimate(view,[]) <= context.hard
    assert view[-1]['content'] == 'Current Worker instruction stays intact'
    assert len(json.loads(view[1]['content'])['session_handoff']['summary_excerpt']) < len(handoff['session_handoff']['summary_excerpt'])


def test_provider_overflow_retry_reprojects_memory_without_new_effect(store, tmp_path):
    project = store.create_project('retry memory','',str(tmp_path/'project'))
    memory = large_memory(store,project)
    spec = AgentRunSpec(kind='conversation',project=project,instruction='work',
        instruction_revision=1,context={'memory':memory},capability_ids=[])
    sizes = []

    class RejectOnce(WindowModel):
        def __call__(self, messages, tools, **kwargs):
            sizes.append(len(json.dumps(messages,ensure_ascii=False).encode('utf-8')))
            if len(sizes) == 1:
                raise LLMProviderError(ProviderErrorDetails('anthropic','test',400,
                    'LLM_BAD_REQUEST','context window exceeds limit',2013))
            return AgentModelResponse(text='recovered')

    requester = ProviderTurnRequester(store=store,model_complete=RejectOnce(),
        spec=spec,run_id='memory-retry')
    messages = [{'role':'system','content':'protocol'},
                {'role':'user','content':json.dumps({
                    'authoritative_current_request':{'content':'Continue'},
                    'authoritative_project_state':{'memory':memory}},ensure_ascii=False)}]
    result = requester.request(messages,[],iteration=1,require_tool=False,required_tool_name=None)
    assert result.text == 'recovered' and len(sizes) == 2 and sizes[1] < sizes[0]


# ---------------------------------------------------------------------------
# Memory scope binding
# ---------------------------------------------------------------------------

def test_workflow_selector_uses_task_frozen_revision_after_new_publication(store, tmp_path):
    project = store.create_project('scope', '', str(tmp_path/'project'))
    _, first = publish_planned_workflow(store, project['id'], agent_workflow(instruction='first'))
    task = create_planned_task(store, project['id'], 'old task')
    _, second = publish_planned_workflow(store, project['id'], agent_workflow(instruction='second'))
    assert task['workflow_revision_id'] == first['id'] != second['id']
    for revision, name in ((first, 'OLD_WORKFLOW_FACT'), (second, 'NEW_WORKFLOW_FACT')):
        store.memory_write(project['id'], {'kind':'fact','scope':'workflow',
            'scope_id':revision['id'],'content':name})
    selector = MemorySelector(scope='workflow', query='WORKFLOW_FACT', required=True)
    context = store.memory.build_context(project, selectors=[selector], include_core=False,
        scope_context=MemoryScopeContext(project['id'],task_id=task['id'],
                                        workflow_revision_id=task['workflow_revision_id']))
    assert [item['content'].strip() for item in context['records']] == ['OLD_WORKFLOW_FACT']
    assert context['manifest']['selected'][0]['scope_id'] == first['id']
    runtime_input = WorkflowRuntime(store, store.registry, 'scope-test')._input_for(
        task, agent_workflow(instruction='first'), agent_workflow(instruction='first').column('work'),
        context_selection=ContextSelection(memory=[selector], include_project=False))
    assert [item['content'].strip() for item in runtime_input['memory']['records']] == ['OLD_WORKFLOW_FACT']


def test_missing_scope_identity_never_becomes_wildcard(store, tmp_path):
    project = store.create_project('scope', '', str(tmp_path/'project'))
    store.memory_write(project['id'], {'kind':'fact','scope':'conversation',
        'scope_id':'some-session','content':'DO_NOT_LEAK'})
    optional = store.memory.build_context(project, include_core=False,
        selectors=[MemorySelector(scope='conversation',query='DO_NOT_LEAK')])
    assert optional['records'] == []
    assert optional['manifest']['omitted'][0]['reason'] == 'scope_unavailable'
    with pytest.raises(ValueError, match='scope=conversation'):
        store.memory.build_context(project, include_core=False,
            selectors=[MemorySelector(scope='conversation',query='DO_NOT_LEAK',required=True)])


def test_agent_memory_tools_bind_default_scope_and_reject_foreign_ids(store, tmp_path):
    project = store.create_project('scope', '', str(tmp_path/'project'))
    foreign = store.create_project('foreign', '', str(tmp_path/'foreign'))
    _, revision = publish_planned_workflow(store, project['id'], agent_workflow())
    task = create_planned_task(store, project['id'], 'local')
    _, foreign_revision = publish_planned_workflow(store, foreign['id'], agent_workflow())
    ctx = CapabilityContext(project_id=project['id'],project=project,store=store,
                            task_id=task['id'],execution_key='scope-write')
    result = store.registry.dispatch('project.memory.write',
        {'kind':'fact','scope':'workflow','content':'Bound to frozen revision'},ctx)
    assert result.ok, result.error
    assert result.output['metadata']['scope_id'] == revision['id']
    searched = store.registry.dispatch('project.memory.search',
        {'query':'Bound to frozen','scope':'workflow'},
        CapabilityContext(**{**ctx.__dict__,'execution_key':'scope-search'}))
    assert searched.ok and len(searched.output) == 1
    assert searched.output[0]['metadata']['scope_id'] == revision['id']
    with pytest.raises(ValueError, match='does not belong'):
        store.registry.dispatch('project.memory.write',
            {'kind':'fact','scope':'workflow','scope_id':foreign_revision['id'],
             'content':'wrong project'},
            CapabilityContext(**{**ctx.__dict__,'execution_key':'scope-reject'}))
    with pytest.raises(ValueError, match='scope=conversation'):
        store.registry.dispatch('project.memory.write',
            {'kind':'fact','scope':'conversation','content':'no conversation identity'},
            CapabilityContext(project_id=project['id'],project=project,store=store,
                              execution_key='scope-missing'))


def test_unbound_legacy_memory_is_excluded_from_selector_but_explicitly_readable(store, tmp_path):
    project = store.create_project('scope', '', str(tmp_path/'project'))
    _, revision = publish_planned_workflow(store, project['id'], agent_workflow())
    task = create_planned_task(store, project['id'], 'local')
    legacy = store.memory_write(project['id'], {'kind':'fact','scope':'workflow',
        'content':'LEGACY_UNBOUND_RECORD'})
    context = store.memory.build_context(project, include_core=False,
        selectors=[MemorySelector(scope='workflow',query='LEGACY_UNBOUND_RECORD')],
        scope_context=MemoryScopeContext(project['id'],task_id=task['id'],
                                        workflow_revision_id=revision['id']))
    assert context['records'] == []
    ctx = CapabilityContext(project_id=project['id'],project=project,store=store,
                            execution_key='scope-reference')
    result = store.registry.dispatch('project.memory.read',{'reference':legacy['reference']},ctx)
    assert result.ok and result.output['scope_resolution'] == 'unbound_or_legacy'
    assert 'LEGACY_UNBOUND_RECORD' in result.output['content']


def test_project_task_and_conversation_selectors_keep_their_own_identities(store, tmp_path):
    project = store.create_project('scope', '', str(tmp_path/'project'))
    publish_planned_workflow(store, project['id'], agent_workflow())
    first = create_planned_task(store, project['id'], 'first')
    second = create_planned_task(store, project['id'], 'second')
    session_id = store.conversation_agent(project['id'])['logical_id']
    for scope, scope_id, content in (
        ('project', None, 'SHARED_SCOPE_MARKER'),
        ('task', first['id'], 'FIRST_TASK_MARKER'),
        ('task', second['id'], 'SECOND_TASK_MARKER'),
        ('conversation', session_id, 'MAIN_CONVERSATION_MARKER'),
    ):
        store.memory_write(project['id'], {'kind':'fact','scope':scope,'scope_id':scope_id,
                                            'content':content})
    selected = store.memory.build_context(project, include_core=False,
        selectors=[MemorySelector(scope='project', query='SHARED_SCOPE_MARKER'),
                   MemorySelector(scope='task', query='TASK_MARKER'),
                   MemorySelector(scope='conversation', query='MAIN_CONVERSATION_MARKER')],
        scope_context=MemoryScopeContext(project['id'],task_id=first['id'],
                                        conversation_session_id=session_id))
    contents = [item['content'].strip() for item in selected['records']]
    assert contents == ['SHARED_SCOPE_MARKER','FIRST_TASK_MARKER','MAIN_CONVERSATION_MARKER']


# ---------------------------------------------------------------------------
# File-first memory contract
# ---------------------------------------------------------------------------

def test_file_memory_is_project_local_searchable_and_supersedable(store, tmp_path):
    project = store.create_project("memory", "file-first memory", str(tmp_path / "project"))

    root = tmp_path / "project" / ".devwerk" / "memory"
    assert (root / "PROJECT.md").is_file()
    assert (root / "DECISIONS.md").is_file()

    first = store.memory_write(project["id"], {
        "kind": "decision",
        "scope": "project",
        "authority": "user_confirmed",
        "content": "Use PostgreSQL for the production deployment.",
        "source_type": "conversation",
        "source_id": "message-1",
    })
    assert first["reference"].endswith(".md")
    assert store.memory_search(project["id"], "PostgreSQL")[0]["metadata"]["id"] == first["metadata"]["id"]

    replacement = store.memory_supersede(project["id"], first["reference"], {
        "kind": "decision",
        "scope": "project",
        "authority": "user_confirmed",
        "content": "Use SQLite for the local-first v0.1.0 release.",
        "source_type": "conversation",
        "source_id": "message-2",
    })
    assert replacement["metadata"]["revision"] == 2
    assert store.memory_read(project["id"], first["reference"])["metadata"]["status"] == "superseded"
    appended = store.memory_append(
        project["id"],
        "CURRENT.md",
        "Implementation is in progress.",
        source_type="conversation",
        source_id="message-3",
    )
    assert appended["metadata"]["revision"] == 2
    assert "Implementation is in progress." in appended["content"]


def test_memory_store_provider_is_replaceable_without_runtime_branching(tmp_path):
    class TrackingFileMemoryStore(FileMemoryStore):
        name = "tracking-file"

        def __init__(self):
            self.writes = 0

        def write(self, project, record):
            self.writes += 1
            return super().write(project, record)

    provider = TrackingFileMemoryStore()
    value = V1Store(
        str(tmp_path / "provider.db"),
        registry=build_core_registry(),
        memory_store=provider,
    )
    project = value.create_project("provider", "replaceable", str(tmp_path / "project"))
    written = value.memory_write(project["id"], {
        "kind": "fact",
        "scope": "project",
        "content": "Provider substitution works.",
    })

    assert provider.writes == 1
    assert written["provider"] == "tracking-file"
    assert value.memory.build_context(project)["manifest"]["store_provider"] == "tracking-file"


def test_memory_context_initializes_a_pre_memory_project_workspace(store, tmp_path):
    project = store.projects.create_project(
        "existing",
        "created before File Memory",
        str(tmp_path / "existing"),
        "",
    )
    memory_root = tmp_path / "existing" / ".devwerk" / "memory"
    assert not memory_root.exists()

    context = store.memory.build_context(project)

    assert (memory_root / "PROJECT.md").is_file()
    assert context["manifest"]["selected"]
