# P0：行为验收计划的工具合同过难，主 Agent 无法派生 Task

状态：方案先行，证据为正式新 Project `prj_411cc4300702426092245f2d8e0da99b`。不改变 Project 主 Agent、Kanban 单 Worker/Column、Requirement 授权、冻结验收或 Runtime 判定，不直接制作测试 Project 的交付物。

## 根因

`software.ddd_delivery` Loop 的 Task Contract 要求五个 Column 在 TaskPlan 保存前各有可执行的行为断言检查；终端 `accept` 覆盖全部场景 ID，并在需求执行后失效时重新验证。`app/v1/services/task_plan_compiler.py::acceptance_diagnostics` 和 `known_nonbehavior_command` 正确拒绝“仅编译/仅查文件/仅打印”的伪检查，不能删除。

当前唯一编辑入口 `app/v1/capabilities.py::workflow.acceptance.configure` 暴露完整嵌套 `WorkflowDefinition.AcceptanceCheck`：Agent 每个 Column 要同时填写 capability、arguments.argv、evidence_kind、purpose、scenario_ids、report_path、launch_validation，再跨层编写 acceptance_obligations 与 invalidated_by。尽管工具允许省略 obligations 自动推导，真实模型仍手写完整数组。模型八次失败没有改变冻结的 Workflow，最后重放错误调用触发协议停机。第一轮曾用 `mvn test` 加任意 report_path 通过规划，但命令本身不会写该 JSON；若进入 Column，Runtime 仍会因缺新鲜报告失败。故仅修改提示词或放松验证都会隐藏下一处交付阻塞。

## 修复界面

在 `app/v1/capabilities.py` 新增 `workflow.acceptance.runners`，这是现有 configure 的窄输入适配层，不创建另一 Agent，也不执行检查。入参：当前 `base_revision_id` 与 `base_digest`，以及 `runners` 数组。每项仅需 `column`、`argv`、`report_path`、非空 `scenario_ids`、`purpose`，可选 `cwd`。接受一项对应一个行为检查；同一 Column 如需多个场景子集可提交多个 runner。Runtime 自动构造 `AcceptanceCheck`：`key` 由 Column 与序号稳定派生，`capability=project.command.run`、`evidence_kind=behavior`、`launch_validation=deferred`；仅配置列出的 Column，保留其它检查。省略显式 obligations，由现有 `_workflow_acceptance_configure` 基于冻结 Task Contract 自动派生正确 invalidated_by。返回原有 `ready_for_task_plan`、`remaining_diagnostics` 和新 revision/hash，支持分批配置。每个 `argv` 必须含 `report_path` 作为独立参数，表示测试入口明确接收新鲜报告路径；普通 `mvn test`、`npm build` 或仅打印命令不能作为此简化入口的行为 runner。继续由原有 `known_nonbehavior_command` 拒绝明显伪断言，由 Runtime 实际运行并校验报告内容；路径出现不代表测试通过。

Agent 可选择项目适合的脚本（例如 Python/Node/PowerShell）及可写位置，通常后端脚本在 `backend/**`，前端脚本在 `frontend/**`，系统级脚本在 `scripts/acceptance/**`，交付/最终验收可复跑系统级脚本但用各自报告路径。`argv` 和目标脚本仍是 Project/Worker 实际工作，不由 DevWerk 代写。Column Worker 必须实现脚本、真实断言和 `devwerk.acceptance-report.v1` 新鲜报告；只生成 JSON 不运行真实测试不能通过后续独立 Review/Runtime 检查。

`workflow.acceptance.configure` 保留供复杂/旧 Workflow 使用，在工具说明中明确其为高级原始结构；给主 Agent 的 `DEVWERK.md` 规划指引增加窄入口的选择条件与一个小型合法参数例子，并说明脚本可能尚未存在时使用 deferred、执行时仍须真实存在。`app/v1/context_manager.py::planning_projection` 的 `loop.apply/workflow.inspect` 回执 next 提示改为优先窄入口，避免继续诱导复杂嵌套工具。修改范围集中在工具适配、说明和测试，不改 Loop graph、TaskPlan schema 或验收引擎。

## 验证与边界

`tests/test_context_acceptance_regression.py`、`tests/test_task_plan_compiler.py` 与 `tests/test_task_entry_admission.py` 验证：有效 runner 自动生成 checks/obligations、分批修订后 ready、缺场景或 argv 未引用报告路径被拒绝、仅打印的伪检查仍被拒绝、终端场景覆盖仍强制、Workflow revision 竞争仍拒绝、旧 configure 仍可用。测试直接检查冻结 definition，不以说明文本为证。

单独提交后，在无在途 Job/Run 时重启服务，创建**新** Project 原样复用七条消息。目标是授权后真实创建 Task 并进入 Column；若仍失败，保留回执并定位下一根因。执行阶段约三分钟取样，优先看 Task/Worker/Mailbox/跨列推进；终态再检验交付物。若 Worker 无法实现冻结脚本或总是缺行为报告，再根据真实失败单独修正 Loop 指令或报告接口，不能提前削弱验收。
