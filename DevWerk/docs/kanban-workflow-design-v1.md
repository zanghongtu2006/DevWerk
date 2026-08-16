# DevWerk Kanban Workflow — Version 1 核心设计

> 实现规范提示：本文定义 Kanban、状态机、等待和恢复产品决策。声明式 Column schema、通用 executor、Capability Registry 与无源码模板约束以 [`generic-conversation-agent-and-declarative-column-runtime.md`](./generic-conversation-agent-and-declarative-column-runtime.md) 为第一事实。

**文档状态**：Version 1 已确认核心设计  
**最后更新**：2026-07-17  
**适用范围**：DevWerk 核心服务  
**关联文档**：[Conversation Agent Version 1 核心设计](./conversation-agent-design-v1.md)

## 1. 文档效力

本文定义 DevWerk Version 1 的 Kanban、Workflow、Column、Task、Column Run、临时 agent、状态机、长时间等待和异常恢复设计。

本文是目标设计。实现与测试必须满足本文定义的显式 schema、状态机、终态和有界恢复契约。

## 2. 核心定义

> Workflow 以 Column 为基础单元。Task 沿 Column 构成的显式状态机运行；每次进入非终态 Column 产生独立 Column Run。Column 统一声明处理逻辑、执行方式、上下文、能力、输入输出契约和 transition，直到 Task 明确进入 `done` 或 `failed` sentinel。

目标：

- Column 是 workflow 的统一抽象单元。
- 状态机不根据列名猜测行为。
- 简单步骤由 deterministic runtime 完成。
- 复杂工作由即生即死的 ephemeral sub-agent 完成。
- 每个 agent 只获取当前阶段必需 context。
- 所有成功、失败、等待、重试和恢复路径显式声明。
- 不允许隐式成功、静默终止或无界 waiting。
- 服务重启后可以从持久状态恢复调度。
- Task 终态和异常必须被 conversation-agent 观察。

## 3. 与 Conversation Agent 的边界

conversation-agent：

- 与用户理解需求。
- 建设和发布 workflow revision。
- 建设 backlog 和 Formal Task。
- 判断 Task readiness、派发和并发。
- 监督 Task、Run、Artifact 和异常。
- 在失败时重启、改派、迁移或向用户汇报。

Kanban Workflow Runtime：

- 验证 workflow 定义。
- 根据固定 revision 执行 Task。
- 创建和管理 Column Run。
- 选择允许的执行方式。
- 验证 input/output contract。
- 执行显式 transition。
- 管理 retry、waiting、lease 和 recovery。
- 产生 event、artifact 和 terminal notification。

Runtime 不重新解释用户项目目标，也不隐式改写 workflow。

## 4. Workflow Definition 与 Revision

Version 1 每个 Project 有一个正式 workflow，但支持不可变 revision。

```yaml
workflow:
  id:
  project_id:
  name:
  description:
  active_revision_id:

workflow_revision:
  id:
  workflow_id:
  revision_no:
  start_column_id:
  success_terminal_key: done
  failure_terminal_key: failed
  created_by_conversation_agent_run_id:
  published_at:
  definition_hash:
```

规则：

- Version 1 只持久化 published immutable revision；Conversation Agent 的草拟内容保存在 conversation/backlog working data，不创建 draft revision。
- Published revision 不可原地修改，也不使用 retired 状态；未激活的旧 revision永久保留用于运行、恢复、审计和解释。
- `v1_workflows.active_revision_id` 是 active revision 的唯一事实源，Project 不保存副本。
- 新 Task 固定当前 revision。
- 运行中 Task 不因新 revision 发布而自动变化。
- Task 迁移必须由 conversation-agent 通过 Intervention Run 显式执行。
- 未来多 workflow 扩展不得要求重写 Task/Column Run 核心模型。
- `done/failed` 是没有 executor、不会创建 Column Run 的 terminal sentinel。

## 5. Column Definition 与 Column Run 分离

### Column Definition

Workflow revision 内的不可变定义，描述一个阶段应该如何工作。

### Column Run

某个 Task 实际进入非终态 Column 后产生的一次 visit 实例，记录 `visit_no`、输入快照、聚合状态、最终 outcome 和工件。

一个 Column Definition 可以被多个 Task 使用；每次进入都创建独立 Column Run。

### Column Attempt

Column Run 内的一次不可变执行尝试，记录 executor、lease、checkpoint、AwaitHandle、receipt 与结果。Retry 在同一 Column Run 下创建递增的 Column Attempt；transition 再次进入同一 Column 才创建新的 Column Run。

## 6. Column 统一抽象

