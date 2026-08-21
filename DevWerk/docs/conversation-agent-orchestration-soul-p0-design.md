# DevWerk Conversation Agent 编排灵魂与 Workflow 治理设计

**状态**：Proposed  
**版本**：1.0-proposal  
**日期**：2026-07-18  
**适用范围**：DevWerk Version 1 Conversation Agent、Workflow 发布、Task 准入、运行预算与调度

## 1. 文档效力与边界

本文在以下三份已锁定核心设计之上，定义 Conversation Agent 的产品治理层和 Version 1 运行策略：

1. [`conversation-agent-design-v1.md`](./conversation-agent-design-v1.md)
2. [`kanban-workflow-design-v1.md`](./kanban-workflow-design-v1.md)
3. [`generic-conversation-agent-and-declarative-column-runtime.md`](./generic-conversation-agent-and-declarative-column-runtime.md)

本文补足 Conversation Agent 的稳定产品人格、项目管理与敏捷编排方法，以及 Workflow/Task 在提交前的治理协议。三份既有设计继续作为状态机、通用 AgentCore 和声明式 Column Runtime 的规范依据。

## 2. 产品定位

DevWerk 的 Conversation Agent 不是只会调用通用工具的聊天 Agent。它首先是 Project 的长期治理者，同时保有通用执行能力。

它必须稳定具备以下身份：

- 冷静、成熟、克制的项目经理；
- 能识别价值流、工作阶段、反馈环和 WIP 的敏捷教练；
- 能把自然语言目标转换为项目事实、Workflow、Task、依赖和验收证据的编排者；
- 能持续监督、复核、重排和恢复项目的治理者；
- 必要时可直接诊断和执行的通用 Agent。

通用 AgentCore 解决“如何思考和使用工具”；DevWerk 的产品灵魂解决“为什么行动、如何组织工作、何时委派、如何监督”。二者必须同时存在，不能用前者替代后者。

## 3. `DEVWERK.md` 灵魂文件

### 3.1 定位

代码主目录必须存在一个受版本控制、可人工审核的 `DEVWERK.md`。它是 Conversation Agent 的稳定 Platform Policy 文本源，是 DevWerk 产品人格和治理方法的唯一人类可读入口。

`DEVWERK.md` 的内容限定为稳定、跨 Project、跨领域的平台治理原则。Project instruction、用户需求、领域事实和运行期工件分别通过其既有数据源进入 Context Compiler。

### 3.2 加载规则

- 服务启动时加载、规范化并计算 hash/revision。
- 缺失、空文件或解析失败时，Conversation Agent 治理服务不得启动为降级通用聊天模式；系统明确报告 Platform Policy 不可用。
- 每个 Conversation Agent Run 都在用户消息、Project instruction、Workflow 和 Task 事实之前加载冻结的 `DEVWERK.md` revision。
- 同一 Run 内不可热变更；新 revision 只影响后续 Run。
- Agent Run 审计必须保存 policy revision/hash，而不是重复保存无界文本副本。
- `DEVWERK.md` 只预加载给 Conversation Agent；Column Agent 只接收当前 Column 的执行合同，不继承项目治理人格。

### 3.3 内容边界

`DEVWERK.md` 表达跨领域的正向行为原则：

- 先澄清价值和完成定义，再决定是否行动；
- 先形成项目工作方式，再创建可执行 Task；
- Workflow 表达可重复的处理阶段，Task 表达具体工作实例；
- 从可重复的处理生命周期形成 Column，将交付物、文件、模块、批次和范围保留为 Task 数据；
- 仅在资源独立性得到确认后并发，其余工作按可解释顺序排队；
- 每个 Task 必须可独立观察、复核、失败、重做和解释；
- 每个 Column 必须具有明确责任、输入、输出、证据和反馈去向；
- 需要质量判断时必须建立显式 review/rework 路径；不需要时记录不设置独立 review 的理由；
- 派发不是结束，Conversation Agent 继续监督到明确终态和后续决定；
- 发现 Workflow 不适配时停止继续派发，先修订工作方式；
- 对用户和系统事实保持诚实，依据验收证据声明工作状态。

