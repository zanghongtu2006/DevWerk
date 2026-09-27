# Context / Memory 五项缺陷：代码修复设计

日期：2026-09-27

状态：**待 review；本文件不是已实施或已通过回归的声明**

代码基线：`8c69b7528413dea221fe31161d5dd3e84a280cd6`

分支：`feature/context-provenance-handoff`

## 1. 本次交付与范围

现有代码、测试、README 和审阅文档已经以基线提交推送至 `zanghongtu2006/DevWerk`。本设计只处理 [代码审阅记录](2026-09-27-context-memory-goal-code-review.md) 中五项已复现缺陷：

| 编号 | 缺陷 | 修复后必须成立 |
| --- | --- | --- |
| F1 | 恢复时完整工具结果进入 action_ledger，并重复装入 system / user | 首次、重试、Await、重启均使用有界模型视图，完整证据仍供 Runtime 校验 |
| F2 | Session 交接摘要未进入实际 Assignment 输入 | 新 Assignment / successor 实际收到适用摘要，来源可回读 |
| F3 | checkpoint 最大消息 ID 错误覆盖未归档指令 | 只排除确实归档的消息；恢复前后有效指令一致 |
| F4 | workflow Memory selector 不绑定当前 Workflow ID | 默认只检索当前冻结 Workflow 修订；其他 scope 同样明确身份 |
| F5 | Memory 无总内容预算，且完整回读会再次膨胀 | Memory 的装配、工具读取、重载都受预算控制，有准确的来源和分页 |

保持现有架构：Project Conversation Agent 是唯一主 Agent；Column 执行单个叶子 Worker；Workflow 负责协作调度；mailbox 只通知。这里新增的投影、预算与来源读取属于 Runtime 内部修复，不增加模型 Agent、Column 内协作循环、Web 控件或用户决策流程。

不在本次范围内：改写任务规划算法、引入向量数据库、自动重设计全部 Loop、对照评估单 Agent 的产品化功能。五项修复完成也不等于已经证明协作效果优于单 Agent。

下文路径均相对于 `DevWerk/` 服务目录。现有方法名称来自基线代码；标注“新增”的名称为本次拟定接口，可以在实现时调整局部命名，但不得改变约定的行为。

## 2. 总体实现决策

### 2.1 分开三种数据用途

| 数据 | 用途 | 能否删减 |
| --- | --- | --- |
| 原始工具结果、operation、执行回执 | 去重、失败关联、completion 和验收 | 不因上下文压缩而删减 |
| 当前 Task / Assignment 合同、当前用户请求、已接收 Worker 指令 | 确定当前应做什么 | 不按“最近 N 条”静默删减；不可容纳时明确阻塞 |
| ledger 视图、历史工具摘要、Memory 参考、交接摘要 | 给模型提供工作参考 | 可投影和归档，必须保留来源、范围与完整回读方式 |

**不得把模型摘要用于判断某工具已经执行、某失败已经修复或某 Task 已完成。** `CompletionAdmissionService`、operation replay、原始 ledger 的判断仍使用现有完整证据。

### 2.2 使用一个请求预算入口

新增纯计算模块 `app/v1/context_projection.py`，集中实现：

- `ContextBudget`：输入上限、估算、按剩余预算分配组件额度；没有数据库或模型副作用。
- `project_ledger(...)`：Runtime ledger → 模型执行摘要。
- `project_memory(...)`：选中 Memory → 模型记录与 manifest。
- `slice_source_text(...)`：字符游标 + UTF-8 字节上限的连续分页，不跳过中间原文。
- `project_run_context(...)`：按明确字段投影当前输入，返回内容与大小统计；不做全对象递归截断以免破坏合同。

保留 `context_manager.py` 负责每次请求的预算检查、工具批次压缩和 checkpoint；复用上述函数。不另建独立的“上下文 Agent”。

预算继续使用现有模型窗口、输出预留、safety_fraction 和 Provider usage 校准逻辑。最终判断必须测量**完整序列化 messages + tools**，不能把几个粗估组件相加就视为通过。

### 2.3 明确不变量

1. Full ledger 和模型 ledger view 是不同对象；投影不原地修改原始记录。
2. 当前 Assignment 输入只在一个模型消息中出现；system 不再重复携带完整动态输入。
3. 一个 checkpoint 只能排除自己明确覆盖的真实消息 ID。
4. Session handoff 不替代 Assignment checkpoint，也不继承旧 Task 的完成状态或执行授权。
5. scope 缺少身份时不得降级成 Project 内全范围搜索。
6. 当前进程压缩结果和数据库重载结果必须使用同一覆盖语义。
7. 回读分页的 next_offset 必须指向实际已返回内容之后；模型投影不能再截断分页中间却保留原游标。
8. 缺陷修复不向 Web 输出内部 manifest、预算、ledger 或 checkpoint 调试信息。

## 3. F1：分离 Runtime ledger 与模型输入

### 3.1 文件与方法

