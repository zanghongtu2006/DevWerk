# DevWerk 全仓类型协议与魔法值清理（2026-10-20）

## 目标与判断标准

本计划替代只对 `capabilities.py` 分组打补丁的做法。用户要求的是代码级协议一致性：进入状态分支、路由、错误分类、限额和持久化决策的值必须由有名称的类型或策略拥有；所有静态 JSON 工具输入/输出及 HTTP API 输入/输出必须从单独文件中的类生成；业务层不得拼装或修改 JSON Schema 字典。工具 ID、wire 字段和持久化数据须维持兼容。动态 MCP 参数、用户声明的任意 JSON Schema 与文件内容不是静态字段，必须由明确的通用容器类承载、在边界验证为 JSON 值，不能以针对业务场景的字典例外绕开协议。

“无魔法值”不是禁止所有字面量：`0`、`1` 等局部算术、SQL/JSON 标准语法和面向用户的文字可以存在，但**不得**作为无名的状态、权限、流程、错误或限额条件进入逻辑分支。所有这类值应有唯一声明位置和类型。JSON 协议 key 只能是模型属性或标准 JSON Schema 类的序列化名称；`capabilities.py` 等执行文件不得靠 `schema['properties']['...']`、`{**schema, ...}` 或字符串模板改变合同。

## 当前审计基线

- `app/` 约 103 个源码文件；`app/v1/api.py` 约 42 个路由，许多响应是无模型的 `dict[str, Any]`。
- `app/v1/capabilities.py` 在本轮局部迁移后仍约 2,500 行、约 250 个字典字面量、约 1,800 个字符串字面量；Registry 仍注册多组手写 JSON Schema。此现状**不满足**目标。
- `app/v1/tool_protocol.py::await_tool_schema` 按 wait kind 更新 `required` 和 `properties` 列表，属于必须消除的 Schema 拼接；`app/v1/contracts.py::_canonicalize` 还会 `{**root, **choice}` 合成验证 Schema。
- 全仓约 206 处 `json.loads/dumps` 调用，需逐处区分合法线/库边界与业务内部反序列化；初筛约 463 处带字符串或非平凡数字的条件语句，不能把这个数字直接当作缺陷数，须按语义审计。
- `domain.py` 已有部分 Pydantic Workflow/Task 类，但它们包含 `dict[str, Any]` JSON Schema 或动态工具参数；这类字段需要单独的受验证容器与边界，不应被错误地固定成某个业务项目的模型。
- HTTP API、Capability、MCP、Column 完成/等待、Repository/DB 是不同线协议；当前错误类型有异常类、`ToolResult.error` 和 HTTP `detail` 多套格式。必须给每个公开接口定义入参、成功回执和失败码，不能只给 Tool 入参建模。

## 唯一协议边界

1. `protocol/values.py`：定义有名的运行状态、目标种类、工具 ID、结果类别、错误码等 `Enum` 或常量；已有 `states.py` 是状态唯一来源，不另建第二套状态。限制值纳入 `policy.py` / `ServiceLimits`，固定格式边界纳入相应模型 `Field`。避免把自由的用户文本或业务 outcome 固定枚举化。
2. `protocol/json_value.py`：递归 `JsonValue` / `JsonObject` 类型验证通用 JSON grammar；它只承载动态 JSON，不允许业务逻辑随意增加语义 key。`JsonSchema` 类描述规范关键字，保存动态自定义扩展时仍按原始 Schema 校验。持久化与 Provider Adapter 才进行 `model_dump` / `json.loads` / `json.dumps`。
3. `capability_contracts_*.py`：每一个内部 Tool 的入参和成功回执各有明确 `BaseModel`，字段说明、约束、默认值由属性声明。注册时只引用模型类，不手写 Schema；错误统一 `CapabilityError`（稳定 `code`、`message`、可选 `field_path` 与可修复信息）。动态工具目录用模型集合实现受控的运行时 enum/约束，而不是对生成 JSON Schema 做索引修改。已存在的 `capability_schema_adapter.py` 是过渡代码，应最终消除 Schema 写入。
4. `api_contracts_*.py`：HTTP 路由声明请求体/查询参数/响应模型；Store 的 dict 结果在 API 边界一次 `model_validate`，再由 FastAPI 序列化。错误码模型与 HTTP 状态映射确定。对流式事件定义事件 envelope、payload 版本和失败事件。
5. `repositories` 与运行时：DB row 可由仓储私有 typed row 转换；所有状态/错误比较改为 enum，配置限额使用已声明 Policy 属性。Workflow 自由图仍由 Workflow 类和状态机维护，协议清理不得引入硬编码业务流程。

