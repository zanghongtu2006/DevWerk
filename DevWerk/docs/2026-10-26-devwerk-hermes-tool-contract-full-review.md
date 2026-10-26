# DevWerk / Hermes 工具契约完整对照与修复方案（评审稿）

> 2026-10-26。主 agent 审阅 DevWerk；独立 sub-agent 在不继承本对话的 session 中审阅 `D:\workspace\github\hermes-agent`，再交叉讨论。本轮只写方案，未修改实现、DB 或运行服务。本文取代同日的初版 `2026-10-26-hermes-style-tool-contract-alignment-plan.md`；初版对 Hermes 校验能力的描述过强。

## 1. 复现事实与 DevWerk 根因

`prj_60c65ad9f77e45f18febe8a565a6aa76` 最后一轮要求续写 20 章。Conversation Agent 取得 `extend` 授权；`project.scope.inspect` 回执明确给出整数 `requirement_revision=2`、当前 Workflow 可修订且无阻塞。它先用 `tool.catalog.select` 选择含 `project.scope.revise` 的工具，后又选择 Task Plan 工具。第二次选择**覆盖**第一次；后续真实 Provider 请求不再含 `project.scope.revise` 的 schema，模型仍原生调用该工具并提交 `"expected_revision":"2"`。第一次严格校验拒绝，第二次同样调用触发重复失败终止。没有 Requirement、Workflow 或 Task 变更。

| 责任 | 代码 | 现状 |
| --- | --- | --- |
| Run 可用集合与初始展示 | `app/v1/agents/agent_run_preparation.py::prepare` | `allowed` 覆盖整个 Run；渐进展示只发送 `DIRECT` schema。 |
| 后续展示 | `app/v1/tooling/tool_presentation.py::selected_ids/selected_schemas`、`app/v1/agents/agent_provider.py::request` | 最近一次成功的 `select` 决定额外 schema，后选替换前选。 |
| Provider 参数转换 | `app/services/anthropic_client.py::complete` | 按本次**展示**的 schema 调用 `canonicalize_contract_value`。隐藏工具取得空 schema，`"2"` 不会成为整数。 |
| 执行门 | `app/v1/agents/agent_tool_execution.py::execute` | 只查 `call.name in allowed`，不查它是否在本次 Provider 请求中展示。 |
| 持久化/重放 | `app/v1/agents/agent_runner.py`、`app/v1/repositories/agent_repository.py::prepare_operations/pending_operations` | 执行前保存原始工具名/参数/`tool_call_id`；恢复时按这些字段重建调用。 |
| 结果投影 | `app/v1/context/context_manager.py::prepare` | 所有 Tool 结果按约 `tool_result_tokens=4000` 的预算投影；直接新增大 schema 描述工具会被截断。 |

`tests/unit/tooling/test_tool_presentation.py` 还明确测试“新选择替换旧选择”，所以这是当前设计和测试一起确认过的行为，不是单纯模型幻觉。`ToolCatalogSelectReceipt.applies_on_next_request=true` 又暗示只影响下一请求，实际会持续到下次选择，语义不一致。

## 2. 隔离审阅的 Hermes 事实

| 主题 | Hermes 实现 | 对照结论 |
| --- | --- | --- |
| 会话 Tool 集 | `toolsets.py`、`agent/agent_init.py`、`tui_gateway/server.py`：按会话来源/启用的 toolset 构建当前工具；启用的 Project 工具直接展示，不是进程全局权限。 | DevWerk 需区分当前 Run 可用、当前请求展示、底层执行授权。 |
| 直接与延迟 | `tools/tool_search.py`、`agent/model_tools.py`：核心及启用的 Project/Desktop 工具直接出现，其他符合条件的插件/MCP 经 `tool_search`、`tool_describe`、`tool_call`。 | 可以借用桥接，但不能称 Hermes 把 Project API 都藏在桥后。 |
| 描述 | `tools/tool_search.py::tool_describe` 从当前会话定义返回 Provider 消毒后的完整参数 schema；目录按当前会话重建，不持久记住一次 search。 | DevWerk 目录应取当前 Run 的 Registry 快照。 |
| 结果预算 | `agent/tool_executor.py`、`tools/tool_result_storage.py` 允许大结果落盘并只给 preview/path。 | Hermes 不保证完整 schema 永远留在下一请求上下文；DevWerk 的 Workflow schema 需额外保证。 |
| 参数预检 | `tools/tool_search.py::validate_deferred_call_args` 仅查注册表原始 schema 的顶层必填键；`agent/model_tools.py::coerce_tool_args` 尽力转换顶层类型；两者并非全面 JSON Schema 校验。 | DevWerk 应保留自己的严格输入/输出校验。不得称 Hermes 能确保参数零错误。 |
| 隐藏原生调用 | `agent/conversation_loop.py` 按本次可见工具名处理未知调用，桥另行解包目标。 | DevWerk 不能让广义 `allowed` 直接许可隐藏原生调用。 |
| MCP 生命周期 | `tools/mcp_tool.py`、`agent/turn_context.py` 在轮间刷新会话工具定义，执行器按 Registry generation 缓存；MCP `inputSchema` 源于外部，并非强类型类自动生成。 | 待执行操作必须锁住目标 schema/版本，不能在重放时用新的 MCP 定义解释旧参数。 |

