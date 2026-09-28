import json

from app.v1.agent_models import AgentRunSpec
from app.v1.context_manager import ContextManager, covered_message_ids
from app.v1.session_replay import replayable_session_messages
from app.v1.store import V1Store
from tests.helpers import agent_workflow, publish_planned_workflow
from tests.test_agent_recovery_and_fencing import begin_agent
from tests.test_persistent_agents import assignment


def current_assignment(store,tmp_path):
    project = store.create_project('checkpoint','',str(tmp_path/'project'))
    workflow = agent_workflow()
    publish_planned_workflow(store,project['id'],workflow)
    task,run,assigned = assignment(store,project,workflow)
    agent = begin_agent(store,project,kind='column',task_id=task['id'],
                        column_run_id=run['id'],agent_session_id=assigned['session_id'])
    with store.tx(immediate=True) as db:
        db.execute('UPDATE v1_agent_runs SET assignment_id=?,agent_instance_id=? WHERE id=?',
                   (assigned['id'],assigned['agent_instance_id'],agent['id']))
    spec = AgentRunSpec(kind='column',project=project,instruction='Continue',
        instruction_revision=1,context={},capability_ids=[],assignment=assigned,
        task_id=task['id'],column_run_id=run['id'],agent_session_id=assigned['session_id'],
        agent_instance_id=assigned['agent_instance_id'],requirement_id=assigned['requirement_id'])
    manager = ContextManager(store,spec,agent['id'],lambda *a,**kw:None)
    return project,assigned,agent,manager


def persisted(store,agent,role,content,*,calls=None,call_id=None):
    row = store.add_agent_message(agent['id'],role,content,calls or [],tool_call_id=call_id)
    message = {'role':role,'content':content,'_source_message_id':row['id']}
    if calls:
        message['tool_calls']=calls
    if call_id:
        message['tool_call_id']=call_id
    return message


def tool_batch(store,agent,name):
    calls = [{'id':name,'type':'function','function':{'name':'project.files.read',
              'arguments':'{"path":"reference.txt"}'}}]
    return [persisted(store,agent,'assistant','Reading '+name,calls=calls),
            persisted(store,agent,'tool',json.dumps({'ok':True,'output':'x'*3000}),call_id=name)]


def test_six_worker_instructions_survive_compaction_and_store_restart(store,tmp_path):
    project,assigned,agent,manager = current_assignment(store,tmp_path)
    marker = 'FIRST_VALID_WORKER_CONSTRAINT'
    notes = [persisted(store,agent,'user',marker if n==0 else f'later requirement {n}')
             for n in range(6)]
    other = begin_agent(store,project,kind='conversation')
    gap = persisted(store,other,'user','ANOTHER_AGENT_MESSAGE')
    batch = tool_batch(store,agent,'read-1')
    messages = [{'role':'system','content':'Current Assignment'},*notes,*batch]
    before = manager.prepare(messages,[],force=True)
    assert marker in json.dumps(before)
    with store.connect() as db:
        checkpoint = db.execute('SELECT * FROM v1_context_checkpoints WHERE scope_id=?',
                                (assigned['id'],)).fetchone()
    covered = covered_message_ids(checkpoint['coverage_json'])
    assert covered=={batch[0]['_source_message_id'],batch[1]['_source_message_id']}
    assert gap['_source_message_id'] not in covered
    reopened = V1Store(str(store.path),registry=store.registry)
    history = replayable_session_messages(reopened.agents.session_history(
        project['id'],assigned['session_id'],assignment_id=assigned['id']))
    assert marker in json.dumps(history) and len(history)==2
    assert history[0]['_checkpoint'] and not any(m.get('tool_calls') for m in history)
    assert history[1]['_source_message_ids']==[n['_source_message_id'] for n in notes]


def test_two_compactions_accumulate_only_actual_tool_message_ids(store,tmp_path):
    project,assigned,agent,manager = current_assignment(store,tmp_path)
    note = persisted(store,agent,'user','KEEP_UNREVOKED_INSTRUCTION')
    first = tool_batch(store,agent,'batch-1')
    messages = [{'role':'system','content':'Current Assignment'},note,*first]
    manager.prepare(messages,[],force=True)
    second = tool_batch(store,agent,'batch-2')
    messages.extend(second)
    manager.prepare(messages,[],force=True)
    with store.connect() as db:
        rows = db.execute('SELECT * FROM v1_context_checkpoints WHERE scope_id=? ORDER BY revision',
                          (assigned['id'],)).fetchall()
    assert len(rows)==2
    expected = {m['_source_message_id'] for m in [*first,*second]}
    assert covered_message_ids(rows[-1]['coverage_json'])==expected
    assert note['_source_message_id'] not in expected
    reloaded = store.agents.session_history(project['id'],assigned['session_id'],assignment_id=assigned['id'])
    assert 'KEEP_UNREVOKED_INSTRUCTION' in json.dumps(reloaded)
    assert sum(m.get('_checkpoint',False) for m in reloaded)==1


def test_legacy_prefix_checkpoint_replays_raw_before_v2_upgrade(store,tmp_path):
    project,assigned,agent,manager = current_assignment(store,tmp_path)
    note = persisted(store,agent,'user','LEGACY_VALID_INPUT')
    batch = tool_batch(store,agent,'legacy-batch')
    with store.tx(immediate=True) as db:
        db.execute('''INSERT INTO v1_context_checkpoints
          (id,project_id,scope_id,agent_run_id,revision,through_message_id,
           summary_json,source_hash,metrics_json,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)''',
          ('old-checkpoint',project['id'],assigned['id'],agent['id'],1,
           batch[-1]['_source_message_id'],json.dumps({'summary':'incomplete old summary'}),
           'old-hash','{}','2026-01-01T00:00:00Z'))
    recovered = store.agents.session_history(project['id'],assigned['session_id'],assignment_id=assigned['id'])
    assert 'LEGACY_VALID_INPUT' in json.dumps(recovered)
    assert len(recovered)==3 and not any(m.get('_checkpoint') for m in recovered)
    manager.prepare([{'role':'system','content':'Current Assignment'},note,*batch],[],force=True)
    with store.connect() as db:
        upgraded = db.execute('SELECT * FROM v1_context_checkpoints WHERE scope_id=? ORDER BY revision DESC LIMIT 1',
                              (assigned['id'],)).fetchone()
    assert upgraded['revision']==2
    assert covered_message_ids(upgraded['coverage_json'])=={
        batch[0]['_source_message_id'],batch[1]['_source_message_id']}
    after = store.agents.session_history(project['id'],assigned['session_id'],assignment_id=assigned['id'])
    assert 'LEGACY_VALID_INPUT' in json.dumps(after)


def test_consumed_worker_input_returns_its_own_stored_message_id(store,tmp_path):
    project,assigned,agent,_manager = current_assignment(store,tmp_path)
    sent = store.agents.send(project['id'],assigned['agent_instance_id'],
                             'Keep the existing API',assignment_id=assigned['id'])
    consumed = store.agents.consume_messages(assigned,agent['id'])
    assert consumed['payload'][0]['message_id']==sent['id']
    with store.connect() as db:
        row = db.execute('SELECT content FROM v1_agent_messages WHERE id=?',
                         (consumed['source_message_id'],)).fetchone()
    assert 'Keep the existing API' in row['content']
    assert store.agents.consume_messages(assigned,agent['id'])==[]
