# 非阻断审查建议误入 Task Feedback 导致终态循环

## 证据与边界

Project `prj_9887385cae194d82a4a09632559d0108` 的 Task 已取得系统测试脚本真实退出码 0、A1–A9 9/9 通过，并完成 `delivery`。`integration_review` 先前调用三次 `task.feedback.record`，描述均称“非阻断”，但该工具的语义是持久化需要责任列重新验证的 Task 缺陷。`accept` 第一次提交 `column.complete delivered` 被 `terminal_blockers` 正确拒绝：例如 `feedback_987dcfd00c074530b89d088158c5debe` 缺少新的 `backend_development/runtime.behavior` 证据。验收 Worker 又把“已接受/已解决”描述调用 `task.feedback.record` 生成新记录，第二次 `delivered` 仍被拒绝，最后提交 `acceptance_failed`，有向图把 Task 送回 `integration_review`。

这不是旧 Project 影响新 Project，也不是 `settle` 改路由。状态机只按已声明的 `acceptance_failed → integration_review` 边运行。根因是列指令中“非阻断建议可以记录”未区分写审查报告与调用缺陷工具，且工具拒绝信息未说明不能用 `record` 来“解决”旧反馈。放宽终态门槛会重现假验收问题，因此保持 `terminal_blockers` 不变。

## 最小修复

1. `loops/ddd-software-delivery/loop.json` 的 `integration_review` 指令明确：非阻断建议只写 `docs/pair-review.md`，不得调用 `task.feedback.record`；只有必须返回责任列并要求新验证的缺陷才用该工具并提交对应返工 outcome。`accept` 指令明确读取现存反馈；未结反馈沿已有 `acceptance_failed` 返工，不用新的 `task.feedback.record` 描述“已解决”。不改图、列、权限或状态。
2. `app/v1/capabilities.py` 的 `task.feedback.record` 描述明确其创建的是阻断终态的缺陷，非阻断建议属于文档，记录“已解决”只会产生新的待验证缺陷。
3. `app/v1/agent_tool_execution.py` 的终态预检错误补一句：`task.feedback.record` 不能关闭既有反馈；应按已声明返工边取得责任列新证据。保留现有拒绝条件和路由来源。
4. 在现有 Loop/工具合同测试中加入窄断言，并为终态拒绝消息增加针对性断言；运行相关回归。旧 Task 已存在反馈，仍按旧图自然返工，不人为清库或跳过验证。新行为以新 Project 测试为准。

## 实施记录

- 软件 Loop 的集成审查列明确“非阻断建议只写审查报告，只有必须返工的缺陷才调用 `task.feedback.record`”；验收列明确不把已解决事项再次记录为新反馈。Loop 版本升到 `1.4.3`，列、边、权限、Runtime 路由未变。
- `task.feedback.record` 工具说明明确其阻断终态的语义；终态预检错误明确它不能关闭旧反馈，只能经声明返工边取得责任列新证据。没有降低 `terminal_blockers` 门槛。
- 首轮针对性测试暴露新增断言误放在相邻测试内，已立即修正；Loop、能力合同、Worker-selected acceptance 三个测试模块共 `70 passed`。
