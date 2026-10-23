# Capability 对象协议与结构清理方案（2026-10-20）

## 当前结构与本次观察

`app/v1/capabilities.py` 同时承担 Registry、约 74 个工具的输入/输出 JSON Schema 声明、Workflow 作者协议的 Schema 字段补丁、动态参数转换、handler 和回执处理。工具定义中大量手写嵌套 `dict`；例如 `build_core_registry` 的回执 Schema 由 `receipt_schema` 与字典展开拼接。`_workflow_create` 把已校验的 `WorkflowAuthoringRequest` 再变为字典、改写 `transitions[*].target` 和 `context`、拼出 `WorkflowPlan` 字典，导致新旧协议字段靠字符串同步。

此前的自然语言黑盒 Project `prj_84c507c41ab444d393b4e3fa38528471` 第五轮有三次 `workflow.create` 失败，三次都把 `workflow` 对象作为**字符串**传入，内部类似 `"columns">[...]`，不是合法 JSON。`_dispatch` 的硬编码工具名映射只在 `json.loads` 成功时才把字符串换成字典；解析失败后又让 JSON Schema 把整段约 0.7–1.2 KB 字符串回显为 `is not of type object`。Agent 随后绕开看板，直接 `project.files.write` 并 `requirement.close`。这里没有 Workflow 或 Task；文件存在不能算工作流交付。用户已要求**停止跟进该 Project**，本方案不安排补救或监控。

## 设计约束

1. **对象拥有字段与校验。** Tool 入参/回执、Workflow 作者图与派生 Plan 定义为单独模块的 Pydantic 模型；业务代码读取属性，不用散落的 `args['field']` 或 `value['field']` 拼对象。输入必须在单一边界转换与校验。
2. **JSON 只在线协议/存储边界。** JSON Schema 由模型生成，持久化调用原有 Store 时才 `model_dump(mode='json')`；动态 MCP/Capability 的 `arguments`、用户声明的任意 JSON Schema 则保留 `dict[str, Any]`，因为无法预先定义其业务字段，但不得在 `capabilities.py` 内随意增加业务 key。
3. **注册表只组装工具。** 将工具合同按能力域移到独立文件，Registry 持有 `CapabilityEntry` 对象及其显式元数据。新增输入字段必须先修改模型/Schema，不能仅在 handler 用字典键读取。
4. **保持兼容。** 保留现有工具 ID、JSON wire 字段、数据库结构、Workflow 图/状态机、现有数值阈值和已有用户数据。此前有效的对象输入仍有效；为兼容 Provider 已有的合法 JSON 字符串包装，可在边界解析成对象，但无效字符串必须给出**短且可操作**的解析错误，不回显完整内容、不隐式修补 JSON。
5. **按能力域迁移。** 先处理直接阻塞的 `workflow.create` / Workflow Plan / Task Plan 工具；再处理文件、记忆、Agent/Task 控制等工具。不能一次机械搬迁 3,000 行后声称完成。

## 代码落点

| 阶段 | 文件/方法 | 具体改动与可观察结果 |
| --- | --- | --- |
| 1：工具入参边界 | `app/v1/capabilities.py::CapabilityEntry`, `_dispatch`; 新 `app/v1/capability_contracts.py` | 用 Entry 的显式对象字段声明可兼容解析的结构化参数，不再用 `{'workflow.create':'workflow', ...}` 硬编码分支；解析后构造受约束的对象。无效字符串返回字段路径、JSON 解析位置及“传对象”的说明，不复制用户全文。 |
| 2：作者图对象转换 | 新 `app/v1/workflow_authoring.py`; `capabilities.py::_workflow_create` | 独立对象转换器接收 `WorkflowAuthoringRequest`，产出 `WorkflowDefinition` 与 `WorkflowPlan`；通过 `ColumnDefinition`、`Transition`、`ContextSelection`、`TaskContract`、`WorkflowColumnPlan` 构造，消除在 `_workflow_create` 里对 `model_dump` 结果逐字段改写和 Plan 字典拼接。保留现有完整图校验与 Store 发布路径。 |
| 3：回执与 Schema | `app/v1/capability_contracts.py`; `build_core_registry` | 将手写的 `saved_plan_receipt`、`workflow_plan_receipt`、`workflow_receipt`、`validation_receipt`、`task_receipt` 变成对象模型生成的 Schema。其他业务域的 Schema 按域迁出；标准 JSON Schema 键只出现在边界适配层。 |
| 4：后续域迁移 | `app/v1/capability_contracts_*.py` 与对应 handler 模块 | 逐组迁移其余工具的入参、回执、校验；只有全组测过才删旧手写 Schema。每一组对比迁移前后 Tool schema 和真实输出，差异须明示；不要在 `capabilities.py` 保留一套隐含旧契约。 |

