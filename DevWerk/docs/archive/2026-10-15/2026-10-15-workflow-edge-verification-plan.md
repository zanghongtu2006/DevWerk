# 已由当前最小修正方案细化：由 Workflow 有向边约束验证证据

当前文件级实施方案见 [单一 Workflow 出边的最小修正](2026-10-15-single-transition-minimal-fix-plan.md)。本文件保留架构讨论背景；实施以新方案为准。

## 架构修正

先前在 Task Plan 加验证义务、再由 `store.finish_run` 针对 `done` 做统一审查的方案撤回。那会在 Workflow 图外形成第二套结束判定。应由 Workflow 自己定义列、出边及每条边的条件；Runtime 仅校验所选边的声明条件并沿图移动，不按 `done` 或 `accept` 等名字推断额外规则。

现有 `Transition(outcome, target)` 描述有向边，其 `evidence_requirement` 只能表示一般证据是否必需。拟增加可选的边条件引用，例如正式检查 `(column, check_key)` 或指定 Column 的结构化审阅输出。无条件边默认为空。引用需核验 Task、Workflow Revision、Run、结果和返工后的新鲜度。条件不能使 Runtime 自行改写目标；不满足时拒绝这次提交，由 Workflow 定义的失败或返工边处理。

`column.complete(outcome, output)` 匹配当前列的声明边。Runtime 执行该列正式检查、核验所选边条件，再移动到目标。指向 `done` 的边与其他边使用相同逻辑，没有独立终态验收守卫。无独立验收的项目可以定义单列 `produce → done`、空边条件；Agent 满足本列输出契约与通用完成协议即可结束，不需要 `accept` 列、验收项或虚构的“通过”结论。

本次登录任务的 Workflow 若要求 HTTP 与页面操作，作者应给成功边配置两项不同的实际验证。纯 HTTP 脚本不能充当页面操作回执；没有页面验证时不能通过该边。企业调研也可以对来源、落地记录设置自身的边条件，或直接由产出列到 `done`。核心不内置任何领域名称。Runtime 可以查引用和回执，不能自动判断脚本是否真实操作了页面，也不能从用户自然语言发现规划 Agent 漏写的条件。

## 代码边界

- `app/v1/domain.py`：`Transition` 增加可选条件引用，`WorkflowDefinition.validate_graph` 校验列、检查 key、输出字段及引用可达性。不在 `TaskPlanItem` 加第二套义务。
- `app/v1/capabilities.py` 与 Workflow 操作协议：向 Conversation Agent 展示条件参数和校验错误，冻结 Task 所用 Workflow Revision。
- `app/v1/agent_tool_execution.py`、`app/v1/services/completion_admission.py`：仅核验当前选中的出边；指定检查回执来自既有正式检查执行路径，不让普通命令成功冒充。
- `app/v1/store.py`：保持按声明的 `outcome → target` 完成。事务内可重复核验边条件新鲜度，但不新增 `terminal == 'done'` 全局守卫。既有 `acceptance_obligations` 如与边条件重复，须收敛为单一的 Workflow 出边语义，不能并行裁决。
- `readiness.acceptance_criteria` 改名并可选化仍按独立记录处理；它是规划提示，不是边条件。

## 回归

无条件单列 Workflow 可到 `done`；有条件的边在缺失、失败、错 key、旧 Run 或错误 Revision 时不能通过；同列不同 outcome 只检查各自的边；返工后旧证据不可复用。黑盒测试必须同时检查 Workflow 是否覆盖用户要求，以及实际执行证据；仅做引用检查不能证明规划完整或脚本真实。

本文件为修订方案，尚未实施代码修改。
