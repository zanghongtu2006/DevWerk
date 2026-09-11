"""Fresh P0 review probes. Uses temporary SQLite databases and workspaces only.

Run from the repository with: .\\venv\\Scripts\\python.exe scripts/review_p0_runtime_2026_09_09.py
Outputs observed behavior; this is a diagnostic script, not a passing regression suite.
Model responses and one asynchronous adapter are deterministic test doubles.
No production database, external provider, or historical test report is used.
"""
import os
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
os.chdir(REPOSITORY)

import os,json,logging,asyncio,sqlite3,tempfile,sys
from pathlib import Path
from unittest.mock import patch
os.environ['APP_ENV']='test'
os.environ['DEVWERK_LLM_CONFIG_PATH']='missing-review-config.json'
os.environ['DEVWERK_LLM_CONFIG_JSON']=json.dumps({'providers':{'test':{'protocol':'anthropic','base_url':'https://provider.invalid','api_key':'test'}},'models':{'main':{'provider':'test','model':'test','max_tokens':1000,'request_timeout_seconds':10,'thinking_mode':'balanced','temperature':0.2}},'routes':{'default':'main','column':'main','conversation':'main'},'runtime':{'trust_env_proxy':False}})
from app.core.config import reload_settings
reload_settings()
logging.disable(logging.CRITICAL)
from app.v1.store import V1Store
from app.v1.capabilities import build_core_registry,CapabilityContext,CapabilityEntry
from app.v1.policy import ExecutionLimits,PlatformPolicyLoader
from app.v1.agent import AgentCore
from app.v1.domain import AgentModelResponse,AgentToolCall,TimerWaitPolicy,PollWaitPolicy,ToolResult
from app.v1.runtime import WorkflowRuntime,_column_completion_contract
from app.v1.execution_control import ExecutionControl,ExecutionOwnershipLost
from app.v1.execution_ledger import ledger_entry
from app.v1.completion_protocol import parse_completion_submission
from app.v1.services.completion_admission import CompletionAdmissionService
from app.v1.conversation import ConversationGateway
from tests.helpers import agent_workflow,sequence_workflow,publish_planned_workflow,create_planned_task

def prepare(base,workflow=None):
    store=V1Store(str(base/'db.sqlite'),registry=build_core_registry())
    store.register_platform_policy(PlatformPolicyLoader(Path('DEVWERK.md')).load())
    p=store.create_project('review','',str(base/'project'))
    if workflow:
        publish_planned_workflow(store,p['id'],workflow)
        task=create_planned_task(store,p['id'],'review')
        return store,p,task
    return store,p
def call(name,args,i='call'):
    return AgentModelResponse(tool_calls=[AgentToolCall(id=i,name=name,arguments=args)])
def due(store):
    with store.tx(immediate=True) as db:
        db.execute("UPDATE v1_await_handles SET next_check_at='2000-01-01T00:00:00+00:00' WHERE status='pending'")
    return store.due_await_handles()[0]
complete={'outcome':'success','output':{'delivered':True},'summary':'done'}

with tempfile.TemporaryDirectory(prefix='devwerk-review-') as tmp:
    base=Path(tmp)
    wf=agent_workflow(instruction='Append x before waiting, and append x again after the timer. Both writes are required.')
    wf.column('work').executor.capabilities.append('project.command.run')
    wf.column('work').wait_policy=TimerWaitPolicy(delay_seconds=1)
    store,p,t=prepare(base/'repeat',wf)
    args={'argv':[sys.executable,'-c',"with open('result.txt','a') as f: f.write('x')"]}
    responses=iter([call('project.command.run',args,'before'),call('column.await',{'provider':'timer'},'wait'),call('project.command.run',args,'after'),call('column.complete',complete,'complete')])
    runtime=WorkflowRuntime(store,store.registry,'worker',AgentCore(store,store.registry,lambda *a,**k:next(responses)))
    runtime.step(t['id']);runtime.reconcile_await(due(store))
    print('REPEAT_AFTER_AWAIT',json.dumps({'task_status':store.get_task(t['id'])['status'],'expected':'xx','actual':(base/'repeat/project/result.txt').read_text()}))

    wf=agent_workflow();wf.column('work').wait_policy=TimerWaitPolicy(delay_seconds=1)
    store,p,t=prepare(base/'cycle',wf)
    store.policy=store.policy.model_copy(update={'execution':ExecutionLimits(agent_max_iterations=1,agent_max_tool_calls=1,max_column_visits=1,max_recovery_attempts=1)})
    core=AgentCore(store,store.registry,lambda *a,**k:call('column.await',{'provider':'timer'}),policy=store.policy)
    runtime=WorkflowRuntime(store,store.registry,'worker',core)
    runtime.step(t['id'])
    for i in range(6):runtime.reconcile_await(due(store))
    with store.connect() as db:
        counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['v1_column_runs','v1_column_attempts','v1_agent_runs','v1_await_handles']}
    print('AWAIT_BUDGET_BYPASS',json.dumps({'status':store.get_task(t['id'])['status'],'control':store.get_task(t['id'])['control_state'],**counts}))

    store,p=prepare(base/'queue')
    job=store.create_conversation_job(p['id'],'first',True)
    gateway=ConversationGateway(store,store.registry)
    gateway._pending[p['id']].append(job['id']);gateway._pending_ids.add(job['id'])
    async def fail_claim():
        with patch.object(store,'claim_conversation_job',side_effect=sqlite3.OperationalError('database is locked')):
            try:await gateway._drain_session(p['id'])
            except sqlite3.OperationalError:pass
    asyncio.run(fail_claim())
    followup=store.create_conversation_job(p['id'],'second',True)
    print('ORPHAN_QUEUE',json.dumps({'old_job_status':store.get_conversation_job(job['id'])['status'],'in_memory_queue':len(gateway._pending[p['id']]),'enqueued_after_dispatcher_tick':store.enqueue_governance_jobs(),'new_job_claim':store.claim_conversation_job(followup['id'],'owner')}))

    contract=_column_completion_contract(agent_workflow(),agent_workflow().column('work'))
    failed=ledger_entry('run','fail','project.command.run','process',ToolResult(ok=False,capability='project.command.run',output={'exit_code':1},error={'message':'delivery failed'}),arguments={'argv':['python','deliver.py']})
    noop=ledger_entry('run','noop','project.command.run','process',ToolResult(ok=True,capability='project.command.run',output={'exit_code':0}),arguments={'argv':['python','-c',"print('done')"]})
    submission={**complete,'failure_resolutions':[{'failed_evidence_id':failed['evidence_id'],'resolved_by_evidence_id':noop['evidence_id'],'reason':'fixed'}]}
    decision=CompletionAdmissionService().evaluate(parse_completion_submission(submission,contract,[failed,noop]),contract,[failed,noop])
    print('UNRELATED_COMMAND_REPAIR',json.dumps({'accepted':decision.accepted,'failed_delivery_was_reexecuted':False}))