## 实施顺序与文件

| 阶段 | 代码范围 | 完成门槛 |
| --- | --- | --- |
| A：基线冻结 | `tests/test_*contract.py`、Tool/HTTP Schema 快照（只存规范化的字段/约束，不存临时数据），原始 Registry 74 ID | 记录旧 wire 合同；把当前失败的 `ContractError` 类型修正并回归；不接触 Project 测试 |
| B：公共 JSON 与错误对象 | `contracts.py`、`tool_protocol.py`、`capability_protocol.py`、`agent_tool_execution.py`，新增协议模块 | `column.await` 和 Provider 结构包装由类生成；无 Schema dict 拼接；错误码在输入拒绝、业务失败、执行结果三层一致 |
| C：内部工具合同 | `capabilities.py` 与按域独立的 `capability_*_contracts.py` | 每个工具输入/成功输出有类；Registry 不持有手写 Schema；handler 读取属性；动态 MCP 参数只在显式容器中；现有 74 ID 和行为不变 |
| D：Workflow/Task/Agent 数据 | `domain.py`、`workflow_authoring.py`、`workflow_transport.py`、`context_manager.py`、`states.py` 等 | 所有逻辑状态和限额改为有名来源；旧草稿兼容保留在单一传输适配器；Schema 从模型属性生成 |
| E：HTTP 与 DB 边界 | `api.py`、`repositories/*`、`store.py` | 路由有明确响应模型和稳定错误码；DB JSON 只在仓储转换；无任意 key 合成业务回执 |
| F：全仓审计 | AST/测试检查加人工审查 | 静态 Tool/HTTP Schema 无手写拼接；逻辑比较无未登记的状态/限额字面量；所有协议字段有默认、取值、说明、错误码或明确的动态容器说明；所有相关回归通过 |

每阶段必须先写相应合同测试，再替换一个边界，运行该边界及邻接测试。不得一次机械搬动整个 3,000 行文件；不得以“代码行数减少”代替协议门槛。完整目标达到前，不恢复黑盒 Project 跟进、不宣称已完成。

## 已落地的局部工作及不足

此前已将 Workflow 创建/发布与计划入参、部分回执、Memory、文件验证和 Task 控制的一部分迁到独立对象，`workflow.create` 的损坏 JSON 包装现在给短错误。`workflow.acceptance.configure` 已改成对象编辑。`capabilities.py` 仍有大量手写 Schema，`tool_protocol.py` 仍拼接 Schema，多数 HTTP 输出仍是 dict，这些都留在后续阶段，不能以局部测试通过代替全仓目标。

## 本轮实施记录（持续更新）

