# DevWerk Runtime P0 详细修改方案 — 2026-09-09

**文档状态：待 Review，未实施。**

关联：[Bug 登记簿](DEVWERK_P0_Runtime_Bug_Register_2026-09-09.md)；[独立复审与复现结果](DEVWERK_P0_Runtime_ReReview_2026-09-09.md)。

本方案在当前工作区源码基础上提出，所有“新增”“修改”“应当”均为计划，不代表已经完成。本文不引用 docs 下历史测试通过结论。

## 1. 本次目标与范围

目标是让 v1 的 Web Conversation → 需求讨论 → workflow/plan → 小 Task 执行 → 阶段完成 → 项目交付链路可正确推进，并在故障下给出可信、可恢复的状态。

覆盖六项已确认 P0：

| 问题 | 需要建立的保证 |
|---|---|
| R1：重复意图误去重 | 新意图拥有新身份；恢复旧意图保持旧身份 |
| R2：无关证据修复失败 | 修复关系由受约束规则建立，模型不能自行清除失败 |
| R3：queued 丢项 | 数据库作为调度来源，内存丢失不等于工作丢失 |
| R4：过期 owner 写状态 | 发起者所有权与目标状态修改在同一事务校验 |
| R5：Await 事实不同步 | ledger 投影当前 receipt 结算，同时保留历史调用 |
| R6：恢复重置预算 | 逻辑阶段预算覆盖所有 AgentRun/Attempt/continuation |

不在本次引入分布式队列、消息中间件、通用事件溯源框架、独立执行服务或通用业务验证语言。继续使用单机进程、SQLite、现有 CapabilityRegistry、AgentCore 和 WorkflowRuntime。

本方案涉及的表与接口名称为拟定名称；实现时可以调整命名，不能改变下面的不变量和验收行为。

## 2. 建议先 Review 的决策

| 编号 | 建议决定 | 收益 | 明确代价/限制 |
|---|---|---|---|
| D1 | 在执行工具前持久化操作意图，参数 hash 只用于比较 | 消除 R1，同时保留可恢复身份 | 新增一个 operation 表，调整 dispatch 调用链 |
| D2 | v1 关闭模型自定义的“任意替代修复”通道；支持受约束的原操作成功重试 | R2 修复可以小而确定 | 不同命令替代修复需回到 workflow rework，或以后增加已发布验收规则 |
| D3 | 普通 shell 的 started 且无结果视为 unknown，不自动重放 | 不重复真实外部副作用 | 结果不明时进入明确监督；不承诺任意 shell exactly-once |
| D4 | 所有 queued job 周期补扫；running 只走租约恢复 | 消除 R3，不加中间件 | 持续 DB 故障时仍只能等待数据库恢复 |
| D5 | DB-only capability 使用短事务包住 handler 与 receipt；混合 I/O 拆成准备和提交 | 关闭 R4 的提交窗口 | 必须盘点全部写入 handler，不能只改 task.resume |
| D6 | ColumnRun 持久化累计预算及无进展次数 | 关闭 R6，符合小 Task 交付目标 | 超大阶段需要拆分；本方案给出可 Review 的默认上限 |
| D7 | 历史记录保留；无法确定映射的活跃执行进入 migration hold | 不伪造执行事实 | 少量旧活跃任务需单独核对后继续 |

推荐接受 D1–D7 作为这一轮实现基线。D2、D3、D6、D7 涉及可见产品行为，尤其值得 Review。方案没有要求为了普通状态恢复反复向用户申请确认；仅事实无法确定时停止自动重放并明确报告。

## 3. 当前结构与改动原则

当前代码已有可利用基础：

- SQLite tx 支持同线程嵌套 savepoint，connect 可借用当前事务连接，见 `app/v1/store.py:255-291`。应复用它，而不是让各层独立提交。
- receipt 已有 `UNIQUE(project_id, execution_key)`；tool invocation 已有 `UNIQUE(agent_run_id, tool_call_id)`。
- AgentRunner 在 dispatch 之前持久化 assistant message；可将这条消息的数据库 ID 用作批次来源。
- AwaitHandle 有 checkpoint 和 execution_key；可以增量建立 operation/receipt 的直接关联。
- Task 已有 state_version 与租约检查；Conversation 已检查 job 状态和 lease owner，但控制 handler 没有在提交事务中使用该条件。
- 现有 max_column_visits、max_recovery_attempts 保留，新增累计预算补齐它们覆盖不到的恢复路径。

禁止使用以下局部替代修复：

1. 将 effect_occurrences 改成另一个内存变量，或简单读取 MAX 后自行拼 key。
2. 每次重试更换 execution_key，以此避开旧 receipt。
3. 将所有 awaiting 都解释为成功，或者只修 None.get。
4. handler 前后多检查一次 owner，却不改变事务边界。
5. 捕获队列异常后仅记录日志，不恢复持久化工作。
6. 每次 Await 后重新给出全额阶段预算。
7. 让模型声明“已修复”“有进展”直接改写执行事实。

## 4. 共同执行模型：意图、回执、调用历史与完成事实

### 4.1 四类记录的职责

| 概念 | 回答的问题 | 写入时机 | 是否作为完成真相 |
|---|---|---|---|
| Operation intent | Runtime 接受了哪一次具体操作？ | handler 前 | 提供身份、范围、恢复位置 |
| Execution receipt | 这次操作目前结算成什么？ | 执行前/等待/终结时 | 是，采用当前结算 |
| Tool invocation | 当时向模型返回了什么？ | 工具响应形成时 | 保留历史，不独立决定当前状态 |
| Completion snapshot | 哪些已结算事实支持此次完成？ | 准入后、阶段提交时 | 是，绑定具体 receipt 版本 |

参数摘要仅用于同操作比较、诊断和受约束重试关联；既不是 operation ID，也不保证业务语义相同。

### 4.2 最小增量数据结构

以下为逻辑 schema，实施时补齐索引、外键、CHECK 和迁移校验。

**新增 `v1_execution_operations`：**

| 字段 | 类型/约束 | 语义 |
|---|---|---|
| id | TEXT PK，Runtime 生成 | 一次被接受的具体意图 |
| project_id | NOT NULL FK | 项目隔离 |
| task_id / column_run_id / column_attempt_id | 可空 FK | Column 操作归属，Conversation 没有 Column |
| conversation_job_id | 可空 FK | Conversation 操作归属 |
| source_kind | agent / sequence / runtime | 谁生成操作 |
| source_key | NOT NULL | 稳定来源位置，不能来自参数 hash |
| source_index | INTEGER NOT NULL | 批次中的工具序号或 sequence step |
| capability / effect_kind | NOT NULL | 执行器类型 |
| arguments_json / arguments_sha256 | NOT NULL | 规范化参数与完整比较依据 |
| origin_agent_run_id | 可空 FK | 最初接受意图的 AgentRun |
| source_message_id | 可空 FK | Agent assistant message 的持久化 ID |
| retry_of_operation_id | 可空 FK | 受约束的失败重试关系，由 Runtime 填写 |
| created_at | NOT NULL | 创建时间 |

