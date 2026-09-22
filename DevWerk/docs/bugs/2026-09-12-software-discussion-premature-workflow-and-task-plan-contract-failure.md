# 软件需求讨论被提前执行并在 Task 准入阶段失败

日期：2026-09-12  
状态：已修复并通过本次回归（2026-09-15）；完整软件后续交付另行验证  
级别：P0（基础软件交付无法启动，且违反用户明确的只讨论边界）

修复设计见 [讨论语义、执行边界与 TaskPlan 准入修复设计](../DEVWERK_Discussion_TaskPlan_Fix_2026-09-12.md)。该方案结合当前代码、数据库证据与本地 Hermes/Codex 源码，覆盖同一主 Agent 的回合边界、Task 粒度、统一准入、需求确认来源和真实多轮语义回归。本文保留原始现场与验收条件，不将设计完成标记为修复完成。

实际修改与新测试结果见 [实施及回归记录](../DEVWERK_Discussion_TaskPlan_Implementation_2026-09-13.md)。

最终验收：360 项本地测试通过；20 个真实多轮讨论变体各重复 3 次，共 60/60、120 个用户回合全部通过且无业务副作用；核心三轮序列重复 5 次全部通过，均创建 1 个合法 Task、状态查询不重复创建、首 Column 完成并进入 domain_design。另有真实 Provider 空响应恢复测试通过。项目 Conversation Agent 保持唯一主 Agent，Task 由 Runtime 调度 Column Worker。历史失败证据仍保留在实施记录中；生产服务未重启、用户测试项目未修改，有限样本不代表所有自然语言情形均已证明正确。

## 测试范围

- Project：`prj_c24e9773680a4955962c7341a710b248`
- 目标：Vue 3 登录/注册前端、Java Spring Boot 后端、用户名与 IP 组合防刷、自动化测试。
- 用户边界：先讨论关键决策与最小交付方案，不启动开发。

## 实际结果

第一轮仅返回四项待确认决策，没有创建 Workflow 或 Task，行为正常。

第二轮用户补充 JWT、锁定规则、注册方式与前端技术栈，并继续询问持久化和防刷最小方案。Conversation Agent 没有回答该问题，而是执行了以下操作：

1. `loop.inspect` 成功。
2. `loop.apply` 成功，项目提前绑定 `software.ddd_delivery` 1.2.0，Workflow revision 为 `wfrev_ac18faf10f9d4eed8b69e4f7d5364159`。
3. `task.plan.save` 第一次失败：把整个 plan 作为字符串提交。
4. `task.plan.save` 第二次失败：Task `design` 仍缺 `requirements_confirmed`。
5. `task.plan.save` 第三次成功，保存 `tplan_fc33bb3782934c76aa04eaf7aba152f9`。
6. `task.create(req_baseline)` 失败：`Additional properties are not allowed ('input' was unexpected)`。
7. 本轮以 `ConversationProtocolStalled` 结束，用户只收到协议失败提示。

最终数据库/API 状态：Workflow 已留下，Task 数量为 0，没有任何开发任务启动。

## 关键偏差

### 1. 讨论边界被违反

用户明确要求继续讨论且不要启动开发，但 Agent 在关键方案尚未回答、用户尚未确认开始之前执行了 `loop.apply` 和 Task Plan 持久化。讨论许可与执行许可没有被可靠地区分。

### 2. Task 与 Workflow 阶段被混淆

`software.ddd_delivery` 的设计是：一个独立软件交付范围作为一个 Task，Task 在 Workflow 的 requirements、domain design、backend、frontend、review、test、delivery、acceptance Columns 中流转。

本轮却把每个 Column 重新规划成一个 Task（需求基线、设计、后端、前端、审查、测试、交付、验收共 8 个 Task）。这重复建立了一层任务图，与 Loop 的 Task Contract 不一致。

### 3. 保存成功的 Task Plan 无法被 task.create 消费

第三次 `task.plan.save` 已成功，但随后仅凭 `task_plan_id + proposed_task_ref` 创建任务时，Runtime 解析出的输入包含额外的 `input` 层，未能满足 Loop Task Contract：

```text
Task req_baseline input rejected value at $:
Additional properties are not allowed ('input' was unexpected)
```

这说明 Task Plan 的保存准入与 Task materialization 的准入结果不一致：前者接受的计划不能保证后者可创建。

### 4. 失败后留下部分副作用

Workflow 已应用、Task Plan 已保存，但 Task 为 0，且用户没有收到可继续决策的自然语言回复。Conversation turn 缺少对跨工具操作的提交边界或明确的部分完成语义。

## 证据

- Conversation Job：`cjob_abeaaa93d863492e9d0103ae5233b8d1`
- Agent Run：`arun_976d2da14c7c411f9a4a779f020954d6`
- Task Plan：`tplan_fc33bb3782934c76aa04eaf7aba152f9`
- Workflow revision：`wfrev_ac18faf10f9d4eed8b69e4f7d5364159`
- 用户消息：413
- 失败提示消息：414

## 修复验收条件

1. “先讨论/不要启动”回合不得调用会改变 Workflow、Task Plan 或 Task 的能力。
2. Conversation Agent 能从 Loop 的 `flow_unit` 和 Task Contract 判断 Task 粒度，不把 Columns 再规划成 Tasks。
3. `task.plan.save` 成功的计划必须可由 `task.create` 使用同一套输入归一化和 Contract 校验；无法 materialize 的计划不得保存成功。
4. 多步规划失败时，不得只留下难以理解的协议错误；用户可见回复必须说明没有 Task 启动及唯一可操作的失败原因。
5. 回归覆盖本次两轮真实对话：第一轮和第二轮均只讨论；明确说“开始开发”后才允许应用 Loop，并应创建一个软件交付 Task。
