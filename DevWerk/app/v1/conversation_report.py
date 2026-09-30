"""Validate structured final replies against the current turn's durable facts."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.v1.conversation_intent import TurnResolution, DiscussionDraftUpdate


class ConversationReply(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['discussion', 'proposal', 'work_result', 'blocked']
    message: str = ''
    task_ids: list[str] = Field(default_factory=list, max_length=100)
    tool_call_ids: list[str] = Field(default_factory=list, max_length=100)


class ConversationReport(ConversationReply):
    turn_resolution: TurnResolution | None = None
    draft_update: DiscussionDraftUpdate | None = None


REPORT_INSTRUCTION = (
    'You may answer ordinary discussion or status questions in natural language. '
    'No question-status update or boundary tool is required for a tool-free reply. '
    'Before business mutations, bind the current user execution request with conversation.execution.authorize. '
    'For completed work call conversation.reply alone with mode=work_result and real tool_call_ids/task_ids; '
    'Runtime renders committed facts. For a blocker use mode=blocked and actual failed receipts. '
    'Never claim a Workflow, Task or delivery was created merely because you proposed it.'
)


def render_report(store, project_id, run_id, text, *, execution_control=None):
    job = store.intents.job_for_run(run_id, project_id)
    if (job and job['trigger_kind'] == 'user' and store.intents.contract(job)['phase'] in {'unresolved', 'discussion'}):
        try:
            json.loads(text)
        except ValueError:
            # A tool-free answer is ordinary discussion, including the first
            # turn of a Project. Persist its hold without requiring a model
            # call just to update an intent ledger.
            invocations = store.tool_invocations(project_id, run_id, hydrate_payloads=True)
            effects = [i for i in invocations if i['ok'] and store.registry.side_effect_kind(i['capability']) in {'write','process','control'}]
            if text.strip() and not text.lstrip().startswith(('{','[','```json')) and not effects:
                if store.intents.contract(job)['phase'] == 'unresolved':
                    from app.v1.capabilities import CapabilityContext
                    ctx = CapabilityContext(project_id, store.get_project(project_id), store, agent_run_id=run_id,
                                            execution_control=execution_control,
                                            agent_instance_id=store.conversation_agent(project_id)['logical_id'])
                    state = store.intents.context(job)
                    store.intents.resolve(ctx, {'source_message_id': job['user_message_id'],
                                                'based_on_intent_revision': state['revision'],
                                                'act': 'discuss'}, final_only=True)
                rendered = '本轮仅讨论，未新建任务。\n\n' + text.strip()
                return rendered, {'report':{'mode':'discussion','message':text.strip()},
                    'tasks':[], 'invocation_ids':[], 'rendered':rendered, 'discussion_prose':True}
    if job and store.intents.contract(job)['phase'] != 'unresolved':
        try:
            raw = json.loads(text)
        except ValueError:
            raw = None
        if isinstance(raw, dict) and raw.get('turn_resolution') is not None:
            raise ValueError('The turn boundary is ALREADY COMMITTED. Omit turn_resolution entirely from the final reply; '
                             'return mode, message, task_ids, tool_call_ids; optionally draft_update for discussion data. For discussion use {"mode":"discussion","message":"your answer"}.')
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
        if (job and job['trigger_kind'] == 'user'
                and store.intents.contract(job)['phase'] == 'execution'
                and not any(item['ok'] and store.registry.side_effect_kind(item['capability']) in
                            {'write', 'process', 'control'} for item in invocations.values())):
            raise ValueError('An execution work result requires a successful write, process or control receipt from this turn; inspect/status of an existing Task is not execution')
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
                   'agent.worker.replace': '执行代理已完成交接', 'agent.worker.send': 'Worker 输入已排队',
                   'project.files.write': '项目文件已写入', 'system.files.write': '文件已写入'}
        for item in selected:
            if item['capability'] in {'workflow.acceptance.configure', 'workflow.acceptance.runners'}:
                output = item['result'].get('output') or {}
                if output.get('ready_for_task_plan') is False:
                    rendered_parts.append(
                        f"工作流验收配置已保存，Task 计划准入仍缺 {output.get('remaining_diagnostic_count', 0)} 项")
                else:
                    rendered_parts.append('工作流验收配置已保存，可验证 Task 计划')
            if item['capability'] in actions:
                rendered_parts.append(actions[item['capability']])
        for task in tasks:
            status = labels.get(task['status'], task['status'])
            if task.get('control_state') == 'paused' and task['status'] not in {'done', 'failed'}:
                status = '已暂停'
            task_text = f"{task['title']}：{status}"
            if task['status'] in {'failed','recovering'}:
                detail = store.feedback.failure_detail(project_id, task['id'])
                if detail and detail.get('column_key'):
                    task_text += f"；失败列：{detail['column_key']}"
                if detail and detail.get('reported_reason'):
                    task_text += '；Worker 报告：' + detail['reported_reason']
                if detail and detail.get('completion_rejection'):
                    task_text += '；完成拒绝：' + detail['completion_rejection']
                elif detail and detail.get('runtime_error') and not detail.get('reported_reason'):
                    task_text += '；' + detail['runtime_error']
            rendered_parts.append(task_text)
        rendered = '；'.join(dict.fromkeys(rendered_parts))+'。' if rendered_parts else '所引用的操作已完成。'
    else:
        errors = [str((i['result'].get('error') or {}).get('message') or '操作失败') for i in selected]
        if not errors and not report.message.strip():
            raise ValueError('A blocked report requires the failure receipt or a concrete question')
        if errors:
            from app.v1.conversation_failure import failure_summary
            ledger = [{'capability':i['capability'], 'ok':i['ok'], 'facts':{'result':i['result']}} for i in invocations.values()]
            rendered = failure_summary(store, project_id, ledger, errors[-1])
        else:
            rendered = report.message.strip()
    evidence = {'report': report.model_dump(), 'tasks': [{'id': t['id'], 'status': t['status'], 'control_state': t.get('control_state')} for t in tasks],
                'invocation_ids': [i['id'] for i in selected], 'rendered': rendered}
    job = store.intents.job_for_run(run_id, project_id)
    if report.turn_resolution and report.draft_update:
        raise ValueError('Use turn_resolution for an unresolved turn OR draft_update after resolution, not both')
    if report.draft_update:
        if report.mode not in {'discussion','proposal'}:
            raise ValueError('draft_update requires a discussion/proposal report')
        from app.v1.capabilities import CapabilityContext
        ctx = CapabilityContext(project_id, store.get_project(project_id), store, agent_run_id=run_id,
                                execution_control=execution_control,
                                agent_instance_id=store.conversation_agent(project_id)['logical_id'])
        evidence['draft_update'] = store.intents.update_draft(ctx, report.draft_update.model_dump(mode='json', exclude_unset=True))
    if job and job['trigger_kind'] == 'user' and store.intents.contract(job)['phase'] == 'unresolved':
        from app.v1.capabilities import CapabilityContext
        resolution = report.turn_resolution or TurnResolution(
            source_message_id=job['user_message_id'], based_on_intent_revision=store.intents.context(job)['revision'],
            act='discuss', constraint_change='set_hold')
        ctx = CapabilityContext(project_id, store.get_project(project_id), store, agent_run_id=run_id,
                                execution_control=execution_control,
                                agent_instance_id=store.conversation_agent(project_id)['logical_id'])
        evidence['turn_resolution'] = store.intents.resolve(ctx, resolution.model_dump(mode='json', exclude_unset=True), final_only=True)
    return rendered, evidence
