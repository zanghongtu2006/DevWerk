# 新 Project 登录防刷复测：规划边界与失败回执修复方案

证据 Project `prj_87d10ac45d654f26a933fd84662760e1`；逐轮记录见 `2026-09-30-vue-java-login-blackbox-retest.md`。五轮讨论正确保持 hold、无提前规划；执行授权后仍无法创建 Task，因而本 Project 尚无 Column Run，也尚不能观察 Worker/mailbox/跨列协作或评价交付质量。

## 复盘结论

首次授权 Job `cjob_8349a072e3e546b6843ef4724a7493a8` 成功保存需求文件、应用 software Loop，得到 Workflow revision 1；`task.plan.save` 因缺五列行为验收与终验场景被正确拒绝。Agent 随后提交空 `scenario_ids`、非法列键、重复 resolve/loop.apply、把 Task Plan 对象序列化为字符串，最后被重复失败调用保护停止。第二次专注验收配置的 Job `cjob_db7019f7a9dc437391db6c0838b6d8eb` 将 backend/frontend/system_test/accept 检查填全，但在 delivery 列连续把纯 build/package 当成行为报告：空场景+报告、artifact+报告、artifact+虚构场景各遭 Pydantic 正确拒绝。第二次 Run 7 iterations，31,348 累计 tokens，仍无 revision 2。

这是**三层相互关联但不能混为一谈的问题**：

1. `app/v1/domain.py::AcceptanceCheck.behavior_requires_execution`、`app/v1/services/task_plan_compiler.py::acceptance_diagnostics` 正确拒绝伪行为证据。放宽它们会造成假交付。`workflow.acceptance.configure` 已在说明中写明每个必需列需要行为检查，模型仍将 delivery 的构建 artifact 当成行为验收；大 JSON 一次性跨五列配置、Scenario ID、报告 schema、obligations 让规划任务过重。当前 Runtime 未提供分阶段、可保留草稿的逐列配置路径，需从接口和上下文成本层面设计，而不是再叠一条针对本案例的提示词。
2. 初始 `software.ddd_delivery` Loop 的 `system_test` 可写路径仅 `docs/test-report.md`。本测试明确要求独立 QA 建立并运行真实浏览器 harness，若按 Agent 提交的 `e2e/**` 路径写测试源，`project.files.write` 会被列写路径约束拒绝。`delivery` 仅能写 README/报告/startup/shutdown，Agent 设想的项目根 `scripts/final-acceptance.js` 也没有明确负责源码创建的列。即使只修好参数、成功发布 revision 2，后续仍可能卡在测试脚本所有权。单 Agent Column/独立 context 的架构本身正确；当前冻结 Workflow 的职责/产物路径不匹配本需求。
3. `app/v1/conversation_failure.py::failure_summary` 将失败消息 `splitlines()[0][:500]`。第二次 Job 的真实最后回执包含 `columns.6.acceptance_checks.0/.1` 和 `A scenario report requires a behavioral command`，Web 最终只显示“2 validation errors for WorkflowDefinition”，用户无法看到可操作原因。第一次 JSON 字符串错误还被截断在参数正文中。失败事实虽然在工具回执与 DB 中存在，交互层却未可靠带出，这与已约定的确定性失败传递原则冲突。

## 本轮最小代码修复范围

先修 **3**，不改已冻结 Project 规划实体：在 `app/v1/conversation_failure.py` 增加失败消息归一化，仅取首个或前几个诊断路径及其 `Value error` 文本，剔除 Pydantic 的 `input_value`、文档 URL、大型参数内容和堆栈；普通单行错误维持简短原文。`failure_summary` 对最后失败工具保留 capability 和可读原因，并在重复失败停止时继续显示该原因。`tests/test_conversation_partial_effects.py` 增加本次 `WorkflowDefinition` 多行 ValidationError 和长 JSON `ContractError` 回归，要求 UI 可读信息包含具体错误而不输出原始计划 JSON。此处是向用户展示业务准入原因，不在 Web 展示 debug trace。

