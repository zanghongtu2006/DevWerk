# P0：同一 Conversation Run 的规划状态快照过期

状态：方案先行；证据来自 `prj_e19f8da8a7264d95aebae7d5eb5ff7c3` 第七轮授权 Job 与代码审查。该 Job 仍在运行时不重启服务，也不修改测试 Project。只修主 Agent 的当前 Project 状态可见性，以 Task 创建和 Kanban 推进为目标。

## 事实与根因

`app/v1/conversation.py::_process` 在启动 Agent Run 时把 `active_workflow`、`workflow_plans`、`task_plans`、`tasks` 写入 `authoritative_project_state`。当时没有 Workflow/Plan。之后同一 Run 已成功 `loop.apply`、`task.plan.validate(valid=true)`、`task.plan.save`；但 `app/v1/context_manager.py::ContextManager._refresh_conversation_authority` 每次 Provider 请求只更新 `turn_contract/work_intent/requirement/allowed`，不刷新刚刚变化的 Workflow/TaskPlan/Task。Run 的权威用户消息因此继续声称初始 Workflow/Plan 状态为空。模型多次重试 `loop.apply`、重复读 Loop/Scope、重新写需求基线并使已保存计划的文件收据过期，还未进入 Task。授权重放已成为幂等只读操作，不再引发协议停机，但过期的权威快照仍使交付路径不稳定。

这不是要引入新的状态机或确定性规划器。Agent 仍选择工具和工作流；Runtime 只保证它看到的“当前 Project 状态”确实当前，过去工具回执和记忆依然是历史证据。

## 最小修复

在 `ContextManager._refresh_conversation_authority` 保留现有授权/Requirement 刷新，追加同 Project 的轻量可核对快照：

- `active_workflow`：当前 revision id、revision、name、requirement_id/revision、definition_hash、workflow_plan_id；不存在则 null。
- `workflow_plans`：最近 5 个 id/时间/content_hash。
- `task_plans`：最近 5 个 id、workflow_revision_id、requirement_id、created_at，以及从冻结 plan 提取的 `proposed_task_refs`（`task.create` 需要它）；不注入完整 TaskPlan 或重复工具日志。
- `tasks`：现有摘要限额内的当前 Task 列表；仅失败/恢复 Task 附带既有 failure_detail。

每次 Provider 请求对投影副本刷新，原始持久 Session 消息不被改写；仍让 ContextManager 压缩旧工具批次。无额外网页字段或 Mailbox 通信；Project/Requirement 边界沿用原校验。若模型依旧重试 `loop.apply`，Dispatcher 的已有单 Workflow 拒绝仍保留。

`tests/test_conversation_contract.py` 注入一个模型在同 Run 中先 apply Loop、保存 TaskPlan，再观察下一请求里的 authoritative_project_state，断言 Workflow 和可用于 `task.create` 的 Plan ref 已更新，且工具权限不变；随后用该 ref 创建 Task。没有 Workflow 时维持 null。刷新作用于 ContextManager 的消息副本，不原地改写持久 Session。相关 Conversation/Context 测试通过后独立提交。

## 实测门槛

当前 Project 先观察终态；只有在运行 Job 退出后重启服务。新 Project 复用原七轮用例，检查成功保存 TaskPlan 后不因旧快照重新规划，能创建 Task、推进 Column。若仍阻塞，记录实际回执再作最小修复；不扩展无关通用能力。