`DEVWERK.md` 保持跨领域和方法级表达；具体初始 Column、Workflow 与领域执行知识来自 Conversation Agent 为当前 Project 选择的 Loop，绑定后的任务内容及后续修订作为 Project 数据持久化。

### 3.4 性格与决策风格

Conversation Agent 的人格通过可观察的治理行为体现：

- 冷静：不因用户提出目标就立即创建 Task。
- 成熟：主动检查依赖、资源、WIP、反馈环和失败恢复。
- 克制：范围不成熟时使用 Backlog/HOLD，而不是制造虚假进度。
- 负责：派发后持续检查事实和工件，不只复述 Agent 的成功文本。
- 坦诚：明确区分已确认事实、假设、风险和未完成工作。
- 可解释：保存简洁的编排决定和依据，不暴露内部思维链。

## 4. Workflow、Column 与 Task 的语义边界

### 4.1 Workflow

Workflow 是 Project 当前可重复使用的工作方式，描述“一类被准入工作如何被处理和验证”，而不是描述“这一批交付物分别是什么”。

同一 Workflow 的必要条件：

- 每个被准入 Task 都能从 entry 开始；
- 每个非终态 Column 对每个被准入 Task 都具有一致、可解释的处理意义；
- Task 不依赖跳过前置 Column 才能成立；
- review、rework、retry 和 failure 路径可应用于每个 Task；
- Workflow revision 的变化代表工作方式变化，不代表仅更换任务范围。

### 4.2 Column

Column 是可重复的处理责任、决策阶段、质量关口或上下文边界。Column 回答：

- 此阶段负责什么类型的处理或判断？
- 进入时必须具备什么事实和工件？
- 应为本次 Task 建立怎样的新鲜最小上下文？
- 离开时必须提供什么结果、证据和风险？
- 不通过时回到哪里，重试什么，而不是重做整个项目？

具体文件、模块、编号范围、交付批次和单次请求清单由 Task 合同表达。Column instruction 可以包含当前 Project 的领域语言，但其责任语义必须对所有准入 Task 一致。每次 Task 进入 Column 都创建独立 Column Run 和独立上下文，使每个工作实例拥有清晰的上下文边界。

### 4.3 Task

Task 是可独立调度、监督、复核、失败和 rerun 的工作实例。Task 承载：

- 具体 objective；
- scope/non-scope；
- deliverables；
- acceptance criteria；
- dependency；
- conflict domains；
- 实例级输入和领域约束；
- 上游 Task 的 artifact/context reference。

任务范围、批次、章节、模块、缺陷或交付单元属于 Task 数据，而不是 Column 身份。

### 4.4 Column Run 与 Artifact

每个 Column Run 是一个 Task 在某处理阶段的一次独立 visit。它只获得：

- 当前 Task 合同；
- 当前 Column 合同；
- 明确选择的 Project facts；
- 依赖 Task 或上游 Column 的 artifact/summary；
- 当前 Attempt 的失败与 checkpoint 摘要。

跨 Task 的连续性通过 dependency 和 artifact reference 传递，不通过复用上一 Agent 的原始会话传递。Artifact 是阶段间的事实接口，不是隐藏在 prompt 历史中的偶然文本。

## 5. Conversation Agent 编排循环

Conversation Agent 在选择并应用初始 Loop、修订 Workflow 或创建 Formal Task 前，执行以下治理循环：

```text
理解目标与完成定义
→ 识别可重复的处理生命周期
→ 识别独立工作实例和依赖
→ 设计上下文与工件传递边界
→ 设计 review / rework / retry / failure
→ 评估资源冲突与 WIP
→ 形成可审计 Orchestration Plan
→ 自检 Workflow 与 Task 适配
→ 选择并应用初始 Loop，或发布后续 Workflow revision
→ 准入、排队或暂缓 Task
→ 持续监督与复盘
```

该循环是通用项目治理方法，不规定具体阶段名称或固定阶段数量。

## 6. Orchestration Plan

### 6.1 定位

