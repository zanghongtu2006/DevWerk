# 已撤回：通用 Workflow 验证证据覆盖修复方案

本文件原方案拟在 Task Plan 加验证义务，并在 `done` 时增设终态守卫。此设计在 Workflow 图外形成第二套结束判定，已撤回。修订方案见 [由 Workflow 有向边约束验证证据](2026-10-15-workflow-edge-verification-plan.md)。以下仅保留讨论记录，不作为实施依据。

## 目标与已确认事实

目标只针对“未执行必要验证，却沿成功边交付”这一问题；不在 DevWerk 核心加入软件、浏览器、企业调研等场景规则，也不强制所有 Workflow 存在验收 Column。案例证据见 `2026-10-14-unverified-page-accepted-evidence-trace.md`：登录 Project 的需求同时要求 HTTP 响应与页面提示；实际脚本只执行 HTTP，`system_test` 的结构化输出为 `{}`，下游沿用其 21/21 总结，最终 `accept` 写入成功报告。该 Workflow 的 `acceptance_checks` 与 `acceptance_obligations` 均为空。给同一个 HTTP 脚本简单套上现有正式检查仍会得到错误的“通过”。

`readiness.acceptance_criteria` 仅是规划提示，其改名与可选化另见 `2026-10-14-readiness-completion-notes-followup.md`；**不得将该字段重新解释为 Runtime 验收开关**。两个问题分开修改和回归。

## 修复契约

### 1. 验证义务由 Task Plan 显式声明，并且可选

在 `TaskPlanItem` 增加可选 `verification_requirements`（名称待代码阶段定稿，默认空列表）。每项包含稳定 `key`、需证明的可观察事实、负责产生证据的 Column、负责审阅的 Column，以及证明方式：

- `runtime_check`：指向既有 Workflow Column 的 `acceptance_checks` 中一个唯一 `(column, check_key)`。用于能由工具执行并判定的条件；证据必须是 Runtime 真实执行的该检查回执。
- `artifact_review`：指定要核对的产物或证据来源，由指定审阅 Column 给出逐项结论和理由；Runtime 只能验证所引回执与产物确实存在、属于本 Task 和当前版本，不能自行判断内容真假。

这不是项目类型枚举，也不规定必须浏览器测试。软件 Task 可以有独立的接口与页面行为义务；企业调研 Task 可声明“清单中每家企业均有来源及落地记录”，由其验证工具核对动态清单，联系人不存在时按该 Task 的规则记录“未找到”。范围未知且开放式的研究不能由 Runtime 推断“已查尽全部企业”，须由审阅 Agent 判断搜索充分性。无需独立验收的 Workflow 可留空并直接沿成功边到 `done`。

### 无独立验收项目的完整路径

Conversation Agent 仅与用户讨论时可不创建 Workflow/Task。若确实需要 Workflow，但任务只需执行并交付产物，Workflow 可以只有 `produce` Column、`ready → done` 一条成功边；不建名为 `accept` 的列，不配置 `acceptance_checks` / `acceptance_obligations`。Task Plan 省略 `verification_requirements`，模型默认 `[]`。Worker 仍需按本列 `output_contract` 交付结果，并通过现有合法 outcome、工具失败回执等通用完成协议。`AgentToolExecutor._run_acceptance_checks` 遍历空列表不执行额外工具；新增终态守卫读到空义务即返回；`V1Store.finish_run` 按原有 `ready → done` 边完成。无需给 `output` 增加 `verification_results`，也无需人为制造“验收通过”文本。

如果 Workflow 本身声明了某个阶段的正式检查，但未声明 Task 级验证义务，则该阶段仍按**原有 Workflow 契约**运行检查；本补丁不会自动增加或删除它。`acceptance_criteria` 当前仍强制填写造成的命名/必填副作用，要由独立字段清理补丁使其可省略。上述“非验收”指无独立审阅和证据覆盖义务，不能理解为绕过 Workflow 自己声明的产物/输出契约。