| 文件 | 方法/类型 | 修改内容 |
| --- | --- | --- |
| `app/v1/agent_models.py` | `AgentRunSpec` | 新增 `runtime_ledger: list[dict] \| None = None`，明确为内部执行证据 |
| `app/v1/runtime.py` | `_execute_agent()` | 把 `_column_action_ledger() + prior_action_ledger` 放入 runtime_ledger，不再放入 context |
| 同上 | `_column_action_ledger()` | 仍重建完整结果，保持 execution_key 去重；此方法不承担模型限长 |
| 同上 | `_resume_awaited_execution()` | 继续传递真实 Await 回执，统一走上述入口，不另造未投影的恢复 prompt |
| `app/v1/agent_run_preparation.py` | `prepare()` / `PreparedAgentRun` | 分离 `logical_ledger` 与 provider payload；旧调用方兼容处理见下文 |
| `app/v1/agent_prompt.py` | 新增 `build_column_system_envelope()` | system 只保留指令、身份、协议、完成规则及必要策略元数据 |
| 同上 | `build_run_envelope()` | 保留审计用途，禁止直接作为 Column Provider envelope |
| `app/v1/agent_runner.py` | `AgentExecutionRunner.run()` | 同一完整 logical_ledger 给 ToolBatchExecutor；Provider 只通过投影函数读取它 |
| `app/v1/agent_provider.py` | `ProviderTurnRequester.__init__()` / `request()` | 接收 ledger 引用；每次请求和 overflow retry 均先生成预算内视图 |
| `app/v1/context_projection.py` | 新增 `project_ledger()` / `project_run_context()` | 纯函数投影，不改变 Runtime 事实 |

不改变 `execution_ledger.py::ledger_entry()` 的 `facts.arguments/result`，也不改变 `CompletionAdmissionService.evaluate()` 的输入含义。这两处必须继续看到原始证据。

### 3.2 兼容旧调用方

Preparer 按以下顺序取 Runtime ledger：

```python
ledger = spec.runtime_ledger
if ledger is None:
    ledger = spec.context.get('action_ledger', [])  # 旧内部调用/测试适配
logical_ledger = copy_of_entries(ledger)
provider_context_source = context_without_action_ledger(spec.context)
```

显式空列表与未提供区分；不修改原始 `spec.context`。基线中的已有测试和恢复入口可以逐步迁移，不要求一次改写外部 JSON 数据。

历史 AgentRun 的 `context_snapshot` 保留原样。恢复模型输入只从真实 Task/Column 与 invocation 重新构建，不把旧 snapshot 原文直接复制进 prompt。

### 3.3 ledger 模型视图

单项允许字段：`evidence_id`、`agent_run_id`、`tool_call_id`、工具名、effect_kind、ok/status、operation/result hash、有限实体引用、退出码、有限错误摘要、必要的文件路径。不得包含完整源码参数或 stdout/stderr。

整体结构拟为：

```json
{
  "version": 1,
  "total_operations": 70,
  "included_operations": 12,
  "unresolved_failure_count": 1,
  "entries": [],
  "omitted_operations": 58,
  "source": {"column_run_id": "..."}
}
```

选取顺序：未解决失败 → 未完成/等待操作 → 最近已完成操作。失败条目多到无法逐个展示时，提供总数和分页入口，不伪装成“没有失败”；最终 completion 始终按完整 ledger 判断。

需要补齐 ledger 摘要中被省略操作的发现路径：在 `capabilities.py` 注册只读 `agent.evidence.list`，以当前 ColumnRun 为默认范围，提供不含原始大参数/输出的回执目录（稳定 sequence/ID 游标）。实现方法放在 `AgentRepository.evidence_page(...)`，叶子限自身 Worker 的可访问执行记录，Main 限当前 Project。查到 source run/tool ID 后复用 `agent.result.read` 取完整结果。这个内部只读能力是让有界目录可回读所需的接口，不承担调度或代理通信。

目录参数拟为 `column_run_id? / after_invocation_id? / limit?`，默认当前 ColumnRun，默认 20 条、最多 50 条；使用数据库 invocation 全局 ID 排序，避免不同 AgentRun 的 sequence 都从 1 开始造成跳项。显式 ColumnRun 必须验证 Project/Worker 归属。返回 `entries / next_cursor / has_more`，每项沿用 ledger_view 字段，分页还受总字节预算限制，游标推进到实际返回的最后一项。

`AgentRunPreparer.prepare()` 在有运行证据的 Column 中，与现有 `agent.result.read` 一样加入该只读 capability；必须在计算 tools 的预算之前完成。目录读取过程中自己产生的读取回执不作为业务 ledger 目录的待处理事项反复展示，按 read 能力种类区分，不把一次回读误认作一个需要解决的新失败。

原始写文件参数需要精确追溯时，使用第 5 节扩展的消息 source 分页读取；不能依赖 `agent.result.read` 读取本不包含 arguments 的结果。

### 3.4 避免重复合同和反复恢复膨胀

- 当前输入使用独立 user 消息：`context_frame_version=2`、`context_frame_kind=assignment_input`、Assignment ID/generation、投影后的 context。
- 当前 frame 的原始结构只保留在 Run 运行内存和审计数据中；Provider 内容由结构化源投影生成。不得把“已经截短的字符串”再次当作完整源进行多轮截短。
- source metadata 使用内部字段，Provider serializer 必须剥离；`provider_messages()` 增加递归数据泄漏测试，不以摘要或日志展示内部原文。
- 同 Assignment 重新装配时，旧 Run 的 runtime-generated Assignment frame 不当成新的 user 指令 replay；用当前权威 frame 替换。旧格式 `current_assignment` 按 Assignment ID 和持久化 Run 归属识别，不按任意文本关键词删除消息。
- 真正的 Worker 输入、当前合同和业务原文不能被上述 frame 过滤规则误删。
- Provider 视图中的 ledger 每次根据当前完整 ledger 更新一次，不在历史中不断追加新副本。

