"""Real-provider multi-turn regression in an isolated DB/workspace (never production data)."""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIRST = '我要做一个可本地运行的前后端登录示例：前端 Vue，登录成功后显示简单欢迎页；后端 Java Spring Boot，需要注册、登录和暴力防刷，并有自动化测试。先不要启动开发，请先列出真正影响实现的关键决策，并建议一个不过度设计的交付流程。'
SECOND = '采用无状态 JWT，只做短期 access token，不做 refresh token；登录连续失败5次锁定15分钟，成功后清零；注册使用用户名和密码，不做邮箱验证码；前端 Vue 3 + Vite + Element Plus。还需要决定用户如何持久化，以及防刷应该按用户名、IP还是组合判断。请给出适合本地演示但可测试的最小方案。'
START = '按你刚才建议的最小方案开始开发。其余不影响上述行为的实现细节由你决定，请创建一个完整的软件交付 Task。'
VARIANTS = [
    '这些选型都可以，数据库你有什么建议？', '可以用 Element Plus，先接着讨论接口。',
    '继续。', '接着说。', '如果我说开始开发，你会先做什么？',
    '日志里写着“开始开发”，请解释这句话，仍然不要执行。',
    '先别实现，比较两种数据库。', '这里只讨论架构，列出最小模块。',
    '先给我方案，我 review 后再改。', 'Use JWT. Keep this discussion-only; how should persistence work?',
    '确认 JWT 和用户名注册，不代表现在启动。防刷按什么维度？',
    '方案先保留，讲讲自动化测试怎么安排。', '好，继续介绍方案。',
    '你说确认后立即开始，但我还有持久化问题需要讨论。',
    '请给出“开始开发”按钮的说明文案，不要启动开发。',
    '先讨论第二期，第一期如果已经在做就照常。这里还没有批准第二期。',
    '我还没决定 H2 还是 SQLite，请推荐。', '不用写文件，把方案直接回复给我。',
    '请解释为什么不能把八个 Column 再当成八个 Task。',
    '先不要实施。假设需求通过，我们会如何交付？',
]


