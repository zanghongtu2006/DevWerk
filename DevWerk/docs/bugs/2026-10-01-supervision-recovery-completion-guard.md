# P0：监督回合将 Worker 输入排队误报为 Task 已恢复

第八次黑盒 Project `prj_7bf8884364104924b67987f894163a96` 的后端 Column 因一小时 Agent wall-clock 预算耗尽，Task 进入 `recovering/paused`。自动监督 Job `cjob_71902ad9321242d8b210e95b2ecebf9a` 检查失败后成功调用 `agent.worker.send`，但未调用 `task.retry`；仍以 `conversation.reply(mode=work_result)` 成功结束。用户通知按 Task 真实状态显示“需要处理”，然而 Job 的 `business_outcome=completed`，且 Worker 输入不会自行解除暂停。

根因是 `app/v1/conversation_report.py::render_report` 把成功的 `agent.worker.send` 视为足够的工作结果，没有对监督 Task 是否仍为 `recovering/paused` 做完成校验。既有提示要求“先发输入，再 retry”，但该规则依赖模型遵从。

最小修复：仅在 `phase=supervision`、`mode=work_result` 且被监督 Task 仍为 `recovering/paused` 时拒绝最终回复，并明确指向 `task.retry`（`failed` Task 指向 `task.reopen`）。允许 Agent 继续选择 `mode=blocked` 报告无法恢复的真实失败，避免强迫无效重试。保持 Mailbox 为被动通知、Worker 输入为单独工具；不自动重试、不修改 Workflow 图或 Task 生命周期。加入回归：先发送 Worker 输入、立即汇报 work_result 被拒；同一回合随后 `task.retry` 成功，Task 可调度。独立提交。
