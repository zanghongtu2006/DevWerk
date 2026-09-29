# Conversation 目标—副作用合同：黑盒阻断后的系统级修复方案

关联现场：`2026-09-29-login-blackbox-system-retrospective.md`。这是 DevWerk 通用 Conversation Agent 的可靠性设计，不是登录 Demo 的专用流程，也不增加 Web 调试信息。

## 判定与量化

登录黑盒 Project `prj_68f94e0f4ba64a1398e48bd61d88270d` 的原 Task 因第 13 次前端 Assignment 再次达到 100 次模型调用而 `recovering/paused`。讨论、Workflow、Worker、反馈、mailbox 有实际持久化轨迹，但未交付。修订 Workflow 的四个用户 Conversation Job 分别止于旧授权快照、空执行误报、Provider 529、以及“recovering Task 禁止保存 Plan”的无回执误判。后者在 `workflow.inspect` 和 `workflow.plan.list` 后直接答复，根本没有调用 `workflow.plan.save`；仓储和规划授权没有这个禁令。

从 `llm_usage` 按四个 Job 时间窗统计：12+12+10+18=**52 次请求**，输入 **953,196 tokens**、输出 **31,303 tokens**、合计 **984,499 tokens**；最后一个 Job 单独耗费 **374,457 tokens**。这些时间窗没有并行的其它 Conversation Job；数字是使用量记录，不是模型上下文峰值。结果仍只有原 `wfplan_98872984b1b54c55bdd7bf6195803fc1` 与 active revision 4。继续原样重试既消耗 token，也不提供新的诊断信息。

## 根因层次

1. **模型语义决定与 Runtime 事实未闭环。** `TurnResolution` 只记录 `act/execution_request/scope_summary` 等权限及需求上下文，没有“本轮具体目标对象/完成条件”。`render_report` 现要求 execute 回合存在某个真实副作用，但不核对副作用是否满足用户要保存 Plan、发布 Workflow、修复 Task 或投递 Worker 输入的目标。
2. **工具目录和提示承担了过多流程约束。** `ConversationGateway._process` 给同一主 Agent 全部工具；`ProviderTurnRequester.request` 在边界未决时仅暴露 resolve，授权后又给整个工具集。完整工具 schema、历史与状态帧使多轮调用昂贵。稳定前缀可能有缓存收益，但本次实际 usage 显示反复数万 token 的输入；不能仅凭缓存假设成本可接受。
3. **失败没有可验证的下一步。** `conversation.reply(mode=blocked)` 在没有失败工具回执时可根据自由文本结束。模型于是把推测当作 Runtime 禁令。用户知道被阻断，却无法区分真实权限/验证错误、Provider 问题和 Agent 自行放弃。
4. **Workflow 预设与 Column 边界失衡。** 这是另一个独立阻断：`frontend_development` 被下游反馈推去修浏览器 harness，导致多个 100-call Run。目标—副作用合同只能修 Conversation 控制面，不能代替修订 Workflow、重新冻结验收和运行 successor Task。

## 设计约束

- 每个 Project 的 Conversation Agent 仍是唯一主 Agent；Column 内仍一个 Worker；mailbox 只通知。执行目标合同属于该 Conversation 回合的控制面，不成为第二个 Main Agent 或新的审批层。
- 不靠字符串关键词推断用户授权；`conversation.turn.resolve` 仍是权限边界。目标类型由同一 Agent 在该边界声明；Runtime 只验证声明的目标与真实工具回执/实体。**模型可能误解目标**，因此这种合同不能数学上保证自然语言意图匹配；必须在用户可见答复中准确显示声明目标及达成事实，不能宣称解决所有语义误解。
- 保留现有 `start_task/mode/Requirement/Grant` 语义；不因旧 Task `recovering` 禁止保存新 Workflow Plan。任务控制必须显式引用 Task ID。
- 不自动生成或运行 Task 以“完成”规划目标，不暗中改 Web UI，不通过直接写数据库补模型漏掉的动作。
- 输出从当前 Run 的成功回执及重新读取的实体构造。`conversation.reply` 引用的旧 Task、其它能力成功、纯读操作、Agent 文本，均不能证明指定目标已达成。

## 建议分三次可独立验证的小步实施

### A. 本回合目标声明和持久化

在 `app/v1/conversation_intent.py::TurnResolution` 增加结构化 `completion_targets`：数组元素含 `capability`（现有 registry 中标记为 write/process/control 的 ID）、可选 `subject_id`、`effect_key`（例如 `workflow_plan_saved`、`workflow_published`、`task_controlled`），而不是自由文本。目标列表最多数项、去重且有合理上限。`act=execute/control` 要求至少一项；`discuss/status/clarify` 不允许带目标。`control` 的目标从现有 `control_capability/control_task_id` 自动构造，避免双份不一致。将用户明确要求的多步骤规划结果拆成多个目标，保存 Plan 与发布 Revision 不互相代替。