## 4. F3：精确的 checkpoint 覆盖集合

这是第二个优先实施的部分；先确保恢复不会丢指令，再接入更多摘要来源。

### 4.1 数据结构与迁移

在 `AgentRepository.init_schema()` 中通过现有 `_ensure_column` 为 `v1_context_checkpoints` 添加：

```sql
coverage_json TEXT NULL
```

新记录示例：

```json
{
  "version": 2,
  "mode": "exact_message_ids",
  "ranges": [[101, 102], [106, 108]],
  "excluded_message_count": 5
}
```

`ranges` 是**实际归档 ID 集合**的连续区间压缩，不能用一组调用的首尾 ID 直接构造范围。不同 Run 的消息 ID 会交错；必须先收集归属已确认的真实 ID，只有数值连续时才合并区间。保留 `through_message_id` 作为诊断最大值，v2 读者绝不把它当作排除前缀。

每一条 v2 checkpoint 保存本 Assignment 的**累计覆盖集合**，不是只保存最近一次增量；这样只读取最新 checkpoint 就能重放。覆盖集合不得包含 system、Worker user 输入、单独的 assistant 说明或不完整调用批次。

所有 checkpoint INSERT 改用显式列名。当前 `_save()` 的 `INSERT ... VALUES(10 values)` 必须同步修改，否则增加列后写入失败。migration 由现有 SchemaRepository helper 执行，不另建迁移框架。

### 4.2 方法级修改

| 文件 | 方法 | 处理 |
| --- | --- | --- |
| `app/v1/context_manager.py` | `groups()` | 继续识别完整工具批次；确认所有 assistant/tool 来源 ID，缺失来源的临时批次不能持久化成覆盖集合 |
| 同上 | `prepare()` | 仅把归档工具消息的真实 ID 加入 coverage；不通过 notes[-4:] 代表仍有效指令 |
| 同上 | `_save()` | ownership 检查后，在同事务中读最新 revision、合并累计 coverage、插入摘要和 coverage |
| 同上 | `_summarize()` | 摘要只描述参考工作；当前有效 user 输入保持独立，不能被摘要淘汰 |
| `app/v1/repositories/agent_repository.py` | `session_history()` | v2 按 coverage 集合过滤；其余消息保留；不再用最大 ID 剪掉整段历史 |
| 同上 | 新增 `load_context_checkpoint()` | 集中解析版本、Project/Assignment 归属与覆盖合法性；ContextManager 和 session_history 共用 |
| `app/v1/session_replay.py` | `replayable_session_messages()` | 合并相邻消息时保留全部 `_source_message_ids`；或者对需要 checkpoint 的消息不合并，不能只留下第一条 ID |
| `app/v1/agent_run_preparation.py` | `prepare()` / `_append_session_history()` | 每条 runtime frame 保存返回的消息 ID；明确 current frame 替换规则 |
| `app/v1/repositories/agent_repository.py` | `consume_messages()` | 提供同事务保存的精确 source_message_id，不再让 Runner 查询 MAX(id) 猜测 |
| `app/v1/agent_runner.py` | `run()` | 使用消费方法返回的 source ID；保留 Worker 输入顺序与 acknowledgment 原子性 |

`consume_messages()` 为内部单调用入口，可返回 `{payload, source_message_id}`；修改唯一生产调用方和对应 fixture。Worker 消息状态不新增第二套“通信协议”。

`_source_message_ids` 仅是回放 provenance。覆盖集合必须由数据库归属验证后的原消息组成；不能因为合并消息提供一个 IDs 数组，就允许它把内部 user 内容归档。checkpoint 的 `source_hash` 也须注明哈希对象是归档的投影材料；完整结果版本仍使用 invocation 的原始结果 hash，不混淆两者。

### 4.3 两条恢复规则

**v2 checkpoint：** 加载覆盖集合，删除这些来源消息，用同一 helper 把最新摘要插在 system 后、正常历史前。当前进程压缩也用该 helper。因此两种路径的业务内容相同；新 Run 的 ID、generation、frame 等运行元数据允许不同。

**旧 checkpoint（coverage_json 为 NULL）：** 不能反推其最大 ID 之前都已安全总结。第一次恢复忽略旧前缀过滤，回读当前 Assignment 原始消息；旧摘要只作可选参考，不作为排除依据。投影工具结果、按新逻辑压缩后，写入 v2 checkpoint。历史原文不删除。

如果当前快照的 coverage 损坏或包含外部范围，记录内部诊断并从当前 Assignment 原文重建；不跳到更宽 Session 或其他 Assignment，也不执行任何恢复性工具副作用。

### 4.4 约束与极端情况

- 本次以保留真实 user/Worker 指令解决 F3，不引入由模型自动判断“哪条用户要求已经失效”的新机制。
- 两条指令含修订关系时，按原始时间顺序提供，让当前合同和明确修订表达语义；不因消息更老就删除。
- 有效 user 输入本身已超过模型上限时，保留原始证据并返回明确的 required_context_overflow 信息；不声称可以无损容纳任意长指令。
- 多次压缩必须满足 coverage 单调包含，revision 单调递增；通过集合变化决定是否写新 checkpoint，不能继续仅比较最大 through ID。
- 已丢失 lease / 被取消的 Worker 不得写 checkpoint。现有 `assert_owner(..., db=...)` 必须保留在最终事务中。