```yaml
column:
  key:
  name:
  instruction:
  executor:
    kind: capability_sequence | agent
    capabilities: []
    steps: []
    completed_outcome:
    outcome_from:
    max_iterations:
    max_tool_calls:
  context:
    include_task: true
    include_project: true
    upstream_outputs: []
    artifact_globs: []
  input_contract:
  output_contract:
  transitions: []
  runtime_outcomes:
    input_missing:
    execution_failed:
    interrupted:
    retry_exhausted:
    max_visits_exceeded:
  retry:
  wait_policy:
  max_visits:
  terminal: null | success | failure
  metadata: {}
```

### Instruction 与 Metadata

- `instruction` 是 Conversation Agent 在对话中生成并持久化到 Workflow revision 的阶段工作指令。
- `metadata` 保存机器可读的展示与扩展信息，不承担运行时路由职责。
- capability、contract、transition、retry、wait_policy 与 context 均使用各自的结构化字段。
- `runtime_outcomes` 把五类基础设施条件显式映射到当前 Column 已声明的 outcome。
- Executor 是按 kind 判别的联合：Agent 使用 capabilities/budget；Sequence 使用 steps，并在 completed outcome/outcome reference 中二选一。不同 kind 的字段不能混用。

Instruction 不替代结构化 contract、transition 和 policy。

### Column Flow

Column 可以具有有限内部流程：

```text
validate_input
→ inspect_context
→ execute
→ validate_output
→ produce_outcome
```

Column flow 不应发展成第二套复杂 workflow。如果内部出现大量分支、长期循环、多个独立交付和不同 agent 角色，应拆为多个 Column。

## 7. Column Run 模型

```yaml
column_run:
  id:
  project_id:
  task_id:
  workflow_revision_id:
  column_id:
  visit_no:
  status:
  health:
  input_snapshot_ref:
  output_artifact_ref:
  outcome:
  error_code:
  error_summary:
  started_at:
  finished_at:
  state_version:
```

```yaml
column_attempt:
  id:
  project_id:
  column_run_id:
  attempt_no:
  status:
  executor_kind:
  executor_id:
  lease_owner:
  lease_expires_at:
  heartbeat_at:
  checkpoint_ref:
  wait_handle_id:
  started_at:
  finished_at:
  state_version:
```

Retry 只创建新的 Column Attempt，不覆盖历史执行证据。

## 8. Run Status 与 Business Outcome 分离

统一 Column Run status：

```text
pending
running
waiting
succeeded
failed
interrupted
```

Column Attempt 使用同一前向状态并额外允许 `cancelled`。Run 在活跃阶段镜像当前 Attempt 的 `pending/running/waiting`，在 contract 通过或重试/恢复决定耗尽后聚合为 `succeeded/failed/interrupted`。

合法 edge、trigger 与 CAS guard 仅以通用设计文档第 9.1 节 Canonical State Transition Tables 为准；本节只解释各状态含义。

Business outcome 表示下一步业务路由，例如：

```text
approve
continue
rework
retry
reject
escalate
complete
```

状态描述运行健康度，outcome 决定 transition：

```text
Current Column + Validated Outcome
→ Explicit Transition
→ Next Column or Terminal Sentinel
```

禁止用 `succeeded` 自动推断唯一下一列，也禁止从输出文本关键词猜 outcome。

## 9. Column Run 与 Attempt 状态定义

### `pending`

Run 已创建但尚未执行，或 Attempt 尚未取得 lease，必须有明确 `pending_reason`：

- waiting_dispatch
- waiting_input
- waiting_dependency
- waiting_resource

Task 同时持久化 `pending_deadline_at`，Column Run 持久化 `claim_deadline_at`。条件始终未满足或满足后仍未领取，都不能无限停留；任一期限到达时由 Runtime Reconciler 将 Task 明确置为 `failed`，写 failure artifact、terminal event 与 Conversation Agent mailbox。

### `running`

Attempt 执行器已取得 lease，必须记录：

- owner
- executor_kind
- attempt
- started_at
- lease_expires_at
- heartbeat_at

### `waiting`

当前 Attempt 已释放执行资源并等待明确条件。必须有判别型 AwaitHandle、resume condition、next check 或关联 event，以及 hard deadline。

### `succeeded`

当前 Column Run 已完成，output contract 与 artifact/evidence policy 通过，并产生合法 outcome。它不等于 Task `done`。

### `failed`

当前 Attempt 无法成功完成。Runtime 先执行 retry/recovery policy；预算允许时在同一 Run 创建新 Attempt，耗尽时令 Run failed，并通过 `runtime_outcomes.retry_exhausted` 选择已声明 outcome。只有 transition 进入 `failed` sentinel 时 Task 才成为 `failed`。

