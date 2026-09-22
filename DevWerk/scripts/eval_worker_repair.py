"""Real-provider fault-injection acceptance for durable cross-Column repair.

Writes only a new isolated evidence directory. It never opens the production DB.
This is framework acceptance, not a Vue/Spring product delivery claim.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-root',type=Path,required=True)
    args = parser.parse_args()
    evidence = args.output_root / ('worker-repair-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    evidence.mkdir(parents=True,exist_ok=False)
    os.environ['DEVWERK_DB_PATH'] = str(evidence/'runtime.db')
    os.environ['LOG_FILE_ENABLED'] = 'false'
    from app.core.config import reload_settings
    reload_settings()
    logging.disable(logging.CRITICAL)
    from app.v1.agent import AgentCore
    from app.v1.capabilities import build_core_registry
    from dataclasses import replace
    from app.v1.conversation import ConversationGateway
    from app.v1.domain import WorkflowDefinition, ColumnDefinition, AgentExecutor, Transition, AcceptanceCheck, AcceptanceObligation
    from app.v1.policy import PlatformPolicyLoader
    from app.v1.runtime import WorkflowRuntime
    from app.v1.store import V1Store
    from tests.helpers import publish_planned_workflow,create_planned_task
    store = V1Store(str(evidence/'runtime.db'),registry=build_core_registry())
    store.register_platform_policy(PlatformPolicyLoader(ROOT/'DEVWERK.md').load())
    workspace = evidence/'project'
    workspace.mkdir()
    (workspace/'calculator.py').write_text('def add(a, b):\n    return a - b\n\ndef subtract(a, b):\n    return a - b\n',encoding='utf-8')
    (workspace/'test_calculator.py').write_text(
        'import unittest\nfrom calculator import add, subtract\n'
        'class Behavior(unittest.TestCase):\n'
        '    def test_add(self):\n        self.assertEqual(add(8, 3), 11)\n'
        '    def test_subtract(self):\n        self.assertEqual(subtract(8, 3), 5)\n'
        'if __name__ == "__main__":\n    unittest.main()\n',encoding='utf-8')
    check = {'argv':[sys.executable,'-m','unittest','-v','test_calculator'], 'cwd':'.'}
    # This evaluation needs only this known local command. Reject interpreter
    # guessing/install attempts before effects, without changing production policy.
    command_entry = store.registry._entries['project.command.run']
    def command_preflight(arguments):
        if arguments.get('argv') != check['argv'] or arguments.get('cwd','.') != '.':
            raise ValueError('Use the frozen local acceptance command exactly: '+json.dumps(check))
    store.registry._entries['project.command.run'] = replace(command_entry,argument_preflight=command_preflight)
    contract = {'type':'object','required':['delivered'],'properties':{'delivered':{'type':'boolean'}},'additionalProperties':False}
    workflow = WorkflowDefinition(name='Two directed repair cycles',entry='review',columns=[
        ColumnDefinition(key='review',name='Behavior review',
            instruction=('You are the independent reviewer. Run the frozen unittest command. If it fails, inspect calculator.py and record '
                         'a persistent task.feedback.record defect using the current task_id, a distinct stable dedupe_key per defective function, '
                         'responsible_column=work and checks=[{column:review,check_key:behavior}]. Then column.complete outcome=rework, '
                         'output={delivered:false}. Never modify files. If the real tests pass, complete success with delivered=true. '
                         'The Runtime reruns frozen checks before admission. Do not call other agents or Mailbox.'),
            executor=AgentExecutor(worker_key='reviewer',capabilities=['project.command.run','project.files.read','task.feedback.record','task.feedback.list']),
            output_contract=contract,acceptance_checks=[AcceptanceCheck(key='behavior',capability='project.command.run',arguments=check,
                evidence_kind='behavior',purpose='Addition and subtraction return correct values')],
            transitions=[Transition(outcome='success',target='done'),Transition(outcome='rework',target='work',allows_unresolved_failures=True)]),
        ColumnDefinition(key='work',name='Targeted implementation',
            instruction=('Repair the current Task feedback in calculator.py. add must add and subtract must subtract. '
                         'Read current files and feedback, change only calculator.py, run the existing tests, and complete success '
                         'with output={delivered:true}. Do not change tests. This is a new Assignment even if prior work was completed. '
                         'Do not communicate through Mailbox or create agents.'),
            executor=AgentExecutor(worker_key='implementer',capabilities=['project.files.read','project.files.write','project.command.run','task.feedback.list']),
            metadata={'writable_paths':['calculator.py']},output_contract=contract,
            acceptance_checks=[AcceptanceCheck(key='implementation_behavior',capability='project.command.run',arguments=check,
                evidence_kind='behavior',purpose='Implementation must pass the existing arithmetic tests')],
            transitions=[Transition(outcome='success',target='review'),Transition(outcome='failure',target='failed')]),
        ],acceptance_obligations=[AcceptanceObligation(column='review',check_key='behavior',invalidated_by=['work'])])
    project = store.create_project('Worker repair acceptance','',str(workspace))
    publish_planned_workflow(store,project['id'],workflow)
    task = create_planned_task(store,project['id'],'Repair arithmetic behavior using feedback')
    runtime = WorkflowRuntime(store,store.registry,'isolated-evaluation',AgentCore(store,store.registry))
    injected = False
    snapshots = []
    print(json.dumps({'evidence_root':str(evidence),'project_id':project['id'],'task_id':task['id']}),flush=True)
    for step in range(8):
        current = store.get_task(task['id'])
        if current['status'] in {'done','failed'}:
            break
        before = current['current_column']
        try:
            runtime.step(task['id'])
        except Exception as exc:
            snapshots.append({'step':step,'column':before,'error':f'{type(exc).__name__}: {exc}'})
            break
        current = store.get_task(task['id'])
        if before == 'work' and current['status']=='pending' and not injected:
            # Deliberate second regression after the first repair, with unchanged
            # frozen tests. This forces a second observed feedback/repair cycle.
            source = (workspace/'calculator.py').read_text(encoding='utf-8')
            (evidence/'after-first-repair.py').write_text(source,encoding='utf-8')
            (workspace/'calculator.py').write_text('def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a + b\n',encoding='utf-8')
            injected = True
        snapshots.append({'step':step,'column':before,'status':current['status'],'next':current['current_column'],'error':current.get('error')})
        print(json.dumps(snapshots[-1],ensure_ascii=False),flush=True)
    notifications = 0
    def forbidden_model(*a,**kw):
        raise AssertionError('Mailbox invoked a model')
    gateway = ConversationGateway(store,store.registry,agent_core=AgentCore(store,store.registry,forbidden_model))
    for identity in store.enqueue_governance_jobs():
        gateway._process(store.claim_conversation_job(identity,'isolated-observer'))
        notifications += 1
    feedback = store.feedback.list(project['id'],task['id'])
    workers = [store.agents.inspect_worker(project['id'],w['id']) for w in store.agents.list_workers(project['id']) if w['role']=='leaf']
    runs = store.agent_runs(project_id=project['id'])
    ok = store.get_task(task['id'])['status']=='done' and injected and len(feedback)>=2 and all(f['state']=='resolved' for f in feedback)
    ok = ok and len(workers)==2 and all(len({a['session_id'] for a in w['assignments']})==1 for w in workers)
    report = {'passed':ok,'scope':'real-provider framework repair; not full software product acceptance',
              'steps':snapshots,'feedback':feedback,'workers':workers,'agent_runs':runs,
              'mailbox_jobs':notifications,'mailbox_model_calls':0,'evidence_root':str(evidence)}
    (evidence/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'passed':ok,'evidence_root':str(evidence),'mailbox_model_calls':0}),flush=True)
    return ok


if __name__=='__main__':
    sys.exit(0 if main() else 1)