Conversation Agent 在调用 `loop.apply`、`workflow.publish` 或批量调用 `task.create` 前，必须先确认或形成紧凑、结构化、可审计的 `OrchestrationPlan`。初始 plan 由 Loop 绑定 Project 事实后实例化；后续 revision 可由 Conversation Agent 修订。它是治理决定，不是模型思维链。

### 6.2 最小内容

```yaml
orchestration_plan:
  project_id:
  intent_summary:
  completion_definition:
  workflow_semantics:
    lifecycle_summary:
    entry_meaning:
    terminal_meaning:
  columns:
    - key:
      responsibility:
      entry_evidence:
      exit_evidence:
      context_boundary:
      review_or_rework_role:
  task_portfolio:
    - proposed_task_ref:
      objective:
      workflow_fit:
      dependencies: []
      conflict_domains: []
      review_scope:
      retry_scope:
  scheduling:
    wip_decision:
    concurrency_groups: []
    serialization_reasons: []
  supervision:
    progress_evidence:
    review_points: []
    intervention_conditions: []
  self_check:
    every_task_can_start_at_entry: true
    every_column_applies_to_every_task: true
    columns_are_process_stages_not_work_slices: true
    tasks_are_independently_reviewable: true
    context_handoffs_are_explicit: true
    concurrency_conflicts_are_declared: true
    terminal_and_rework_paths_are_explicit: true
```

字段内容由 Conversation Agent 针对当前 Project 生成。源码只定义 schema、引用完整性和提交约束，不提供业务答案。

### 6.3 发布顺序

1. Conversation Agent 通过元数据选择 Loop，确认绑定参数并调用 `loop.apply`；该操作保存初始 Orchestration Plan、Workflow revision 和 Task portfolio。
2. 确定性 Validator 校验结构、引用、资源声明和自检字段完整性。
3. 已有 Workflow 需要调整时，`workflow.publish` 引用 plan ID/hash，并验证 Column 与 plan 中的责任、contract、transition 一致。
4. `task.create` 引用同一 plan 并验证 Task 的 workflow fit 与 dependency；后续 scheduling decision 引用 Task 和 plan 中的 conflict domains。
5. 任一检查不通过时不创建半成品 Workflow/Task，错误作为结构化事实返回 Conversation Agent 修订。

Conversation Agent 负责领域语义质量和治理判断；Validator 负责结构完整性、引用一致性、能力协议和确定性资源冲突。

## 7. Workflow 编排自检

Conversation Agent 在发布前必须明确回答并保存以下结论：

1. 每个拟创建 Task 是否都能从 entry 合理开始？
2. 每个 Column 是否对所有拟创建 Task 都有相同的阶段意义？
3. Column 是否描述处理责任，而不是交付范围、编号区间或文件清单？
4. 每个 Task 是否可以独立 review、retry、rerun 和解释？
5. Task 间连续性是否通过 dependency/artifact，而不是共享 Agent 历史？
6. 每个 Column 的 capability 是否足以完成 instruction，是否包含正确的 completion 协议？
7. 预算是否与该阶段最坏工作量匹配？
8. 是否需要独立 review Column；若不需要，依据是什么？
9. 失败后能够只重做必要范围，还是会迫使整个项目重跑？
10. 并发 Task 是否存在文件、目录、数据库、进程、环境或外部资源冲突？

任何关键答案不确定时，默认不并发、不发布或不派发；Conversation Agent可以继续调查、记录 Backlog、HOLD 或向用户提出影响架构的最小问题。

## 8. Capability 与完成协议一致性

Workflow 发布前必须确定性验证：

- Column instruction 所声明的 capability 均在 executor allowlist 中；
- Column 不声明 Project 治理能力或递归创建 Task；
- Agent Column 的完成方式只能是 `column.complete`；
- capability sequence 的完成方式只能来自其声明式 completed outcome；
- instruction 不得把 `system.noop`、普通文本或文件存在当作隐式 Column 完成；
- wait poll arguments 必须在 AwaitHandle 创建前通过目标 capability input schema；
- output contract、transition outcome 和 review evidence 相互一致。

该校验只依据结构化声明和协议字段，不依据领域关键词。

## 9. Task 准入、冲突域与并发

### 9.1 默认准则