### `interrupted`

服务重启、executor process 崩溃、lease 丢失或执行器消失造成运行中断时，先将 active Attempt 置为 `interrupted`。证据允许 retry 时 Run 保持 active 并创建新 Attempt；恢复预算耗尽时 Run 置为 `interrupted`，通过 `runtime_outcomes.interrupted` 路由。

## 10. 两种显式 Executor

Conversation Agent 在发布 Workflow revision 时，为每个非终态 Column 明确选择一种 executor。Runtime 只解释已经冻结的选择。

### Capability Sequence Executor

`kind=capability_sequence` 不启动 LLM Agent，按声明的 capability steps 与参数引用执行。全部 step `completed` 后使用固定 `completed_outcome` 或受限 `outcome_from`；Capability `failed` 只进入 Attempt retry 和 `runtime_outcomes`，不直接产生业务 outcome。所有 step 都通过统一 Capability Registry 完成；Version 1 release 内置 project file、command、数据转换和 contract validation，其他 adapter 作为兼容扩展注册。

### Agent Executor

`kind=agent` 启动即生即死型 Column Agent，并冻结 instruction、context、capability allowlist、预算和 completion contract。

Version 1 只接受 `capability_sequence` 与 `agent`。Conversation Agent 在发布 revision 前完成选择，Runtime 不再进行语义判定。

## 11. Agent 判定边界

如果执行过程需要模型：

- 读取 prompt
- 选择工具
- 观察工具结果
- 继续推理
- 决定下一动作

它本质上就是 agent run，即使实现没有把它命名为 sub-agent。

“Column 自己完成”只指：

- 完全确定性的 Column Runtime；或
- 由 Agent Executor 创建一个轻量 ephemeral agent。

## 12. Ephemeral Agent 组装

```text
Agent Runtime 基础约束
+ Conversation-agent 提供的 Project Context
+ Column Instruction
+ Task Brief 与 Input
+ Input/Output Contract
+ Capability Allowlist
+ Relevant Artifacts
+ Current Attempt Context
= Ephemeral Column Agent
```

agent：

- 不继承完整 Project 对话历史。
- 不继承其他 agent 原始对话。
- 不拥有 Project 治理权限。
- 不修改 workflow 或其他 task。
- 只获取完成当前 Column Run 必需 context。
- 完成后销毁。
- 重试创建新 Column Attempt，不复用污染上下文。

## 13. Context Pack 契约

本设计只定义 context 输入边界，不重新设计记忆系统。

### Conversation-agent Context

- Project 目标
- 当前约束
- 已确认决策
- 当前 Task 相关项目事实
- 当前风险
- Task 派发原因

不是完整 conversation history。

### Column Context

- 阶段目标
- Column instruction/metadata
- 输入、输出、验收契约
- capability allowlist
- allowed outcomes
- retry/failure/wait policy

### Task Context

- objective
- scope/non-scope
- inputs
- deliverables
- acceptance criteria
- dependencies
- task brief/input
- 上游 artifact reference

### Run Context

- Column Run ID
- attempt
- 上次失败摘要
- 当前相关 artifact
- workspace snapshot reference
- 当前资源、预算和时间限制

Context Pack 保存引用、hash、预算和编译摘要；不无界拼接历史。

Context Compiler 通过 Project-scoped Artifact Repository 解析声明引用：metadata 使用 `artifact.inspect`，正文使用带类型/大小/分页上限的 `artifact.read`。Project file capability 永远不能直接读取 `internal_artifact_root`。

## 14. Input Contract

Task 进入 Column 后先验证输入。

```text
Input Contract 满足
→ 允许执行

Input Contract 不满足但可等待
→ pending/waiting + 明确原因
→ 建立依赖或 AwaitHandle

Input Contract 无法满足
→ Column Run failed
→ `runtime_outcomes.input_missing` 对应 transition
```

输入不满足时不能启动 agent 让其猜测缺失信息。

Task 首次 dispatch 与从 waiting/recovering 恢复前，Repository 在短事务内使用 `state_version` 检查 readiness、声明的 dependency、control state 和活跃 Attempt。条件未满足时不取得 Attempt lease；首次 dispatch 前只创建或更新 Scheduling Entry。Conversation Agent 决定并发和优先级，Repository 防止同一 Task 被重复执行。

## 15. Output Contract

agent/runtime 返回后必须验证：

- 必须字段
- artifact 类型和数量
- 文件或外部结果
- evidence
- quality/acceptance rule
- allowed outcome
- tool/API receipt

