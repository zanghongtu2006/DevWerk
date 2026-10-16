# 值班交接黑盒阻断：代码复盘与小范围修复方案

日期：2026-09-28。关联项目 `prj_00c8fd54148b44ba8b513238797a0770`，原始记录见同目录 `2026-09-28-shift-handover-software-black-box-regression.md`。

## 证据与边界

只读检查 `data/devwerk.db` 的 `v1_agent_runs`、`v1_tool_invocations`、`v1_workflow_revisions`、`v1_work_intents`。`data/logs/devwerk.log` 的最后修改时间为 2026-09-21 20:25，不覆盖本次 09-28 测试；本次详细时序以数据库持久化回执为准。未修改测试项目目录或数据库。四个失败 Run 合计 117 次模型迭代、141 次工具调用、41 次失败；讨论阶段 8 轮成功，执行授权后已创建 Workflow revision 2，但始终没有 Task Plan 或 Task。

1. `agent_provider.ProviderTurnRequester.request` 仅对 `unresolved`、`discussion`、`targeted_control` 限制工具目录。执行轮次解析成功后仍向模型暴露 `conversation.turn.resolve`。第二、三次恢复以及状态问答均重复调用已解析轮次，得到 `TurnAlreadyResolved`，又触发 `agent_tool_execution.AgentToolBatchExecutor` 的重复失败保护，整轮变成 `ConversationProtocolStalled`。保护机制本身正确；不应移除。
2. `task.plan.save` 对 `TaskPlan` 和 Loop 的 Task 输入契约实施严格校验，这是正确的。但模型在 3 个轮次里反复混淆 `input.requirements_confirmed=true` 与 `entry_evidence.confirmed_scope=<execution_grant_id>`、`entry_evidence.requirements_file=<project.files.write execution_key>`；遗漏 `acceptance_scenarios`、使用多 Task 分拆同一交付、把整个 `plan` 序列化为字符串、错误填写 `exact_input_strings`。`task.plan.validate` 有诊断，却要求模型先自行拼出完整复杂对象。应给出当前冻结契约与真实证据的紧凑只读规划输入，并在保存失败时指出正确的下一工具；不放宽类型、证据和身份约束，不代填业务语义。
3. `services/task_plan_compiler.acceptance_diagnostics` 仅拒绝已知非测试命令和缺失场景覆盖。revision 2 的 backend、frontend、integration、system_test、delivery、accept 六列均指向同一条 `python -m pytest backend/test_scenarios.py -v --tb=short`，同一 `docs/test-report.md`，却将它分别宣称为前端界面和进程清理证据。`services/acceptance_execution.run_acceptance_check` 后续会验证报告新鲜度、断言及源码哈希，但不证明场景内容与列的业务语义一致。对这种具体的跨异质阶段复用配置，应在 `workflow.acceptance.configure` 与直接 `workflow.publish` 两条 Agent 发布路径拒绝；不能声称静态校验能识别任意测试脚本是否真实测了浏览器或进程。
4. 已实读测试项目 `requirements/baseline.md` 第 64 行，它把用户消息 569 已确认的完成权限写反。最新 `v1_work_intents` 里没有该权限的结构化 decision，说明依赖 Agent 写出的自然语言基线仍有语义误抄风险。软件无法可靠地从任意汉语自然文本推断「相反」；不做关键词式自动修文。要求 Conversation Agent 在写基线前读取用户原话和来源消息，按原话修正；当前错误基线不应视为已通过质量验收。该项目要恢复执行，必须先修正第 64 行并产生新的文件写入回执，再用新的回执保存 Task Plan。
5. 用户问失败原因时 `act=status` 已成功解析，后续仍调用变更工具和已解析 `turn.resolve`。数据库证明 Runtime 正确拒绝未授权变更；已解析工具目录收紧后，状态轮次应保留只读能力及 `conversation.reply`，并可在一个 Run 内返回可读解释。

## 代码修改顺序

### A. 轮次工具状态机

在 `app/v1/agent_provider.py::ProviderTurnRequester.request` 根据持久化 phase 计算可用工具：`unresolved` 只允许 `conversation.turn.resolve`；所有已解析 phase 从目录移除 `conversation.turn.resolve`。`discussion`、`targeted_control` 继续沿用当前只读/专用控制限制；`execution` 保留现有规划权限。对已解析后模型硬编码提交的 resolve，`app/v1/agent_tool_execution.py` 仍由既有权限检查拒绝，重复失败护栏保持不变。回归检查目录变化、执行可用规划工具、状态问答可正常回复。

### B. Task Plan 的冻结契约导航

在 `app/v1/capabilities.py` 增加一个**只读**规划辅助能力，返回当前 Workflow revision、Task Contract 的必填 input 字段/指针、必需 entry evidence 的真实候选 ID、已冻结的验收 scenario IDs 与每类值的形状提示。数据只来自 `get_workflow`/`get_workflow_plan`、当前 scope grant、`v1_execution_receipts`，不创建、修改或推断需求。输出应限长，避免再次放大上下文。`task.plan.save`、`task.plan.validate` 仍是唯一准入路径；辅助能力不生成 Task、不填 `readiness` 或业务描述。`conversation_intent.TURN_INSTRUCTION` 增加一条简短指向：遇到输入契约失败先读取这个工具，再构造一个跨所有 Column 的 Task，并先 validate 后 save。