唯一约束：`UNIQUE(project_id, source_kind, source_key, source_index)`。一个来源位置只能有一个意图；命中同位置时 capability/参数必须完全一致，否则报一致性错误，不能覆盖。

source_key 的构造规则：

- Agent：assistant message 的数据库 ID；恢复这条已保存消息使用相同 ID。
- Sequence：持久化 sequence 执行代号与 step 路径；继续同一步不变，明确业务重试获得新代号。
- Runtime poll/cancel/cleanup：AwaitHandle ID + 动作种类 + 已持久化动作序号。新的 poll 是新观察，不能永久复用第一次 pending 的读取结果。
- 不以外部模型返回的 tool_call_id 单独作全局 key。它仅在原消息/原 AgentRun 内关联。

同一 ColumnRun 内另需持久化单调的接受次序（可在 operation 表增加 scope_sequence 并设置范围内唯一约束），用于跨 AgentRun/Sequence 的稳定 ledger 排序。分配序号与插入意图在同一写事务完成；仅用 created_at 排序不足以处理同时间戳的多个意图。

**扩展现有表：**

| 表 | 新增字段/约束 | 目的 |
|---|---|---|
| v1_execution_receipts | operation_id，可空唯一 FK；state_version 默认 1；failure_disposition；effect_certainty | 将新 receipt 绑定意图，支持有版本的 CAS 结算 |
| v1_tool_invocations | operation_id、receipt_id，可空 FK | 历史响应能找到当前结算 |
| v1_await_handles | operation_id、receipt_id，可空 FK | 异步生命周期直接绑定原执行 |
| v1_agent_runs/checkpoint | 持久化 source_message_id、已处理 source_index 等恢复信息 | 从已接受消息继续，而非重新让模型猜旧动作 |
| v1_column_runs | completion_snapshot_json 或现有输出内的版本化字段 | 冻结完成时引用的 operation/receipt/version |
| v1_conversation_jobs | claim_token，可空，成功领取时生成 | 区分同 session_owner 名称下不同领取代次 |

operation 的状态不再复制一份 receipt.status；未有 receipt 表示仅已接受，执行/等待/终态统一由 receipt 表达。避免两个可写状态字段再次分叉。

scope 字段由 Runtime 从真实执行上下文填写；禁止直接接受模型传来的 project/run/operation 身份作为所有权证据。

### 4.3 receipt 状态与失败语义

| 当前事实 | 可执行的恢复动作 | 能否支持成功完成 |
|---|---|---|
| operation 已保存，无 receipt | 校验 owner 后 CAS 创建 started，执行一次 | 否 |
| started，原 owner 仍活跃 | 等待原 owner，不并发执行 | 否 |
| started，原 owner 已失效且无终结事实 | 转为 unknown / 待核对 | 否 |
| awaiting | 使用关联 AwaitHandle poll/订阅，不再次启动 | 否 |
| completed | 返回已存结果，补写缺失投影 | 是 |
| failed | 保留失败；明确重试需新 operation | 否，除非被受约束的后续重试修复 |
| unknown | 可用已声明 adapter 查询确认；否则监督 hold | 否 |
| rejected_before_effect | 返回工具输入/权限/协议错误供纠正 | 不是已执行的业务失败 |

建议保留现有 started/awaiting/completed/failed 字符串，增补 unknown。执行前拒绝通过 failure_disposition 表达，不把异常拒绝当作 completed 成功。effect_certainty 至少区分 no_effect、effect_known、effect_unknown；非零 shell exit 本身不能证明没有部分副作用。

所有终结更新必须检查预期状态与 state_version，检查 rowcount；重复相同结算幂等，重复不同结算报 `receipt_settlement_conflict`，不能静默覆盖。failed 不能原地重置为 started；真正重试创建新意图/新 receipt，旧失败保留。

### 4.4 事务与恢复顺序

1. Provider 返回完整响应，先规范化工具 ID；保存 assistant message 及本批 operation 意图，再允许任何 handler 执行。消息与操作批次尽量在同一个短事务提交。
2. 获取已保存批次中下一条未处理意图，owner 校验后领取 receipt。批次工具预算也在此之前预留，避免执行一半才发现超限。
3. DB-only 操作按第 8 节与 receipt 原子提交。外部操作按状态 CAS 启动，不能长时间持有 SQLite 写锁。
4. 结算 receipt；将 invocation、tool message 与处理位置在同一 DB 事务补齐。外部效果到 DB 结算仍有不可消除窗口。
5. 遇到 Await，AwaitHandle、awaiting receipt 和 checkpoint 原子保存。协议工具 column.await 自身也要保存恢复位置，但它不产生外部业务成功证据。
6. 崩溃恢复先读取未处理的持久化批次和 receipt，再决定是否请求模型；不能重新生成旧工具批次后靠 hash 猜测去重。
7. 新一轮模型响应必然有新 source_message_id，即使参数完全相同也是新意图；新意图不能用于绕过同一逻辑执行中未知的旧副作用。

**崩溃点矩阵：**

| 崩溃位置 | 恢复结果 |
|---|---|
| 响应返回但尚未保存 | 尚未执行工具，可以重新请求模型 |
| 批次已保存，尚未领取 receipt | 执行保存的批次，保留原意图 ID |
| started 已提交，handler 是否启动不明 | 按 unknown 保守处理，不能猜测未启动 |
| 外部效果发生，receipt 尚未结算 | unknown；有 adapter 查证则核对，否则 hold |
| receipt 已 completed，invocation/tool message 未写 | 返回保存结果并补齐历史，不重跑 handler |
| awaiting 与 handle 已保存 | 恢复同一个外部等待 |
| poll 成功已结算，Agent 尚未恢复 | 读取 completed 事实，恢复模型一次 |
| 完成已准入，阶段 transition 尚未提交 | 按固定 completion snapshot 恢复提交，重新校验 owner/状态版本 |

普通命令“执行 exactly-once”不作为可实现承诺；保证的是不会把未知结果伪装成安全重试，也不会把新操作吞掉。

## 5. R1 的具体修改

### 5.1 修改入口