### 2. 冻结前校验契约本身，不让字段只是漂亮文字

Conversation Agent 仍通过 `workflow.inspect`、`task.plan.contract`、`task.plan.validate`、`task.plan.save`、`task.create` 创建执行计划。`task.plan.validate` 对每项验证义务检查 Column/检查 key 是否存在、证明方式是否合法、产证和审阅列在 Workflow 中可达；发现无可执行检查却声称 `runtime_check` 时拒绝。Task 创建后固定 Workflow Revision 与验证义务。用户修改目标时创建修订/新 Task，不暗改运行中 Task 的标准。

Conversation Agent 对原始用户目标是否已被完整拆成义务仍需负责；Runtime 无法由自然语言自动证明拆分完整。对此增加**规划阶段独立对照**：在 Workflow/Task Plan 的可读回执中展示用户已确认范围与验证义务清单，让 Conversation Agent 在开始前核对，而不是以无义务的模板默认为“已覆盖”。这一步不增加用户审批门槛，也不自动推断场景类型。

### 3. 复用已有检查表和 Run 输出，不新建证据状态机

`runtime_check` 的事实来源只用现有 `v1_acceptance_results`：`AgentToolExecutor._complete` 已在成功 outcome 前运行 Column 的 `acceptance_checks` 并记录回执；`TaskFeedbackRepository.verified` 已能根据 Task、Workflow Revision、Column Run 和返工后的新鲜度查验它。新增的 Task 义务只负责把“本 Task 要证明的事实 key”绑定到该检查，不能让 Agent 用任意成功命令代替。

审阅 Column 在已有 `column.complete(output=...)` 的 `output.verification_results` 中提交逐项结论，例如 `{key, status: pass|fail|unverified, evidence_refs, reason}`。这些字段作为普通 Run 输出随 `V1Store.finish_run` 持久化；Workflow 的 `output_contract` 必须允许该字段。`artifact_review` 的回执指向本 Task 的真实工具调用及产物版本，Runtime 只检查引用、版本及审阅结论，不解读自然语言内容。

在 `done` 原事务内加载冻结 Task Plan，对每一项验证义务核对所指向的正式检查，以及指定审阅 Column 最新有效 Run 的 `verification_results`。必需项为 `fail`、`unverified`、缺项、回执错误或返工后失效时拒绝 `done`；不能只接受总评、报告文件存在或空 `{}`。QA 的成功边在其指定正式检查失败时已经会被现有完成协议拒绝；终态再做一次跨列完整性校验，覆盖其他 executor/等待路径。

**不可过度承诺**：Runtime 可以确定性阻止“无指定检查回执”“缺项”“旧回执”“把 A 检查回执填给 B key”；对任意脚本本身是否正确实现了页面操作、调研来源是否可信，只能由专门的执行工具/脚本断言与审阅 Agent 判断。若页面检查仅调用原来的 HTTP 脚本，仍会错误通过。因此该案例的验证检查必须独立执行实际客户端操作，或明确标 `unverified`，不能用 21/21 HTTP 统计替代。

## 预计修改位置与最小落地顺序

