# P0：监督回合无法给 blocked Worker 交接诊断，恢复后继续空转

## 黑盒证据

Project `prj_b2ff5d6dae2243828f5d6ae34a2b3ccf` 的首个后端 Assignment 在 100 次模型请求后预算失败。通知 Job `cjob_d64c161b4acb45e0bbb210466588aa1d` 使用同一个 Project Conversation Agent Session，成功读取 Task、Workflow 和失败回执，随后成功 `task.retry`。但这个 Job 仍继续运行到接近普通用户回合的 100 次模型迭代、共 108 次工具调用，期间多次读取同样的文件/回执，又多次成功 retry（已由独立提交 `fa63c91` 修复准入与计数）。它两次尝试 `agent.worker.send`，均因 `SupervisionWorkerMismatch` 被拒；第二个后端 Assignment 因而主要依赖原 Worker 上下文，最终又在 100 次模型请求后预算失败。Task 停于 `recovering/blocked_runtime`。

第二次 Worker 确有进展（完整 Maven 测试从 9 failures/2 errors 下降至 6 failures/0 errors），但卡在锁定持久化。交付物 `AntiBruteForceService.createLockAndThrow` 从同类方法内部调用，标注的 `REQUIRES_NEW` 不经 Spring 代理生效，抛异常会回滚所写锁记录。主 Agent 在监督回合读过相关失败回执，却没有可靠的渠道把分析交给等待续跑的 Worker。

## 代码原因

1. `ConversationIntentRepository._supervision_denial` 对 `agent.worker.send` 要求目标 Assignment `status='running'`。预算失败时原 Assignment 恰是 `blocked`，当前 Task 为 `recovering`，所以通知中无法先交接诊断。
2. `AgentRepository.validate_message_target`、`send` 与 `assign` 已允许对当前 blocked Assignment 排队输入，并在下一 Assignment 创建时转移。这是现成的生命周期能力，控制面检查过严。
3. `AgentExecutionRunner.run` 把监督 Run 当普通对话处理。成功的 `task.retry/reopen` 是该通知的完成事实，后续用户通知由 `ConversationGateway._process` 从当前 Task 状态确定性生成；继续调用模型以求 `conversation.reply` 不增加可交付效果，却可消耗完整 100 轮预算。

## 修改方案

1. `app/v1/repositories/conversation_intent_repository.py::_supervision_denial`：允许同一失败 Task 当前 Column 的**最新** blocked Assignment 接收 `agent.worker.send`，前提 Task 仍 `recovering`、Worker/Assignment ID 精确匹配、Requirement/Grant 有效；running Assignment 的既有路径保留。禁止对已 superseded 的旧 Assignment、其他 Task/Column、pending/failed/cancelled Assignment 投递。AgentRepository 的实际目标校验仍是最终裁决。MailBox 不承载输入正文，只作为唤醒和交付通知。
2. `app/v1/conversation.py` 的监督指令说明：先读取真实失败回执，若有具体诊断可通过 `agent.worker.send` 给 blocked Worker 排队，之后只进行一次同 Task retry/reopen；不要无限重跑只读工具。消息必须是证据支持的建议，不能把主 Agent 推测写成已验证结论。
3. `app/v1/agent_runner.py::AgentExecutionRunner.run`：监督 Run 的某一工具批次一旦产生成功的 `task.retry/reopen` 回执，立即在该批次所有回执持久化后构造由 Runtime 验证的 `work_result` 报告（真实 Task ID/Tool call ID），结束 Run。`ConversationGateway` 沿用当前 Task 状态确定性呈现用户通知。此停止条件只作用于事件监督 Run，绝不截断用户发起的自由 Conversation 回合；失败的 retry 可由模型更正。已经成功的 Worker 输入仍为排队状态，随 Worker 续跑消费。一次监督 Job 不因模型持续闲逛耗尽普通会话预算。
4. 对未发生恢复的监督回合增加较小的独立迭代/工具上限（建议 20/50，均不高于全局上限）；到上限时保持失败事实并确定性通知用户，不再自动重试。需核对现有执行策略字段与回归，不把该上限施加到用户普通回合或 Column Worker。

## 验证

`tests/test_project_supervision.py` 增加真实 blocked Assignment、同 Task/latest Column 消息投递与后继 Assignment 消费；跨 Task/旧 Assignment 被拒。模型脚本先 `task.inspect`、`agent.worker.send`、`task.retry`，断言监督 Run 在成功 retry 后结束，不再进行下一次模型请求，最终 Job/用户通知仍显示 Task `pending` 且回执可查。另覆盖失败 retry 可继续更正、无效果监督达到独立预算时原失败通知仍送达。扩大运行 Conversation、Assignment、Context 测试。旧 Project 留作证据，新代码加载后另建 Project 复测。

## 实施进度

已将 blocked Worker 输入准入限定为同 Task、当前 Column 最新 Assignment、Task `recovering` 且 Assignment `blocked`；运行中 Worker 的原通路保留。成功 retry/reopen 的真实回执经现有 `render_report` 验证后，`AgentExecutionRunner` 当批结束监督 Run；未恢复的监督 Run 至多 20 次模型迭代/50 次工具调用，事件回合墙钟上限 600 秒，普通用户回合和 Column Worker 仍使用原预算。通知由 Gateway 读取当前 Task 状态渲染。专项监督测试 **8 passed**，包含 blocked Worker 输入排队并在新 Assignment 中续用、成功恢复后不再进行第三次模型请求、无效果时有界停止并保留原失败通知。Conversation、Assignment、Context 扩大回归 **131 passed**。原 8000 服务尚未重启，本次真实黑盒仍反映修复前行为。
