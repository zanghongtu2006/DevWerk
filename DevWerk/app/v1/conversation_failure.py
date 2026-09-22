"""Render interrupted turns from receipts, including partially committed work."""


def failure_summary(store, project_id, ledger, error):
    created, reused = set(), set()
    workflows, plans, failures = [], [], []
    for entry in ledger:
        result = (entry.get('facts') or {}).get('result') or {}
        output = result.get('output') or {}
        capability = entry.get('capability')
        if not entry.get('ok'):
            detail = (result.get('error') or {}).get('message')
            if detail:
                failures.append(f'{capability}: {detail}')
            continue
        if capability in {'loop.apply', 'workflow.publish'}:
            workflows.append(capability)
        if capability == 'task.plan.save':
            plans.append(capability)
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
    reason = (failures[-1] if failures else error).splitlines()[0][:500]
    parts.append('本轮未完成：' + reason)
    if 'ConversationProtocolStalled' in error:
        parts.append('重复失败调用已停止。')
    return ''.join(parts)