```text
Output Contract 通过
→ Column Run succeeded + outcome

Output Contract 不通过且可重试
→ 新 attempt

Output Contract 不通过且预算耗尽
→ failed + `runtime_outcomes.retry_exhausted` 对应 transition
```

允许有限、明确的协议重试；禁止无限修复 LLM 输出，禁止 output 不合法仍推进下一列。

## 16. Task 状态

```text
pending
running
waiting
recovering
done
failed
```

只有 `done` 和 `failed` 是终态。

Task execution/control state 的合法 edge、trigger 与 CAS guard 仅引用通用设计文档第 9.1 节，不在本文定义第二套转移图。

Task 另有独立 control state：`active -> pause_requested -> paused -> active`。Pause 请求必须带创建后不可延期的 `pause_deadline_at`。Pause 先阻止新领取；运行中的 Attempt 在 capability checkpoint 创建关联 `task.resumed` 的 event AwaitHandle并释放 lease，既有 AwaitHandle 则冻结。Deadline 前显式 resume 会原子 settlement control handle 并重新入队；deadline 到达仍为 paused 时，Supervisor 将 Task 置为 `failed` 并通知 Conversation Agent。Terminal Task 的 control state 规范化为 `active`。

### `done`

必须通过显式 success terminal transition 到达，并满足：

- 当前 Column Run succeeded
- output contract 通过
- 必需 artifact 存在
- workflow success terminal sentinel 明确
- terminal state/event 原子持久化

Conversation Agent 在终态后异步观察和汇报。需要项目级复核的 workflow 必须在 `done` 前声明独立 review Column，Runtime 不依赖隐藏的同步验收门。

### `failed`

必须通过显式 failure terminal sentinel 到达，并记录：

- 最后失败 Column/Run
- failure reason/code
- attempts
- 已产生 artifact
- 是否可恢复
- 推荐恢复方式

取消原子进入 `failed`，并额外记录 `failure_code=cancelled`、`task.cancelled` event、failure artifact 与 Project mailbox 通知。

`task.retry` 只在 Task 非终态时为当前 Run 创建新 Attempt。可恢复的 `failed` Task 使用 `task.reopen` 保留原 Task ID、Run/Attempt、产物、失败事件和逻辑 Agent session，并创建新的 pending Column Run；`done` 不可 reopen。需要独立交付世代时使用 `task.rerun` 创建带 `rerun_of_task_id` 的 successor Task。

### Revision 迁移

迁移只允许在 `control_state=paused` 且没有活跃 Task/Attempt lease 时执行。请求必须声明目标 revision、目标 Column、context/artifact 继承策略和期望 `state_version`。Runtime 重新验证目标 input contract，以 CAS transaction 切换 revision 与 Column、创建新的 Column Run 与首个 Attempt 并写审计事件；验证或 CAS 未通过时保持原 revision 和状态。

“没有下一列”不能表示 `done`。它是 workflow definition error 或 runtime anomaly。

## 17. 状态流转图

```mermaid
flowchart TD
    E["Task 进入 Column"] --> I["验证 Input Contract"]

    I -->|不满足| W["pending / waiting<br/>记录原因并通知 Conversation Agent"]
    I -->|满足| D["Runtime 读取冻结的 Executor"]

    D --> R["Capability Sequence Executor"]
    D --> A["Ephemeral Agent Executor"]

    R --> O["产生 Output + Outcome"]
    A --> O

    O --> V["验证 Output Contract"]
    V -->|失败，可重试| RT["创建新 Attempt"]
    RT --> D

    V -->|失败，不可恢复| F["Column Run failed"]
    V -->|通过| S["Column Run succeeded"]

    S --> SM["状态机根据 Column + Outcome<br/>选择显式 Transition"]
    F --> FP["Retry / Recovery / Escalate / Failure Transition"]

    SM --> N["Next Column"]
    SM --> TD["Task done"]
    FP --> N
    FP --> TF["Task failed"]

    TD --> CA["通知并唤醒 Conversation Agent"]
    TF --> CA
    W --> CA
```

## 18. Transition 模型

```yaml
transition:
  outcome:
  target:
```

规则：

- 每个 `(Column key, outcome)` 必须有且只有一个 target。
- Version 1 不支持 transition guard 或 priority；业务分支由 executor 返回不同的枚举 outcome。
- 每个循环由 Column `max_visits` 提供确定性出口。
- terminal sentinel 没有隐藏后继，也不创建 Column Run。
- Runtime 只能执行当前 revision 已声明 transition。

## 19. Retry、Recovery 与 Failure