- `agent_runner.py`：保存响应批次、暴露稳定 source_message_id；恢复前优先 drain 已保存批次。
- `agent_tool_execution.py`：删除以内存 occurrence 决定身份的逻辑；每次 dispatch 接收 operation_id，execution_key 统一为 `op:{operation_id}`。
- `agent_models.py`、`agent_run_preparation.py`：携带真实执行 scope、continuation 恢复位置及 owner token，不让 prompt 承担恢复协议。
- `capabilities.py`：验证 operation 与参数/scope 一致；通过 receipt 决定执行或恢复。
- `store.py` / 拟新增 `repositories/execution_repository.py`：封装 accept_batch、claim、settle、resume 查询，避免新增 SQL 继续散落。
- `runtime.py`：Agent 与 Sequence 共用执行身份原则；Await source agent/sequence/runtime 都有稳定来源。
- `session_replay.py`：恢复 tool response 时保持原调用来源，避免给同一历史响应再次分配新意图。

### 5.2 v1 行为案例

| 场景 | 意图身份 | handler 次数 |
|---|---|---|
| 同消息中明确两次同参调用 | source_index 不同，两条意图 | 2 |
| Await 前后分别同参调用 | source_message_id 不同 | 2 |
| 重放同一保存消息 | 同来源意图 | 已完成则不新增执行 |
| 新模型调用重试已知失败 | 新意图，可关联 retry_of | 依重试规则执行 |
| 新模型调用重复未知外部操作 | 新意图存在，但执行屏障拒绝派发 | 0 次新增，先核对旧操作 |
| 两个 worker 恢复同一意图 | 相同唯一键，仅当前 owner 能领取 | 至多一个合法启动 |

有 unresolved unknown 外部副作用时，同一逻辑阶段先进入 reconcile/hold，不能只换参数绕过。这是执行一致性要求，不是通用安全权限机制。

## 6. R5 的具体修改：以结算事实建立 ledger

### 6.1 投影算法

当前 `_column_action_ledger` 按 AgentRun 扫 invocation。改成以 ColumnRun 内 operation 为主要集合，左关联当前 receipt；invocation 作为历史来源补充。

顺序以持久化来源/接受次序确定，不能因 receipt 异步完成先后改写意图顺序。每个 operation 在当前 ledger 只出现一次；重复 tool response 不新增业务效果计数。

每条投影至少包含：

```text
operation_id, capability, normalized_arguments, origin_scope,
receipt_id, receipt_state_version, current_status, result,
failure_disposition, effect_certainty, retry_of_operation_id,
origin_agent_run_id, source_message_id, source_index
```

- awaiting 的历史 invocation 保留原样；receipt completed 后 current_status 是 completed。
- poll 的成功只说明 poll 请求成功。只有 adapter 解释外部业务状态为 terminal success，才能将原 receipt 结算 completed。
- 纯 read 成功、timer 到期、heartbeat 不是原 process 操作成功的替代证明。
- receipt 存在但 invocation 缺失仍必须进入 ledger；否则崩溃窗口会漏掉真实副作用。
- 从模型输出中出现的 receipt_id 不建立可信关联，所有关联来自数据库。
- error 的 JSON 边界解析使用显式类型检查；None、非 dict、缺失 message 均产生结构化错误说明，不能抛 AttributeError。

### 6.2 Await 结算事务

```text
BEGIN IMMEDIATE
  assert current Task owner / generation
  load current handle and receipt
  validate expected status/version and operation association
  CAS settle receipt to completed/failed/unknown
  persist normalized external result and handle checkpoint
  append settlement event
COMMIT
```

外部失败必须同样结算原 receipt，不只将 AwaitHandle failed。重复 poll/事件结果不能反向覆盖已结算状态。旧 owner 收到迟到成功不能提交，由当前恢复者核对。

成功后创建 continuation 时，handle 上用 CAS 保存/认领 continuation 身份；创建新 AgentRun 和这个关联原子完成。重复 scheduler tick 先复用已认领 continuation，不重复请求模型。这是 R5 回归应覆盖的恢复闭环，不能只修 ledger 读取。

完成准入对 pending/unknown 返回不同错误码：

- `pending_execution`：继续当前 Await，不允许成功 transition。
- `effect_outcome_unknown`：核对/hold，不允许猜测。
- `unresolved_execution_failure`：允许受约束重试或 workflow 声明的失败/rework。

允许失败/rework 的 outcome 不能借此让仍在运行的外部操作失去归属。transition 前必须已结算或已有显式的 cancel/cleanup/监督接管记录；默认先保留当前等待并报告阻塞。

## 7. R2 的具体修改：受约束的失败恢复

### 7.1 本轮选择

**v1 去除模型任意指定 failed_evidence_id → resolved_by_evidence_id 的完成豁免权。**

模型正常提交 outcome、output、summary；Runtime 收集机器证据与修复关系。旧 failure_resolutions 参数若非空，返回 `unsupported_failure_resolution` 并解释可行路径；不能静默忽略后继续接受成功。

同时移除 `unresolved_execution_failures` 中“任意后续同 hash 成功就 pop 所有旧失败”的隐式规则。仅按 hash 也会把两个独立同参意图混淆。

### 7.2 原操作重试如何工作

1. Runtime 找到当前 scope 内未解决失败，确认是已结算失败，不能是 pending/unknown。
2. 新调用规范化后与失败操作 capability、参数、执行目录/目标作用域一致。
3. 匹配必须唯一：同时有两个不同失败意图时，不自动用一次成功修复两个。歧义返回结构化说明，不猜测。
4. Capability 的 retry 语义必须允许该类重试；在 effect_unknown、无法确定可重入的外部提交等情况下停止自动关联。
5. 创建新 operation，Runtime 填写 retry_of_operation_id，执行新的 receipt，原失败不可变。
6. 只有该重试 receipt 确定 completed 且 adapter 业务结果成功，失败链才被标为已修复。
7. 有“失败 → 重试失败 → 再重试成功”时沿明确链条解析；保持所有历史失败，禁止形成环或跨项目/跨逻辑阶段关系。
8. 即使原失败被修复，其他独立失败仍阻挡成功。

本轮不新增要求模型填写数据库主键的专用 retry 工具。Runtime 将可重试的原参数和失败原因放进有限上下文，模型修好环境/文件后再次提交原命令，Runtime 按上述约束建立关系。

### 7.3 command 的现实限制

不能用 exit_code=1 推断“没有任何外部效果”。建议给 capability 增加 Runtime 配置的 retry_policy：

| 策略 | 含义 | v1 使用 |
|---|---|---|
| never_automatic | 不自动重复副作用 | 未声明可重入的外部提交 |
| repeatable_after_known_failure | 已明确失败时允许重试 | workflow 已声明的本地构建/校验命令 |
| adapter_reconcile | 先查询原外部任务再决定 | 有 job ID 的异步 adapter |

这些策略由已发布 workflow/运行时注册配置确定，不能由一次工具调用的 success_exit_codes 或模型 reason 临时覆盖。现有普通 command 保持“可以执行任意 argv”的能力，但自动重试能力不能推导为任意 argv 都可重入。

实施需要为内置软件交付 loop 明确哪些构建/测试操作支持上述重试，且做正向交付回归，避免修 R2 后把所有正常开发修复都锁死。没有这项配置时，用明确失败/rework 交回 workflow 调整，不伪造成功。