可借鉴的是**一致的可见入口、会话范围目录和延迟调用桥**。Hermes 本身也可能有类型错误、provider schema 与 raw schema 漂移或描述结果被投影的问题；不做 1:1 复制。

## 3. 方案选择及代价

### 3.1 三个集合独立

1. **Run 可用集合**：`spec.capability_ids` 经当前 Registry/Project 可用性解析，是上界，不能推导“可原生调用”。
2. **请求展示集合**：某一次真实 Provider 请求带出的原生 Tool 名及 schema，是该响应的不可变快照。
3. **有效执行集合**：原生调用必须在对应请求展示集合；桥接目标必须在 Run 可用集合、非 `INTERNAL`、当前可用，并通过原有授权/Column delegation/执行租约。

`ToolExposure.INTERNAL` 不可通过桥调用。Column Agent 仍沿用列定义给出的能力，不因 Conversation Agent 目录扩大权限。MCP 是显式动态契约；固定内置 Tool 仍由 typed class 生成 schema。

### 3.2 小型常驻 Tool + 无状态桥接

- 常驻 Conversation 核心入口增加 `project.scope.inspect`、`project.scope.revise`，保留 `workflow.inspect`、`task.inspect` 和已有通用 Agent 工具。常驻展示**不授予执行权限**；`scope.revise` 仍走既有用户授权。
- 新建 `tool.catalog.describe` 与 `tool.catalog.call`；`tool.catalog.search` 保留。`tool.catalog.select` 对**新 Run**退出 Provider 工具列表；旧 Run 的已落库调用仍可恢复。不要把 select 改成累加，因为隐式展示状态会继续增长并与预算冲突。
- `workflow.create/publish`、`task.plan.save/validate` 等大型图工具和动态 MCP 工具先走 describe → call。外层 `call` 用固定 typed 字段 `tool_id`、`arguments`、可选 `schema_digest`；`arguments` 是唯一显式动态对象，必须再按目标 Tool 的 schema 验证。一次桥调用最多触发一个目标效果。
- 这与 Hermes 的 Project 工具全直接展示**不同**：DevWerk 现有 19 个直显 Tool 约 15.6 KB；全部 75 个内置 Tool 约 126 KB；27 个图治理 Tool 约 79 KB；`workflow.create` 约 17.5 KB、`workflow.publish` 约 17.3 KB、`task.plan.save` 约 9.2 KB。全量直显会重现上下文成本问题。具体常驻清单可在真实 Provider 请求和首次调用成功率数据下微调，但不可按小说/软件等场景分支。

### 3.3 describe 必须真正可读

`tool.catalog.describe` 返回目标当前**Provider 参数 schema**、目标 ID、schema digest、可用性及必要字段说明；不是手写简化 JSON。`context_manager.py::prepare` 对最近一次尚未消费的描述结果保留完整 schema，并计入全局输入预算；在相应 call 或 turn 结束后按普通历史压缩。如完整 schema 加现有请求超过硬预算，返回明确 `ToolSchemaTooLarge` 和可读的分段/引用协议，不能返回“成功”却把必填字段截掉。首版设可验证的最大 schema 字节数；若超限机制未实现，禁止启用该目标桥路径。`agent.result.read` 可回看历史，不应成为读当前参数的必经拼图。

## 4. 单一调用链：Provider、校验、授权、审计、重放

### 4.1 请求和响应边界

`ProviderTurnRequester.request` 随本次响应保存展示工具 ID/schema 的请求快照。`anthropic_client.py` 只负责解析 Provider 线协议和原始参数，不能用“本次展示 schema 是否存在”决定业务参数是否规范化。保留模型原始工具名、原始参数在 trace 和会话消息中。

`agent_runner.py`/`AgentToolBatchExecutor` 在作用前按以下顺序处理：

1. 原生工具不在该次展示集合：返回 `ToolNotPresented`、search/describe/call 指引和 `rejected_before_effect`，无业务收据。
2. 桥接调用：先验证外层 typed contract；解析 `tool_id`，确认 Run 可用、当前可用、`ON_DEMAND`、digest 匹配。不能借桥跳过 `conversation.turn.resolve`、执行授权、规划租约或 Column 能力边界。
3. 从 `CapabilityRegistry` 读取目标 Provider schema 与 canonical input schema。复用 `app/v1/contracts.py::canonicalize_contract_value` 中无歧义的包装/数值字符串转换，随后对 canonical input 做严格 JSON Schema 和 preflight。比如整数契约下 `"2"` 可变为 `2`，`"2x"` 必须报字段路径/期望/实际类型。不可静默丢弃未知字段或任意宽松转换。
4. 以**底层目标 Tool** 执行授权、side-effect 分类、执行收据、输出验证、Task 启动判定、逻辑 ledger 和 Conversation 报告；以外层 Provider `tool_call_id` 回复模型。一次外层调用至多一个底层效果。

