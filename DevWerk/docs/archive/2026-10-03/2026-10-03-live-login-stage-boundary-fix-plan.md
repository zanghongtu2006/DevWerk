# 登录防刷实测：集成审查与交付阶段边界修复方案

## 范围与现场证据

- 实测 Project：`prj_6ea4c8e4296e44d193f1a050e15f278c`；先经过五轮自然语言讨论，第六轮授权创建 Workflow 和 Task。
- Conversation Agent 自行选用 `software.ddd_delivery`；Task 已经过需求、设计、后端、前端，但在 `integration_review` 反复返修，尚未进入 `system_test` / `delivery`。
- `v1_column_runs` 第 6、9、14、16 次集成审查均把项目根目录缺少 `README.md` 列为未解决的验收问题；输出同时把该文件的负责 Column 标为 `delivery`。第 9 次审查后 Task 回到 `requirements`，第 16 次后回到 `backend_development`。
- 第 19 次审查仍列出同一个 README 缺口，同时把已废弃但不影响当前行为的 `CorsFilter.java` 清理列为后端返工；Task 到第 22 次 Column 访问仍未进入 `system_test`。这说明审查门槛还混入非阻断性维护建议。
- 第 24 次集成审查另有 `mvnw.cmd` 命令失败，Agent 未补救该执行证据却重复提交 `accepted`，Runtime 以 `CompletionProtocolStalled` 中断并把 Task 置为 `recovering`。这是另一条执行失败路径，本次模板修改不放宽执行证据门槛；在新 Project 回归中独立判断是否复现。
- 当前 Workflow 的 `integration_review` 只允许写 `docs/pair-review.md`；`delivery` 才允许写 `README.md`。这使集成审查无法自行补齐被其当成前置条件的交付文件。

## 原因

`loops/ddd-software-delivery/loop.json` 的 `integration_review.instruction` 没有清楚区分当前阶段的代码/API 检查与后续 `delivery` 阶段的运行说明。这能解释 Agent 把 README 缺失当作当前阻断项，但**不能单独解释完整的循环**。保留的运行日志显示，README 缺口曾多次以 `responsible_column=delivery` 成功写入反馈；同时存在真实的前后端缺陷、非阻断性清理建议以及把完成报告误记为反馈的记录。第 24 次审查还有命令执行失败。由于清理旧测试数据时删除了该 Project 的数据库记录，无法逐次重建每一次反馈路由；这是清理造成的证据损失，与 Project 隔离无关。

Runtime 的 `task.feedback.record` 仅记录责任 Column 与复验条件；`TaskFeedbackRepository.settle` 会选最早的未解决反馈，沿 Workflow 边自动路由，且可覆盖 Column 选择的成功转移。**日志与代码共同指出更直接的循环原因：**15:36 和 15:38，前端把运行环境/联调限制分别记为两条反馈，责任设为 `integration_review`，复验却设为 `frontend_development/runtime.behavior`。16:06 的反馈列表显示两条均已 `resolved`；16:18:52 审查 Column 提交 `accepted` 后，Runtime 仍把 Task 送回前端。前端下一次的运行上下文显示，第一条反馈已被重新置为 `verifying`，`repair_run_id` 正是刚结束的 16:18 集成审查。这与 `settle` 对**所有反馈（包括已解决反馈）**在责任 Column 每次成功访问时无条件重新设为 `verifying` 的代码相符；复验目标位于前端，于是旧反馈再次抢在 README 反馈之前将 Task 路由回前端。16:02 的 `accepted → frontend_development` 也与同一机制一致。README 反馈虽指向 `delivery`，但因更早反馈反复重新打开而未轮到交付 Column；单凭审查指令或写入路径无法解释这一点。

现有工具没有反馈接收、拒绝或责任重议协议。`writable_paths` 是该 Loop 配置的 `project.files.write` 路径约束：后端/前端不包含根目录 README，而 `delivery` 包含；它不是操作系统层的完整写入权限。应让已解决反馈保持已解决，避免旧反馈饿死后续交付；对真正的责任冲突提供有限交接与 Conversation Agent 裁定，不能让自动路由无限循环。Mailbox 只传递通知，不承担协商对话。

## 最小修改

当前已做的最小补丁仅修改 `loops/ddd-software-delivery/loop.json` 中 `integration_review` Column 的 `instruction`：

1. 明确本阶段只有影响已确认用户行为、API 契约、编译或必要集成检查的缺陷才阻断通过；可维护性建议照常写入报告，但不因建议本身走返修边。保留已有后端/前端/需求返修路径。
2. 对明确属于后续 `delivery` Column 的 README、启动/停止说明等文件，允许记录“待交付阶段完成”，但**不得仅因其尚未生成而退回前序 Column**。当前阶段的检查通过时，应走 `accepted`。
3. 不改变 Column 顺序、转移边、可写路径或终验标准；`delivery` 仍须实际生成 README，`accept` 仍须核对最终交付条件。不能把真正的当前阶段缺陷也递延。

该补丁只处理阶段误判，不能视为完整根因修复或已完成黑盒回归。若恢复开发，应先最小修复 `app/v1/repositories/task_feedback_repository.py` 的 `settle`：已解决反馈不得因责任 Column 再访而重新打开，并为“解决后再访”补针对性单元测试。随后用新 Project 核对反馈记录、路由与复验全过程；再评估重复反馈、正向完成报告误记反馈和责任冲突是否需要分别处理。不能把单向 `feedback.record` 误称为 Column 间协商。

## 验证

1. 校验 Loop JSON 与现有 Loop 契约测试，确认模板仍可发布。
2. 用全新 Project 重做同一案例：至少五轮自然语言讨论，Agent 自行建 Workflow/Task；五分钟间隔观察。
3. 验证集成审查能把仅属于 `delivery` 的缺失文件交给后续阶段；Task 到达 `delivery` 并生成 README，最终达到 `done`。再独立检查前后端编译与约定的登录、防刷行为。

已有实测 Project 使用冻结的 Workflow revision，修改模板不会改变其运行中的定义；它仅用于保留故障证据，回归必须使用新 Project。
