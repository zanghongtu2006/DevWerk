# Conversation 跨回合上下文预算回归

2026-10-23。新小说 Project `prj_5335da2c4f63416cb8a8aefe888914b8` 第三轮确认开写时，在首次 Provider 请求前发生 `ContextExhausted`。该回合没有创建 Workflow 或 Task。

## 根因与边界

本轮请求的 Tool schema 为 187,527 bytes，系统消息 6,815 bytes，用户消息 18,917 bytes；预估 131,392 tokens，输入预算 129,025。前两轮同一 Conversation Session 的 Provider 实际输入为 43,393 和 45,628 tokens，但两轮 `agent.context_budget` 的 ratio 均为冷启动的 0.5。`ContextManager` 只在一次 Agent Run 内调用 `observe` 校准；下一用户回合建立新 Run，又因 `scope` 回退为 run_id、且未发生 checkpoint 写入而重置到 0.5。强类型 Tool schema 的体积使冷启动估算在第三轮越过硬预算。该错误是本地预检拒绝，不是 Provider 的窗口拒绝；不是小说 Loop 或 Task 参数错误。

## 最小修复

上下文预算由 `ContextManager` 负责，不改 Workflow、Task、Tool 协议。为已有 Agent Session 增加独立的 Provider 用量校准记录，以 `(project_id, session/assignment scope, model, protocol)` 隔离；成功 Provider 响应后保存已带安全余量的 ratio，后续 Run 读取。同一 Session 的 checkpoint scope 也应使用稳定 session ID；无 Session 的测试/一次性 Run 仍使用 run_id。checkpoint 只承载压缩历史，不拿它冒充用量记录。模型或协议变化回到保守冷启动。

回归应覆盖连续两个 Conversation Run：第一轮实测用量明显低于字节冷估算，第二轮在无 checkpoint 时继承校准并能容纳同一大型 Tool schema；不同 Session/模型不得复用。之后重启 8000 服务，在新 Project 重试自然语言确认开写，核对真实 Workflow/Task 回执与后续推进。测试未完成前不宣称通过。

## 实际验证与独立阻塞

- `tests/unit/agent/test_context_lifecycle.py`：21 passed。覆盖无 checkpoint 的跨 Run 校准复用、Session 隔离与模型隔离。
- 修复后服务在 8000 健康运行。全新 Project `prj_6fde95ef827b4add8c6db2c6b6727fa1` 经过三轮自然语言讨论：前两轮讨论并确认大纲，第三轮要求开写。第三轮成功调用 Provider 多次，预算 ratio 约 0.28–0.31；数据库 `v1_context_usage_calibrations` 留有该 Conversation Session 的 `MiniMax-M2.7/anthropic` ratio 记录。原 `required_context_overflow` 未复现。
- 第三轮成功调用执行授权、`loop.apply`，并发布 Workflow `wfrev_072902b73f7144c6b51909584cb35fde`，但 0 个 Task。`task.plan.validate` 曾报告 `chapter_number` 以字符串提交，Loop 准入无法比较 `'1'` 与整数 `6`；之后又出现其他无效参数以及重复只读调用，最终 Job 因 `ConversationProtocolStalled` 失败。这是独立的 TaskPlan/Agent 参数稳定性阻塞；本次上下文校准补丁没有改其协议，小说端到端交付不能报通过。
- 邻近的存储与 Conversation 合并测试在本环境长时间无输出，已中断；不计入通过数。保留失败 Project、日志与数据库供后续诊断，未替 Agent 创建 Task。