### 7.4 替代修复与业务验收

不同参数/不同命令的替代修复，本轮不构建通用推理验证框架。推荐两条路径：

- 已发布 workflow 有失败/rework transition：按事实离开失败阶段，后续修复阶段重新执行原验收任务。
- 必须支持替代实现的业务：以后增加受版本控制的 acceptance_rule，规则在 workflow 发布时固定；Runtime 执行并记录所验证的目标、原失败、输入/产物版本及验证结果。

第二条是扩展点，不是关闭本轮 R2 的隐含前提。不能为了支持它加入一个仍由模型任意选择 `print('done')` 的“验收命令”。

本方案保证执行事实正确，不声称通用 shell exit 0 就证明整个项目满足用户需求；业务交付质量仍由 workflow 的具体验收阶段承担。

### 7.5 完成事务

准入使用当前结算投影生成 completion snapshot，包含 outcome、输出摘要、operation/receipt/version 与 Runtime 认可的 retry 关系。阶段提交事务内重新校验 owner、ColumnRun 状态及引用 receipt 版本；任何相关事实变化要求重新准入。

只有此事务可以写入 terminal status、输出引用和对应事件/mailbox/projection。模型说明文本不直接驱动 done。

## 8. R4 的具体修改：把所有权带进业务提交

### 8.1 发起者与目标对象分别校验

拟新增不可变 `ExecutionOwner` 值对象，放入 CapabilityContext：

- kind：task / conversation / trusted_runtime。
- project_id、task_id 或 conversation_job_id。
- lease_owner、Task generation/state_version 或 Conversation claim_token。
- ExecutionControl 的本地取消标记仍用于快速终止；不能替代数据库提交条件。

Conversation 每次成功领取生成新的随机 claim_token，写入 job 并随上下文传递。续租、终结、控制写入均检查 job ID + token + 当前租约。即使 session_owner 字符串相同，也不能让上次领取者重新有效。旧 job 被终结后不可再次使用。

Task 继续使用现有 owner + state_version，实施时确认所有接管路径都递增 generation。若业务 state_version 会随当前执行正常推进而改变，应保持当前逻辑的 generation 语义或明确拆出 claim generation，不能为了通过校验读取并采用最新版本。

提交时包含两个不同条件：

1. 发起者仍持有合法 lease/generation。
2. 被修改 Task/workflow/plan 仍是预期状态/版本。

这两个检查的任一个失败，都不能写出业务事件、mailbox 或成功 receipt。

### 8.2 DB-only handler 的推荐实现

新增明确的 capability 提交分类，而不依靠 side_effect_kind 推断是否有外部 I/O：

```text
commit_mode = db_transaction | external_effect | read_only
```

对于 db_transaction：

```text
BEGIN IMMEDIATE
  assert initiating owner using this DB connection
  load/claim receipt and verify operation scope
  execute DB-only handler using the same connection/savepoints
  validate result contract
  assert initiating owner again before commit
  CAS settle receipt
  persist invocation/event/projection as applicable
COMMIT
```

- 当前 store.tx 的嵌套 savepoint 可复用，但接口应显式传 db/owner，便于审查，不依赖隐蔽的线程局部行为保证全部正确。
- handler 的目标 CAS 失败、输出 contract 失败、最终 owner 检查失败，都回滚业务写入与 receipt。
- 派发后检查本地取消标记失败，也不能留下已经独立提交的 Task 更新。
- DB-only handler 同一事务失败则没有业务效果，可明确标记 no_effect；receipt 失败诊断若另行记录，必须保留“业务事务未提交”的语义。
- 同一线程事务内不能使用 asyncio.to_thread 另开提交，也不能把 db connection 传给别的线程。
- 当前执行者修改自身 lease/generation 的特殊操作要预先定义允许的状态转移；不能无条件在修改后要求旧状态仍存在。v1 优先将这些操作限制在已有 Runtime 控制路径，并为其写专门的提交条件。

### 8.3 必须盘点的 handler

以下是当前注册的状态写入入口，不代表全部已独立复现 R4：

| 类别 | capability | 实施要求 |
|---|---|---|
| Task 控制 | task.retry / reopen / rerun / pause / resume / fail / cancel | 发起者 fencing + 目标 CAS；事件与投影同事务 |
| 规划与任务创建 | workflow.plan.save / task.plan.save / workflow.publish / task.create | 计划/发布/任务写入均携带 owner；禁止单独提交子步骤 |
| 编排决策 | scheduling.decide / backlog.record | 任务状态和决策记录不可脱离同次 owner |
| 监督 | supervision.review.schedule / governance.decision.record | 旧 owner 不能继续安排未来监督或写有效决策 |
| 指令更新 | agent.instruction.update | 指令 revision 与事件受提交条件保护 |
| Loop | loop.apply | 区分读取/解析文件与 DB 发布；不能持写锁做大段文件 I/O |
| 记忆 | project.memory.write / append / supersede | 盘点 DB 与文件副作用；不可因为标注 write 就跳过控制一致性 |

`project.files.write`、`system.files.write`、command 已有专门外部效果控制路径，要检查它们与新 operation/receipt 接口的兼容；不把全部外部操作包进一个大事务。

loop.apply 等混合操作采用“事务外准备不可变输入 → 事务内 owner 校验与 DB 发布”。若准备会写工作目录，需说明 orphan 文件的清理与可见性；不能把文件写入假称成 SQLite 可回滚事务。

DB-only 分类只有在盘点确认 handler 全程可事务化后才能注册；未分类的状态写入 capability 应在启动校验中报错，避免以后新增 handler 绕过约束。

### 8.4 非 Agent 调用者

Web API、scheduler、startup recovery 仍可调用 store；显式使用 trusted_runtime 或实际 Task owner，并保留其目标状态/version 校验。

不能将 `owner=None` 作为 Agent 路径的隐式放行。内部 API 可有独立入口，CapabilityContext 派发路径必须有可验证 owner。此设计服务于执行正确性，不新增用户权限系统。

## 9. R3 的具体修改：数据库成为可恢复队列

### 9.1 调度规则

保留内存队列作为唤醒和顺序缓存，新增 store 查询 `list_dispatchable_conversation_jobs`：

- 查询 durable queued，不仅是本次 newly created 的治理 job。
- 每个项目优先返回队头；排序统一使用稳定全序，如 created_at + user_message_id + job_id。
- 同项目若已有合法 running lease，则本轮不派发该项目后续 job。
- 查询结果只是候选，最终以 claim 事务的 CAS 为准。
- 分页采用 keyset，批次内避免一个项目占满所有名额。持续多个繁忙项目也不能使页尾项目永久饥饿。
- 相应索引覆盖 project/status/稳定排序字段，沿用现有 page/batch policy，不全量读入所有消息。