`CapabilityEntry.provider_input_schema` 可能不同于 `input_schema`：描述必须展示实际 Provider 形状，执行最终必须符合 canonical schema。注册时检查明显不兼容的双 schema，不能让模型按 describe 正确填参后仍被 canonical schema 意外拒绝。

### 4.2 外层调用和有效目标分别持久化

当前 `v1_agent_operations` 只有 `capability`、`arguments_json` 等原生字段，且在执行前写入。若简单把桥注册为普通 handler，收据、`queued_task_entry`、重复失败指纹、ledger 与重放会把 `tool.catalog.call` 当成业务目标，造成漏报或重复执行。需 typed `ResolvedToolOperation`，记录：外层 Provider 名/原始参数/`tool_call_id`，目标 Tool ID、规范化参数、schema digest、展示集合摘要及解析状态。`agent_repository.py` 用兼容迁移字段或版本化 payload 存储；旧操作按旧格式恢复。

顺序为：持久化模型原始回答 → 解析并持久化有效调用或拒绝原因 → 用同一 `operation_id` 执行一次 → 持久化目标 Tool 结果 → 交付外层 `tool_call_id`。恢复使用已持久化的目标及参数，不重新 search、规范化或按最新 MCP schema 改写旧调用；若旧 schema 已不可取，安全失败并保持可协调状态。`execution_key` 仍取 operation ID，保证桥不新造第二个收据。必须覆盖 `execution_recovery.py`、`execution_ledger.py`、`queued_task_entry`、`v1_tool_invocations`、`v1_execution_receipts` 与报告引用的消费者。**在这些映射闭环之前，不上线有副作用的桥接调用。**

## 5. 小步实施清单

| 步骤 | 文件/方法 | 完成标准 |
| --- | --- | --- |
| A. typed 契约与展示 | `protocols/tools/core/tool_catalog_contracts.py`；`tooling/builtin_tool_catalog.py`；`tooling/capability_registration.py`；`agents/agent_run_preparation.py::prepare`；`agents/agent_provider.py::request`；`tooling/tool_presentation.py` | 常驻工具不因 search/describe 消失；新 Run 不需 select；Provider 工具定义大小可测。 |
| B. 单一解析入口 | `agents/agent_runner.py` 传请求展示快照；`agents/agent_tool_execution.py` 做隐藏原生调用门；`tooling/capability_registry.py` 做目标规范化/校验；`services/anthropic_client.py` 删除展示依赖的业务转换 | `"2"` 成功，`"2x"` 明确拒绝；隐藏原生调用无副作用。 |
| C. 大 schema 回执 | `tooling/tool_presentation.py::describe`、`context/context_manager.py::prepare` | `workflow.create` 完整 schema 在下一请求可见，或显式超限；不出现成功但截断。 |
| D. 桥与持久化 | `protocols/agent/` 新增 typed 有效操作；`repositories/agent_repository.py::prepare_operations/pending_operations` 兼容迁移；`agents/agent_runner.py`、`agents/agent_tool_execution.py`、`execution/execution_recovery.py`、`execution/execution_ledger.py` 接通外层/目标映射 | 中断恢复后同一调用仅执行一次；Task 启动、ledger、报告均指向真实目标。 |
| E. 旧协议退场 | `tooling/capability_registration.py`、`builtin_tool_catalog.py` 及相关测试 | 新 Run 无 select；旧已落库操作可恢复，不改写历史。 |

可先完成 B 的隐藏原生调用拒绝和 Registry 规范化作为止血；D 完成前不能开启有副作用的 `tool.catalog.call`。不改 Workflow graph、Column 状态机、Loop 或场景规则。

## 6. 验证与通过标准

1. 单元测试：更新 `tests/unit/tooling/test_tool_presentation.py` 中“替换选择”的旧预期；覆盖 typed bridge contract、Provider/canonical 双 schema、整数转换、未知/内部/未授权目标、MCP schema 变更、隐藏原生调用、输出校验及 describe 投影。
2. 事务与重放测试：分别在准备操作、执行副作用前、收据提交后、结果交付前模拟中断；同一 `operation_id` 只作用一次，旧格式 operation 能恢复，外部效果未知仍走既有不确定状态。
3. 集成测试：同一对话依次 inspect、describe Task Plan、修订范围、建图/发布并创建 Task；目录操作不能让关键 Tool 消失；Column 现有能力边界不变。
4. 真实 Provider 新 Project 黑盒：至少五轮自然语言讨论，然后建 Workflow/Task，检查首次有效调用率、Provider 实际 schema、DB/文件收据、Task 推进；再修订已有 Workflow 并创建后续 Task。Agent 自述不能充当成功证据。
5. 预算：记录单轮 Tool 定义字节、完整 describe 字节、总输入 tokens、模型调用和失败重试数。不能退回每轮发送全部 75 个工具。若 describe 无法完整进入下一请求，桥接方案不得判为通过。

本评审稿不授权本轮重启服务。实施时须遵守 `AGENTS.md`：DevWerk 只用 8000；端口已占用时由用户决定，不擅自终止或换端口。