import time
with tempfile.TemporaryDirectory(prefix='devwerk-review-extra-') as tmp:
    base=Path(tmp)
    store,p,t=prepare(base/'stale',sequence_workflow())
    store.pause_task(t['id'])
    job=store.create_conversation_job(p['id'],'resume',True)
    owned=store.claim_conversation_job(job['id'],'old-owner')
    context=CapabilityContext(p['id'],p,store,agent_run_id='review-agent',
        execution_control=ExecutionControl(time.monotonic()+60,validate_owner=lambda:store.assert_conversation_owner(owned)))
    original=store.start_execution_receipt
    def lose_lease(*args,**kwargs):
        receipt=original(*args,**kwargs)
        with store.tx(immediate=True) as db:
            db.execute("UPDATE v1_conversation_agents SET lease_until='2000-01-01T00:00:00+00:00' WHERE project_id=?",(p['id'],))
        store.recover_expired_conversation_jobs()
        replacement=store.create_conversation_job(p['id'],'new turn',True)
        assert store.claim_conversation_job(replacement['id'],'new-owner')
        return receipt
    outcome='unexpected success'
    with patch.object(store,'start_execution_receipt',side_effect=lose_lease):
        try:store.registry.dispatch('task.resume',{'task_id':t['id']},context)
        except ExecutionOwnershipLost:outcome='ExecutionOwnershipLost'
    print('STALE_CONTROL',json.dumps({'reported':outcome,'old_job_status':store.get_conversation_job(job['id'])['status'],'task_control_after_stale_handler':store.get_task(t['id'])['control_state']}))

    store,p=prepare(base/'async')
    store.registry.register(CapabilityEntry(id='review.async',description='start async job',input_schema={},output_schema={},side_effect_kind='process',
        handler=lambda a,c:ToolResult(ok=True,status='awaiting',capability='review.async',await_handle_draft={'provider':'review','poll_capability':'review.poll','poll_arguments':{}},checkpoint={'job':'job-1'})))
    store.registry.register(CapabilityEntry(id='review.poll',description='poll',input_schema={},output_schema={},side_effect_kind='read',
        handler=lambda a,c:{'status':'succeeded','output':{'delivered':True}}))
    wf=agent_workflow();wf.column('work').executor.capabilities=['review.async','review.poll'];wf.column('work').wait_policy=PollWaitPolicy(poll_capability='review.poll',poll_interval_seconds=1)
    publish_planned_workflow(store,p['id'],wf);t=create_planned_task(store,p['id'],'await completion')
    n=0
    def model(*a,**kw):
        global n
        n+=1
        return call('review.async',{}) if n==1 else call('column.complete',complete)
    runtime=WorkflowRuntime(store,store.registry,'worker',AgentCore(store,store.registry,model))
    runtime.step(t['id'])
    problem=None
    try:runtime.reconcile_await(due(store))
    except Exception as exc:problem=type(exc).__name__+': '+str(exc)
    with store.connect() as db:
        receipt=db.execute("SELECT status FROM v1_execution_receipts WHERE capability='review.async'").fetchone()[0]
        rejected=db.execute("SELECT COUNT(*) FROM v1_tool_invocations WHERE capability='column.complete' AND ok=0").fetchone()[0]
    print('COMPLETED_AWAIT_STALE_LEDGER',json.dumps({'external_receipt':receipt,'task_status':store.get_task(t['id'])['status'],'control':store.get_task(t['id'])['control_state'],'exception':problem,'completion_rejections':rejected}))

