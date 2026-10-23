# 现有 Workflow 修订未发布：最小修复计划

## 证据与边界

在 `prj_b6c7a8b0c5204fe1a94c63dc55fcd520`，第一版五列 Workflow 已发布并完成 Task。后续用户要求增加验证列时，Conversation Agent 成功调用 `project.scope.revise`，并保存六列 Workflow Plan `wfplan_d026bb99bef9437d981f0306682e353a`，却没有调用 `workflow.publish`。`scope.revise` 在无 Loop 的路径中按旧定义发布修订，因此运行中的图仍是五列。保存 Plan 后一次上下文压缩丢失了显式的“接着发布”步骤；Agent 随后反复读取相同状态和文件，直到预算耗尽。压缩与漏调用之间有关联，但不能仅凭时序断言它是唯一原因。该 Plan 的示例生命周期还把终态后继续执行写进同一条路径，即使尝试发布也会被结构校验拒绝；需要在回归中区分“未调用”和“调用被拒绝”。

## 目标

仅修复通用控制面协议：Agent 能辨别 Requirement 修订、Workflow Plan 保存和 Workflow 发布是不同操作；压缩后仍能看到未完成的发布步骤；相同无进展读取不会无限消耗迭代。Workflow 图及验收语义仍由用户和 Agent 选择，Runtime 不增加文章、调研或软件项目专属规则，不自动替 Agent 发布。

## 修改顺序

1. `app/v1/conversation.py` 的 `turn_authority.instruction` 与 `app/v1/capabilities.py` 的三个工具说明：明确同一 Requirement 内仅改列图时使用 `conversation.execution.authorize(request=continue) → workflow.plan.save → workflow.publish`，真正规模/目标扩展时使用 `extend → project.scope.revise`；若还要改列图，仍需另行保存并发布。`scope.revise` 的修订号不等于新列图已应用。
2. `app/v1/capabilities.py::_workflow_plan_save` 为回执增加 `published=false` 与带 Plan ID 的 `next_action`；`scope_repository.py::revise` 回执标明本次是否改变列图。输出 schema 同步扩展，保持旧字段与行为。
3. `app/v1/context_manager.py` 的当前会话权威状态增加一个有界的、从持久化计划及当前 Workflow 推导出的“未发布 Plan”提示；即使工具输出被摘要，Agent 仍可通过该 Plan ID 继续。只保留最新相关 ID 和下一动作，不复制完整方案。
4. `app/v1/agent_tool_execution.py` 沿用现有失败调用保护，为 Conversation Agent 增加通用的重复、成功、只读调用无进展保护：相同参数连续重复且期间没有成功控制/写入操作时，先给一次明确纠偏反馈；继续重复则结束本轮。不得阻止一次普通复查、分页或变更后的复查。

## 回归与黑盒

- 单元测试分别覆盖首次发布、现有 Requirement 的列图修订、Scope 修订沿用旧图时的回执、压缩后未发布 Plan 指针、重复读取保护及变更后的合法复查。
- 检查本分支相对 `main` 的代码差异，确认只含通用 MCP/Workflow/Context 行为、无场景专属分支或提示词；运行相关测试与静态检查。
- 新建独立 Project，以自然语言讨论并执行一次完整文章 Workflow；完成后要求增加/调整一列，再创建后续 Task 验证新版图真实发布并由看板推进。长运行期间每约五分钟检查一次，不以模型宣称代替工具回执、数据库状态或交付文件。

## 代码审查记录（实现后）

本次改动只涉及通用的工具说明与回执、Requirement 修订回执、Conversation 当前控制面指针以及重复无进展调用保护。`scope.revise` 原有事务和发布行为未改变；`workflow.publish` 仍是唯一应用显式新图的工具。重复读取保护只对同一 Run 中相同参数且实际结果未变化的第四次只读调用生效；授权保护在重复授权真正执行之前拒绝，避免把已发生的控制操作误报失败。不同结果、分页参数变化，以及成功发布后的再次读取仍允许。既有 Task Plan 回执保持原 schema。分支相对 `main` 的其他产品改动是通用 MCP 工具注册、首次 Workflow 前的 Scope 修订和通用 Task 指针规范化；Chrome 只出现在可选 MCP 配置样例中，产品代码没有文章、调研、小说或软件项目特化分支。