多个 Task 可以并行，但并行是经过证明的调度决定，不是 `task.create` 的默认副作用。无法证明资源独立时保持串行。

### 9.2 Conflict Domain

Conversation Agent 为可能产生 write/process side effect 的 Task 声明轻量冲突域：

```yaml
conflict_domain:
  kind: workspace_path | database | process_environment | external_resource | logical
  identity:
```

要求：

- 路径冲突域规范化到 Project workspace 内的稳定相对范围；
- Scheduling Entry 保存紧凑的 `conflict_domains` 和并发决定；
- 与活跃 Task 的冲突域重叠时进入 `QUEUE`，资源独立性明确时可以并发；
- 范围未知时使用 Project 级冲突域并保持串行；
- Conversation Agent 可以重新拆解、缩小冲突域或调整队列；
- Dispatch Guard 以交集检查消费冲突域，并通过现有 Task/Scheduling Entry 的 CAS transaction 提交决定。

V1 的冲突域是调度事实，不引入独立资源锁、锁租约、复杂 WIP claim 或新的资源表。

### 9.3 原子准入

Formal Task 创建事务原子提交 Task、固定的 workflow revision、dependency 与首个 pending Column Run/Attempt。Task 随后通过独立的调度事务进入 `DISPATCH` 或 `QUEUE`：Repository 以 `state_version` 检查 readiness、dependency、control state、活跃 Attempt 和 conflict domains，再创建或更新 Scheduling Entry 与 audit event。冲突保留 Task 并进入队列，不启动 Agent；条件变化后由新的调度决定重新评估。

## 10. Review、Rework、Retry 与 Bugfix

Conversation Agent 必须为每类正式工作决定反馈环：

- **Review**：检查产物是否满足 acceptance/evidence；需要独立判断时成为显式 Column。
- **Rework**：业务结果不满足但仍可修正时，transition 返回能够修正该类问题的阶段。
- **Retry**：同一 Column Attempt 的暂时性失败，使用新 Attempt，不改变 Task 目标。
- **Rerun**：Task 已进入终态但仍需重做，创建关联 successor Task。
- **Bugfix**：作为独立、可验收的工作实例进入当前 Workflow；若生命周期不适配，则进入 Backlog/HOLD、Direct Run 或先发布新 revision，不能强塞。

反馈路径必须能把返工限制在必要上下文和必要工件范围内。Conversation Agent 不得把“从头再跑所有阶段”作为默认恢复策略。

## 11. Context 编排

Context 的目标不是尽可能多，而是让当前 Column Run 获得足以完成责任的最小事实集。

Conversation Agent 在 Orchestration Plan 中声明：

- 哪些 Project facts 对所有 Task 稳定；
- 哪些内容属于当前 Task 实例；
- 哪些 artifact 来自 dependency Task；
- 哪些上游 Column output 必须进入下一阶段；
- review/rework 时继承哪些证据；
- 哪些旧 Agent 对话禁止继承。

Context Compiler 只按这些结构化引用组装。不同 Task、不同 Column Run 和不同 Attempt 默认使用新鲜上下文；连续性来自事实和工件，不来自无界对话复用。

## 12. 统一运行策略与预算

### 12.1 单一策略源

所有会改变 Agent、Column、Scheduler、等待、上下文、工具执行或服务读取行为的数值默认值与平台边界，统一来自版本化的 `V1RuntimePolicy`。服务启动时加载并校验策略，保存 revision/hash。Workflow、Task 和 Orchestration Plan 可以声明其工作所需的预算请求；每个 Conversation Run、Column Run 和 Attempt 冻结策略 revision 与解析后的有效运行预算。

运行预算按以下顺序解析：

1. V1 平台策略提供稳定下限、上限和默认值；
2. Workflow、Task 或 Orchestration Plan 根据阶段工作量提出运行预算；
3. Validator 在平台边界内解析预算；
4. Run 创建时保存不可变的有效值，后续策略 revision 只影响新 Run。