## 本轮验证

- 对 `workflow.create` 分别给合法对象、合法 JSON 字符串、损坏的 JSON 字符串、类型错误对象；前两者走同一模型与图校验，损坏值拒绝前无持久副作用，错误短且指向 `workflow`，不得包含整段输入。
- 现有 Workflow/Task Plan、Registry、回执重放和 Provider-facing Schema 测试必须通过；对比发布后的 Workflow 定义、派生 Plan 与迁移前等价。
- 每完成一个能力域记录剩余的手写 Schema/字典处理量；`capabilities.py` 缩小只是结果，不是牺牲合同完整性的目标。

## 本轮实际落地与边界

- 新增 `capability_contracts.py`：`workflow.create`、`workflow.publish`、Workflow Plan/Task Plan 保存与 Task Plan 校验的输入对象，以及相应 Plan/Workflow/Task 回执对象。相关工具的注册 Schema 由对象生成；合法的旧 JSON 字符串包装在统一边界解码，损坏的包装只返回短错误及位置，不再回显整段内容。
- 新增 `workflow_authoring.py`：从 `WorkflowAuthoringRequest` 构造 `ColumnDefinition`、`Transition`、`WorkflowDefinition`、`TaskContract` 与 `WorkflowPlan`，保留作者原出边；`_workflow_create` 不再修改 `model_dump` 的字段或拼 Plan 字典。动态工具参数的对象正规化也在此模块。对旧 `workflow.publish` 可接受的**不完整草稿**，`workflow_transport.py` 提供容许缺字段的对象视图，不提前执行完整图校验。
- `domain.py` 的模型字段直接声明作者图/旧图关键字段的说明及必填条件；原本在 Registry 中静默修改这些 JSON Schema 字段的函数已经删除。运行时目录枚举确实必须在注册时注入，集中到 `capability_schema_adapter.py`，缺少预期模型/字段会报错，不再静默跳过。
- `capability_protocol.py` 维护副作用类别、执行回执状态和结构化对象参数边界；Store/Registry 使用同一回执状态值，保留现有 DB/API 字符串。
- 这些迁移目前只覆盖**规划链与通用执行回执**。`capabilities.py` 仍包含其他能力域的大量手写 Schema 和字典 handler；结构清理**尚未全量完成**。当前文件约 2,761 物理行，AST 有 2,579 处字符串和 201 处数字（排除 `0/1/-1` 后 78 处），其中多数仍是公开字段/Schema 或未迁移域的限额。下一步应按文件、记忆、Agent/Task 控制等能力域继续迁移，而不是宣称已消除所有 magic value。
- 已验证：`test_capability_contract.py`、`test_provider_contract.py`、`test_domain_contract.py` 合计 87/87 通过；回执重放相关 4/4 通过；`compileall app/v1`、`git diff --check` 通过。后续针对模型字段说明迁移的 25 个聚焦测试也通过。用户已暂停 Project 测试，未重启服务或重新发起黑盒，不把这些代码级回归当作流程交付成功。
