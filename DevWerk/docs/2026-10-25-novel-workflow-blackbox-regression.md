# 小说工作流黑盒回归记录

## 目标与边界

- 用新 Project、真实 Provider 和 Web/API 同一路径验证 Conversation Agent 能通过自然语言讨论、创建 Workflow 与 Task，并由 Kanban 推进交付。
- 第一阶段写前两章样章；第二阶段基于实际产物讨论并扩展后续章节。由 Agent 决定合理的职责列和任务划分，测试者不提供 Tool 名称、JSON 参数或架构答案。
- 以数据库中的 Tool 回执、Workflow 修订、Task 状态和实际文件为证据；Agent 声称已完成不等于完成。
- 仅修复使建图、任务创建、推进或交付停止的通用 P0 缺陷。内容审美、非阻断的重试和模板优化单独记录，不扩展本轮代码范围。

## 执行步骤

1. 核对 8000 进程和健康状态。若需重启加载代码，遵守 AGENTS.md 的端口占用确认要求；保留已有测试 Project 和服务现场。
2. 新建 Project。以自然语言讨论作品定位、人物、篇幅和前两章目标；让 Conversation Agent 自行决定工作流和任务。记录建图、任务计划及授权 Tool 的首次调用结果。
3. Task 启动后，约每五分钟检查一次 Workflow/Task 状态及错误。遇到 `recovering`、`blocked`、持续无推进或失败时，按 Run、Tool 回执、Provider 请求与数据库事件定位原因；不要仅凭 UI 或答复猜测。
4. 前两章形成终态及实际文件后，再发自然语言请求继续后续章节，并观察 Agent 如何沿用或修订 Workflow、创建新 Task。
5. 对 P0 先写明根因和代码落点，再做最小修复、焦点单测与真实 Provider 回归。测试期间不引入小说专属的 DevWerk 逻辑。

## 待填写的实际证据

- Project：`prj_10e8f109f1a649efa7525978d47f9475`，目录 `data/projects/novel-blackbox-20261025-232735`。
- 第一轮纯讨论 Job `cjob_96d6812276bc4b85b20c81b128b1ea90` 成功，Agent 讨论故事冲突并提出三个设定问题。第二轮 Job `cjob_e8030d1ea9bd46d499c49e992fce9c33` 成功；Agent 自行用通用文件 Tool 写创作方案与前两章样章，没有创建 Workflow。这体现其自由选择直接执行，不作为 P0。
- 第三轮明确要求看板管理样章审阅与终稿，Job `cjob_e3bef615c8054a7dbee647471b68e304` 成功，Run `arun_071be22e0853454d827dba348a59e32f`。`workflow.create` **第一次即成功**，发布 `wfrev_b63073eecb1b43c1a5ef1cc41fb57c2e`，两项 Task `tsk_2e22091a0f404b9db3fb77327ddd78cb`、`tsk_46e8f85bf9e0479ebe755d68f58da94e` 已入图。列为 `review → revise（必要时）→ final → done`，失败边通往 `failed`；图由 Conversation Agent 自主建立。
- `task.plan.validate` 首次因 `plan/tasks/0/readiness` 缺失失败，第二次成功；`task.plan.save` 首次成功。Agent 随后试图在已排队状态读取 Worker，被规划租约拒绝一次；`conversation.reply` 两次引用当前轮不存在的 Tool Call ID 后第三次成功。均未阻断建图和派工，但作为调用稳定性问题记录。
- 两项样章 Task 均到达 `done`，实际存在 `终稿/01_雨停之前.md`（6,640 bytes）和 `终稿/02_三行签字.md`（5,750 bytes），另有各章审阅意见、修改说明、交付说明。`review → revise → final → done` 的实际路径由 ColumnRun 记录确认。
- 第二章 `final` 的一次 Column Agent Run 发生 `LLM_TIMEOUT`：MiniMax 请求等待 600 秒后连接读超时；Runtime 自动重试同列，下一次成功，Task 最终 `done`。这次错误未阻断工作流；需要继续观察是否在后续章节反复出现并导致恢复耗尽。
- 第 3–6 章扩展 Job `cjob_7ef1b7531be3498eba15f3ff02ffcba4` 成功，Run `arun_2a4fc80d39db44e89ca76ea0e709efdf`。Agent 检查旧图和终稿后，`conversation.execution.authorize`、`project.scope.revise`、`workflow.create` 均首次成功。Workflow 修订 3 新增 `draft` 入口，随后沿用 `review → revise（必要时）→ final → done`；四项新 Task 已创建，首项运行中。
- 扩展轮 `task.plan.validate` 首次把 `item/readiness/retry_scope/review_scope/workflow_fit` 放错到 `plan` 根，第二次成功；`task.plan.save`、`task.create` 和 `conversation.reply` 首次成功。这是计划参数生成稳定性问题，尚非工作流阻塞。
- 第 3–6 章终态与文件交付：运行中，待按五分钟间隔核对。
- 第 4 章 Task `tsk_219249ee787948c8b7f4e32e7feb6bf5` 在 `revise` 列连续三次 `LLM_TIMEOUT`（600 秒整包等待），夹一次 MiniMax `722 (1000)`，最终 `recovering`。WIP=1 使后续两章仍停在 `pending@draft`，这是本轮真实 P0 阻断。根因位于非流式 Anthropic 兼容 Provider 传输，而不是 Kanban 路由；通用流式修复与独立真实请求重放见 [Anthropic 流式传输方案](2026-10-26-anthropic-stream-transport-p0-plan.md)。旧 Project 保留作证据，修复后用新 Project 复测。
- P0 根因、修改、单测与复测：如发生则填写。