## 5. F2：Session 交接摘要与 Assignment 历史并行加载

### 5.1 明确两种摘要的职责

| 摘要 | 保存位置 | 进入模型的方式 |
| --- | --- | --- |
| Session 交接摘要 | `v1_agent_context_snapshots` | 新增的 handoff reference frame；说明来源、覆盖边界、摘要版本 |
| 当前 Assignment 工具历史摘要 | `v1_context_checkpoints` | 第 4 节 checkpoint frame，配合精确消息覆盖集合 |

Session 摘要的 through ID 只描述它引用到哪里，**不再用它剪掉当前 Assignment 历史**。两种摘要不能互斥查询。

### 5.2 文件与方法

| 文件 | 方法 | 修改内容 |
| --- | --- | --- |
| `app/v1/repositories/agent_repository.py` | 新增 `session_handoff(project_id, session_id)` | 获取当前 Worker Session 最新有效 snapshot，返回 ID、revision、coverage boundary、完整 summary、来源元数据 |
| 同上 | `replace_worker()` | 沿用 successor 私有 Session；保存 predecessor ID 与摘要，返回 snapshot ID，便于审计和回读 |
| 同上 | `save_context_snapshot()` | 继续限定 idle Worker、合法来源边界；事务内复核 idle/lifecycle，防止与 Assignment 启动竞争 |
| 同上 | `session_history()` | 专注当前 Assignment 消息/checkpoint；有 Assignment 的路径不再承担 Session handoff 的隐式选择 |
| `app/v1/agent_run_preparation.py` | `_append_session_history()` | 先加载适用 handoff，再加载当前 Assignment replay；最后附唯一当前合同 |
| `app/v1/context_projection.py` | 新增 `project_handoff()` | 大摘要预算内投影，保留 snapshot ID、hash、来源 Worker，不冒充当前授权 |
| `app/v1/capabilities.py` | `agent.context.read` 注册及 `_agent_context_read()` | 增加精确 snapshot / message source 分页模式，保留现有消息目录模式 |
| `app/v1/repositories/agent_repository.py` | 新增 `context_source_page(...)` | 按当前 Worker/Project 校验来源，连续返回完整原文的片段 |

### 5.3 读取接口的兼容约定

现有 `agent.context.read(worker_id?, after_message_id?, limit?)` 仍返回原先的消息目录数组。

新增可选模式：

- `snapshot_id`：读 Session snapshot 的 canonical JSON 文本。
- `source_message_id`：读某条消息的 canonical JSON（包含原 content / tool_calls）。
- 两者互斥，也不与 `after_message_id`/目录 limit 混用。
- 分页字段：`offset`、`max_characters`、可选 `expected_sha256`。

精确模式仍返回数组，但只有一个 source 对象：`source_kind/source_id/sha256/offset/content/next_offset/complete`。这样目录模式和既有调用不改变；模型从参数即可确定分页方式。内容采用连续字符切片，并加 UTF-8 字节上限；游标按真正返回的字符数推进。

叶子只能读自己的 Session/snapshot；继任 Worker 可读自己 Session 中的交接摘要，不能凭 predecessor ID 获得另一 Worker 全部私有 transcript。Main 可以显式指定同 Project Worker。需要更多前任知识时，由 Main 读取后通过既有交接摘要/Worker 输入交付；mailbox 不参与。

### 5.4 复用和兼容

- 同一个 Worker 的新 Assignment 读取最新已保存的 Session handoff，不自动把所有旧 Assignment 工具日志导入。
- 若没有显式摘要，仍提供 existing prior_assignments 的索引与消息回读；不额外增加自动总结模型调用。
- replacement 第 0 边界摘要是合法的；无需凭空制造 source message。
- 已有 snapshot 无新增元数据时，Session → Worker → Requirement 的数据库关系用于校验范围；不能仅信任 summary 中的 predecessor 字符串。
- 跨 Requirement 不自动复用 handoff；同 Requirement 的 scope revision 允许带来源引用，旧摘要必须标成历史参考，以当前 Task 合同为准。
- handoff frame 不写成反复可重放的用户命令；每次装配最新版本一次，摘要更新不会叠加无限副本。

## 6. F4：绑定 Memory 的真实工作范围

### 6.1 scope 含义在本次定死

| scope | 自动装配绑定值 | 说明 |
| --- | --- | --- |
| project | 当前 Project 容器 | 允许 Project 内有意共享；现有 core 与无 scope_id 的 project 记录保持兼容 |
| workflow | `task.workflow_revision_id` | 使用 Task 已冻结修订，不能改用 Project 最新活动 Workflow |
| task | `task.id` | 只匹配当前 Task |
| conversation | `conversation_job.conversation_session_id` | 绑定主 Conversation 的真实会话；Worker 的私有 Session 不能冒充 Conversation Session |

本次不新增 requirement Memory scope，也不重新定义整套权限等级。Workflow 自动选择严格绑定修订；显式读取旧修订是另一次有来源的参考读取，不能默默混入默认 selector。