1. **模型和规划入口**：`app/v1/domain.py` 的 `TaskPlanItem` 增加可选验证义务模型；`app/v1/capabilities.py` 的 `task.plan.contract` 与自动生成的 `task.plan.save` schema 暴露它；`app/v1/services/task_plan_compiler.py` 的 `prepare_task_input` / `diagnose_task_plan` 校验唯一 key、Column、检查 key、审阅 Column 和该列 `output_contract` 对 `verification_results` 的兼容性。仅补协议，不改变 Workflow 图和调度。
2. **Worker 可见性和正式检查**：`app/v1/runtime.py` 的 `_input_for` 已通过 `current_goal` 提供冻结 Task Plan；只给相关产证/审阅列补充精简的义务与现有检查回执索引。`app/v1/agent_tool_execution.py` 的 `_complete` / `_run_acceptance_checks` 保持正式检查在成功 outcome 前执行；`app/v1/services/completion_admission.py` 继续校验本列执行账本，不把任意 `project.command.run` 成功算作指定检查。`app/v1/repositories/task_feedback_repository.py` 的 `record_check` 与 `verified` 原样承担正式回执持久化/新鲜度查询。
3. **逐项审阅与终态守卫**：不改 `CompletionSubmission` 外层字段；审阅结果放在现有 `output`。新增一个小的通用验证函数/服务，从 `TaskPlanItem.verification_requirements` 和最新 Column Run 输出读取逐项结论，验证引用与检查结果。`app/v1/store.py` 的 `finish_run` 在已有 `feedback.apply_completion_evidence` 之后、状态变更之前，于同一事务对 `terminal == 'done'` 调用它。无义务时直接返回；现有 `acceptance_obligations` 和反馈机制继续按原逻辑工作。
4. **可选模板回归**：只在通用能力具备后修改 `loops/ddd-software-delivery/loop.json` 的适用说明，区分页面行为与接口行为需要不同证据；不写死登录 API、技术栈、脚本路径或场景数量。若环境没有实际页面验证能力，QA 必须标 `unverified` 并走 Workflow 声明的失败/阻塞边。
5. **独立字段清理**：另一个小步提交按 `2026-10-14-readiness-completion-notes-followup.md` 把 `acceptance_criteria` 改为可选规划提示，保留旧请求兼容。允许清理 1.0 前测试生成物与重建本地 DB，但协议兼容不依赖清库。

## 回归判据

- 单元层：空验证义务的单列通用 Workflow 可直接 `done`；有义务时，缺项、错 key、只引用同任务的另一种工具回执、未验证、旧版本回执均不能 `done`；真实指定检查和审阅结论齐全则能到 `done`；返工后旧证据不可复用。
- 软件黑盒：新 Project 经自然语言规划并自动执行；HTTP 与实际客户端行为分别留证。只跑 HTTP 时不得 `tests_passed`/`done`；两者实际通过且可编译时能交付。不能把测试者手工补调用、事后报告或无意义退出码当成证据。
- 非软件黑盒或契约测试：企业清单/来源/落地记录可以使用同一通用协议；无独立验收的 Workflow 不受影响。检查不存在场景类型判断或固定 `accept`/`system_test` 字段进入 `app/v1`。

## 固定的数据流与代码级示例

以登录 Task 为例，Conversation Agent 在 Workflow 发布/配置阶段给 `system_test` 声明两个不同的正式检查 key：接口请求和实际客户端操作；`launch_validation=deferred` 允许执行脚本由后续 Column 生成。随后在 Task Plan 的 `verification_requirements` 写两个不同的事实 key，分别引用两个检查，并指定 `accept` 审阅。两项检查的脚本/能力可随项目而变，但页面检查必须真实操作页面并断言可见结果；不能让同一个纯 HTTP 脚本冒充两个事实。`system_test` 提交 `tests_passed` 时，现有 `_run_acceptance_checks` 执行两项；页面检查未执行或失败，`CompletionAdmissionService.evaluate` 拒绝成功 outcome。若两项检查通过，`accept` 的 `output.verification_results` 再逐项记 `pass` 和对应回执；`finish_run(..., terminal='done')` 核对两项均存在且为当前有效版本。

企业调研 Task 可用相同字段，把“每家企业有来源与落地记录”绑定到读取动态企业清单的项目检查；“联系人是否可信”可交给审阅列的 `artifact_review`。无需验收的 Task 留空 `verification_requirements`，代码不会寻找固定的 `accept` 列。若 Conversation Agent 根本漏列了一项用户需求，本方案仍不能由代码自动发现语义遗漏；黑盒回归必须检查规划是否列全，而不能声称仅有结构校验就完全解决模型判断失误。