已通过的回归：6 个定向修订/回执/重复读取用例，45 个上下文用例，42 个能力契约用例；修正回执 schema 后再次运行的 3 个关联用例通过；新增的 Conversation Agent 集成用例验证了第四次无变化读取收到纠偏回执、后续控制操作与再次读取仍可成功。`compileall` 与 `git diff --check` 通过。更宽的旧 Scope/Conversation 套件中有用例长时间无输出，已中止该进程；不能将未完成套件计为通过。

## 新 Project 黑盒记录（进行中）

Project `prj_908b5a50adb444e8af6bb176e11b1d87`。前四轮讨论分别确定首篇目标、读者与篇幅、由 Agent 提出的六阶段看板、列间交接和返工边界。第五轮自然语言明确授权创建 Workflow 与首篇 Task。未通过 API 直接替它发布图或创建 Task；后续需从 Conversation Run 的工具调用、Workflow 修订和 Task 状态核验。

第五轮 Run `arun_676d2e5d789a49e8a32f61057a97dedb` 在 `workflow.publish` 第 59 次工具调用发布了六列图 `wfrev_c6b760d90e8b4ac4b4aeec31bd82aab9`，但没有创建 Task，最后以 `ExecutionBudgetExceeded` 结束。该 Run 将 Plan/Workflow 多次作为 JSON 字符串传入对象字段，也曾提交无效边和不匹配 Plan 的图；发布成功后，`task.plan.save` 因 Task 输入不满足刚发布的 `task_contract.input_schema` 而被拒，例如缺少必需的 `article_title`。`task.plan.contract` 返回了准确必填字段，但 Agent 后续没有用它修正输入。六列图本身不等于交付成功。现已在同一测试 Project 发一轮目标层自然语言续办，检验它能否从已有图恢复并创建 Task；没有替 Agent 填写字段。

续办暴露第二个独立阻塞：所发布 Workflow Plan 的 `task_contract.feedback_columns` 包含 `review`，而对应 Column executor 未开放 `task.feedback.record`。`task.plan.validate/save` 因 `FeedbackContractMissing` 拒绝；Agent 反复尝试 `workflow.acceptance.configure`，但该工具只能配置检查，不能改变 executor 的能力列表。当前 `workflow.publish` 没有检查这一已有的不变量，允许不可启动 Task 的图先发布。

若续办 Run 仍不能修复，下一小步只改通用协议：`app/v1/capabilities.py::_dispatch` 将已用于 `task.plan.save/validate` 的“JSON 字符串内含对象”规范化一致地用于 `workflow.plan.save` 与 `workflow.publish` 的对象参数；`_workflow_publish` 在提交前调用现成的 `acceptance_diagnostics`，拒绝缺少声明反馈能力的图；该诊断增加明确的通用修复路径“修改 Workflow Plan/Column executor 后显式发布新修订，验收配置工具不能改 executor”。测试使用中性列名和能力，不引入文章或调研逻辑，也不自动补齐 Agent 漏写的业务字段。

续办 Run `arun_695b3632af4a4749a82ec6b60dcc3452` 最终仍以 `ExecutionBudgetExceeded` 结束，未创建 Task。它正确调用了 `task.plan.validate` 并逐步补字段，但无法修复已发布图的反馈能力缺口，多次调用不能修改 executor 的 `workflow.acceptance.configure`，也误用 `project.scope.revise`。上段的通用修复现已实现：规范化位于 `CapabilityRegistry._dispatch`；反馈契约校验位于 `Store._publish_workflow_revision`，因此首次、修订和 Loop 发布路径一致；错误信息明确指出需修正 executor 再发布。新增测试证明 JSON 字符串经解码后仍需通过原 schema、无效图不发布、修正能力后可以发布、未知反馈列有明确诊断。相关回归：44 个能力契约、26 个 Store、19 个 Task 入口、19 个 Loop 用例通过。已重启服务，并创建全新 Project `prj_3a39adc4c924493691c07bd7fae6a244` 复测。