Column Attempt 失败后的顺序：

1. 判断 retry policy 和 attempt budget；允许时在同一 Run 创建新 Attempt。
2. 预算耗尽时将 Column Run 标记 failed，并产生 `runtime_outcomes.retry_exhausted` 声明的 outcome；不可重试执行失败使用 `execution_failed`。
3. 根据唯一 transition 进入 recovery Column 或 `failed` sentinel。
4. 判断是否需要 conversation-agent Intervention Run。
5. 写 failure artifact/event 并通知 conversation-agent。

Column Run `failed` 不自动令 Task `failed`。只有 failure terminal 使 Task 终止。

## 20. Waiting 设计目标

DevWerk 不能保证 LLM 或第三方服务成功，但必须保证：

> 不存在无限期、无解释、无 owner、无下一次检查、无退出策略的 waiting。

长时间等待可能是正常状态，不能用一个固定秒数判断所有能力。

### 长等待五个核心组件

1. **AwaitHandle**：持久化异步操作身份、外部 job、状态、期限和恢复信息。
2. **WaitPolicy**：以判别联合定义 poll、event 或 timer 恢复方式、deadline、取消和 timeout outcome。
3. **Progress Evidence**：可选能力进度统一为 alive、value、message、last activity 和 provider status。
4. **Runtime Reconciler**：在定时巡检和服务重启后恢复 orphan pending/running/waiting。
5. **Workflow Liveness Validator**：发布 revision 前验证所有 waiting、循环、失败和中断路径最终能够到达 `done` 或 `failed`。

五个组件共同提供 eventual decision。只实现其中某一个，例如固定 timeout 或异步 job token，都不足以保证 workflow 自驱完成。

## 21. Running 与 Waiting 分离

### Running

系统仍在主动执行并存在活动证据：

- LLM stream/token
- agent tool call
- shell process
- 文件持续生成
- heartbeat

### Waiting

当前不需要保留推理 agent，正在等待 poll 结果、相关 event 或 timer 到期。

长时间 active computing 保持 `running`；持久异步等待使用 `waiting`。

## 22. Wait Policy 判别联合

- `kind=poll`：查询 capability、arguments、interval、resume condition、soft/stale/hard deadline、success/timeout outcome required；cancel/cleanup capability optional。
- `kind=event`：event type、correlation key、hard deadline、success/timeout outcome required；用于用户输入和 Task dependency 等内部事件。
- `kind=timer`：`resume_at` 或 `delay_seconds` 二者之一，以及 hard deadline 与 success/timeout outcome required。

每种策略均冻结到 AwaitHandle；Version 1 release 不定义 provider 专用 waiting kind。

## 23. Durable AwaitHandle

```yaml
await_handle:
  id:
  project_id:
  task_id:
  column_run_id:
  column_attempt_id:
  kind: poll | event | timer
  idempotency_key:
  status:
  health:
  progress_value:
  progress_message:
  created_at:
  updated_at:
  last_progress_at:
  next_check_at:
  soft_deadline:
  stale_deadline:
  hard_deadline:
  policy_snapshot:
  checkpoint_ref:
  secret_reference:
  state_version:
```

AwaitHandle 必须持久化，不能只存在 executor 内存。

Handle 在外部任务终态、取消或最终过期并完成 reconciliation 后清理；agent 退出时不销毁。

## 24. Capability Result 与恢复

所有 capability 返回统一判别结果：

```yaml
capability_result:
  status: completed | awaiting | failed
  output:
  error:
  await_handle_draft:
  checkpoint:
```

`awaiting` 要求 `await_handle_draft` 与 `checkpoint`。Runtime 在同一短事务保存 Handle、Attempt checkpoint、execution receipt 与 sequence step cursor，并将 Attempt/Run/Task 置为 waiting。

```text
Agent 或 Sequence Step 调用 Capability
→ Capability 返回 awaiting
→ Runtime 原子保存 AwaitHandle 与 compact checkpoint
→ Agent Segment 结束并释放 context
→ Poll/Event/Timer Reconciler 跟踪
→ 恢复条件成立
→ Agent Executor 创建同一 Attempt 的新 Segment；Sequence Executor 从持久 step cursor 继续
```

新的 segment 只获得原 Task、Column、checkpoint 和外部结果，不恢复上一 agent 的完整会话。

## 25. Soft、Stale、Hard Deadline

Hard deadline 适用于所有 AwaitHandle；soft/stale deadline 是 poll handle 的附加健康阈值。

### Soft Deadline

比预期慢，需要关注但不失败：