### 6.2 文件与方法

| 文件 | 方法/类型 | 修改内容 |
| --- | --- | --- |
| `app/v1/memory.py` | 新增 `MemoryScopeContext` | 传递 project_id、task_id、workflow_revision_id、conversation_session_id |
| 同上 | `MemoryManager.build_context()` | 增加 scope_context 参数；每个 selector 先解析实体身份再检索 |
| 同上 | `_scope_id()` → `resolve_selector_scope()` | 返回明确过滤条件；缺失 scope 身份不得返回 None 并继续查询 |
| 同上 | `FileMemoryStore.list/search()` | 保持底层显式 scope_id 匹配，不把自动选择的 None 当作 wildcard |
| `app/v1/runtime.py` | `_input_for()` | 从当前 task 的冻结关系构造 scope_context |
| `app/v1/conversation.py` | `_process()` | 从实际 Job/session 构造 scope_context；不依赖模型传来的会话标识 |
| `app/v1/capabilities.py` | Memory read/search/write handler | 把现有 lambda 提取成具名 handler，落实默认绑定、显式引用校验和预算投影 |
| `app/v1/store.py` | `memory_read/write/search()` | 保留存储 facade，按需新增分页 facade；不在这里猜测当前运行范围 |
| `app/v1/domain.py` | `MemorySelector` 字段说明 | 说明 scope 默认绑定当前实体；本次不加任意 cross-scope 自动装配字段 |

缺少身份时：`required=True` selector 明确失败，指出缺少哪个 scope 身份；optional selector 返回无匹配并在 manifest 中记录 `scope_unavailable`。不能把查不到精确记录转成搜索整个 Project 的该类 scope。

### 6.3 写入与已有数据

只修读取会导致模型继续写入无 scope_id 的记录，因此同一修复要覆盖 Agent Memory 写入：

- Agent 写入 task/workflow/conversation 记录时，scope_id 缺失则从真实执行上下文填入；无可推导身份则返回参数错误。
- 显式 ID 必须属于当前 Project，按既有工具职责允许 Main 明确访问历史实体；不能将某个 Project 的 ID 直接用于另一个 Project。
- 自动 selector 不继承显式工具查询中的 cross-scope 选择。显式 search/read 的输出必须带 scope_id，告知它是历史参考。
- 既有 workflow/task/conversation 记录的 scope_id 为 None 或自定义未知字符串时，不猜测迁移归属、不改写原文件；自动选择排除，显式 reference 仍可回读并显示范围未解析。
- 原来 `project.memory.search(scope='workflow')` 无 scope_id 的 Agent 调用改为绑定当前 Workflow；需要旧范围时显式提供 ID。无 scope 的 Project 广泛搜索仍可保留为显式探索操作，输出必须有范围标识并限长。
- `api.py` 的管理性 Memory 读取不是自动 prompt 装配，不改成偷偷套用某个最新 Task；若公共 API 写入缺少范围，保持存储兼容并标成未绑定，不能自动污染当前 Agent 输入。

`required` 仍表示 selector 必须命中，不把它解释为命中记录的全部文本必须无条件塞入模型。关键执行约束仍由 Task 合同和当前 Worker 输入保留，见下一节。

## 7. F5：Memory 装配与完整回读预算

### 7.1 两阶段处理，最终只有一个总预算

`MemoryManager.build_context()` 负责范围正确的候选收集、去重、基础内容限制；`ContextManager.prepare()` 在知道实际 tools、当前合同、校准比例之后，执行最终请求分配。

这是同一条装配链的两个阶段，不能变成两个各自声称“在上限内”的独立预算器。初步选中 Memory 的完整来源可以留在运行内存/存储，最终 Provider 输入必须由原始结构按剩余预算重新生成，避免层层截断后游标或 hash 对不上。

### 7.2 预算计算与优先级

延用：

```text
B = floor(model_window * (1 - safety_fraction)) - reserved_output
T = floor(B * target_fraction)
```

先计入不可静默裁剪内容 P：system 协议、工具 schemas、当前 Task/Assignment 合同、当前用户请求、仍保留的 Worker 指令及必要调用配对结构。不要把整份 project_core Memory 或 ledger 原文混入 P。

`R = max(0, T - estimate(P))` 为软目标内的参考空间；如果 P 大于 T 但不超过 B，允许只带必要内容和最小引用，继续执行。若 P 本身超过 B，则给出明确 required_context_overflow，不截断合同。

拟新增 ContextPolicy 的上限参数（初始值，后续只依据测量调整）：

| 参数 | 初始值 | 用途 |
| --- | --- | --- |
| `ledger_view_tokens` | 4,000 | 恢复执行摘要总量 |
| `handoff_view_tokens` | 2,000 | Session 交接参考总量 |
| `memory_view_tokens` | 8,000 | Memory 自动装配总量 |
| `memory_record_tokens` | 2,000 | 单条可选 Memory 摘录 |
| `memory_candidate_limit` | 100 | 单 selector 进入候选装配的记录数 |

这些均为软组件上限，不承诺一定分得这么多。按当前剩余 R 分配：未解决执行事实和来源 → handoff → Memory → 最近历史；保留原有完整工具批次压缩，最后重新测量整个请求，不满足则继续缩减可选部分或明确阻塞。

