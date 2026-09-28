import pytest

from app.v1.capabilities import CapabilityContext
from app.v1.domain import ContextSelection, MemorySelector
from app.v1.memory import MemoryScopeContext
from app.v1.runtime import WorkflowRuntime
from tests.helpers import agent_workflow, create_planned_task, publish_planned_workflow


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