- 产生 `run.long_running`
- 唤醒 conversation-agent
- 检查 provider
- 可以继续等待

### Stale Deadline

长时间没有可信进展：

- health 改为 `stalled`
- 执行确定性诊断
- 触发 recovery policy
- 唤醒 conversation-agent

### Hard Deadline

本次等待不允许继续无界延长。到达后先执行声明的 cancel/cleanup capability，再产生 Column `wait_policy.timeout_outcome`。该 outcome 必须在 revision 中唯一映射到 retry/recovery Column 或 `failed` sentinel。

conversation-agent 可以延期，但每次必须记录原因、新期限和 extension budget；延期次数有上限。

## 26. Waiting Health

Run status 与健康度分开：

```text
healthy
slow
degraded
stalled
unknown
```

示例：

```yaml
status: waiting
kind: poll
health: healthy
progress_value: 72
last_progress_at: 30_seconds_ago
```

总耗时不是唯一判断依据；持续提供可信 progress evidence 的长等待可以保持 healthy，连续查询失败或超过 stale deadline 的等待进入 degraded/stalled。

## 27. Progress Evidence

Capability 可以把自身进度转换为统一的可选证据：

```yaml
progress_evidence:
  alive:
  progress_value:
  progress_message:
  last_activity_at:
  provider_status:
  estimated_completion:
```

Version 1 Reconciler 只依赖该通用结构与 deadline，不要求 capability 专用 ProgressAdapter。Evidence 证明活跃度，不证明结果质量。

## 28. Provider 错误分类

### 通常不可重试

- 400 invalid request
- 401 credential
- 403 permission
- 404 endpoint/job missing
- policy rejection
- unsupported parameters

应快速失败或由 conversation-agent 修正。

### 通常可重试

- 408
- 429（遵循 Retry-After）
- 502/503/504
- overload
- connection reset
- temporary DNS/network failure

采用指数退避、jitter、attempt budget、总时间预算和 circuit breaker。

### Unknown External Result

请求可能已产生副作用但连接中断：

- 先按 idempotency key 查询。
- 有 status endpoint 时先查询。
- 不能确认时进入 `unknown_external_result`。
- 不重复执行缺少 completed receipt 的非幂等副作用。
- 唤醒 conversation-agent。

## 29. Workflow Liveness Validator

conversation-agent 发布 revision 前必须通过静态完整性检查。

每个非终态 Column 明确处理：

- success outcome
- business rejection
- execution failure
- timeout
- interrupted
- waiting expired
- retry exhausted

`runtime_outcomes` 必须完整提供 `input_missing/execution_failed/interrupted/retry_exhausted/max_visits_exceeded`，每个 value 都对应已声明的唯一 transition。Runtime 只按条件 key 查表，不从错误文本或业务内容选择 outcome。

Cancellation 不依赖业务 transition；Validator 验证每种 executor/wait kind 都具备 lease release、cancel/cleanup 与原子 `failed(failure_code=cancelled)` 协议。

每个 waiting 明确：

- resume condition
- check strategy
- hard deadline；poll 另有 soft/stale deadline
- timeout outcome

每个循环明确：

- traversal/attempt count
- 时间或预算上限
- 退出 edge
- 可达 done 或 failed

Version 1 不支持 guard/priority，因此 liveness 基于 `(column,outcome)` 唯一 target 与 `max_visits` 做静态验证。

禁止：

- 无界自循环
- 没有 hard deadline 的 waiting
- 没有 timeout transition 的外部调用
- 没有 failure terminal
- 无下一列时默认成功
- 永远只回到自己的恢复路径

验证目标：从任意可达 Column 和合法 outcome 出发，都存在受限路径最终到达 `done` 或 `failed`。

## 30. Runtime Reconciler

Runtime Reconciler 独立于原 executor process 定期检查：

- pending 是否应该已入队
- running lease 是否有效
- heartbeat 是否过期
- waiting 是否到 `next_check_at`
- soft/stale/hard deadline
- 到期 poll/event/timer AwaitHandle 是否满足恢复条件
- dependency 是否满足
- 是否存在无 owner 的 run
- terminal event 是否已通知 conversation-agent
- Task control state 是否允许恢复执行

可执行：

- 重新入队
- 标记 active Attempt interrupted
- 查询 AwaitHandle
- 恢复 waiting
- 创建新 Column Attempt
- 执行 recovery transition
- 唤醒 conversation-agent
- 明确进入 failed

## 31. 服务重启恢复

持久化：

- Task 当前 Column
- Column Run status/health
- executor kind
- Column Run visit 与 Column Attempt
- lease/heartbeat
- input/output artifact
- AwaitHandle
- tool/API receipt
- pending/waiting reason
- transition decision
- terminal state