模型 usage 校准使估算比例上升后，下一次请求重新投影所有可选组件，不沿用上次“已经通过”的字节额度。Provider 超限后的单次缩减重试同样要缩减 ledger/Memory/handoff，不能只删工具批次。

### 7.3 选取、manifest 与语义边界

- 每个 selector 的候选依旧使用现有索引排序；同分按稳定 reference 排序。
- 多个 selector 命中相同 reference 只计一次，同时保留全部选择原因。
- core 文件读取尊重 active 状态；空白/失效条目不自动注入。
- 可选 Memory 不再全部带全文。返回片段必须带 `reference/revision/sha256/scope/scope_id/content_complete`，需要其余原文时使用分页 read。
- manifest 区分 `selected`（全文或部分）、`omitted`（预算/范围/状态）、candidate 截断数量。`total_candidates` 未穷举时标为未知，不报假精确数值。
- 默认 limit 从无界变为候选上限，不把 `omitted=[]` 用作“没有丢失内容”的暗示；manifest 自身也有预算，很多遗漏时使用计数和有限样例。
- `DECISIONS.md`/`CONSTRAINTS.md` 是可持久化的知识来源，但不能依靠文件名证明其中任意历史文本仍属于当前 Task 的授权。当前确认的合同和 Worker 指令必须完整保留；Memory 参考被摘录时明确可回读。
- 本次解决装配的范围和容量，不声称纯关键词摘录能保证理解所有长文语义。若某任务必须使用一份长记录，Agent 应按引用读相关片段；确定性测试和后续真实任务验收均要检查关键事实未被遗漏。

对于“Memory 是唯一来源、而且有大量仍有效的强约束”的情况，不允许自动把它归为无关历史。装配提供未全文读取标识和来源，当前规划/执行在确认关键约束前必须回读；若确认后的必要上下文仍超限，报告阻塞。此次不增设一位模型来代替主 Agent 判断约束有效性。

### 7.4 分页读取与版本稳定

| 文件 | 方法 | 修改内容 |
| --- | --- | --- |
| `app/v1/capabilities.py` | `project.memory.read` 注册、具名 handler | 增加 offset、max_characters、expected_sha256；短内容保持原有字段，长内容返回连续页及 next_offset |
| 同上 | `project.memory.search` handler | 返回预算内记录视图，不把每条命中全文作为工具输出 |
| `app/v1/memory.py` | 新增 `MemoryManager.read_page()` | 读取单记录、检查 revision/hash、连续切片、返回总长度和游标 |
| `app/v1/store.py` | 新增 `memory_read_page()` | facade 转发；原 `memory_read()` 保持完整读取供文件/API 内部使用 |
| `app/v1/context_projection.py` | `slice_source_text()` | 与 context snapshot/message 分页共用字节与字符游标规则 |
| `app/v1/context_manager.py` | `project_result()` 及分页投影分支 | 对合法分页结果只进一步缩短连续片段并重算 next_offset，不能做头尾摘要 |

两次分页间原文 hash 变化时，返回明确 `source_changed` 及当前版本；不拼接两个版本的原文，不移动原来的 next_offset 冒充连续读取。自动 Memory manifest 和工具回读均使用文件内容 hash，不能用摘要 hash 冒充来源版本。

初步候选限制通过 `MemoryManager` 现有 Store/Index 接口实现；不要求所有自定义 Provider 马上新增方法。`read_page` 可以在 Manager 中基于现有 `store.read()` 实现，从而维持可替换 Provider 的兼容。

### 7.5 接入位置与当前输入的其他部分

- `runtime.py::_input_for()` 与 `conversation.py::_process()` 都必须接入，不能只修 Column 而留 Main Agent 自动加载无界 core Memory。
- `agent_run_preparation.py` 为当前 frame 保存结构化组件来源；`context_manager.py` 每次请求重新投影。`_interpretation_messages()` 生成的 Conversation 阶段视图也必须通过同一最终预算入口。
- `agent_provider.py::_interpretation_messages()` 应从当前 frame 的结构化来源构造阶段视图，并同步保留内部投影元数据；更新 work_intent/turn_contract 后重新预算，不从已经截短的 Memory 文本生成另一份不可回溯输入。该修改不改变现有讨论/执行边界判定规则。
- `AgentRunSpec`、`PreparedAgentRun` 的内部数据不直接序列化为 Provider 输入；公开的 `model_complete(messages, tools, ...)` 签名保持不变。
- `AgentRunPreparer.prepare()` 遇到实际装配的 Memory 时，确保 `project.memory.read` 和必要的 `project.memory.search` 只读回读能力可用，再生成最终 tools 列表；不能向模型提供不可调用的读取提示。既有 Column 不因此得到 Memory 写入或主 Agent 规划能力。
- 原有 artifact 限制仍保留；本次不重新设计 artifact 生命周期。它们的实际大小必须计入 P/参考总预算；必要输入不足以容纳时明确报告，不因只修 Memory 而忽略其他字节。

实现时固定调用次序：Runner 构造 Run → Preparer 提取完整 Runtime ledger、装配当前 frame 与私有结构化来源、解析只读回读工具 → Provider 建立/复用 ContextManager → 从原始组件生成最终有界 frame → 工具历史投影/压缩 → 测量完整请求 → 调用 Provider。`ContextManager.observe()` 更新的估算比例必须在下一次组件投影前生效。不得在第一次投影前发送“试试看能否容纳”的完整请求。