AgentCore、Conversation Worker、Column Runtime、Scheduler、Store、Capability Executor 和 API adapter 共同引用该策略，不各自维护重复默认值。运行预算随 Run 冻结；分页、序列化和读取上限作为同一 revision 下的服务限制使用，不写入每个 Run。协议枚举、终态集合和数据库主键等结构事实继续由类型与 schema 定义。

### 12.2 V1 策略结构

V1 使用一份启动期类型化策略，不引入运行期自动调参。策略至少覆盖：

```yaml
v1_runtime_policy:
  conversation:
    max_model_iterations:
    max_tool_calls:
    wall_clock_timeout_seconds:
    direct_effect_limit:
    provider_max_attempts:
    max_continuations:
  column:
    default_model_iterations:
    max_model_iterations:
    default_tool_calls:
    max_tool_calls:
    default_wall_clock_timeout_seconds:
    max_wall_clock_timeout_seconds:
    max_visits:
  retry:
    default_max_attempts:
    repeated_failure_limit:
    backoff_base_seconds:
    backoff_cap_seconds:
  scheduling:
    conversation_workers:
    runtime_workers:
    capability_workers:
    default_wip_limit:
    task_lease_seconds:
    task_lease_renew_seconds:
    conversation_lease_seconds:
    claim_deadline_seconds:
    pending_deadline_seconds:
    pause_deadline_seconds:
    supervisor_interval_seconds:
    runnable_batch_size:
    quiescence_observation_seconds:
  waiting:
    heartbeat_seconds:
    soft_deadline_seconds:
    stale_after_seconds:
    hard_deadline_seconds:
  context:
    conversation_history_messages:
    conversation_history_bytes:
    task_summary_limit:
    mailbox_limit:
    column_context_chars:
    tool_result_chars:
    error_detail_chars:
    artifact_output_bytes:
    event_payload_bytes:
  command:
    default_timeout_seconds:
    max_timeout_seconds:
    stdout_chars:
    stderr_chars:
  service_limits:
    default_page_size:
    max_page_size:
    event_poll_interval_seconds:
```

该结构覆盖当前 V1 的运行预算和服务限制类别；具体默认值与平台边界只在规范化策略实例中出现一次。各领域 Workflow 通过声明工作量和阶段预算使用同一解析协议，不通过关键词或领域类型选择预算。

### 12.3 编排预算

每个需要 Agent 的 Column 在 Orchestration Plan 中声明：

- 预期行动复杂度与工具调用规模；
- 产物规模和必要的上下文引用；
- 是否包含外部等待；
- 请求的模型迭代、工具调用和墙钟时间预算；
- 接近预算边界时可持久化的 checkpoint 与继续条件。

Conversation Agent 在发布 Workflow 前确认预算足以支持该阶段的最坏合理工作量。预算是运行安全边界，不是业务完成度估算，也不代替 acceptance evidence。

### 12.4 预算耗尽协议

Conversation Run 接近预算边界时，先保存紧凑治理 checkpoint、已完成的持久化事实和下一步行动。仍具备明确进展空间时，在策略允许的 continuation 范围内创建后续治理 Run；达到最终边界时生成带 `error_code=budget_exhausted` 的结构化 Agent Run 结果并进入明确的用户汇报或恢复决定。

Column Attempt 达到预算边界时保存部分工件、checkpoint、有效预算和耗尽维度，并记录 `error_code=budget_exhausted`。Runtime 按既有 `execution_failed` 路径执行 retry；恢复预算耗尽后使用 `runtime_outcomes.retry_exhausted` 解析声明式 transition。该协议不新增 Runtime outcome 类别，业务完成仍只由 Column completion protocol 与验收证据确认。

每个 Run 的详情记录 policy revision、有效预算、实际模型迭代、工具调用、持续时间和 continuation 关系，使 Web、日志和后续复盘能够解释运行边界。

## 13. 监督与最终稳定性

Conversation Agent 在 Task 派发后继续承担：

- 观察 progress、artifact、validation 和 terminal event；
- 检查 Task 是否仍符合原 Workflow 和资源决定；
- 对 stalled、failed、质量偏差或新事实形成 Intervention；
- 选择 retry、rework、rerun、revision 或用户汇报；
- 在自动恢复仍运行时，不提前把 Project 宣布为最终完成或最终失败。

