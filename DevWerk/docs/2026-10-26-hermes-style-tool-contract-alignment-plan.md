# DevWerk 工具契约与 Hermes 对齐方案（待评审）

## 已确认的问题

`prj_60c65ad9f77e45f18febe8a565a6aa76` 中，两类失败有相同根因：`tool.catalog.select` 只保留最近一次选择；Agent 改选 Task Plan 工具后，`workflow.create` 或 `project.scope.revise` 的 schema 从 Provider 请求消失，但 Run 的允许调用列表仍让模型直接调用这些工具。`AnthropicClient.complete` 又只用**本次展示**的 Tool schema 规范化返回参数。于是 `expected_revision="2"` 既没有转换成整数，也没有在未展示工具的调用边界被挡回；入口 JSON Schema 拒绝后，重复失败保护终止整轮。

Hermes 的可借鉴边界：核心工具在启用的会话中持续可见；MCP/插件等延迟工具经稳定的 `tool_search`、`tool_describe`、`tool_call` 桥接。桥接调用使用注册表中的权威 schema 校验/规范化参数，不依赖该工具是否恰好作为原生 Tool 出现在最近一次请求中。Hermes 仍可能遇到模型参数错误；目标是让错误可定位、可恢复，不让展示状态漂移导致正常工作失败。

**与 Hermes 的差异：**此方案借用工具展示/调用边界，不是 1:1 复制。Hermes 对启用会话中的 Project 工具也保持原生 schema 可见；本方案因 DevWerk 曾遇到 Tool schema 预算溢出，暂拟只稳定展示小型管理入口，让 `workflow.create` 等较大契约经桥接调用。此外，Hermes 的延迟调用预检重点是缺少必填字段，类型规范化来自注册表；本方案拟在 DevWerk 已有的严格 JSON Schema 校验前做注册表驱动的无歧义规范化。是否把 `workflow.create` 也列为稳定可见核心工具，应以实际 schema 预算和首次调用成功率决定，不能称当前方案与 Hermes 完全相同。

## 目标与非目标

- Conversation Agent 能稳定调用 Requirement、Workflow、Task 管理工具；动态 MCP 仍可按需发现。
- 任意一次模型 Tool 调用，均按唯一注册表契约做参数规范化、校验、授权和效果记录。
- 模型不能原生调用本次未展示的工具；延迟工具通过一个固定桥接入口调用。
- 保留上下文成本控制；不把全部约 70 个内置工具及 MCP schema 每轮重发。
- 不改变 Requirement、Workflow、Task 的业务语义、图状态机或 Column 调度。

## 具体修改

### 1. 稳定可见的核心工具

在 `app/v1/tooling/builtin_tool_catalog.py` 与 `capability_registration.py` 中，把使用频繁且 schema 小的项目管理入口明确标为 `ToolExposure.DIRECT`，包括 `project.scope.inspect`、`project.scope.revise`、`workflow.inspect`、`task.inspect`。实际清单以 schema 字节预算审计确定，不把重量级 `workflow.create`、`task.plan.save` 或 MCP 全量直接展示。直接工具在一个 Conversation Run 中保持同一契约，授权仍由既有执行授权检查负责。

### 2. 用无状态桥接替代有状态选择

在 `app/v1/protocols/tools/core/tool_catalog_contracts.py` 定义 `ToolCatalogDescribeInput/Receipt` 和 `ToolCatalogCallInput` 等固定契约；`arguments` 是明确标记的动态对象，必须再按目标 Tool 的注册表 schema 校验。在 `builtin_tool_catalog.py` 登记固定的 `tool.catalog.describe`、`tool.catalog.call`；`tool.catalog.search` 保留名称/摘要检索。`tool.catalog.select` 不再控制新 Run 的 Provider 工具数组；旧 Run 的回执保留可读，迁移时可暂时兼容。

`tool.catalog.describe` 返回目标 Tool 的完整参数 schema、字段说明和可用性；`tool.catalog.call` 接收 `tool_id` 与 `arguments`，通过同一注册表调用延迟 Tool。它不能扩大 Run 的授权范围。一次调用只作用于一个 Tool；不创建新的 Workflow/Task 操作语义。

### 3. 入口顺序与单一契约来源

在 `app/v1/agents/agent_runner.py` / `agent_tool_execution.py` 的模型结果入口，按顺序执行：

1. 根据**本次请求展示的 Tool ID 集合**判断：直接调用是否真实可见；未展示的原生调用只返回 `ToolNotPresented`（含 `tool.catalog.describe/call` 指引），不执行副作用。
2. `tool.catalog.call` 解包后，从 `CapabilityRegistry` 取得目标 `CapabilityEntry` 的输入 schema；仅对已声明的、无歧义的 Provider 字符串包装做规范化（例如整数 schema 下的 `"2"` → `2`），随后做严格 JSON Schema 校验。
3. 用**目标 Tool** 的 side-effect 类别、Run 允许列表、当前用户授权、执行租约、幂等/回放键和输出 schema 完成既有执行链。桥接的 Provider `tool_call_id` 与底层操作建立一一对应；审计同时保存原始输入和规范化输入，避免隐藏真实模型行为。
4. 对无法规范化的值返回字段路径、期望类型、实际类型及该 Tool 的参数 schema 或检索引用；保持重复失败保护，但模型收到的首次失败必须足以修正参数。

`app/v1/tooling/capability_registry.py` 提供按 ID 读取权威 schema 的入口；`app/services/anthropic_client.py` 不再把“本次 Provider 请求展示了哪些工具”当成业务工具校验的唯一依据。Provider 适配只处理线协议；规范化和校验归 Agent Tool 入口与 Registry 所有。

### 4. Provider 工具数组与上下文预算

`app/v1/tooling/tool_presentation.py`、`app/v1/agents/agent_provider.py` 只组装稳定核心工具和固定桥接工具，不再根据最新一次 `tool.catalog.select` 回执替换可见 schema。重量级 Workflow/Task schema 经 `describe` 按需返回，并通过现有工具结果投影/上下文压缩限制历史保留。按真实 Provider payload 统计修改前后单轮工具定义字节数及总请求大小，拒绝回到全量工具定义重发。

## 验证

1. 单元测试：两次不同工具搜索/描述不会改变核心工具可见性；未展示原生调用没有副作用；桥接只调用 Run 授权内的目标工具；`"2"` 在整数 schema 下规范化，`"2x"` 明确拒绝；原始/规范化参数和幂等回放均正确。
2. 集成测试：同一对话先检查 Requirement/Workflow，再查询 Task Plan，随后修订范围、更新 Workflow、创建 Task；此前复现的“切换工具后 scope/workflow schema 消失”不再发生。
3. 黑盒测试：新 Project 中通过自然语言完成 Workflow 创建、后续范围修订与 Task 创建；核对真实 Provider 请求、首次有效调用率、DB 结果和请求体大小。Agent 自述不算通过证据。

## 实施顺序

先落地固定契约与注册表规范化/可见性门，再加桥接并停用新 Run 的有状态选择，最后做真实 Provider 回归。每步保持既有服务运行；需要重启时遵守 8000 端口纪律。方案评审前不改代码。