启动 reconcile：

### 异常 pending

输入已满足但无 queue/dispatch 时重新入队或升级 conversation-agent。

### 异常 running

lease 过期、owner 不存在或 executor process 丢失时：

- 不假设成功或失败。
- 将 active Attempt 转为 interrupted。
- 检查 artifact、checkpoint 和 execution receipt。
- 可恢复且预算允许则创建新 Column Attempt；否则将 Run 置为 interrupted 并执行 `runtime_outcomes.interrupted`。
- 通知 conversation-agent。

### 外部副作用

每个 step 需要 idempotency key、started receipt、completed receipt、result reference 和 side-effect classification。恢复时先查询 receipt，再决定重试。

## 32. Conversation-agent 通知

以下事件进入 Project mailbox：

- `column.input_missing`
- `run.long_running`
- `run.degraded`
- `run.stalled`
- `await.deadline_exceeded`
- `external.result.unknown`
- `retry.exhausted`
- `column.interrupted`
- `task.done`
- `task.failed`
- `task.cancelled`
- `task.paused`
- `task.resumed`

Task 终态记录：

```text
terminal_at
terminal_event_id
conversation_agent_notified_at
conversation_agent_observed_at
conversation_agent_acknowledged_at
conversation_agent_action
```

Mailbox 使用 claim lease 与 at-least-once delivery。读取只进入 claimed；只有治理决定或显式 audited no-op 成功持久化后，才在同一短事务写 `observed_at/acknowledged_at`。失败或 claim lease 过期自动重新投递。

## 33. SQLite 单库设计

服务级 `data_root/devwerk.db` 是所有 Project 共享的 SQLite。Project 通过 `project_id` 隔离结构化状态，通过 `workspace_root` 隔离用户工作区；每个 `internal_artifact_root=data_root/projects/{project_id}` 保存该 Project 的证据文件，并且不向 Project 文件 capability 暴露。

物理表名和约束以通用设计的 Canonical Data Dictionary 为准，本节只列 Kanban 相关子集：

### Workflow Definition

- `v1_workflows`
- `v1_workflow_revisions`

### Task Runtime

- `v1_tasks`
- `v1_column_runs`
- `v1_column_attempts`
- `v1_agent_runs`
- `v1_agent_segments`
- `v1_await_handles`
- `v1_execution_receipts`
- `v1_task_dependencies`
- `v1_scheduling_entries`

### Evidence

- `v1_events`
- `v1_artifacts`
- `v1_project_mailbox`

所有 Project-scoped 表直接携带 `project_id`；复合外键和 repository API 验证 Project 一致性。Version 1 Web 使用有界 indexed read query 与 event cursor；物化 projection 是兼容优化，不属于 release gate。

## 34. SQL 与文件分工

SQLite 保存：

- workflow/column/transition 定义
- task/run 状态
- lease、heartbeat 汇总
- AwaitHandle
- retry/timeout/recovery 决定
- event metadata 和紧凑 payload
- artifact metadata、path、hash、size、type
- terminal state 和 notification

文件保存：

- 大模型超长 raw response
- 二进制与媒体 artifact
- 代码包、压缩包
- 大型 tool output
- screenshot
- 大日志
- checkpoint 大正文
- 历史归档

Dashboard 和 scheduler 不读取文件正文，只读取 SQLite metadata；正文在详情页或 agent context 编译时按需加载。

## 35. SQLite 写入性能

关键原则：

- WAL mode
- 合理 busy timeout
- 短 transaction
- transaction 内禁止网络、模型调用、外部 capability 和大文件写入
- 关键状态立即提交
- 高频进度合并节流
- 批量维护小批次执行

### 立即持久化

- Task/Column transition
- terminal state/event
- dispatch/retry/recovery 幂等记录
- AwaitHandle 创建/终态
- execution receipt
- lease owner 变化

### 节流合并

- heartbeat
- token usage
- progress percentage
- 重复 provider poll
- dashboard counter

禁止逐 token、逐 stdout 行、逐秒 heartbeat 写 SQLite。可以按时间窗口、状态变化或阶段 checkpoint 合并。

## 36. 索引建议

```text
v1_workflows(project_id, id)
v1_workflow_revisions(project_id, workflow_id, revision_no)
v1_tasks(project_id, status, updated_at, id)
v1_tasks(project_id, workflow_revision_id, status, id)
v1_column_runs(project_id, task_id, visit_no)
v1_column_attempts(project_id, status, lease_expires_at, id)
v1_column_attempts(column_run_id, attempt_no)
v1_agent_runs(project_id, column_attempt_id, id)
v1_await_handles(project_id, status, next_check_at, id)
v1_await_handles(project_id, health, hard_deadline, id)
v1_events(project_id, id)
v1_events(task_id, id)
v1_artifacts(project_id, task_id, created_at, id)
v1_execution_receipts(project_id, capability_id, idempotency_key)
```