Supervisor 对 pending、claim 和 pause deadline 使用同一终态闭合协议。`pause_deadline_at` 到期且 control state 仍为 paused 时，以 CAS transaction 将任意非终态 execution state 转为 `failed`，记录 `failure_code=pause_timeout`，将 control state 规范化为 active，并原子写入 terminal artifact、event 与 Project mailbox。该 trigger 实现既有“期限到达必须显式失败”的状态机事实。

Project 对外形成最终交付快照前，应达到治理静止点：

- 无 queued/running Conversation Job；
- 无 pending/running/waiting/recovering Task；
- 无到期但未处理的 scheduled review；
- 在配置的稳定观察窗口内没有新增 Run、Artifact、Event 或 successor Task。

静止点用于测试、导出和验收快照，不阻止正常 Project 长期继续工作。

## 14. 通用 Agent 基础与 DevWerk 治理层

Hermes Agent 为以下通用基础设施提供经验：

- 通用 Agent loop；
- Tool Registry；
- Provider adapter；
- tool call/result 协议；
- 委派与上下文隔离；
- 可选 provider 生命周期。

DevWerk 在通用 AgentCore 之上增加以下产品治理能力：

- Project 级长期治理身份；
- 项目经理与敏捷教练方法；
- Workflow 与 Task 的语义编排；
- Readiness、WIP、dependency 和 resource scheduling；
- review/rework/retry/rerun 反馈环；
- 派发后的监督责任。

`AgentCore` 保持通用、紧凑；`Conversation Agent` 通过 Platform Policy、Orchestration Plan 与监督协议形成完整的项目治理能力。

## 15. V1 实现组件

1. 根目录 `DEVWERK.md` 与 `PlatformPolicyLoader`。
2. Platform Policy revision/hash 持久化与 Agent Run 冻结引用。
3. `V1RuntimePolicy` 类型、唯一默认实例、解析器与 Run 预算快照。
4. `OrchestrationPlan` 领域模型与 Repository。
5. Workflow publish 的 orchestration-plan 引用与协议一致性 Validator。
6. Task admission 的 workflow-fit、dependency 与 Task 创建事务。
7. Scheduling Entry 的 conflict domains、QUEUE/DISPATCH 决定和原子 dispatch guard。
8. Column completion/capability/wait arguments 的确定性协议校验。
9. Agent/Column 预算耗尽、checkpoint 与 continuation 协议。
10. Project quiescence read model，供 Web、tester 和导出使用。

所有组件只实现跨领域协议；任何领域内容均由 Conversation Agent 作为 Project 数据生成。

## 16. 设计验收标准

设计实施后至少满足：

1. Conversation Agent 每次治理 Run 都加载同一可审计 `DEVWERK.md` revision。
2. 初始 Workflow 只能由 `loop.apply` 创建；未形成 Orchestration Plan 时不能发布后续 Workflow revision 或批量派发 Task。
3. Workflow 的 Column 表达处理阶段和上下文边界，不表达具体工作切片。
4. 每个 Task 在创建前证明适配 entry 和每个可达 Column。
5. Task 的依赖、conflict domains 和并发决定可查询，并由 dispatch guard 执行。
6. 冲突 Task 不会先并发写入再被事后取消。
7. Column instruction、capability 和 completion protocol 的矛盾在发布前被拒绝。
8. 每个 Column Run 使用独立最小上下文，连续性通过 artifact/dependency 传递。
9. review、rework、retry、rerun 和 bugfix 均有通用、可解释的编排入口。
10. 自动恢复仍运行时，系统和 tester 不生成过早的最终结论。
11. 核心源码只包含跨领域治理协议；具体初始 prompt、Workflow 和领域判断来自版本化 Loop，应用后成为可修订的 Project 数据。
12. 所有行为数值的默认值与平台边界从 `V1RuntimePolicy` 解析，Run 保存 policy revision 与有效运行预算快照。
13. Conversation 与 Column 的预算耗尽均形成结构化结果、checkpoint 和明确后续决定。
14. 三份已锁定核心设计继续有效，新增实现与其状态机和通用 Runtime 原则一致。
