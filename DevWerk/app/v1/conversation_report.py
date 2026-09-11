"""Validate structured final replies against the current turn's durable facts."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['discussion', 'proposal', 'work_result', 'blocked']
    message: str = ''
    task_ids: list[str] = Field(default_factory=list, max_length=100)
    tool_call_ids: list[str] = Field(default_factory=list, max_length=100)


REPORT_INSTRUCTION = (
    'Finish each turn with a JSON object (no Markdown fence) using mode, message, task_ids, tool_call_ids. '
    'mode=discussion or proposal: message contains discussion/questions or proposed future work only, never completed actions or task states. '
    'mode=work_result: cite real task_ids and/or successful tool_call_ids from this turn; Runtime renders the result from stored facts, ignoring message. '
    'mode=blocked: cite failed tool_call_ids when available; explain only the remaining question in message. '
    'Do not invent IDs. Creating a scope/plan is not creating or running Tasks. No plain-text execution claims are published. '
    'Example discussion: {"mode":"discussion","message":"Which character should narrate the next chapter?"}. '
    'Example result: {"mode":"work_result","tool_call_ids":["the-actual-scope-revise-call-id"],"task_ids":["the-real-task-id"]}.'
)


def render_report(store, project_id, run_id, text):
    report = ConversationReport.model_validate_json(text)
    invocations = {str(i['tool_call_id']): i for i in store.tool_invocations(project_id, run_id, hydrate_payloads=True)}
    selected = []
    for identity in dict.fromkeys(report.tool_call_ids):
        item = invocations.get(identity)
        if item is None:
            raise ValueError('The report references a tool call absent from this turn: '+identity)
        if report.mode == 'work_result' and not item['ok']:
            raise ValueError('A failed operation cannot support a successful work report: '+identity)
        if report.mode == 'blocked' and item['ok']:
            raise ValueError('A successful operation is not a failure receipt: '+identity)
        selected.append(item)
    tasks = []
    for identity in dict.fromkeys(report.task_ids):
        task = store.get_task(identity)
        if task['project_id'] != project_id:
            raise ValueError('The reported Task belongs to a different project')
        tasks.append(task)
    if report.mode in {'discussion', 'proposal'}:
        if selected or tasks or not report.message.strip():
            raise ValueError('Discussion/proposals require text and no execution claims; use work_result for facts')
        rendered = report.message.strip()
    elif report.mode == 'work_result':
        if not selected and not tasks:
            raise ValueError('A work result requires a real Task or a successful tool receipt')
        # A task.create receipt may materialize a whole plan. Report all actual
        # tasks created by that plan, without claiming they have started.
        for item in selected:
            output = item['result'].get('output')
            if item['capability'] == 'task.create' and isinstance(output, dict) and output.get('task_plan_id'):
                tasks.extend(t for t in store.list_tasks(project_id) if t.get('task_plan_id') == output['task_plan_id'])
        tasks = list({t['id']: t for t in tasks}.values())
        labels = {'pending': '待执行', 'running': '执行中', 'waiting': '等待外部结果', 'done': '已交付', 'failed': '失败', 'recovering': '恢复中'}
        rendered_parts = []
        actions = {'project.scope.revise': '需求范围已修订', 'task.plan.save': '任务计划已保存', 'workflow.publish': '工作流已更新',
                   'loop.apply': '工作流已建立', 'requirement.create': '需求已建立', 'requirement.close': '需求已关闭',
                   'agent.worker.replace': '执行代理已完成交接', 'project.files.write': '项目文件已写入', 'system.files.write': '文件已写入'}
        for item in selected:
            if item['capability'] in actions:
                rendered_parts.append(actions[item['capability']])
        for task in tasks:
            status = labels.get(task['status'], task['status'])
            if task.get('control_state') == 'paused' and task['status'] not in {'done', 'failed'}:
                status = '已暂停'
            rendered_parts.append(f"{task['title']}：{status}")
        rendered = '；'.join(dict.fromkeys(rendered_parts))+'。' if rendered_parts else '所引用的操作已完成。'
    else:
        errors = [str((i['result'].get('error') or {}).get('message') or '操作失败') for i in selected]
        if not errors and not report.message.strip():
            raise ValueError('A blocked report requires the failure receipt or a concrete question')
        rendered = ('操作未完成：'+'；'.join(errors)+('。'+report.message.strip() if report.message.strip() else '')) if errors else report.message.strip()
    evidence = {'report': report.model_dump(), 'tasks': [{'id': t['id'], 'status': t['status'], 'control_state': t.get('control_state')} for t in tasks],
                'invocation_ids': [i['id'] for i in selected], 'rendered': rendered}
    return rendered, evidence
