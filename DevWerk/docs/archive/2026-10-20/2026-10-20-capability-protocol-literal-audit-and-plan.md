# Capability 协议字面值审计与修改方案（2026-10-20）

## 范围与统计口径

审计范围为 `app/v1/capabilities.py` 当前工作树版本。以 Python AST 统计 `ast.Constant`，排除布尔值；此数字是可复现的**字面值出现次数**，不是「缺陷数量」。文件约 3,000 行，有字符串 2,987 处（774 个不同值）、数字 212 处（32 个不同值）。排除常见结构下标/边界 `0`、`1`、`-1` 后，非平凡数字仍有 **79 处**：工具注册与 Schema 段 53 处，运行/辅助逻辑段 26 处。文件声明了 **74 个**公开工具 ID。AST 可见约 **120 个含字符串常量的比较表达式**与 **7 个 `startswith`/`endswith` 表达式**；它们包含数据键判断和格式解析，不能直接相加视作 magic value。

这些值分四类，必须按**意义**而非文字形态处理：

| 类别 | 例子 | 处理原则 |
| --- | --- | --- |
| 外部协议文字 | JSON Schema 的 `type`、`properties`、`required`、`$ref`；74 个 `workflow.create` 等工具 ID；SQL 列名 | 名称本身是线协议。保留在协议声明/注册边界，不机械地转换为无意义常量；消费端不得另造不受校验的同义值。 |
| 有限集合/状态 | `side_effect_kind` 的六种取值、执行回执的 `started/awaiting/completed/failed`、执行阶段、`Workflow` typed target | 在既有 domain/协议模型或专门的协议类型中声明一次；分支判断引用声明，必要时在边界转换为原有字符串。 |
| 限额/截断 | 搜索默认 8、预览 160 字符/320 字节、工具结果 4,000/8,000 字符、诊断列表前 20 项、事件/反馈列表上限等 | 区分公开输入约束、运行策略、纯展示截断。前两种与 `V1RuntimePolicy` 或单一工具契约绑定；展示截断用具名常量。Schema 最大值与实际运行上限不得漂移。 |
| 算法常量与文案 | JSON Pointer 的 `/`、`~0`/`~1`，数组索引，合法的 `0/1` 边界，错误提示 | 维持局部表达；仅对非显然算法参数命名。把全部字面值搬到常量文件会降低可读性。 |

`target.kind == "column"` 曾是有风险的决策字符串：作者输入已经是带判别字段的 `WorkflowColumnTarget | WorkflowTerminalTarget`，运行时再独立比较字面值会让类型定义和执行路径存在两个真相。本轮先前已改为 `isinstance(target, WorkflowColumnTarget)`。`suffix` 只是错误列表超过 20 个时的文案尾注，不参与图路由；先前已删除该变量，诊断边界 `20` 仍应具名。

## 可维护性问题与优先级

1. **P1：分支协议散落。** `CapabilityEntry.side_effect_kind` 用 `Literal` 标注，但 `dispatch/_dispatch/validate_workflow_capabilities` 仍以重复字符串集合判断。回执状态也是跨 Store/Registry 的隐式约定。修改某一种副作用或状态时，容易遗漏消费端。工具 ID 的注册文字是公开协议，但在 `_dispatch` 的特殊分支又重复写工具 ID；应至少让高风险分支引用同一声明。
2. **P1：Schema 限额与执行限额分离。** 内存搜索把 `8` 同时写作默认与硬上限；部分读取上限在 schema 与 handler 各写一次；结果截断有 `4000/8000/512/6000`。应形成同一来源，保留现有默认和线上行为。
3. **P2：纯展示/诊断数值未命名。** 例如 `_workflow_create` 只列出 20 个 Column key，`_workflow_acceptance_configure` 列 12 个诊断。这些不应被误认为图约束，应命名为展示上限。
4. **不属于 magic-value 缺陷。** JSON Schema 标准键、用户可定义的 Column key/outcome、公开工具 ID 的**单次注册声明**、正常列表边界 `0/1`。将这些全部改为枚举/常量会制造第二份映射，破坏兼容且不改善维护。

## 协议抽象设计

- 保留 `WorkflowAuthoringTarget` 判别联合与 `WorkflowDefinition` 现有存储/状态机协议。作者入口的对象类型负责区分 Column 与终态，转换器只做对象到原有 target key 的显式转换；不能猜测或补图边。
- 在一个小型内部协议定义中声明 **副作用类别**、**执行回执状态**等有限集合及其语义分组，供 Registry 注册、派发和验证共用。公开值仍序列化为原字符串，数据库/Provider/API 字段不改名。避免创建覆盖所有工具/JSON Schema 键的巨大枚举。
- 公开工具名以 Registry 的注册项为权威；只有在消费端出现重复特殊判断的 ID 使用具名标识，或优先借助 `CapabilityEntry` 元数据消除按名字分支。不得改变 Tool schema 或权限含义。
- 限额要有明确归属：用户可调运行配额进 `V1RuntimePolicy`；固定的单工具输入/输出展示约束与 schema 绑定；纯错误诊断数量用局部具名常量。所有迁移沿用当前数值，不调优。
- 运行回执、状态、Schema 与数据库写入保持原 wire 值；协议改动仅消除重复定义，不迁移数据、不修改 Workflow 图、不增加场景特定逻辑。

## 实施顺序与验证

1. 冻结统计和定位（本文），检查已有 `policy.py`、`domain.py`、`tool_protocol.py`，不新建与已有模型竞争的来源。
2. 先处理 Workflow 作者入口的 `20` 诊断阈值及判别逻辑；测试非法目标、保留终态 key、合法终态边的回执一致。
3. 再把 Registry 中高风险的副作用/回执有限集合收束到一个协议定义，替换该文件的相应判断；测试回执重放、失败前回滚、Column 委派和工具可用性。独立 review diff，不触碰业务工具的 handler 实现。
4. 最后将 Schema/handler 中**实际重复**的配额逐一与既有 `ServiceLimits` 或具名工具契约对齐；每一个值保持原数值，核对 Provider-facing schema 和执行实际限制。单次改动只覆盖同一语义组，避免 3,000 行机械替换。
5. 聚焦测试、相应 Runtime 测试、`git diff --check`；记录已替换、保留及有意延期的值。黑盒工作流创建单独观察，不能把字符串重命名等同于模型稳定性保证。

这是一份按风险分组的协议清理方案，而不是「消灭所有字面值」的重构。统计口径与保留理由可供后续审计复核。

## 第一批执行结果

已引入 `capability_protocol.py` 的副作用类别、执行回执状态与失败处置值，Registry 和 Store 以这些值判断，数据库字符串不变；`CapabilityEntry` 注册时验证副作用类别。Workflow 作者目标的运行时判别使用 `WorkflowColumnTarget` 对象类型，执行器校验使用 `AgentExecutor` / `CapabilitySequenceExecutor` 对象类型。两个纯诊断截断数已命名。结构化对象入参的 JSON 字符串兼容解析由 Entry 元数据指定，移除了工具 ID 到字段名的硬编码分支。

同时按另一份[对象协议结构方案](2026-10-20-capability-object-protocol-structure-plan.md)把规划链合同、作者图转换、动态参数正规化和 Schema 动态目录适配迁出 `capabilities.py`。这一步减少了该文件的字面值，但**不是** 78 个非平凡数字都已经解决；剩余多为其他工具的字段限额或展示/算法值，必须随其对应对象合同逐组迁移，避免随意把不同语义的 `200` 合并成一个常量。