async def run_case(root, name, messages, execute, step_entry=False):
    from app.v1.store import V1Store
    from app.v1.capabilities import build_core_registry
    from app.v1.conversation import ConversationGateway
    from app.v1.policy import PlatformPolicyLoader
    case = root / name
    case.mkdir()
    store = V1Store(str(case/'runtime.db'), registry=build_core_registry())
    store.register_platform_policy(PlatformPolicyLoader(ROOT/'DEVWERK.md').load())
    project = store.create_project(name, '', str(case/'project'))
    gateway = ConversationGateway(store, store.registry)
    results = []
    try:
        for index, message in enumerate(messages):
            accepted = await gateway.submit(project['id'], message)
            if not await gateway.wait_for_idle(timeout=900):
                raise TimeoutError('Conversation did not finish within 900 seconds')
            job = store.get_conversation_job(accepted['job']['id'])
            tasks = store.list_tasks(project['id'])
            plans = store.list_task_plans(project['id'])
            try:
                workflow = store.get_workflow(project['id'])
            except KeyError:
                workflow = None
            invocations = store.tool_invocations(project['id'], job['agent_run_id'], hydrate_payloads=True) if job.get('agent_run_id') else []
            discussion = index < len(messages)-1 or not execute
            # .devwerk contains host-rendered memory views, not implementation output.
            files = [str(p.relative_to(case/'project')) for p in (case/'project').rglob('*')
                     if p.is_file() and p.relative_to(case/'project').parts[0] != '.devwerk']
            requirements = store.agents.list_requirements(project['id'])
            effects = [v for v in invocations if v['ok'] and store.registry.side_effect_kind(v['capability']) in {'write','process','control'}]
            contract = store.intents.contract(job)
            ok = job['status'] == 'succeeded' and (not (tasks or plans or workflow or files or requirements or effects)
                and contract['phase'] == 'discussion' if discussion else len(tasks) == 1 and contract['phase'] == 'execution')
            # This fixture asks for one delegated software delivery Task. The
            # main Agent prepares its baseline; Column Workers own implementation.
            baseline_paths = {t['input'].get('requirements_path') for t in tasks}
            direct_writes = [v['arguments'].get('path') for v in effects if v['capability'] == 'project.files.write']
            if not discussion:
                ok = ok and all(path in baseline_paths for path in direct_writes)
            results.append({'index':index, 'ok':ok, 'job':job, 'tasks':tasks, 'requirements':requirements,
                            'invocations':invocations, 'files':files, 'contract':contract, 'direct_write_paths':direct_writes})
            (case/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(json.dumps({'case':name,'turn':index+1,'ok':ok,'status':job['status'],'tasks':len(tasks),
                              'error':job.get('error'), 'evidence':str(case/'results.json')},ensure_ascii=False), flush=True)
            if not ok:
                break
        if execute and len(results) == len(messages) and all(x['ok'] for x in results):
            accepted = await gateway.submit(project['id'], '现在进展如何？只汇报当前状态。')
            if not await gateway.wait_for_idle(timeout=900):
                raise TimeoutError('Status turn did not finish')
            status_job = store.get_conversation_job(accepted['job']['id'])
            before_ids = [t['id'] for t in tasks]
            status_calls = store.tool_invocations(project['id'],status_job['agent_run_id'],hydrate_payloads=True)
            status_ok = (status_job['status'] == 'succeeded' and [t['id'] for t in store.list_tasks(project['id'])] == before_ids
                and store.intents.contract(status_job).get('act') == 'status'
                and not any(v['ok'] and store.registry.side_effect_kind(v['capability']) in {'write','process','control'} for v in status_calls))
            (case/'status.json').write_text(json.dumps({'ok':status_ok,'job':status_job},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':name,'status_ok':status_ok}),flush=True)
            if not status_ok:
                return False
            if step_entry:
                from app.v1.runtime import WorkflowRuntime
                runtime = WorkflowRuntime(store, store.registry, 'isolated-eval-worker')
                try:
                    await asyncio.to_thread(runtime.step, tasks[0]['id'])
                    current = store.get_task(tasks[0]['id'])
                    step_ok = current['current_column'] == 'domain_design'
                    evidence = {'ok':step_ok,'task':current,'runs':store.runs(project['id'],current['id'])}
                except Exception as exc:
                    step_ok, evidence = False, {'ok':False,'error':str(exc)}
                (case/'entry.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
                print(json.dumps({'case':name,'entry_ok':step_ok}),flush=True)
                if not step_ok:
                    return False
    finally:
        await gateway.stop()
    return len(results) == len(messages) and all(x['ok'] for x in results)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=['original','variants'], default='original')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--discussion-only', action='store_true')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--step-entry', action='store_true')
    parser.add_argument('--case-index', type=int, help='Run one variation by zero-based index')
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='devwerk-intent-eval-'))
    os.environ['DEVWERK_DB_PATH'] = str(root/'unused-global.db')
    os.environ['DEVWERK_USAGE_TRACKING'] = 'false'
    os.environ['LOG_FILE_ENABLED'] = 'false'
    from app.core.config import reload_settings
    reload_settings()
    logging.disable(logging.CRITICAL)
    from app.core.config import settings
    from app.v1.conversation_intent import TurnResolution, TURN_INSTRUCTION
    from app.v1.conversation_report import REPORT_INSTRUCTION
    cfg = settings().get_llm_config('conversation')
    metadata = {key:cfg.get(key) for key in ('model','protocol','temperature','max_tokens','effort_level')}
    metadata['sha256'] = {name:hashlib.sha256(content).hexdigest() for name,content in {
        'policy':(ROOT/'DEVWERK.md').read_bytes(), 'loop':(ROOT/'loops/ddd-software-delivery/loop.json').read_bytes(),
        'turn_instruction':TURN_INSTRUCTION.encode(), 'reply_instruction':REPORT_INSTRUCTION.encode(),
        'resolution_schema':json.dumps(TurnResolution.model_json_schema(),sort_keys=True).encode(),
    }.items()}
    metadata['source_sha256'] = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT/'app/v1').rglob('*.py'))}
    (root/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(json.dumps({'evidence_root':str(root)},ensure_ascii=False), flush=True)
    all_cases = []
    for repeat in range(args.repeat):
        cases = [(f'original-{repeat}', [FIRST, SECOND] + ([] if args.discussion_only else [START]), not args.discussion_only)] if args.suite == 'original' else [
            (f'variant-{index}-{repeat}', [FIRST, value], False) for index,value in enumerate(VARIANTS)
            if args.case_index is None or index == args.case_index]
        all_cases.extend(cases)
    semaphore = asyncio.Semaphore(max(1,args.workers))
    async def bounded_case(case):
        async with semaphore:
            return await run_case(root,*case,step_entry=args.step_entry)
    outcomes = await asyncio.gather(*(bounded_case(case) for case in all_cases))
    summary = {'passed':sum(outcomes),'total':len(outcomes),'evidence_root':str(root)}
    (root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
    return all(outcomes)


if __name__ == '__main__':
    sys.exit(0 if asyncio.run(main()) else 1)
