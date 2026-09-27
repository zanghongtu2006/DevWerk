"""Fresh offline review probes. Only temporary databases/workspaces are used."""
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pytest
from tests.conftest import isolated_settings, store as store_fixture
from tests.helpers import agent_workflow, publish_planned_workflow
from tests.test_persistent_agents import assignment
from tests.test_scope_extension import turn, invoke
from tests.test_agent_recovery_and_fencing import begin_agent
from app.v1.agent_models import AgentRunSpec
from app.v1.agent_run_preparation import AgentRunPreparer
from app.v1.context_manager import ContextManager, ContextExhausted
from app.v1.domain import MemorySelector, ToolResult
from app.v1.memory import MemoryRecord
from app.v1.runtime import WorkflowRuntime, _column_completion_contract

report = {}

def setup(root):
    root.mkdir()
    isolated_settings.__wrapped__(root, monkeypatch)
    store = store_fixture.__wrapped__(root)
    project = store.create_project('offline review', '', str(root/'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store, project['id'], workflow)
    return store, project, workflow

def spec_for(project, workflow, task, run, assigned, context):
    return AgentRunSpec(kind='column', project=project, instruction='Complete this assignment',
        instruction_revision=1, context=context, capability_ids=workflow.column('work').executor.capabilities,
        task_id=task['id'], column_run_id=run['id'], column_attempt_id=run['attempt_id'],
        assignment=assigned, agent_session_id=assigned['session_id'],
        agent_instance_id=assigned['agent_instance_id'], requirement_id=assigned['requirement_id'],
        completion_contract=_column_completion_contract(workflow, workflow.column('work')))

def prepare(store, spec):
    return AgentRunPreparer(store=store, registry=store.registry, runtime_policy=store.policy,
        platform_policy=store.latest_platform_policy()).prepare(spec)

with tempfile.TemporaryDirectory(prefix='devwerk-review-') as directory, pytest.MonkeyPatch.context() as monkeypatch:
    root = Path(directory)
    store, project, workflow = setup(root/'handoff')
    task, run, old = assignment(store, project, workflow)
    requirement = store.agents.get_requirement(project['id'], old['requirement_id'])
    evidence = store.prepare_terminal_evidence(task, run['id'], 'done', {}, None)
    store.finish_run(task, run['id'], {}, 'success', 'done', terminal='done', terminal_artifact=evidence)
    store.agents.set_lifecycle(project['id'], old['agent_instance_id'], 'retired')
    ctx, _ = turn(store, project, requirement, request='continue')
    marker = 'HANDOFF_CRITICAL_LETTER_REMAINS_UNOPENED'
    successor = invoke(ctx, 'agent.worker.replace', {'worker_id':old['agent_instance_id'], 'summary':marker})
    store.finish_conversation_job(store.agents.conversation_job(ctx)['id'], None, ctx.agent_run_id, {})
    task, run, current = assignment(store, project, workflow, requirement=requirement)
    prepared = prepare(store, spec_for(project, workflow, task, run, current, {'input':'Continue the existing work'}))
    report['worker_handoff'] = {
        'successor_assigned': current['agent_instance_id'] == successor['worker']['id'],
        'snapshot_visible_without_assignment_filter':marker in json.dumps(store.agents.session_history(project['id'],current['session_id'])),
        'handoff_visible_in_actual_prepared_messages':marker in json.dumps(prepared.messages),
        'handoff_visible_in_context_read':marker in json.dumps(store.agents.context_page(project['id'],current['agent_instance_id']))}

    store, project, workflow = setup(root/'memory')
    for name in ('workflow_A','workflow_B'):
        store.memory.store.write(project, MemoryRecord(scope='workflow',scope_id=name,kind='decision',content=name+' specific decision'))
    context = store.memory.build_context(project,selectors=[MemorySelector(scope='workflow')],task_id='task_A',include_core=False)
    report['memory_scope'] = {'selected_scope_ids':[r['metadata']['scope_id'] for r in context['records']],
        'omitted':context['manifest']['omitted']}
    store.memory.store.append(project,'DECISIONS.md','LARGE_CORE_MEMORY '+('x'*230000),source_type='review')
    context = store.memory.build_context(project)
    task, run, current = assignment(store, project, workflow)
    spec = spec_for(project, workflow, task, run, current, {'input':{'memory':context}})
    prepared = prepare(store,spec)
    manager = ContextManager(store,spec,prepared.run['id'],lambda *a,**kw:None)
    try:
        manager.prepare(prepared.messages,prepared.tools)
        error = None
    except ContextExhausted as exc:
        error = str(exc)
    report['memory_budget'] = {'selected_content_characters':sum(len(r['content']) for r in context['records']),
        'omitted':context['manifest']['omitted'],'error':error,'input_budget':manager.hard}

    store, project, workflow = setup(root/'ledger')
    task, run, current = assignment(store, project, workflow)
    initial = begin_agent(store,project,kind='column',task_id=task['id'],column_run_id=run['id'],agent_session_id=current['session_id'])
    raw = 'RAW_COMMAND_LOG_'+('x'*110000)
    for index in range(4):
        result = ToolResult(ok=True,capability='project.command.run',output={'exit_code':0,'stdout':raw,'stderr':''})
        store.record_tool_invocation(agent_run_id=initial['id'],tool_call_id=f'command-{index}',capability='project.command.run',
            arguments={'argv':['synthetic-offline-command']},result=result.model_dump(mode='json'),ok=True)
    ledger = WorkflowRuntime(store,store.registry,'offline')._column_action_ledger(project['id'],run['id'])
    spec = spec_for(project,workflow,task,run,current,{'input':{'objective':'continue'},'action_ledger':ledger})
    prepared = prepare(store,spec)
    manager = ContextManager(store,spec,prepared.run['id'],lambda *a,**kw:None)
    try:
        manager.prepare(prepared.messages,prepared.tools,force=True)
        error = None
    except ContextExhausted as exc:
        error = str(exc)
    report['resume_ledger'] = {'receipts':len(ledger),'ledger_characters':len(json.dumps(ledger)),
        'raw_log_copies_in_prepared_messages':json.dumps(prepared.messages).count(raw),
        'compactable_tool_batches':len(manager.groups(prepared.messages)),
        'estimated_tokens':manager.estimate(prepared.messages,prepared.tools),'input_budget':manager.hard,'error':error}

    store, project, workflow = setup(root/'notes')
    task, run, current = assignment(store,project,workflow)
    spec = spec_for(project,workflow,task,run,current,{'input':'small current assignment'})
    prepared = prepare(store,spec)
    messages = prepared.messages
    marker = 'CRITICAL_WORKER_INPUT_DO_NOT_CHANGE_PUBLIC_API'
    for index in range(6):
        content = marker if index == 0 else f'Later note {index}'
        source = store.add_agent_message(prepared.run['id'],'user',content,[])
        messages.append({'role':'user','content':content,'_source_message_id':source['id']})
    calls = [{'id':'read-1','type':'function','function':{'name':'project.files.read','arguments':'{"path":"a.txt"}'}}]
    source = store.add_agent_message(prepared.run['id'],'assistant','Reading source',calls)
    messages.append({'role':'assistant','content':'Reading source','tool_calls':calls,'_source_message_id':source['id']})
    source = store.add_agent_message(prepared.run['id'],'tool','{"ok":true}',[],tool_call_id='read-1')
    messages.append({'role':'tool','content':'{"ok":true}','tool_call_id':'read-1','_source_message_id':source['id']})
    manager = ContextManager(store,spec,prepared.run['id'],lambda *a,**kw:None)
    compacted = manager.prepare(messages,prepared.tools,force=True)
    reloaded = store.agents.session_history(project['id'],current['session_id'],assignment_id=current['id'])
    report['checkpoint_replay'] = {'critical_input_in_live_context':marker in json.dumps(compacted),
        'critical_input_in_reloaded_context':marker in json.dumps(reloaded),
        'critical_input_in_source_archive':marker in json.dumps(store.agents.context_page(project['id'],current['agent_instance_id'])),
        'live_message_count':len(compacted),'reloaded_message_count':len(reloaded)}

output = Path(__file__).with_name('result.json')
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=False,indent=2))
