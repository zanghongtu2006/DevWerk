# 待修改：Readiness 的 `acceptance_criteria` 命名与实际语义不符

## 结论与边界

`ReadinessDecision.acceptance_criteria` 和 `TaskPlanReadiness.acceptance_criteria` 当前均为必填的 `list[str]`（`app/v1/domain.py`）。它们是规划文字，随 Task Plan/Task readiness 保存；Column 默认可从 `current_goal.readiness` 读到 Task Plan 的文字。Runtime 不解析文字、不凭它选择验收 Column、不凭它运行检查，也不凭它阻止 `done`。真正的路由由 Workflow `transitions` 决定；可执行检查另由可选的 `acceptance_checks` / `acceptance_obligations` 定义。

因此该字段的名称和必填性暗示了代码并未提供的强制验收语义。此问题与“未测试却通过”分开处理：改名和改为可选不会修复证据误判。

## 已确定的修改方向

1. 承认该字段只是规划提示，将名称改为不暗示强制验收的名称，例如 `completion_notes`；默认空列表。没有独立验收 Column 的 Workflow 可完全不填写。Workflow 是否存在验收 Column 仍仅由其图定义，不因该字段有无而自动增删节点。
2. 修改 `app/v1/domain.py` 的两个 Readiness 模型、`app/v1/capabilities.py` 的工具 schema/说明、`tests/helpers.py` 及相关测试。检查 Task Plan 的保存、加载、Worker 上下文和 backlog 路径，保持现有信息在填写时能够继续传递。
3. 尽量兼容已发布协议：旧请求中的 `acceptance_criteria` 可作为输入别名映射到新字段；明确两个字段同时提供时的处理规则，避免静默覆盖。工具协议对外提供新字段和迁移说明。已有持久化数据读取须有兼容路径；允许清空 1.0 前测试生成物和重建本地 DB，但不以删除数据代替协议兼容。
4. 回归覆盖旧字段输入、新字段输入、省略字段、重复字段冲突、保存/读取、Column 上下文、无独立验收 Column 的 Task 到 `done`。修改只处理命名和可选性，不借机增加验收机制或场景指导。

## 另案排查

“未测试却通过”的直接路径是：Workflow 成功 outcome 与实测证据未按冻结场景逐项绑定，Column Agent 对 HTTP 证据及页面证据的适用范围判断错误；`acceptance_criteria` 文字字段没有参与 Runtime 决策。需以具体 Project 的 Workflow 快照、原始工具回执、Column 输出和完成校验代码继续核实，不将两个问题合并修复。