## 8. 数据兼容、恢复与发布

| 现有数据 | 升级处理 | 禁止行为 |
| --- | --- | --- |
| v1 checkpoint，无 coverage_json | 从当前 Assignment 原消息保守重建，然后写 v2 | 继续按最大 ID 删除全部前缀 |
| 已有 Worker snapshot | 按真实 Session 归属加载为 handoff reference | 将摘要解释成新的 Task/授权 |
| 旧 prompt 中的大 ledger/current_assignment | 重建有界视图，识别 runtime frame 并替换 | 把整个旧 Run snapshot 当历史 user 内容重放 |
| Memory 无法解析 scope_id | 留原文件，自动装配排除，显式 reference 可读 | 根据内容或最近 Task 猜测归属 |
| paused / blocked_runtime Task | 保持现状，用户或已有明确控制入口恢复时用新代码 | 升级时批量自动重跑命令或重启所有 Task |
| pending/acknowledged Worker 输入 | 保持原状态，消费与源消息存储同事务 | 迁移时重新投递已确认消息 |

发布采用停旧 Runtime、备份数据库与 Memory、升级、启动新 Runtime 的顺序，避免新旧 checkpoint reader 混跑。新增列与旧位置式 INSERT 不兼容，因此不能承诺降级旧二进制继续写新数据库。若需回滚，恢复成套代码/数据库/Memory 备份，并核对备份后外部副作用；不能只恢复 Task 状态后盲目重放。

此次实施不主动处理任何生产 Project 的 paused 状态。先用生产数据结构的只读副本/隔离副本验证恢复，再通过原有明确操作恢复具体任务。

## 9. 回归设计：精确到入口与断言

新建 `tests/test_context_memory_fix_contract.py` 集中放五项反例的修复验收；原始诊断脚本保留为基线证据，不把失败观测文件覆盖成“修复成功”。新增恢复与迁移用例可单独放 `tests/test_context_checkpoint_replay.py`，Memory 用例放 `tests/test_memory_scope_budget.py`。

| 用例 | 真实入口 | 关键断言 |
| --- | --- | --- |
| F1-1 大日志恢复 | `_column_action_ledger` → Preparer → Provider 注入模型 | 4×110 KB 原文不出现在 system/current frame；完整 ledger 仍有 4 条完整结果 |
| F1-2 实际 Await 恢复 | `WorkflowRuntime.reconcile_await()` / `_resume_awaited_execution()` | 已完成副作用不再次调用；模型恢复视图有界；原回执可定位 |
| F1-3 overflow retry | `ProviderTurnRequester.request()` | 第二次请求严格更小；ledger、Memory 同样可缩减；仍最多一次重试 |
| F1-4 completion 真实性 | `AgentToolBatchExecutor._complete()` | 截短视图不能掩盖 Runtime ledger 中的未解决失败；伪造 evidence ID 被拒绝 |
| F1-5 多次恢复 | 同 Assignment 重建多个 Run | 当前合同和 ledger 不随恢复次数线性重复；真实 Worker 指令不被 frame 过滤 |
| F2-1 successor | 真实 `agent.worker.replace` → 新 Assignment → Preparer | 注入模型实际看见 marker；旧 Worker 仍 retired，完成状态未继承 |
| F2-2 idle compact | `save_context_snapshot()` → 同 Worker 新 Assignment | 实际 prompt 包含 Session 摘要，同时当前 Assignment checkpoint 正常加载 |
| F2-3 摘要回读 | `agent.context.read(snapshot_id, offset)` | 可重组完整中文长摘要；叶子不能读别的 Worker snapshot |
| F2-4 生命周期竞争 | snapshot 写入与 assign 竞争 | 在同一事务的 ownership/idle 检查下只允许合法结果，不写入运行中 Worker 的错误边界 |
| F3-1 六条输入反例 | 真实 Worker send/consume → 压缩 → 新 Store 重载 | 第 1 条有效要求重载后仍在，不只保留最后 4 条 |
| F3-2 ID 交错 | 两个 Assignment 交错写消息 | coverage 只含自己的实际工具消息 ID；不得按首尾误覆盖其他消息 |
| F3-3 连续两次压缩 | prepare/save 两次，再重启 | 最新 coverage 累计包含前次归档；不恢复旧原文批次、不丢 notes |
| F3-4 旧 checkpoint | 只有 through ID 的旧数据库 fixture | 从当前 Assignment 原文重建；新 checkpoint 为 v2；用户输入保留 |
| F3-5 摘要失败/失去 lease | 注入 summary error / ownership change | 原文仍可读；无权限时不提交 checkpoint；不重复工具副作用 |
| F3-6 合并/不完整工具尾部 | `replayable_session_messages()` | 所有 source IDs 可追溯；不持久化 orphan tool 或缺失结果的覆盖 |
| F4-1 同 Project 两 Workflow | `_input_for()` | 只选当前 task 冻结修订的 Memory；Project 当前活动修订变化不影响它 |
| F4-2 四种 scope | Runtime + Conversation + Memory handlers | 缺身份不 wildcard；跨 Project ID 被拒绝；Project 共享记录可选 |
| F4-3 旧未绑定记录 | FileMemoryStore + selector | 文件仍在，自动装配不混入；explicit reference 有范围提示 |
| F4-4 默认写入 | 真实 capability dispatch | scope_id 来自真实执行上下文；无身份时明确失败，不写 unknown 记录 |
| F5-1 core 增长 | `build_context()` → Main/Column 实际 Provider 输入 | 230 KB optional core 产生有界视图和 source 引用；当前合同仍完整 |
| F5-2 混合大输入 | 大 Memory + ledger + tools + artifact | 完整请求低于预算，不能只检查 Memory 字段长度 |
| F5-3 中文分页 | Memory read 工具经 ContextManager 投影 | 返回片段可连续重组；next_offset 不跳字；变更 hash 时停止拼接 |
| F5-4 必要输入超限 | 合同/Worker 输入本身过大 | 明确阻塞且包含来源与组件大小；不调用 Provider，不静默删约束 |
| F5-5 自定义 Provider | 现有 Memory Store/Index 替换 fixture | 不依赖 FileMemoryStore 私有路径，不要求 Provider 新抽象方法 |
| 边界回归 | mailbox / 叶子能力 / repair 测试 | mailbox 仍零模型调用；Column 不新增多 Agent 调度；两轮修复链可运行 |