### C. 异质列验收复用约束

在 `app/v1/services/task_plan_compiler.py::acceptance_scope_collision_diagnostics` 识别软件 Loop 的 `backend_development`、`frontend_development` 与 `delivery` 三种不同责任：若各列唯一行为检查拥有相同 command `argv`、`cwd` 和 `report_path`，却声称不同场景集，则拒绝配置，给出 `AcceptanceEvidenceScopeCollision` 及对应列路径。`acceptance_diagnostics`、`capabilities.py::_workflow_publish` 共用该判断，涵盖配置与直接发布。保留同一完整测试套件在 `integration_review`、`system_test`、`accept` 的合理复跑；也允许一个列额外运行共享回归套件。该规则只挡住本次被证实的碰撞，不企图证明脚本语义。对旧 revision 不做原地改写，新配置须发布新 revision，已有 Task 仍保持冻结。

### D. 回归与恢复

新增或扩展 `tests/test_conversation_intent_contract.py` 与 `tests/test_context_acceptance_regression.py`：解析前后 Provider 目录、status/execute 权限、只读规划数据的真实候选和空证据、错误类型与无副作用、跨列同命令碰撞及合法独立命令。跑聚焦测试与全套测试。最后记录测试结果和实际限制。生产项目不自动重试、不自动修改用户产品文件：先由后续正常授权的 Conversation 轮次修正基线、配置修正后的 Workflow revision，再验证并保存一个 Task Plan、物化 Task。

## 完成标准

- 已解析的用户轮次不再收到 `conversation.turn.resolve` 工具目录；状态问答仍可读取并回复。
- 规划辅助输出来自真实持久化证据；缺失证据明确为空，不伪造 ID；旧准入护栏不弱化。
- 本次 revision 2 的三类异质责任复用同一后端命令，会被新的准入拒绝；合法分列测试仍可配置。
- 测试覆盖上述场景，仓库回归通过。**不能仅凭单元测试宣称此次黑盒项目已完成交付**：真实模型调用、产品运行与需求语义正确性需要后续端到端复测。

## 实施与验证记录（2026-09-28）

- 已改 `agent_provider.py::ProviderTurnRequester.request`：执行、状态、讨论等已解析 phase 不再暴露 `conversation.turn.resolve`；保留未解析轮次只能 resolve 的现有规则。未修改重复失败保护。
- 已改 `capabilities.py::_task_plan_contract`：提供只读 `task.plan.contract`，按当前 Workflow/Plan、绑定 Requirement revision、活跃 grant 和同一 grant 的已完成文件写入回执返回候选；文件内容哈希已变化的回执不列为候选。不填业务输入、不创建实体。`task.plan.save` 描述与 `conversation_intent.TURN_INSTRUCTION` 指向先读取契约、校验完整计划，且复用既有 Workflow。
- 已改 `task_plan_compiler.py::acceptance_scope_collision_diagnostics` 和 `capabilities.py::_workflow_publish`：软件 Loop 的后端、前端、交付三列若各自唯一行为检查实际共用一条命令及报告，却宣称不同场景集，配置和直接发布均报告 `AcceptanceEvidenceScopeCollision`。对集成、系统测试和最终验收复跑完整套件不作一刀切限制。
- 聚焦测试 `test_task_entry_admission.py`、`test_conversation_intent_contract.py`、`test_context_acceptance_regression.py`：**74 passed**。补充测试检查真实证据、陈旧文件、status 轮次工具目录和碰撞配置的原子拒绝。
- 将新 `acceptance_diagnostics` **只读**作用于生产 DB 的 revision 2：前端列 `/workflow/columns/3/acceptance_checks` 与交付列 `/workflow/columns/6/acceptance_checks` 均报预期碰撞；没有修改原项目或 DB。
- 首次全套 `pytest tests -q`：**444 passed, 1 failed**，用时 566.53 秒。失败项 `test_agent_wall_clock_exits_even_when_provider_hangs` 在 0.1 秒极短 deadline 下实际抛 `ExecutionOwnershipLost` 而非预期的 `ExecutionBudgetExceeded`；该路径与本次代码改动无关。随后单独重跑该用例 **1 passed**，其所在 `test_p0_runtime_regressions.py` **36 passed**。补上直接 `workflow.publish` 路径后，完整套件重跑 **445 passed**，用时 550.79 秒。最终又把场景比较改成集合比较，避免只是 ID 顺序不同就误报；该单项聚焦测试再次 **1 passed**。极短 deadline 测试存在偶发竞态，未因此改动无关 Runtime。

### 仍须黑盒复测

当前项目 revision 2 与错误需求基线均保持原样，Task 数仍为零；修复框架后不自动篡改测试证据。下一次正常用户授权恢复时，应先按消息 569 的原话修正完成权限，取得新 `project.files.write` 回执；配置能分别验证前端与交付行为的新版 Workflow；读取 `task.plan.contract`，构造并验证一个贯穿所有 Column 的 Task Plan，再创建 Task。用真实 Provider 检查是否收敛及实际 Token/工具消耗。静态命令检查只能阻止本次确证的复用形状，无法证明任意测试脚本的业务语义；完整产品仍需真实运行和验收。
