# 待 review：当前版本单一 Workflow 出边的最小修正

## 目标与不变部分

一次 Column 完成只选择一个 `outcome → target`。Workflow Revision 是路径和业务通过条件的唯一来源；Runtime 负责执行与核验，Conversation Agent 通过现有工具创建、启动、观察、修订 Workflow 和 Task。保留现有 Column、`done/failed` 终点、Task 生命周期状态机、工具调用账本、Mailbox 通知、Workflow Revision 冻结机制；不新增调度器、项目类型枚举、Task 级验收状态机或第二个终态守卫。

“单一出边”不是禁止一个 Column 声明多个候选 outcome，而是每次 `column.complete` 只能选择**一个**声明的 outcome，并在同一事务只应用该边的条件和目标。条件失败只拒绝该提交，不由反馈或 Runtime 擅自选择另一条边。

## 当前冲突，按实际代码路径

1. `WorkflowDefinition.acceptance_obligations` 在 `TaskFeedbackRepository.apply_completion_evidence` 里只于 `terminal == 'done'` 执行；它属于 Workflow 数据，却不是某条边的条件。`store._prepare_task` 还以这个全局字段判定 successor 必须继承哪些义务。
2. `task.feedback.record` 可在运行中新增缺陷；`AgentToolExecutor._complete` 的 `terminal_blockers` 和 `TaskFeedbackRepository.apply_completion_evidence` 的 `done` 分支把未解决反馈变成图外终态门槛。它目前不改写 `target`，但仍形成第二套完成裁决。无检查反馈还要求存在后续独立 review Column，使一条通知记录反过来约束图结构。
3. Agent 与 capability-sequence 执行路径对**本列全部** `acceptance_checks` 自动运行并要求通过，只要所选边不允许未解决失败。不同正常 outcome 不能各自选择所需检查。
4. `runtime._validate_task_agent_terminal` 在 `done` 时按 TaskPlan 的 `agent_execution` 再判一次。这是执行拓扑兼容性，应在计划准入时校验，不作为完成边之后的额外裁决。
5. `ReadinessDecision`/`TaskPlanReadiness.acceptance_criteria` 是必填规划文字，不参与 Runtime 路由；它不直接造成双重路由，但与无独立验收 Workflow 的协议表达不一致，按已有独立记录改名、可选化。

执行租约、并发版本、输出 JSON 契约、工具调用回执真实性与故障处理仍是 Runtime 完整性规则，不属于替 Workflow 决定业务路径。`CompletionAdmissionService` 的未解决命令失败账本目前按所选边的 `allows_unresolved_failures` 判定；本轮只确保它读取所选边，不扩大为全局验收语义。

## 唯一决策链

`column.complete(outcome, output)` → 校验本列输出契约 → 从冻结 Workflow 当前 Column 的唯一同名 Transition 取得目标与约束 → 只执行/读取该 Transition 要求的检查 → 校验回执与边条件 → 在 `finish_run` 同一事务提交 Run、Task 状态及唯一目标。其他来源可记录事件并通知 Conversation Agent，但不能改目标，也不能在 `done` 时另做业务验收。

最小协议扩展：在 `Transition` 增加可选 `required_checks: list[{column, check_key, invalidated_by?}]`，默认 `[]`。检查定义仍放在 `ColumnDefinition.acceptance_checks`，用已有 `v1_acceptance_results` 留回执。当前列的检查在提交该出边时执行；前序列的检查只查其已持久化且未失效的回执。`WorkflowDefinition.validate_graph` 校验键唯一、列和 check key 存在、来源列可在该边之前到达；某条可达路径缺少回执时不能通过该边。任意普通边和 `done` 边使用完全相同的判定函数。既有 `evidence_requirement` 和 `allows_unresolved_failures` 仍是该边的协议属性。

无独立验收时，`produce` 的成功边直接指向 `done`，`required_checks=[]`；它只须满足现有 Column 输出契约与通用完成协议。需要验证时，Workflow 作者显式把检查引用挂到需要阻挡的边，例如 `system_test.tests_passed → delivery`；如果还需独立审阅，再用审阅列及其输出契约和 `accept.delivered → done` 的条件表达。核心没有固定 `system_test`、`accept`、HTTP 或页面概念。