正常 tick 顺序：

1. 恢复已过期的 running lease，保留原执行事实。
2. 创建符合现有条件的新治理 job。
3. 补扫所有 eligible queued job 并加入内存候选。
4. 由每个 project 的单会话 drain 尝试原子 claim。

用户消息创建后仍可立即唤醒调度，但正确性不依赖唤醒成功。

### 9.2 drain 的异常语义

先查看队头，只有确认持久化状态后才移除本地项；不再用默认 settled=True 推断安全移除。

| claim/查询结果 | 内存处理 | 持久化处理 |
|---|---|---|
| 成功领取 | 移除 queued 缓存，执行当前 job | running + claim_token 已提交 |
| 仍 queued，但当前不能领取 | 保留或允许下一次 DB 扫描补入 | 不变，按退避重试 |
| 已 running，由其他 owner 持有 | 移除重复候选 | 不干预合法 owner |
| 已终结 | 移除 | 不重复执行 |
| OperationalError，无法知道结果 | 不假定成功/失败；退出当前 drain，等待补扫 | DB 恢复后重新查状态 |
| cancellation/shutdown | 清理内存任务引用 | 依 DB 状态恢复，不能随意把 running 改回 queued |

事务提交后客户端收到异常的情况尤其需要重新读取状态：可能已 running，不能直接再次 dispatch。

### 9.3 后台恢复与降级

- 用有限退避避免数据库异常时忙循环；建议 0.5s 起、上限 5s，复用/新增明确 policy。
- 同步修改 `_session_finished`：因基础设施异常退出的 drain 不得仅因内存还有候选就立刻重建自身；由退避到期的调度 tick 再启动。队列头保留和自动重启不能组合成异常忙循环。
- 短暂领取故障不标记业务 job failed，更不将 mailbox 提前 acknowledged。
- 真正执行失败走现有失败记录/监督；running 且 lease 未过期不会被 queued 扫描重复启动。
- 数据损坏或 schema 错误不能无限吞掉；保留结构化异常并报告服务不可用，不能为了放行后续消息把队头当成成功。
- 运行中 DB 恢复后，应在下一次成功扫描和领取周期推进，回归用逻辑 tick 断言，不依赖长时间 sleep。
- 启动恢复与正常 tick 使用一致的 queued 补领入口；原 running 的恢复策略须与 operation unknown 规则兼容，不能仅改 status=queued 后直接重放所有工具。

## 10. R6 的具体修改：跨恢复的阶段预算

### 10.1 计数归属

现有 agent_* 上限继续约束单 AgentRun。新增 ColumnRun 累计预算，覆盖同阶段的所有 AgentRun、Attempt、Await continuation 和 provider 重试。

建议在 `v1_column_runs` 增加下列计数/快照字段，避免引入独立预算服务：

| 字段 | 用途 |
|---|---|
| execution_budget_json | 创建 ColumnRun 时冻结的累计限制与 policy hash |
| model_requests_reserved | 已派发或预留的模型请求次数 |
| tool_calls_reserved | 已接受且预留的工具调用数，包括协议工具 |
| agent_continuations_started | 已启动的恢复 AgentRun 数 |
| active_time_charged_ms | 已使用及尚未退还的活动执行时间额度 |
| no_progress_awaits | 连续无实质进展的新 Await 次数 |
| progress_marker_json | 上次 Await 时的机器进展标记 |
| budget_state / exhausted_reason | active / exhausted 及具体原因 |

AgentRun/checkpoint 记录自己持有的活动时间 grant、已退还标记和当前来源批次的预算预留。计数更新与 owner 校验在事务中 CAS，重复恢复同批次不能重复收费，也不能免除新批次计数。

Attempt 更换不清零 ColumnRun 累计值。创建新的业务 ColumnRun 则获得新阶段额度，但必须经过已有 Task max_column_visits / recovery 限制；不能让同一恢复逻辑随意创建新 Run 绕过限制。

### 10.2 可 Review 的建议默认值

以下是建议值，不是当前代码已经配置的值，也不声称经过真实项目负载验证。

| 新 policy | 建议默认 | 说明 |
|---|---:|---|
| column_max_model_requests | 200 | 全部 continuation 与底层实际 provider 尝试累计 |
| column_max_tool_calls | 600 | 包含 read、write、await、complete；不允许改用 read 循环绕过 |
| column_max_agent_continuations | 20 | 初始 AgentRun 不计，恢复模型前预留 |
| column_max_active_seconds | 7200 | 阶段的活动执行累计，不包含正常外部等待 |
| column_max_no_progress_awaits | 3 | 达到第 3 次连续无进展新 Await 时停止循环 |

保留当前 per-AgentRun 默认值：100 iterations、300 tool calls、3600 wall seconds。其与累计上限取同时约束，不能用较大的累计值放宽单次限制。

这组值意图容纳正常有限修复，不是扩大任务上下文；超限意味着阶段应该拆小或进入明确监督。正式实现需将其写入 policy、文档及 workflow 发布快照，不能通过修改运行中全局默认值悄悄重置旧任务。

### 10.3 计费点与边界

这里的“额度”只指执行收敛计数，不是货币费用。

- 模型调用：在真正请求 provider 前原子预留 1。若 ProviderTurnRequester 内部会重试 HTTP/model 请求，每次真实尝试都经过预留入口；不能只统计外层 iteration。
- 工具批次：完整批次执行前预留数量；超限则整批不 dispatch，避免部分执行后无法处理剩余工具。
- 恢复：在创建/领取新的 continuation AgentRun 前预留 1；重复恢复同 handle 的相同 continuation 不重复记数。
- 当 remaining=0 时停止下一次派发，不能先执行再报超限。
- 记录已预留的工作即使遇到崩溃也不自动清零。已结算意图的纯 DB 投影恢复不再次预留工具额度。
- 外部 pending poll 不是新模型 continuation；受 poll interval 和 wait deadline 约束。需要区分观察外部任务与让模型不断重新决定等待。

### 10.4 活动时间的持久化

不能把 time.monotonic 的绝对值存入数据库后跨进程复用；UTC 与 monotonic 分别承担持久化时间和当前进程耗时。

推荐分段预留方式，避免崩溃使使用时间丢失：

1. AgentRun 开始前预留一个有限活动时间片，例如最多 30 秒、且不超过剩余额度和单 Run 剩余时间。
2. 将 grant 身份/额度记入 AgentRun 与 ColumnRun，同一事务扣减可用额度。
3. 当前进程用 monotonic 运行/续领，续领必须 owner 有效且尚有余额；每次 grant 都只能退还一次。
4. 正常结束或进入外部 Await，退还当前未使用部分；之前已使用部分不退还。
5. 崩溃后未退还的片段保守计为已用，单次最多多计一个片段；不能直接重新发全额。
6. 本地有耗时 command/provider 请求时，续领机制必须持续运行并更新 ExecutionControl deadline；未能续领应取消当前执行，不能让后台线程继续派发工具。
7. 外部 Await 期间不持有活动 grant；醒来只有 poll 所需的独立有界调用，以及实际启动 Agent continuation 时才重新预留。