对 **2**，先形成修订 Workflow 的明确目标：`system_test` 负责其需要的浏览器测试源码路径和执行，`delivery` 的行为检查可复用前列已产生的可执行 runner，但必须重新运行并写新鲜报告；构建命令只能作为附加 artifact。可在 `loops/ddd-software-delivery/loop.json` 的将来版本为 system_test 明确增加 `e2e/**`、`tests/**` 可写路径及相应说明，经 Loop loader/Workflow 单测后仅影响新应用；本 Project 的 revision 1 不可就地改。当前 Project 若继续，应由正式 Conversation Agent 保存新 Workflow Plan、发布 revision，再配置验收和创建 Task；不能直接改数据库或手工将 Worker 脚本写进交付目录绕过流程。是否需要独立新 Column 取决于责任是否能由既有 `system_test` 单 Agent 清晰承担，不为了名字增加列。

对 **1**，需要单独设计一个通用、可验证的逐阶段规划合同：让 Agent 一次处理一列验收草稿并得到该列及全局剩余项的结构化诊断，最终原子发布；只有完整且真实的行为检查可激活 Workflow。这可能涉及持久草稿与现有 `workflow.acceptance.configure` 的兼容，范围大于此次失败显示修复，不能在没有状态/并发/版本测试时当作小 patch。当前再次提交同一五列大 JSON 已两次失败，暂停这种无效 token 消耗。可利用先前 `2026-09-29-conversation-objective-effect-contract-plan.md` 的目标—副作用准入，要求修订回合以真实新 revision 为完成条件；该计划目前尚未实施。

## 验证与继续测试

先按上述最小范围修复失败摘要并运行相关 Conversation 测试，安全空闲重启后用一个受控失败回归验证 Web/Conversation API 的确显示业务诊断。当前 Project 保持 Workflow revision 1、Task 0，作为失败证据。后续在完成逐列规划合同或明确修订方案后，沿同一 Project 经 Conversation Agent 发布适配的 revision；若需要改 Loop 模板并重开 Project，必须标记新 Project 与 Loop 版本，不能把本次五轮讨论及失败覆盖掉。再次有 Task 后每约三分钟看控制面推进与 mailbox；只在终态检查交付质量。

## 最小修复实施

`app/v1/conversation_failure.py` 已提炼 Pydantic 多行 ValidationError 的具体路径和 `Value error`，剔除原始 `input_value`、文档 URL 与长 JSON；Plan 字段误传 JSON 字符串时显示类型错误而不回显计划正文。新增两条回归于 `tests/test_conversation_partial_effects.py`。相关 Conversation 测试 **31 passed**。本修改只改善失败事实进入交互层的确定性，不解决模型生成五列验收配置的能力，也不更改当前 Project revision。

全局 `queued/running` Conversation Job、Agent Run、Column Run 均为 0 后，停止原 8000 监听 PID 18820，使用原 `startup.bat` 隐藏窗口重启；新监听 PID 29924，`/v1/health` 返回 200、Supervisor/Gateway 均 running。控制台重定向仅在用户 Temp 目录 `devwerk-retest-20260930.*.log`，没有新增产品日志文件。重启加载已修失败摘要；由于未再向 LLM 发相同错误请求，真实 Web 的新错误文案尚未黑盒复现，不能把单测误写成线上验证。

## 下一轮代码方案：可逐列配置的冻结验收

用户要求循环修复直至完整交付。下一轮先改下述**通用接口行为**，再开全新 Project 复测；旧 Project revision 1 保留，不暗改冻结实体。

`app/v1/capabilities.py::_workflow_acceptance_configure` 当前虽允许 `columns` 只列部分，但随即对整张图执行 `acceptance_diagnostics` 并拒绝缺其它列的配置；同时要求模型每次重发全量 `acceptance_obligations`。一个八列 Workflow 只能一次写对至少五列的命令、场景、报告和 invalidated_by，和“每列独立上下文、低成本逐步推进”的设计相冲突。可在不新增 DB 表和 Agent 的前提下使用现有不可变 Workflow revision 作为阶段性配置快照：