## 文件级最小改动顺序

1. `app/v1/domain.py`：增加边级 `required_checks` 模型与图校验；移除全局 `acceptance_obligations` 的运行期语义。`app/v1/completion_protocol.py` 的每个 `CompletionOutcomeRule` 携带该边所需检查引用，不再把整列检查当作统一成功门槛。
2. `app/v1/agent_tool_execution.py`、`app/v1/runtime.py` 的 capability-sequence 路径和 `app/v1/services/completion_admission.py`：仅对所选边执行当前列检查、核验前序检查。仍可在 Column 中定义可复用检查，但未被该边引用的检查不会自动影响它。失败返回明确的边/检查错误，不自动改为另一 outcome。
3. `app/v1/repositories/task_feedback_repository.py`、`app/v1/store.py`：移除 `done` 专属的全局义务和未解决反馈阻挡；保留反馈记录、状态与回执查询，并发出只含反馈 ID 的 Mailbox 通知供 Conversation Agent 监督。`finish_run` 在原事务内验证所选边回执新鲜度并提交唯一目标。Successor 使用显式的新 Workflow Revision；不在 Task 创建阶段暗中继承旧 Revision 的业务条件，原 Task 及其约束保持不可变。
4. `app/v1/capabilities.py`、`app/v1/context_manager.py`、`app/v1/agent_prompt.py`：`workflow.inspect`/发布 schema 显示边条件；`workflow.acceptance.configure` 同时编辑检查和相应出边引用，或用现有 `workflow.publish` 修订完整 Workflow，不能继续写旧全局义务。`task.feedback.record` 描述改为缺陷通知，不宣称记录本身阻挡终点；去掉“必须有后续 reviewer 才能记录”的图结构限制。Conversation Agent 负责检查反馈并决定修订/新 Task；Worker 仍可选择 Workflow 声明的返工边。
5. `app/v1/services/task_plan_compiler.py`、`app/v1/runtime.py`：`agent_execution` 与 Workflow executor 拓扑的兼容性在 Task Plan 校验和 Task 创建前处理，移除 `_validate_task_agent_terminal`；保留运行时所有权与执行回执校验。`acceptance_criteria` 的独立改名/可选化按 `2026-10-14-readiness-completion-notes-followup.md` **另行**落地，不把该字段当成路由条件。

这是局部协议收敛，不重写调度器。已有未提交代码和测试文件保持原样。1.0 前测试数据可以清理重建；若保留旧 Workflow JSON，则在读取/发布时把旧列级自动检查转换为该列正常出边的 `required_checks`，把非空全局 `acceptance_obligations` 转换为相应源列正常出边及 `done` 出边的条件。转换后只运行边条件，绝不让两个字段并行生效。无法无歧义转换的 Workflow 必须报迁移错误，不能静默放行。

## 验证与实际保证

- 单元回归：同列有两个候选 outcome 时，每次只有被提交边的检查执行；拒绝缺回执、失败回执、错 Task/Revision/Run、返工后失效回执；拒绝未声明目标；反馈存在不改写或隐式阻挡路线；无检查单列 Workflow 直达 `done`；Agent 拓扑在准入阶段而非终点检验。
- 集成回归：普通 Agent Column、capability-sequence、await/resume 三条完成路径都通过同一个边校验；事务失败不产生半次状态迁移；Conversation Agent 能从工具协议读取和修订边条件。
- 黑盒回归：新 Project 的 Workflow 必须实际声明 HTTP 与客户端交互两个不同检查，并运行真实客户端检查。只完成 HTTP 时不能走要求两项回执的成功边；两者完成且编译通过才能交付。若 Conversation Agent 漏声明页面条件，Runtime 仍无法从自然语言自动发现；这是规划质量问题，需由 Conversation Agent 监督与黑盒检查识别，不能用场景硬编码弥补。

本文件仅供 review，尚未实施代码修改。