- 内部 Tool 输入：`capabilities.py` 的手写静态输入 JSON Schema 已替换成独立 Pydantic 类；Workspace、Loop、事件、监督与治理 Tool 的 handler 也改为读取同一输入类。运行时限额由 `create_model` 绑定到模型字段，不改写生成后的 Schema。`CapabilityEntry.tool_schema` 的空对象回退改为 `EmptyInput` 类生成。
- Tool 输出：新增 Workspace 文件/命令与事件/治理成功回执类，并将其 Schema 接入 Registry 运行时输出校验。Workflow/Task Plan 回执中的 `plan` 和 Workflow 回执中的 `definition` 已由原始 dict 收紧为领域模型；Task 回执状态使用既有状态枚举。尚有大量 Tool 输出无完整模型，不能宣称 C 阶段完成。
- 通用状态：常用内置 Tool ID、会话阶段/触发来源、Memory scope、命令成功退出码及搜索边界有命名声明。仍需审计其它执行模块、仓储和 API 的逻辑字面量。
- Loop 动态 JSON Schema：`store.get_loop` 过去用 `{**parameter_schema, 'required': []}` 构造默认值校验 Schema；现在保持原 Schema 不变，只延后缺失的顶层必需绑定。新增回归覆盖嵌套必需字段仍须有效、未知字段仍拒绝。动态 bindings/data 通过 JSON 值类型校验。
- 已验证：`tests/test_capability_contract.py` 50 passed；`tests/test_memory_scope_budget.py` 联合 capability 共 68 passed；`tests/test_loop_contract.py` 19 passed；新增 Loop 默认值聚焦测试 1 passed。新增的事件/治理回执批次仍在测试中。两组较早启动的长时间无输出对话测试已中断，不能计为通过。
- 未完成且不得掩盖：`contracts.py` 的 Provider 兼容视图仍对动态 Schema 做合并与投影；大多数 Tool 成功回执和 `api.py` HTTP 响应仍缺专用模型；Store/Repository 仍有许多 JSON 字段与状态字面量。全仓目标尚未达到，黑盒 Project 跟进继续暂停。

## 最新边界计数与下一步

Registry 现有 74 个内部 Tool，其中 30 个已声明从类生成的成功输出 Schema，44 个仍缺；所有 74 个输入 Schema 现在都从类生成。Loop 的 list/inspect/apply、Project/Workflow inspect、Plan list、文件与命令等回执已加运行时合同验证。`tests/test_capability_contract.py` 的新增 Project/Workflow 无图及有图检视、Plan list 用例通过；Loop/Apply 共 21 passed；Task Plan list 的监督路径聚焦用例通过。整个 `tests` 批次和一组对话测试长时间无输出，已中断，不能算回归通过。

下个可追踪单元应逐个建模剩余 44 个 Tool 回执（先 Task/Feedback 与 Conversation，后 Agent/Memory/Requirement），每批输出使用真实 Store/Registry 调用测试；之后再处理 Provider schema 兼容视图和 HTTP 路由。动态 MCP 与用户 Loop schema 需要明确外部 Schema 验证边界，不能伪装为已知静态字段。最后才可进行状态字面量全仓替换与完整回归。`api.py` 目前约 42 个路由，响应模型仍未完成，不可宣称所有 API 入出参已校验。

## 2026-10-21 JSON 拼装清理进度