1. `workflow.acceptance.configure` 的 `acceptance_obligations` 改为可选。**省略时**保留未修改列的义务，并从当前 WorkflowPlan 的 `task_contract` 为本次修改列的 behavior check 自动生成义务；invalidated_by 按冻结 `required_behavior_columns/acceptance_invalidated_by` 计算。**显式提供时**维持现有全量替换语义，兼容已存在客户端。
2. 对当前调用始终拒绝结构非法、`NonBehaviorCheck`、`AcceptanceEvidenceScopeCollision` 等已知不可能产生真实证据的检查。其它列尚未配置、全局场景覆盖尚未齐全时，允许发布**不可创建 Task 的部分 revision**，回执明确 `ready_for_task_plan=false`、精简 `remaining_diagnostics` 和下一列。`task.plan.validate/save` 已有完整 `acceptance_diagnostics`，仍是执行准入闸门；不可借部分配置绕过。现有 `workflow.publish` 已可发布缺完整验收的 revision，因此这与当前持久化模型相符。版本检查 (`base_revision_id`/hash) 和发布保持同一事务；并发旧 base 继续拒绝。
3. `app/v1/capabilities.py::_task_plan_contract` 返回冻结的 `required_behavior_columns`、各列必需 `invalidated_by`，帮助同一 Conversation Agent 一次只处理一个列。`app/v1/conversation_report.py::render_report` 对被引用的部分配置回执要如实呈现“已保存但 Task 准入仍缺 N 项”，不能将 partial revision 渲染为已完成交付。最终 `ready_for_task_plan=true` 只表示合同完整，实际测试仍由 Column Runtime 用新鲜报告验证。
4. `loops/ddd-software-delivery/loop.meta` 升版本至 1.4.1，`loop.json` 的 `system_test` 责任和写入范围明确允许该**同一单 Agent**在 `e2e/**`、`tests/**`、`scripts/acceptance/**` 建立独立自动化 harness，并写 `docs/test-report.md`。开发列仍负责其本端实现与测试。`delivery` 只执行前列所有的可复现行为 runner、构建/启动并写交付报告，不凭 build/package 冒充行为验收；`accept` 独立重跑所需行为。此模板修改仅用于新的 Loop 应用，不更改旧 Project 的不可变 revision。

回归重点：分次配置 backend→frontend→system_test→delivery→accept，每次产生新 revision、累计保留已配置检查和自动义务，前四次 TaskPlan 被拒、第五次满足合同；显式 obligations 旧行为不变；同一命令跨阶段伪覆盖、echo/type/skip 仍拒；旧 base 并发拒绝；partial 回执与 Conversation 结果不误报交付；Loop version/digest 与 system_test 写路径。运行聚焦测试后再运行覆盖规划/Loop/Conversation 的回归，空闲重启，开启全新 Project 并重复五轮讨论和执行测试。

### 实施与代码回归

`app/v1/capabilities.py` 已将 `workflow.acceptance.configure` 的 obligations 改为可选；省略时按冻结合同推导本次列的行为义务并保留其它列，显式传入仍全量替换。部分 revision 只容许“尚缺检查/覆盖”，已知伪行为命令、跨阶段同证据复用和反馈合同错误继续拒绝；回执给出 `ready_for_task_plan` 与剩余诊断。`task.plan.contract` 暴露必需行为列和失效维度。`app/v1/conversation_report.py` 将部分配置如实报告为 Task 计划准入未完成。`loops/ddd-software-delivery` 版本升至 1.4.1，扩展 `system_test` 的独立测试源码范围并明确其责任；已冻结旧 Project 不变。新增分列配置、TaskPlan 持续拒绝直至完整、部分回执呈现的测试，更新 Loop 合同测试。规划/Loop/回复聚焦测试 **80 passed**，Conversation 边界/完成协议 **69 passed**。下一步安全加载服务并开启新的 Project；不能把单测当作黑盒完成。