## 新 Project 复测结论（2026-10-19）

在 `prj_3a39adc4c924493691c07bd7fae6a244`，五轮自然语言交互中，前三轮讨论目标、读者、来源和五步看板；第四轮授权首篇执行；第五轮要求在起草与校对之间增加独立事实核验列。第四轮 Conversation Run `arun_a0ba08aa9f7c4bad93ee65ee8f2fa721` 确实发布了修订 1 `wfrev_b9828fa437f24b40ba185db3c9a6a351`，五列为 `research → synthesis → draft → review → deliver`，并创建 Task `tsk_173652d3fa184e939ed980ca3713942a`。观察时 Task 已由 Runtime 推进至 `review`，证明首版看板及 Column Worker 调度在本例中工作。

第五轮修订请求 **未成功**。Run `arun_3d823b5111944d7a8e55c697a82b642f` 查到现有图与 Requirement 后，先在未授权状态调用 `project.scope.revise`，随后正确取得 `request=continue` 授权，却再次调用仅供 `extend` 使用的 `project.scope.revise`；收到 `ScopeExtensionRequestMissing` 后重复同一失败调用，本轮以 `ConversationProtocolStalled` 结束。未保存新 Plan，也未调用 `workflow.publish`。

仅追加一次自然语言纠偏，强调 Requirement 范围未变、只改现有看板。该轮 Run `arun_296ff24ac6cb47e595c1cd120da03bdc` 仍误选 `request=extend`，使 `project.scope.revise` 发布修订 2 `wfrev_90f1ce8ed2df4912a7ab3c1c75c6a067`。它沿用修订 1 的 Plan 和五列图；这是 Requirement 修订，不是所要求的 Workflow 修改。之后 Agent 成功保存新 Plan `wfplan_9e4afa25fe8f41cdb276cf4a2d16775e`，但 `workflow.publish` 曾引用不存在的 Plan ID，又曾用旧 Plan 发布六列图而被列集合校验拒绝；引用新 Plan 的一次调用把 Workflow 作为无法解析的 JSON 字符串发送，仍被 schema 拒绝。截至北京时间约 09:41，该轮仍在运行，活动图仍是五列修订 2，未出现事实核验列。Task 仍绑定首版修订，运行状态不代表新图生效。

因此，本次复测只确认了 Conversation Agent 可创建首版 Workflow、Task 可由看板推进；**尚未证明它能可靠修改已有 Workflow**。不能把修订号 2、Agent 的文字声称或保存 Plan 当作修改成功。剩余阻塞是通用控制面用法：同一 Requirement 的图修订被误导入 `extend/scope.revise`，并且 Agent 未稳定遵循 Plan 保存回执和 `workflow.publish` 的对象参数协议。此处没有为文章场景增加专属代码或提示词；继续修复应聚焦这些通用工具协议与实际调用行为，而非加入内容生产规则。本轮按收尾要求不再无限追加测试或改动。

最终代码审查：本次未提交改动只涉及通用的工具描述、保存回执、跨压缩的待发布 Plan 指针、重复无进展保护、Workflow 发布前的通用反馈契约校验和对应测试。相对 `main` 的已有分支改动是通用 MCP 列工具暴露、首次 Workflow 前的 Scope 修订和 Task 指针规范化。改动的产品代码中未发现文章、调研、小说或软件项目关键词/分支；Chrome 仅作为可选 MCP 配置样例。本轮相关测试通过数详见上文，`compileall` 与 `git diff --check` 通过；未跑完的旧大套件不计为通过。