新增断言要放在**实际发送给注入模型的 messages**、数据库状态和副作用调用计数上；不能只测一个新 helper 的输出形状。实际 token 通过注入 usage 模拟比例校准，测试报告不得把本地 estimate 写成真实 Provider usage。

已存在测试需同步检查：

- `test_architecture_conformance.py` 中从 `messages[0]['context']['action_ledger']` 取证据 ID 的断言，改为读取唯一当前 frame 的有界 evidence view，再验证 Runtime 完整证据。
- `test_scope_extension.py` 的 successor 用例，增加真实 Assignment Preparer/model 输入断言，不能只查无 Assignment 过滤的 session_history。
- `test_agent_recovery_and_fencing.py` 的 snapshot、ownership 测试，补 v2 coverage 及 Session handoff 独立加载断言。
- `test_context_lifecycle.py`、`test_context_acceptance_regression.py` 保留大日志、overflow retry、验收失败和冻结场景验证。
- `test_memory_workcell_contract.py` 保留 Provider 可替换测试；文件名虽有旧术语，本次不顺手重命名或改回 Workcell 架构。
- `test_repair_notification_contract.py`、`test_persistent_agents.py`、`test_conversation_contract.py` 保留通知隔离与生命周期回归。

## 10. 实施顺序与分批提交边界

| 批次 | 内容 | 完成条件 |
| --- | --- | --- |
| 1 | F1 ledger 分离、唯一 current frame、基础投影与证据目录 | 大日志恢复和 Await 回归通过，completion 证据不变 |
| 2 | F3 coverage migration、精确 replay、Worker source ID | 六条指令反例、多次压缩、旧库升级和 fencing 通过 |
| 3 | F2 Session handoff、snapshot/message 分页 | successor 与 idle compact 的实际模型输入、完整回读通过 |
| 4 | F4 scope 绑定、Agent Memory 默认写入校验 | 四种 scope 与旧数据兼容通过 |
| 5 | F5 Memory 总预算、分页及最终请求预算整合 | Main/Column、中文、大混合输入、Provider 校准重试通过 |
| 6 | 五项组合回归与文档更新 | 恢复 + 交接 + Memory + repair 组合通过，README 不夸大效果 |

每批先完成失败用例，再改实现，执行对应测试。所有批次完成后运行上述相关套件与完整离线 tests；真实外部模型端到端另行记录，不把注入模型测试当作语义交付完成证明。本设计阶段没有执行这些拟新增用例，也没有宣称它们已经通过。

建议组合验收场景：同一个需求中，开发 Worker 产生大测试日志后等待；Main 发送六条修订输入；运行一次压缩并重启；继续执行完成；同 Requirement 新 Task 显式复用或替换 Worker；另一 Workflow 的同关键词 Memory 存在但不进入默认输入；之后走现有审核/返修 Column 完成交付。检查完整过程中无重复副作用、无丢失有效输入、无 mailbox 模型调用、无窗口超限发送。

## 11. Review 时需要确认的具体设计选择

本方案采用以下确定选择，便于逐项 review，而非留给实施时临时猜测：

1. workflow Memory 绑定冻结修订 ID；未绑定旧记录不自动归属。
2. checkpoint 使用真实消息 ID 累计覆盖集合；不再使用最大 ID 剪前缀。
3. 旧 checkpoint 通过原始消息保守重建，不做破坏性批量迁移。
4. 当前 user/Worker 指令不做语义自动淘汰；超出必要输入预算时明确阻塞。
5. Session snapshot 仅作交接参考，与 Assignment checkpoint 同时加载。
6. 模型 ledger 为有界视图，内部 ledger 保留全文；新增只读 evidence 目录是为可追溯性服务。
7. Memory 预算不靠新模型调用维持；按范围、已有排序和来源分页工作，效果仍需要真实任务验证。
8. 不改 Web、不改变 mailbox 职能、不新增主 Agent 层级、不在 Column 内多 Agent loop。

完成标准：五项基线反例在真实装配/恢复入口均不再出现，原始证据和有效指令仍可追溯，既有交付、验收、生命周期和通知隔离回归通过。达到此标准后才能把这五项缺陷标记为已修复。