- 消除 `capabilities.py` 中残留的 Requirement/Scope 手写输入 Schema；新增独立 `capability_requirement_contracts.py` 并在 handler 边界使用相同模型校验。加入 AST 回归，阻止核心 Tool 注册重新写入 `properties/required/$defs/oneOf/anyOf` 字典字面量。
- Provider 兼容视图从 `contracts.py` 的 Schema 字典覆盖、分支合并和字段写入迁到 `provider_schema_contracts.py` 的 JSON Schema 对象；严格原始合同仍是验证源。实际 Provider/Workflow 合同 74 项通过。`WorkflowDefinition` 的旧列检查迁移改在 `workflow_transport.py` 通过传输对象执行；模型实例直接保留，避免丢失默认判别字段。相关领域与 Provider 测试 38 项通过。
- Loop 默认值、现有绑定和补丁通过 `LoopBindings` 验证并按声明优先级合并；Loop 合同 20 项与 Scope 聚焦 4 项通过。固定持久化 JSON 改成 `runtime_json_contracts.py` 的类，覆盖 Worker 消息、检查点、对话队列元信息、失败证据与终态元数据。上下文结果投影和执行检查点由 `context_result_contracts.py`、`runtime_checkpoint_contracts.py` 承载。工具文件回执与 Task 创建回执进一步由已有输出类生成。
- 回归中发现两处新行为差异并修复：Worker 消息 `consume_messages()` 对内仍需返回字典数组；等待请求的合同类须模块级导入以覆盖无执行结果分支。相关聚焦测试通过。长时间停滞的两组全量测试主动中断，不计为通过。
- AST 全仓扫描随后发现跨行 `**`（Prompt、Runtime、API 和 Store）；现已迁到对象或对 `CapabilityContext` 使用 dataclass `replace`。新增 AST 回归检查：`app/` 不得有字典展开，也不得把字典字面量直接传给 `json.dumps` / `_pack_json`。直接文本搜索不再作为完成依据。
- **仍未完成全仓目标**：部分 Tool 成功输出、`api.py` 的 HTTP 输出和动态 MCP 输出尚未全部类化，不能将本节视为完整协议收口。后续必须按 Tool/HTTP 边界逐个定义输出模型、运行时验证并补回归；AST 检查只能排除硬拼接形式，不能证明所有返回结构都有类与语义约束。

## 2026-10-21 第二轮：直接 JSON 拼装收口

- `app/v1/capabilities.py` 中的 Workflow 验收编辑回执、Memory 搜索投影、Conversation 回复/当前轮/历史回执、Task Plan 合同目录与校验诊断，改为独立类构造；对应 Registry 输出 Schema 接入运行时校验。`CapabilityEntry.tool_schema()` 也改为 `FunctionToolDefinition` 对象序列化。`project.scope.inspect` 原已通过 `ScopeInspectReceipt` 输出，现在注册同一对象生成的输出 Schema。
- `app/v1/context_manager.py` 的最后一级摘要字典合并改为 `CompactContextSummary`。`app/v1/loops.py` 的目录概要和详情改由 `LoopSummaryReceipt` / `LoopInspectReceipt` 生成，避免 `_summary()` 字段白名单推导后再 `result.update()`。旧 Loop 元数据含内部路径，模型仅明确映射公开字段；第一次回归发现严格模型拒绝内部路径，已修正并复测 25 passed。
- 三个 Provider 客户端的请求体与 MCP JSON-RPC 外发消息改为独立合同对象；任意 MCP Tool 参数仍处于协议允许的动态 JSON 容器里。HTTP/SSE 与 stdio 仍由传输边界序列化对象。MCP HTTP 路径首次回归发现遗留的 `message.get()`，已改为模型属性并复测。
- `tests/test_json_protocol_source_contract.py` 增加 AST 约束：`app/` 不得使用字典展开、字典字面量合并、静态 Schema `type` 字典、直接序列化字典字面量或同函数内局部拼好的字典；`capabilities.py` 不得返回匿名字典字面量或向列表追加匿名字典。检查只阻止已识别的手工拼装形式，不替代逐个输出合同的运行时校验。
- 阶段性回归：Provider 24 passed、Loop 25 passed、Memory/MCP 聚焦 23 passed、Context/Task Entry 46 passed、Provider+Capability+Loop+Memory+AST 联合 120 passed；之后的 Task Plan 合同批次 72 passed，最终 AST 约束 7 passed。长时间无输出的 Conversation 全量批次主动中断，不计为通过；此前聚焦的对话合同已通过，尚不能据此宣称全量对话回归完成。
- **剩余边界**：仍有 30 余个内部 Tool 注册没有成功输出 Schema，HTTP 路由也仍有原始字典响应。当前已识别的 JSON Schema 手写/合并形式与 `capabilities.py` 匿名回执已清掉，但“所有 JSON 都来自类且每个 API 入出参都校验”的完整目标尚未完成；不能因源码 AST 检查通过就宣称全仓目标达成。