这是拟定实现中复杂度较高的一项。若实施选择更简单的“每次 Run 预留整个 remaining wall 并在正常退出退还”，必须明确其崩溃可能耗尽整阶段预算的代价，不能作为无行为差异的小改动替换。

### 10.5 什么算进展

不接受模型文字声明。建议机器进展标记取以下持久化变化：

- 已登记工作产物的内容版本/hash 实际变化；
- 有业务身份的异步操作从 pending 到 terminal；
- Runtime 认可的失败重试链从 unresolved 到 resolved；
- 已发布验收规则产生新的有效验收事实。

以下不算实质进展：timer 到期、heartbeat、再次读取同内容、产生新 AgentRun/handle、模型说“正在处理”、无业务验证的普通 print/echo 成功。

普通任意 shell 是否改变业务状态未必可自动判断。因此本轮不设计万能语义进展判断器：可验证进展重置 no_progress_awaits；无法验证的变化不重置，但累计预算始终生效，避免错误的“进展”标记带来无限额度。

进展标记在成功保存新 AwaitHandle 的同一事务比较/更新。恢复同一 handle 不重复增加 no_progress_awaits。首次没有业务进展就新建 Await 计 1；同一外部操作保持 pending 的轮询不增加该计数。

### 10.6 超限后的状态

建议复用现有状态体系：

- Column/Attempt 记录结构化 `column_budget_exhausted` 或 `await_no_progress`，保留计数与最后进展位置。
- Task 进入现有可监督的 paused/waiting 状态，并设置明确 supervision_action；不得继续保持可自动重复派发的 active re-await。
- 未结算外部操作仍保持可查询归属，需要 cancel/reconcile 才能彻底释放相关运行资源。
- 创建一条按 Task + ColumnRun + reason 去重的监督事件/mailbox；不能每 tick 重复刷通知。
- supervision 自动 resume/retry 不重置相同 Run 的预算。若仅重复原任务而没有新计划/阶段输入，应继续阻止派发，受既有恢复上限约束。
- 新的明确任务拆分、workflow 修订或用户要求重开属于业务新工作，可创建新 Run，但要保留 exhausted lineage；不能把后台重试伪装成无限的新任务链。

## 11. 上下文与产品行为

本轮不重构整个记忆系统，但必须避免为正确性把完整执行历史塞进每次 prompt：

- 完整 operation/receipt/history 留在数据库，恢复和准入由代码查询。
- prompt 只放当前输入、需要继续的 checkpoint、未解决操作摘要、相关错误及最近必要证据。
- 操作 ID 用于后台审计，不要求普通用户或模型手工维护数据库引用。
- 不按任意页大小截断完成 ledger；投影应覆盖整个当前逻辑阶段，prompt 可以摘要化。
- UI 至少能区分：等待外部任务、需要核对结果、阶段预算耗尽、已知失败待修复、已完成。
- 文本“完成”不能覆盖 Task 的执行状态；现有事件流/API 字段应传递错误码和可执行的下一步。
- 对应业务结果例子：R1 文件真写两次才 done；R3 DB 恢复后聊天继续；R5 外部成功后阶段可继续；R6 停止循环并说明建议拆分的位置。

这些变化优先通过现有 task detail、runtime timeline、conversation 消息表达，不另做复杂管理面板。

## 12. 历史数据库与在途任务迁移

### 12.1 迁移原则

本轮 Review 阶段不执行迁移。实施前先在数据库副本完成演练；正式切换时暂停调度并结束/冻结活跃 worker，避免旧代码与新 schema 语义并行写入。

推荐增量迁移，新增字段对旧行允许 NULL；不删除旧 invocation、receipt、日志或 finished Task，不重写旧 execution_key。初始化迁移必须可重复运行，并有明确版本标记。

不执行以下自动修正：

- 把历史 waiting 一律改 completed；
- 把同参数 invocation 全部绑定到一个 receipt；
- 把历史 started 无结果一律改 failed 后重新执行；
- 把历史 done 一律回退或批量重跑；
- 以“测试通过”为理由修改真实失败记录。

### 12.2 历史记录分组

| 旧数据情况 | 推荐处理 | 是否自动恢复 |
|---|---|---|
| 已结束 Task、完整历史 | 保留只读历史，按 legacy 投影展示 | 不重跑 |
| 活跃 Await 的 checkpoint 有唯一 execution_key，匹配 receipt 的 scope/参数可靠 | 生成 legacy operation 映射，填关联 | 核对后可 |
| 活跃 AgentRun 的旧 key 能从持久化来源唯一确定，且全部效果已结算 | 生成迁移映射并保持原 key | 可 |
| 同参重复调用已被旧 key 合并，无法判断哪些是新意图 | 标记 migration hold，列出冲突 | 否 |
| started 且无结果 | unknown，查询 adapter 或人工核对 | 不盲重放 |
| completed receipt 缺 invocation | 确认来源后补投影，不重跑 handler | 可 |
| awaiting invocation，receipt 已 completed | 建立确定关联后按 completed 投影 | 可 |
| 旧 failed receipt 曾原地重试，历史结果已丢失 | 标记历史事实不完整，不伪造旧状态 | 活跃任务逐项核对 |
| durable queued 且没有执行历史 | 新 dispatcher 自然补领 | 可 |
| running Conversation | 停旧 worker 后按租约/事实恢复，不直接改 queued 重放 | 视效果结算 |

所谓“唯一确定”要求显式旧 execution_key、持久化 source 或可靠 checkpoint 能形成一对一关系；仅参数相同、时间接近或模型摘要相似都不足以回填。

历史预算能从完整 AgentRun/调用记录保守汇总时回填。无法汇总的活跃 ColumnRun 进入 migration hold，不用 0 初始化来宣称恢复有界。已终结历史无需为新预算强行补精确耗时。

### 12.3 指定历史阻塞 Task 的单独处置

对象：

- project_id：`prj_d3b99300e1624232a9c3462e5ea3c571`
- task_id：`tsk_cfc85c0496834a2baae81d9b3eb055f0`

实施时先生成独立核对记录，读取其 Task、Run/Attempt、AgentRun、invocation、receipt、Await、terminal artifact 和关联 log。确认：

1. 最后一个可信状态与 owner；
2. 是否存在已生效但未记录的 command/file/control 副作用；
3. 是否有 Await 成功而 ledger 旧化、key 碰撞或完成豁免证据；
4. 项目工作目录当前内容与已登记 artifact 是否一致；
5. 可以从哪个已确定 checkpoint 恢复，需要补记录还是需要新的修复 Task。