要求：

- Reconciler 查询必须命中 `status + due time` 索引。
- Kanban 页面按 Project/status/cursor 查询。
- 禁止对每个 Task 单独查询 Run/Event/Artifact 的 N+1。
- 大 JSON 中的常用查询字段必须提升为正式 column。
- release 前用 query plan 检查核心路径。

## 37. Web Kanban 渲染

Kanban 用户侧只读，Version 1 使用有界 indexed read query 返回轻量 read model：

```text
task_id
project_id
title
task_status
current_column_key
current_column_name
run_status
run_health
progress_summary
risk_summary
last_activity_at
state_version
```

流程：

1. 首次加载分页 snapshot。
2. SSE/WebSocket 推送 `state_version` 之后的增量。
3. 前端只更新受影响卡片。
4. Event、Artifact、Run detail 按需分页。
5. 不轮询整张 board，不加载 raw artifact。

关键状态转移与 event cursor 在同一短 transaction 提交；客户端按 event 后的资源 `state_version` 拉取一致 read model。物化 projection 是后续兼容优化。

## 38. Event 降噪

必须保留：

- transition
- attempt start/end
- retry/recovery
- wait health level 变化
- terminal
- external result unknown
- conversation-agent intervention

不应逐条保留：

- 每个 token
- 每秒 heartbeat
- 没有状态变化的重复 poll
- 重复 progress 百分比

重复事件使用聚合计数和最后时间，降低 SQLite 写锁竞争和 Web 渲染压力。

## 39. 事务不变量

以下操作原子提交：

- Task 创建 + workflow revision 固定 + start Column pending Run/Attempt
- Column Attempt succeeded + Run output artifact metadata + transition + next pending Run/Attempt
- Column Attempt failed + retry/recovery decision + 可选新 Attempt
- waiting + AwaitHandle + checkpoint + execution receipt + step cursor
- AwaitHandle terminal + Attempt 恢复入队
- Task terminal + terminal event + Project mailbox notification
- governance decision/audited no-op + mailbox ack

使用 `state_version` 和幂等键防止重复 event、poll、timer 和 recovery 覆盖新状态。

## 40. 自驱动保证

DevWerk 不能保证外部服务一定成功，但必须保证：

1. 每个 waiting 有明确 poll/event/timer kind、owner 和 AwaitHandle。
2. 每个 waiting 有 `next_check_at` 或 event correlation key。
3. 每个 waiting 有 hard deadline；poll waiting 另有 soft/stale deadline。
4. 每个 waiting 有 resume 和 timeout outcome。
5. 每个循环有次数、时间或预算上限。
6. 服务重启后可以恢复。
7. 无法恢复时显式进入 failed。
8. done、failed 和异常唤醒 conversation-agent。
9. 不允许静默、无界、无解释等待。

系统保证的是 eventual decision，不是外部操作必然成功：

> Task 最终成功完成，或者在可解释、受限的恢复尝试耗尽后明确失败。

## 41. Version 1 验收不变量

- Workflow 以统一 Column Definition 为基础。
- 每次进入非终态 Column 创建独立 Column Run；retry 在 Run 下创建新的 Column Attempt。
- Run status 与 business outcome 分离。
- 每个非终态 Column 显式使用 `capability_sequence` 或 `agent` executor；Runtime 不根据列名或业务文本选择 executor。
- Agent Context 不包含无界历史。
- Input 不满足不执行，Output 不满足不推进。
- transition 全部来自固定 revision，且每个 `(column,outcome)` 只有一个 target。
- Task 只有 done/failed 两种终态。
- 无下一列不代表成功。
- Column failed 先走 retry/recovery，failure terminal 才使 Task failed。
- waiting 可以长期健康，但不能无期限和无退出策略。
- CapabilityResult、AwaitHandle、Progress Evidence、Liveness Validator、Reconciler 都存在。
- Task pause/resume 使用独立 control state，只有 paused 且无活跃 lease 才允许迁移。
- 服务重启不会留下永久 orphan pending/running/waiting。
- terminal 和异常进入 conversation-agent mailbox。
- SQLite 不承受逐 token、逐 heartbeat 和大正文写入。
- Kanban Web 使用分页 read model 和增量 event cursor。
