# 通用 Agent 核心去除领域指导：修改方案

## 目标与边界

DevWerk 核心只定义通用 Agent、工具调用合同和 Workflow/Task 运行规则。`loops/` 是运行时发现的可选外部方法包；核心不能以小说、软件开发、前后端列名、特定构建工具或 Windows 编码经验替 Conversation Agent 选择流程。保持现有架构和工具 ID，不改变已发布 Workflow 的冻结定义。

本次先清除核心中的具体指导，再执行定向回归；新黑盒测试必须仅用自然语言描述交付目标与授权执行，不指定 Loop、列数、Task 数或工具调用顺序，由 Agent 自行选择。若新测试暴露流程阻塞，再基于证据作最小修复。

## 定位与修改

1. `app/v1/conversation.py` 的 `turn_authority.instruction`：删除小说续写、固定列顺序和特定交付物分配的指导。保留会话授权、Task/Worker 路由、通知与执行边界。
2. `app/v1/capabilities.py` 的 `project.command.run` 工具说明：保留进程生命周期事实与命令结束语义；移除 npm/npx、PowerShell BOM 等具体技术栈建议。
3. `app/v1/services/task_plan_compiler.py`：删除写死 backend/frontend/delivery 的 Runner 冲突检测。运行时已逐次检查报告的新鲜度、声明的每个场景断言和测试源哈希；同一 Runner 可以合法覆盖多个场景集，静态规则会错误限制 Workflow。删除 Maven 生命周期推断，保留通用的“明显非行为性命令不能证明行为验收”规则。
4. `app/v1/capabilities.py` 的行为 Runner 错误文案：不提 Maven/npm，按通用的脚本参数合同提示。
5. `app/v1/services/completion_admission.py`：当前用 Maven/npm/Vite/pytest 等工具名猜测成功命令能否覆盖先前失败。直接改为“任意成功命令”会产生虚假通过，因此改为相同操作成功重试自动消除失败；不同操作由 Worker 在 `column.complete.failure_resolutions` 显式关联失败/成功回执和原因，Runtime 检查回执顺序、相同能力、成功状态、引用及明显非验证命令。冻结验收检查仍由 Runtime 运行。
6. `app/v1/completion_protocol.py`：把上述通用故障关联协议写入 `column.complete` 工具说明，删除“可识别的 build/test”一类技术栈提示。
7. `DEVWERK.md`：该文件会注入 Conversation Agent，删除“实现/审查/返工固定分列”“最终验收列”“未来测试脚本”等领域/模板假设；保留可选 Loop、工具合同、独立 Worker、通知和真实回执的通用原则。

## 回归

- 测试任意列名与不同场景集可以复用同一 Runner，运行时仍分别验证各自所需场景；既有软件列名不触发额外规则。
- 测试不存在领域指导的运行时提示与工具描述；Loop 仍通过目录扫描可选使用。
- 运行受影响的合同/验收单元测试；随后重启代码并创建全新 Project，以自然语言多轮讨论和一次明确执行授权验证 Agent 自行选流程、创建 Task、Kanban 推进和交付。

## 实施结果

已移除核心会话中的小说续写与固定 Column 顺序指导，平台策略中的固定“实现/审查/返工/最终验收列”和未来测试脚本预设，以及命令工具说明中的 npm/PowerShell/BOM 建议。`loop.apply` 仍是可选工具；`LoopCatalog` 仍只扫描 `loops/` 外部目录。

原软件列名专用的 Runner 冲突判断已删除：同一 Runner 能合法覆盖多个场景集，运行时 `acceptance_execution.run_acceptance_check` 仍逐次检查新报告、场景断言和测试源哈希。先前按编程工具名判断“后续成功命令自动修复失败”的规则改成显式 `failure_resolutions` 证据关联；完全相同操作的成功重试仍由 ledger 自动处理。

最终代码单元回归：受影响合同、Loop 与平台策略测试 129 passed；ConversationGateway 完整合同测试 28 passed（允许 Windows asyncio socketpair 的本机环境）。沙盒内运行 ConversationGateway 用例会在 asyncio 创建 socketpair 时阻塞，已用 Python faulthandler 证实为环境限制，因此该组在有权限的本机测试环境执行。

黑盒项目：`prj_0a015cf429524ea6a73af2433131d3d8`，从空 Project 开始。五轮纯自然语言讨论均成功且没有提前建 Task；Agent 自主提出防刷方案、清零时机、验收边界、交付技术选项和工作顺序。第六轮仅授权交付目标和实际验收，不指定 Loop、Column、Task 数或调用顺序。执行 Job：`cjob_1e2fbd16adf4463e89ae119ace053c22`。Workflow/Task 实际创建和执行结果待补。

第六轮首个五分钟检查：Conversation Agent 已自行 `loop.inspect` + `loop.apply` 发布八列 Workflow `wfrev_369caa4efa2e4275a784f8a03dd6759f`，但 Task 尚未创建。此前它先直接写了 24 个后端/前端/验收源文件，随后 `task.plan.save` 一次传 JSON 字符串被 schema 拒绝，另一次把八个生命周期阶段当作八个 Task，被 Task Agent-use 合同拒绝。此后又多次读写 Project 文件。该行为显示“主 Agent 规划、Column Worker 实施”的边界尚未达成，且错误修复消耗大量工具调用；先等待当前 Job 自我恢复结果，不在运行中打补丁。

第二个间隔检查：Agent 在多次合同错误后终于通过 `task.plan.validate` 和 `task.plan.save`，保存了四个 Task（后端、前端、系统测试、交付），但此时仍未 `task.create`。该 Loop 的 `flow_unit` 是独立可验收的完整交付范围，所有 Task 都会经历相同八列；这四个阶段 Task 虽有不同 `requirements_path` 因而通过身份检查，语义上仍可能造成重复工作。当前继续观察是否能真正创建和推进 Task，不把保存计划误报为交付。

第三个间隔检查：Job 仍运行，Task 数仍为 0。工具序列已到至少 173：在一次 `task.plan.save` 成功后，Agent 未调用 `task.create`，而是再次授权、反复提交新 Plan，出现 `requirements_path`、`acceptance_scenarios`、`entry_evidence` 相关合同错误。错误属于可诊断的参数/计划错误，但主 Agent 未沿工具合同收敛；它还直接运行命令并继续改源文件。当前仍让原 Job 运行到自然结果，以判断预算边界和恢复路径。
