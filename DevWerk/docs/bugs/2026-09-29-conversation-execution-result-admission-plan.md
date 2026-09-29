# Conversation 执行回合的空结果被误报为成功

## 现场

修复动态授权投影后，Workflow 修订 Job `cjob_8088d30d3a1f444db3dfa3e4d8eef7de` 已取得 `phase=execution` Grant，但模型重复调用已完成的 `conversation.turn.resolve`，又把 resolve 与只读 `workflow.inspect`、`workflow.plan.list` 混在同一批工具调用，Runtime 按现有协议拒绝整批，没有任何 Workflow Plan/Revision 持久化。最后模型以 `conversation.reply(mode=work_result, task_ids=[已有暂停 Task])` 成功结束，Job 标为 `succeeded`；前端只看到“Task 已暂停”，用户请求的 Workflow 修订并未发生。这是执行回合完成准入缺口，不能把 `Job succeeded` 误当目标完成。

## 最小修复

在 `app/v1/conversation_report.py::render_report` 的 `work_result` 分支，仅当**当前用户 Job 的持久 turn contract 为 `phase=execution`** 时，要求本 AgentRun 至少存在一项已成功的真实执行副作用回执（registry `side_effect_kind` 为 `write`、`process` 或 `control`）。`conversation.turn.resolve`、`conversation.reply` 是 session_control，读取历史 Task、只读 inspect/list 都不能满足；只引用旧 Task ID 不再足以把执行请求报告为 work_result。明确告知 Agent 继续执行实际动作，或者用 blocked 报告真实拒绝。`act=status`/讨论回合引用已有 Task 的现状报告保持可用；已有成功的 `task.retry`、`agent.worker.send`、Plan/Workflow 写操作仍可基于真实回执报告，渲染只列事实，不信任模型叙述。

不添加自然语言目标匹配或特定 Workflow 白名单，不改变 Grant、工具目录、mailbox、Worker 生命周期。这个准入只能保证“执行回合确有副作用”，不能保证该副作用一定是用户请求的那一种；最终仍需按具体 Plan/Revision 事实验收。该限制需在代码和对话中明确，不宣称已解决模型对工具协议的所有误用。

单测覆盖：execution Grant + 只读操作 + 旧 Task ID 的 work_result 被拒；status 回合 + 旧 Task ID 仍可报告；execution Grant + 当前 Run 成功 `agent.worker.send`（或其他 control）可报告其排队事实；不同 Project 证据仍按原规则拒。随后运行 Conversation 回归，再在全局空闲时加载服务并重试 Workflow 修订；真实成功以 `workflow.plan.save` 和 `workflow.publish` 回执及新 revision 为准。

## 实施与验证

`conversation_report.py::render_report` 已在 execution 用户回合的 `work_result` 分支核查本 Run 真实成功的 write/process/control 回执；状态/讨论回合仍可引用旧 Task。提示词合同同步说明这一条。回归用真实 `project.files.write` 和持久化调用回执验证允许路径，仅旧 Task 路径被拒；相关 Conversation 测试 **37 passed**。旧 Job `cjob_8088...` 的真实轨迹是 resolve 成功→重复 resolve 被拒→与只读工具混合批次被拒→仅凭旧 Task 结果成功结束；此补丁能阻止最后一步误报，但不能代替模型正确调用 Workflow 写工具。待安全重启后黑盒验证。

全局空闲重启后的 8000 健康检查 `ok`，监听 PID 18820。为避免单回合同时生成 Plan 和完整 Workflow 导致上下文混杂，正式 Conversation API Job `cjob_7769bf0e524e4d91962979ab34e6044a` 只要求保存一个修订 Workflow Plan，后续发布另行一回合；API 受理不代表保存成功。
