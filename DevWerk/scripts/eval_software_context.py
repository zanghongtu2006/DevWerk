"""Isolated real-provider software delivery + user repair; never opens production DB.

All generated artifacts and raw evidence stay in the supplied new output directory.
This is deliberately separate from deterministic runtime regression tests.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DISCUSS = ('做一个本地运行的 Vue 3 + Vite 前端、Java Spring Boot 后端注册登录演示。只保存在内存。'
    '用户名密码注册，重复用户名409，登录正确200并进入欢迎页，错误密码401，未登录访问当前用户401，注销后不能访问当前用户。'
    '使用Cookie Session。先只讨论最小交付和测试方案，不开始执行。没有注册限流、登录锁定、Origin检查等额外产品需求。')
START = ('以上需求和最小方案已经确认，现在开始实现并交付一个完整软件Task。'
    '前后端分别实现，独立审查和测试。交付源码和本地启动停止说明。你决定不影响需求的工程细节。'
    '真实运行后端测试、前端构建、HTTP测试以及至少一条浏览器注册登录注销路径，测试结束必须finally关闭服务/浏览器。'
    '冻结场景保持稳定，修复不可通过删除失败测试缩小覆盖。遵循已选择Loop的验收合同。')
REPAIR = ('第一轮交付后发现欢迎页没有展示当前用户名。现在要求修复欢迎页显示已登录用户名，'
    '同时保持注册、重复用户名409、错误密码401、未登录401和注销失效等全部行为。'
    '请基于原交付物建立明确返修任务并执行，补充浏览器断言，复用源码，不重新创建项目。')


def resume_evidence(path):
    evidence = path.resolve()
    if not evidence.is_relative_to((ROOT/'data/evals').resolve()):
        raise ValueError('Resume is restricted to isolated data/evals directories')
    report = json.loads((evidence/'report.json').read_text(encoding='utf8'))
    with sqlite3.connect((evidence/'runtime.db').as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,base_dir FROM v1_projects').fetchall()
    if len(rows) != 1 or rows[0][0] != report['project_id'] or Path(rows[0][1]).resolve() != evidence/'project':
        raise ValueError('Resume DB/project does not match the isolated evidence directory')
    return evidence, report


async def deliver(store, runtime, project_id, report, save, *, max_steps=32,
                  time_limit=10800, sleep=asyncio.sleep, clock=time.monotonic):
    deadline = clock()+time_limit
    executed = 0
    while executed < max_steps:
        if clock() >= deadline:
            raise TimeoutError('Delivery exceeded evaluation wall-clock bound; evidence can be resumed')
        tasks = store.list_tasks(project_id)
        pending = [t for t in tasks if t['status'] not in {'done','failed','cancelled'}]
        if not pending:
            if not any(t['status']=='done' for t in tasks) or any(t['status']=='failed' and not t.get('resolved_by_task_id') for t in tasks):
                raise RuntimeError('No successful delivery')
            return
        task = pending[-1]
        if task.get('control_state') == 'paused':
            raise RuntimeError('Runtime paused: '+str(task.get('error')))
        retry_at = task.get('next_retry_at')
        if retry_at:
            remaining = (datetime.fromisoformat(retry_at)-datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                await sleep(min(30, remaining, max(0, deadline-clock())))
                continue
        await asyncio.to_thread(runtime.step, task['id'])
        current = store.get_task(task['id'])
        if current.get('state_version') == task.get('state_version'):
            await sleep(min(5, max(0, deadline-clock())))
            continue
        executed += 1
        report['steps'].append({'task_id':task['id'],'column':task['current_column'],
            'status':current['status'],'next':current['current_column'],
            'state_version':current.get('state_version'),'error':current.get('error')})
        save()
        print(json.dumps(report['steps'][-1],ensure_ascii=False),flush=True)
    # A final step may have delivered; do not fail a task that just reached done.
    tasks = store.list_tasks(project_id)
    if any(t['status']=='done' for t in tasks) and all(t['status'] in {'done','cancelled'} or t.get('resolved_by_task_id') for t in tasks):
        return
    raise RuntimeError(f'Evaluation exceeded {max_steps} executed runtime steps')


async def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--output-root',type=Path)
    source.add_argument('--resume',type=Path)
    parser.add_argument('--message-file',type=Path,help='Explicit Conversation instruction for this isolated resume')
    args = parser.parse_args()
    if args.message_file and not args.resume:
        parser.error('--message-file requires --resume')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report = None
    if args.resume:
        evidence, report = resume_evidence(args.resume)
        (evidence/('report-before-resume-'+stamp+'.json')).write_text(
            json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        report.setdefault('resumes',[]).append({'at':stamp,'prior_error':report.get('error')})
        for key in ('error','traceback'):
            report.pop(key,None)
    else:
        evidence = args.output_root.resolve()/('software-context-'+stamp)
        evidence.mkdir(parents=True,exist_ok=False)
    os.environ['DEVWERK_DB_PATH'] = str(evidence/'runtime.db')
    os.environ['LOG_FILE_ENABLED'] = 'false'
    from app.core.config import reload_settings
    reload_settings()
    logging.disable(logging.CRITICAL)
    from app.v1.capabilities import build_core_registry
    from app.v1.conversation import ConversationGateway
    from app.v1.policy import PlatformPolicyLoader
    from app.v1.runtime import WorkflowRuntime
    from app.v1.store import V1Store
    store = V1Store(str(evidence/'runtime.db'),registry=build_core_registry())
    store.register_platform_policy(PlatformPolicyLoader(ROOT/'DEVWERK.md').load())
    project = (store.get_project(report['project_id']) if report else
        store.create_project('Isolated software context regression','',str(evidence/'project')))
    gateway = ConversationGateway(store,store.registry)
    runtime = WorkflowRuntime(store,store.registry,'isolated-software-eval')
    report = report or {'project_id':project['id'],'evidence_root':str(evidence),'turns':[],'steps':[],'passed':False}
    def save():
        (evidence/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'evidence_root':str(evidence),'project_id':project['id']}),flush=True)
    async def turn(message):
        accepted = await gateway.submit(project['id'],message)
        if not await gateway.wait_for_idle(timeout=1200):
            raise TimeoutError('Conversation exceeded evaluation time bound')
        job = store.get_conversation_job(accepted['job']['id'])
        report['turns'].append({'job_id':job['id'],'status':job['status'],'error':job.get('error')})
        save()
        print(json.dumps(report['turns'][-1],ensure_ascii=False),flush=True)
        if job['status'] != 'succeeded':
            raise RuntimeError(job.get('error') or 'Conversation failed')
    try:
        if not args.resume:
            await turn(DISCUSS)
            if store.list_tasks(project['id']):
                raise AssertionError('Discussion prematurely created work')
            await turn(START)
            if len(store.list_tasks(project['id'])) != 1:
                raise AssertionError('Expected one complete software delivery task')
        elif args.message_file:
            await turn(args.message_file.read_text(encoding='utf8'))
        if not report.get('initial_delivery_passed'):
            await deliver(store,runtime,project['id'],report,save)
            report['initial_delivery_passed'] = True
            save()
        if not report.get('repair_requested'):
            original_ids = {t['id'] for t in store.list_tasks(project['id'])}
            await turn(REPAIR)
            if not {t['id'] for t in store.list_tasks(project['id'])}-original_ids:
                raise AssertionError('Repair did not create an executable successor/new task')
            report['repair_requested'] = True
            save()
        await deliver(store,runtime,project['id'],report,save)
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        import traceback
        report['traceback'] = traceback.format_exc()
    finally:
        await gateway.stop()
        report['tasks'] = store.list_tasks(project['id'])
        report['agent_runs'] = store.agent_runs(project_id=project['id'])
        save()
        print(json.dumps({'passed':report['passed'],'error':report.get('error'),'evidence_root':str(evidence)},ensure_ascii=False),flush=True)
    return report['passed']


if __name__ == '__main__':
    sys.exit(0 if asyncio.run(main()) else 1)