本方案不预设这个 Task 一定命中六项中的某项。只有具体核对完成后，才能给出“恢复原 Task”“创建修复后继 Task”或“先核对未知副作用”的具体执行动作。不能直接把状态改 active 作为修复验收。

### 12.4 切换与回滚

1. 记录当前代码版本/工作区差异；保持用户现有未提交改动。
2. 通过 SQLite backup API 或停写后的完整备份备份数据库，避免只复制主 db 漏掉 WAL。
3. 在副本迁移并跑一致性查询：孤立 operation、跨 scope 关联、重复 source、重复 terminal、无 handle 的 awaiting、负预算等必须为零或有列明的 legacy hold。
4. 暂停调度并冻结旧执行，执行正式迁移，生成可恢复/hold 清单。
5. 启用新 Runtime，先观察 queued 补领和确定的 Await 恢复，再执行真实交付 smoke。
6. 失败时停止新调度。旧二进制不能安全解释新 operation/unknown/budget 语义，禁止直接回滚代码并继续写新数据库。
7. 若新系统尚未产生真实外部效果，可恢复备份与匹配旧代码；若已有新效果，需先核对外部世界与数据库差异，优先向前修复。恢复 DB 备份不会撤销已经执行的命令。

## 13. 修改包与文件级工作清单

所有修改包都以本方案 Review 后为起点；当前没有开始生产代码实施。

| 包 | 内容 | 主要文件/拟新增文件 | 依赖 | 完成门槛 |
|---|---|---|---|---|
| M0 | Schema、操作 repository 基础、Owner 类型、claim_token | repositories/schema_repository.py；拟 execution_repository.py；execution_control.py；store.py；agent_models.py | 无 | 幂等迁移、唯一键、owner/事务契约 |
| M1 | 操作意图、批次恢复、receipt/Await 关联 | agent_runner.py；agent_tool_execution.py；agent_run_preparation.py；capabilities.py；runtime.py；session_replay.py | M0 | R1 红转绿，关键崩溃窗口不漏/重执行 |
| M2 | 当前事实 ledger、受约束 retry、completion snapshot | execution_ledger.py；completion_protocol.py；services/completion_admission.py；runtime.py；domain.py/工作流 contract | M1 | R2/R5 红转绿，正常构建修复可交付 |
| M3 | DB-only 提交 guard、handler 盘点、queued 补扫 | capabilities.py；services/recovery_manager.py；repositories/planning_repository.py；conversation.py；store.py | M0；完整集成依赖 M1 | R3/R4 红转绿，无重复消费者或过期控制写入 |
| M4 | 累计预算、持久化进展、超限监督 | policy.py；agent_runner.py；agent_provider.py；runtime.py；services/scheduler.py；services/recovery_manager.py | M1/M2 | R6 红转绿，有限 Await 正常交付 |
| M5 | 旧数据演练、内置 loop、产品状态、全链路回归 | loops 中相关交付配置；api.py；app/web/static/pages/tasks.js；tests；迁移核对脚本 | M1–M4 | 迁移与交付门槛全部满足 |

开发顺序建议 M0 → M1 → M2 → M3 → M4 → M5。M3 中 queued 补扫可先独立完成，但不能将它的通过解释为所有 P0 已关闭。

每个包提交必须附不变量、主要事务边界和回归断言，不写泛化的“优化稳定性”。不为本次顺便重构无关前端、provider API 或成本配置。

## 14. 回归测试设计

现有 `scripts/review_p0_runtime_2026_09_09.py` 保留为故障证据，不通过删掉输出、改变期望或只检查退出码来“修绿”。

实施先将这些场景转成有断言的 pytest 用例，随后才改对应生产路径。现有测试文件可补充；只有跨模块恢复测试需要新文件。下面名称是验收 ID，不强制每行对应一个新 Python 文件。

### 14.1 六项缺陷与正向配对

| 验收 ID | Bug | 输入/注入 | 必须断言 |
|---|---|---|---|
| I01 | R1 | timer 前后同参追加 | 文件 xx；两个意图和 receipt；Task done |
| I02 | R1 | 同消息两次同参调用 | 都执行；source_index 不同 |
| I03 | R1 | completed 后、invocation 前崩溃 | 重启恢复不再追加；补回模型工具响应 |
| I04 | R1 | 批次持久化后未领取 | 继续原批次；不重新生成新意图 |
| I05 | R1 | started 未结算、owner 失效 | unknown/hold；不重跑；不 done |
| I06 | R1 | 两个 worker 竞争同一来源 | 一个合法启动；唯一约束无重复意图 |
| C01 | R2 | 失败 deliver + 成功 print + 自报修复 | 成功准入拒绝；失败保留 |
| C02 | R2 | 修文件后重新执行已声明可重试的原构建命令 | 新 receipt 成功；retry 链结算；正常完成 |
| C03 | R2 | 两个独立同参失败后一次成功 | 不清除两个失败；歧义有结构化结果 |
| C04 | R2 | 非零退出且副作用不确定 | 不自动重试；不能被 noop 清除 |
| C05 | R2 | 失败/rework outcome | 真实失败保留，合法 transition，不标成功交付 |
| A01 | R5 | async awaiting → poll success → complete | 原 receipt completed；Task done；只启动一次外部任务 |
| A02 | R5 | async terminal failure | receipt failed；准入不能成功；无 None.get |
| A03 | R5 | 同一 poll/事件重复或反序到达 | 终态不可反转；冲突明确记录 |
| A04 | R5 | receipt 结算后、continuation 前崩溃 | 一次合法 continuation；不重复外部启动 |
| A05 | R5 | pending/unknown/no-error 的各种结果 | 分别结构化拒绝；无 AttributeError |
| Q01 | R3 | claim 一次 OperationalError | DB 恢复后无需重启处理旧/新消息，保持顺序 |
| Q02 | R3 | DB 创建后未入内存 | tick 自动发现 |
| Q03 | R3 | claim 提交后调用者收到异常 | 重新查询，不重复启动 running |
| Q04 | R3 | 多页 queued、多项目、两个 Gateway | 无饥饿；同项目串行；无重复合法领取 |
| F01 | R4 | receipt 前置检查后强制接管 | 旧 task.resume 零状态/事件/mailbox 写入 |
| F02 | R4 | handler 内部更新后提交前抛异常 | DB 业务与 receipt 一起回滚 |
| F03 | R4 | 相同 session_owner、不同 claim_token | 旧 token 的续租/结算/控制均拒绝 |
| F04 | R4 | 当前 owner + 目标 Task 版本冲突 | 目标 CAS 失败；无成功 receipt |
| F05 | R4 | 合法控制、规划、记忆类别 | 正常成功；guard 不导致死锁或错误禁止 |
| B01 | R6 | 极小累计限额 + 连续 timer Await | 精确边界前停止派发；paused/监督；非无限 active |
| B02 | R6 | 同一外部任务 pending 多次 poll | 不新建 AgentRun；不增加 re-await 无进展次数 |
| B03 | R6 | 崩溃恢复、换 Attempt/owner | 累计计数不归零；已预留批次不重复计费 |
| B04 | R6 | 正常等待后有产物变化/成功修复 | 无进展计数合理重置；累计总量不重置 |
| B05 | R6 | 只有 print/heartbeat/改 summary | 不伪造业务进展 |
| B06 | R6 | 超限后监督反复 resume | 不恢复完整额度；不产生无限通知/任务链 |

