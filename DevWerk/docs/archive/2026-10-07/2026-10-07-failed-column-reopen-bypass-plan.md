# 失败列被 Task reopen 跳过：最小修复方案

## 现场证据

全新 Project `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 的 Task `tsk_6d60783ed2b84d0ebd552feb60b95c61` 顺序完成需求、设计、后端列。`frontend_development` Worker 的 `npm.cmd install` 因当前 Windows npm 缓存目录权限返回 EPERM，构建没有通过。Worker 记录 `runtime.behavior` Feedback，错误地把责任列写成未来的 `system_test`，随后以 `column.complete(outcome=execution_failed)` 将 Task 送入 `failed`。这是诚实的失败声明，不能当作前端完成。

失败 Mailbox 通知触发 Conversation Agent 监督 Job `cjob_e0c732f538c24da8a7b812e695842496`。它调用 `task.inspect` 和 `task.feedback.list` 后，使用 `task.reopen(column_key=system_test)`，Runtime 接受并直接开始 `system_test`，此时前端列仍为 `failed`，Vue 构建没有成功证据。`v1_events` 中 `task.failed` 1413、`task.reopened` 1423、`column.started(system_test)` 1428 构成完整证据链。

`app/v1/services/recovery_manager.py::reopen_task()` 当前只检查目标是 Workflow 内非终态列，不检查目标是否已在失败前经历。这允许监督 Agent 在失败后跳到未访问的未来列，绕过看板图上的必经步骤。`app/v1/capabilities.py` 的 `task.reopen` 描述也误导为“任意声明的非终态列”。

补充代码核查发现同一管理器的 `retry_task()` 也允许显式选择任意非终态列。当前旧代码下误跳到 `system_test` 的 Task 因服务重启成为 `pending/paused`，如不同时约束 `task.retry`，它仍可从暂停态再次跳过失败前端列。两条恢复入口须共用“不跳过未修复失败列”的边界。

## 修改边界

1. 在 `RecoveryManager.reopen_task` 中以当前 Task 的终态 `task.failed` 事件定位失败 Column Run。恢复目标只允许该失败列，或 Workflow 入口（完整重跑）；若失败事件没有对应 Column，则只允许入口。任何其他目标均在写事务前拒绝，并返回清楚的允许目标。这样既可在原列续修，也不会通过任意已访问/未访问列跳过失败列；不改变状态机或 Workflow Definition。
2. 在 `RecoveryManager.retry_task` 对非终态重试施加同样的边界：没有未修复失败时，仅可留在当前列、回到入口或回到本 Task 已运行过的列；若存在尚未被该列后续成功 Run 修复的失败列，仅可回到那一列或入口。阻止暂停态被用作任意跳列入口，同时允许旧错误状态回退到前端修复。
3. 更新 `task.reopen`、`task.retry` 工具说明，明确不能跳过失败阶段。Mailbox 仍仅承载通知，不增加调度权。
4. 补充回归：前端列失败后，`task.reopen(system_test)` 被拒且 Task/Run/事件不变；`task.reopen(frontend_development)` 成功；原有入口重开测试继续通过。暂停 Task 对未访问后续列的 `task.retry` 被拒；遗留的“前端失败、当前已误在系统测试”状态只允许回到失败前端列或入口。
5. 单元测试通过后，仅为加载代码重启 DevWerk。当前测试 Project 保留为证据，通过自然语言监督请求或自动监督恢复到前端列，并观察 Vue 构建、后续 HTTP 验收直到 Task 终态。若当前运行已出现错误通过，不把该结果视为通过。

第一步代码已完成：`RecoveryManager.reopen_task` 用当前 `task.failed` 事件关联的失败 Column Run 确定唯一可局部恢复的列；入口完整重跑仍允许。`task.reopen` 工具描述同步收窄。新增回归先构造三列 Workflow，在中列失败后验证跳到未执行的后列被拒且 Task/Run/终态事件不变，再验证恢复失败列成功；既有入口重开回归不变。相关三份测试文件共 **68 passed**。服务已为加载第一步修复重启，旧代码下误跳过的 `system_test` 被启动恢复逻辑保留为 `pending/paused`；在执行人工/Agent 重试前必须补齐第二步的 `task.retry` 边界。

第二步代码已完成：`RecoveryManager.retry_task` 限制未访问目标及未修复失败列；工具说明同步更新。新增回归验证失败列尚未修复时，不能从暂停态重试到后续列，允许退回失败列或 Workflow 入口。扩展后的相关测试仍为 **68 passed**，`git diff --check` 通过。仅为加载完整补丁再次重启 DevWerk，健康接口返回 `ok`；原 Task 保持 `pending/paused`。通过公开 Conversation API 发送自然语言修复请求（消息 94，Job `cjob_6787724a13d742aa9e33fe25e3e24dc7`），要求从失败前端重做、使用项目内可写 npm 缓存、确认真实构建后再推进。后续每约五分钟观察真实工具回执和 Task 路由。

## 验收边界

Task 必须重新经过失败的前端列并取得真实构建证据，再沿声明图走联调、系统测试、交付与验收。终态 `done` 本身不足以证明本次测试通过。