在 `app/v1/repositories/conversation_intent_repository.py::resolve`，在同一事务校验目标 capability 存在、允许该 Project Conversation Agent 使用、与阶段/显式 Task ID 一致；写入现有 `turn_contract_json` 并包含在 resolve receipt。不得把模型自报目标当作新授权；所有 mutation 仍通过 `denial` 和 Grant 检查。旧 Job 的 contract 缺字段时按现状兼容读取，不能迁移后误使历史 Job“成功”。`context_manager.py::_refresh_conversation_authority` 每次 Provider 请求展示最新 contract，因此模型可见未完成目标。

风险：Agent 可能把用户请求错误地表述为别的目标。回归测试需给出显式用户消息与所选择的目标，检验 Runtime **只保证对该声明的事实一致性**；不写关键词规则假装能验证语义。若生产观察到高误分类率，可在后续增加用户显式选择的结构化 action，需另行设计，不夹进本次 Web 页面。

### B. 目标完成/阻断的确定性准入

在 `app/v1/conversation_report.py::render_report` 与 `app/v1/conversation.py::ConversationGateway._process` 复用一个小的目标评估函数（建议新建 `app/v1/services/conversation_effect_admission.py`），只读取当前 Job 的 durable contract、`v1_tool_invocations` 和对应实体。对每个 target：查本 Job/AgentRun 的成功且真实持久化调用，capability 必须匹配；按类型核对输出中的对象 ID/版本以及仓储当前记录（Plan ID、Workflow revision、Task ID/状态、Worker input ID 等）。`workflow.plan.save` 只能由它的成功 receipt + `get_workflow_plan` 证明，`workflow.publish` 只能由新 revision 证明；成功的 `project.files.write` 不能完成这两种目标。对不具备稳定输出实体的 capability，第一版仅用成功 invocation 证明并明确结果范围，不猜测更强状态。

`work_result` 只有**所有声明目标**完成才允许结束；所引用 `tool_call_ids` 应包含证明目标的当前 Run 调用，而不是用未引用的副作用或旧 Task 代偿。部分目标成功时，保留事实并返回“部分完成/未完成目标”，不把整个 Job 标成完全完成。`blocked` 如果声称 Runtime 拒绝，须引用当前回合失败回执；Provider/预算异常沿已有失败路径标明；无失败回执的模型自行放弃应是 `goal_incomplete`，不能渲染成系统禁令。最终用户消息只展示对象和状态，不显示 debug/context 内部字段。

回归：保存 Plan 成功但未发布时不准称修订 Workflow 完成；旧 Task + 无目标回执拒绝；不相关文件写入拒绝；失败工具能形成真实 blocked；无失败工具的“recovering 不允许保存”归类目标未完成；已完成目标 + 另一目标 Provider 失败保留部分成功；跨 Project/旧 Run 回执拒绝；讨论/status 路径行为不变。

### C. 每阶段成本与恢复边界

只在 A/B 稳定后评估 `app/v1/agent_provider.py::ProviderTurnRequester.request` 的阶段工具投影。以已声明且未完成目标及必要 inspect/validate 工具构成**可扩展相关工具集合**，但保持 `conversation.reply`、当前阶段检视、错误恢复、依赖目标能力；不能把通用主 Agent 锁死在单一命令里。应先记录本地 schema token 估算、Provider 实际 `input_tokens/cached_input_tokens` 和完成率，再改目录；否则缓存前缀被破坏可能反而更贵。此项不是 A/B 的前置改动，也不是提高模型调用上限。

目标完成后可由 Agent 提交下一阶段目标；未完成时 Context checkpoint 必须保留目标和未完成项、最近失败回执 ID，避免压缩后“忘记要保存 Plan”。`ContextManager.prepare` 已有 authority refresh 和 checkpoint；本阶段只加这些小字段的保留与测试，不改变 Worker context 管理。

## 验证顺序与黑盒准入

先做 A 的 schema/持久化单测，再做 B 的回执/实体/报告单测；每步独立 diff、测试和记录。随后在全局无运行中 Job/Task/Run 时重启 8000。正式 Conversation API 只提交一次修订 Plan：必须观察 `workflow.plan.save` 成功且 Plan ID 存在；下一回合发布：必须观察 `workflow.publish` 成功且 active revision 增加。失败则记录当前目标、失败回执和 token，不再发同语义提示碰运气。成功后按 `2026-09-29-browser-workflow-boundary-and-evidence-plan.md` 安置独立单 Agent browser harness Column，再创建 successor Task 继续黑盒。每约 3 分钟查一次 Kanban 控制面，直至终态后审查交付物与验收预设；停止时保留旧 Task 现场。

本文件是实施方案；截至撰写时 A/B/C **均未实施**，当前黑盒仍未交付。