### 14.2 真并发、故障窗口与测试隔离

- 小多数逻辑可用确定性 adapter 和 fake clock，避免等待真实 timer。
- R3/R4 的核心竞争至少补一组两个独立 V1Store/SQLite 连接、用线程 barrier 协调的测试；不能只 mock assert_owner 返回异常来证明事务正确。
- R1/R5 的恢复至少补一组子进程在指定提交点退出，再由新 Runtime 打开同一临时 DB 的测试；普通函数异常不能完全代表进程消失。
- 子进程仅执行临时目录中可计数的无害文件追加；不调用真实交付命令。
- Await adapter 记录启动次数、poll 次数和业务 terminal 状态，不能只断言 Task.status。
- DB 查询验证事件、mailbox、projection 与业务状态一致；不只测 Python 返回值。
- 测试使用临时 DB 和工作目录，历史 data 保持只读。真实项目 smoke 单独记录执行范围。

### 14.3 迁移验收

| ID | 场景 | 结果 |
|---|---|---|
| G01 | 空库创建、旧库升级、重复升级 | schema 正确，重复执行无损 |
| G02 | 旧 Await 明确 execution_key | 正确关联；不重放 |
| G03 | 同参旧 key 冲突/历史不完整 | 生成具体 hold 清单，不猜测回填 |
| G04 | 旧 completed + awaiting invocation | 新投影反映完成，历史仍保留 |
| G05 | 旧预算不完整 | 有保守汇总或 hold，不能默认为无限/全新额度 |
| G06 | 混合终态/活跃 Task | 历史终态不批量改变，活跃项分组恢复 |

### 14.4 全链路交付门槛

最后在隔离的新项目完成以下用户可见流程，使用真实 HTTP/API 路径，必要时补 Web 手动验收：

1. Conversation 讨论需求，保存并发布合法 workflow/plan，创建可执行 Tasks。
2. 执行一个会失败的本地构建任务，模型修复文件后按允许的原操作重试，最终产物可验证。
3. 执行 Await 前后都需要真实副作用的任务，确认两个效果都发生。
4. 在进行中重启服务，queued 消息继续处理，已完成效果不重复，明确可恢复的 Await 继续。
5. 下游 Task 消费上游已登记交付物并完成一个小项目。
6. 检查 Task terminal、artifact、事件与 Conversation 交付说明一致。

确定性故障回归必须通过；真实模型 smoke 用于验证提示词与工具协议可用，不替代故障回归，也不以模型偶然没有触发问题作为关闭依据。

## 15. 事件、错误码与排障信息

建议复用现有 event/mailbox 机制，新增明确事件，不建立另一套日志系统。

| 情形 | 错误码/事件建议 | 最少字段 |
|---|---|---|
| 来源位置与参数冲突 | operation_source_conflict | operation、source、capability、scope |
| 外部效果不明 | effect_outcome_unknown | receipt、原 owner、最后可信 checkpoint |
| 不支持的模型修复声明 | unsupported_failure_resolution | 原失败摘要、可行重试/rework 指引 |
| Await 尚未结算 | pending_execution | handle、operation、下次观察时间 |
| 结算矛盾 | receipt_settlement_conflict | 预期/实际 state_version、原/新 terminal |
| stale 控制提交 | execution_ownership_lost | 发起 scope、token/generation 摘要、目标对象 |
| queued 再发现 | conversation.queue_rediscovered | job、project、来源 tick；避免每次重复日志 |
| 阶段超限 | column_budget_exhausted | used/limit、原因、当前 checkpoint |
| 无进展 Await | await_no_progress | 次数、最近进展标记、handle |
| 历史无法映射 | execution_migration_hold | 冲突记录、待核对项 |

不把完整大 payload、整个上下文或所有历史证据复制到每条事件。事件引用现有 payload/artifact 存储，UI 获取详细记录时再展开。

## 16. 风险与明确限制

| 风险 | 应对 |
|---|---|
| 操作表与调用历史又形成双真相 | operation 只保存身份；receipt 负责可变结算；ledger 为只读投影 |
| 新身份让模型重复已完成业务步骤 | 持久化原响应并先恢复；新模型意图仍可能业务重复，需要 workflow 输入/验收约束，不靠参数全局去重 |
| 收紧失败修复阻碍正常开发 | 必须配套 C02 与实际构建修复 smoke；内置 loop 声明可重试操作 |
| DB guard 引发长锁 | 明确 commit_mode；文件准备和外部命令在事务外；真正 DB 修改用短事务 |
| queued 补扫造成重复工作 | 数据库 claim CAS + claim_token；内存仅加速 |
| 超限过于激进 | 默认可 Review；等待不计活动执行；明确可见原因，不静默失败 |
| 迁移无法还原历史合并意图 | 保留证据并 hold，不从参数相似性伪造过去 |
| shell crash window 无法 exactly-once | unknown + adapter 核对/监督，不承诺不存在的原子外部事务 |
| 方案扩张成通用框架 | 限定一个 execution repository、现有表增量、现有队列和状态；替代修复验证语言留待后续 |

## 17. Review 后的执行与关闭规则

建议 Review 按以下顺序进行：

1. 第 2 节 D1–D7：先确认 v1 行为取舍，特别是替代修复限制、unknown 处置和预算默认值。
2. 第 4 节：确认操作身份、receipt 状态与恢复窗口是否覆盖预期用法。
3. 第 7–10 节：确认重试规则、控制事务、queued 补领与预算不会阻碍正常交付。
4. 第 12 节：确认历史数据和指定阻塞 Task 的处理原则。
5. 第 14 节：确认这些断言足以成为本轮关闭门槛。

实施严格遵循：**方案 Review → 按修改包落地 → 对应故障回归 → 历史副本迁移演练 → 全链路交付验收 → 更新 Bug 状态。**

完成一个修改包不等于六项 P0 全部关闭。只有每个 Bug 的负向复现已被阻止、正向流程可交付、相关故障窗口及迁移验收通过后，才能在登记簿填“已关闭”。

本轮交付到方案为止；生产代码、真实数据库与历史 Task 均未因这份方案发生修改。
