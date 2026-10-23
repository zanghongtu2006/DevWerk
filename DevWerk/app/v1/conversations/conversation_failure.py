"""Render interrupted turns from receipts, including partially committed work."""

import re


def _concise_failure(message: str) -> str:
    """Keep actionable validation paths without echoing large submitted payloads."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    diagnostics = []
    for index, line in enumerate(lines[:-1]):
        if not re.fullmatch(r'[A-Za-z_][\w]*(?:\.(?:[A-Za-z_][\w]*|\d+))+', line):
            continue
        detail = lines[index + 1]
        if detail.startswith('Value error, '):
            detail = detail[len('Value error, '):]
        detail = detail.split(' [type=', 1)[0]
        if detail and not detail.startswith(('input_value=', 'For further information')):
            diagnostics.append(f'{line}: {detail[:240]}')
        if len(diagnostics) == 3:
            break
    if diagnostics:
        return '; '.join(diagnostics)[:700]
    first = lines[0] if lines else '操作失败'
    if "input rejected value at " in first and ": '{" in first:
        return first.split(": '{", 1)[0] + ': 参数类型不符；应按工具 schema 传对象，而非 JSON 字符串'
    return first.split(' input_value=', 1)[0][:500]


def failure_summary(store, project_id, ledger, error):
    created, reused = set(), set()
    workflows, plans, failures, worker_inputs = [], [], [], []
    for entry in ledger:
        result = (entry.get('facts') or {}).get('result') or {}
        output = result.get('output') or {}
        capability = entry.get('capability')
        if not entry.get('ok'):
            detail = (result.get('error') or {}).get('message')
            if detail:
                failures.append(f'{capability}: {detail}')
            continue
        if capability in {'loop.apply', 'workflow.publish', 'workflow.create'}:
            workflows.append(capability)
        if capability == 'task.plan.save':
            plans.append(capability)
        if capability == 'agent.worker.send' and isinstance(output, dict) and output.get('id'):
            worker_inputs.append(output['id'])
        if capability == 'task.create' and isinstance(output, dict):
            materialization = output.get('materialization') or {}
            created.update(materialization.get('created_task_ids') or [])
            reused.update(materialization.get('reused_task_ids') or [])
    started = sum(bool(store.runs(project_id, identity, limit=1)) for identity in created)
    parts = [f'本轮新建 {len(created)} 个任务，其中 {started} 个已启动。']
    if reused - created:
        parts.append(f'复用 {len(reused - created)} 个已有任务。')
    if workflows:
        parts.append('工作流已保存。')
    if plans:
        parts.append('任务计划已保存。')
    if worker_inputs:
        parts.append(f'已向 Worker 排队 {len(set(worker_inputs))} 条输入；是否消费需另查。')
    reason = _concise_failure(failures[-1] if failures else error)
    parts.append('本轮未完成：' + reason)
    if 'ConversationProtocolStalled' in error:
        parts.append('重复失败调用已停止。')
    return ''.join(parts)
