# DevWerk Agent + Workflow Runtime P0 架构 Review 汇总

> 日期：2026-09-08  
> Review 目标：以 **“单机 / SQLite / 暂不考虑安全与沙箱，优先保证 Agent + Workflow 长时间稳定运行”** 为基准，重新梳理当前代码中的 P0 级架构问题。  
> 重要说明：**本报告不采信 `docs/` 内任何测试结论、测试覆盖率、通过数量或“某测试证明设计正确”的结论。** `docs/` 仅作为架构意图参考。P0 判断依据来自生产代码控制流、状态/事务/lease 语义、真实日志以及 failure-interleaving 分析。

---

## 1. 结论摘要

当前 DevWerk 的主体对象模型仍然是合理的，不建议推翻：

```text
Project
  └─ Task Plan
      └─ Task
          └─ Workflow Revision
              └─ Column Run
                  └─ Attempt
                      └─ Agent Run / Capability Execution
```

现阶段最严重的问题不在于“是否能调度起来”，而在于这些对象之间的 **执行事实、执行所有权、失败分类、最终活性** 没有形成闭环。

本轮建议将所有 P0 归并为 10 个根问题：

| 编号 | P0 根问题 | 核心维度 | 真实日志直接触发 |
|---|---|---|---|
| P0-1 | Completion Admission 完成事实模型错误 | Truth | **是** |
| P0-2 | Execution Ownership / Fencing 不成立 | Ownership | 部分为故障推演，已有 race 可复现 |
| P0-3 | Recovery 缺少稳定 Side-effect Replay / Idempotency 语义 | Truth + Ownership | 高风险，代码结构确定存在 |
| P0-4 | Runtime Failure 与 Business Failure 混为一谈 | Failure | **是** |
| P0-5 | Agent / Workflow / Command 没有可靠执行上界 | Liveness | **是，目标 Task 到 31 iterations** |
| P0-6 | Supervision / Mailbox / Conversation Recovery 不闭环 | Failure + Liveness | **是，Mailbox 尝试恢复被禁止** |
| P0-7 | Capability Execution Truth 可被 LLM 自定义 | Truth | **是，exit=1 异常被记录 success** |
| P0-8 | Artifact Provenance 不是 Immutable Ledger | Truth | 代码结构确定存在 |
| P0-9 | Loop 不是 Durable / Versioned Runtime Package | Truth + Failure | 代码结构确定存在 |
| P0-10 | Task Dependency 在 rerun successor 场景可能永久断链 | Truth + Liveness | 代码结构确定存在 |

如果只按修复优先级排序，建议：

```text
第一批：Runtime Kernel
P0-1 Completion Truth
P0-4 Failure Taxonomy
P0-2 Execution Fencing
P0-3 Effect Idempotency

第二批：Runtime Availability
P0-5 Liveness Budgets
P0-6 Supervision 闭环

第三批：Durable Workflow Correctness
P0-7 Execution Truth
P0-8 Artifact Ledger
P0-9 Loop Snapshot
P0-10 Dependency Lineage
```

---

# 2. Review 方法：后续重新审代码时必须持续验证的四个 Invariant

后续 Review 不应再以“文件是否写得合理 / 测试是否通过”为核心，而应该对所有跨模块调用持续问下面四个问题。

## 2.1 Truth — 谁是事实权威

必须明确：

- LLM 可以描述意图、判断和业务结论；
- Runtime 必须掌握真实执行记录；
- 外部进程 exit code、文件 hash、execution receipt、task state、artifact provenance 不能由模型自由定义；
- 历史事实必须不可被后续写操作静默改写。

重点危险信号：

```text
LLM 提交 Runtime 内部 ID
LLM 定义 command 是否成功
Current Workspace State 被当成 Historical Artifact
Runtime error 最终写成 Business failed
```

## 2.2 Ownership — 当前到底谁有权产生副作用

所有会改变世界状态的操作都必须能回答：

```text
当前 Task / Attempt / Agent Run 是否仍拥有执行权？
它的 lease 是否仍有效？
是否已经被 successor / retry / recovery 替代？
```

重点审查：

```text
project.files.write
project.command.run
system.* write/process/control
await cleanup/resume
terminal artifact
Conversation direct mutation
pause/retry/fail/cancel
```

## 2.3 Failure — 这里失败以后属于什么失败

至少需要区分：

```text
business failure
agent contract failure
capability failure
provider transient/permanent
infrastructure transient/permanent
runtime consistency failure
```

不能继续：

```python
except Exception:
    task.status = "failed"
```

## 2.4 Liveness — 一定能前进或终止吗

必须验证：

- Agent Run 是否有 hard wall clock；
- Workflow cycle 是否有 visit ceiling；
- recovery 是否有 retry / elapsed ceiling；
- command 是否有 timeout；
- stdout/stderr 是否有限制；
- supervisor/lease keeper/dispatcher 异常后是否自恢复；
- orphan running / waiting 是否会永久卡死。

---

# 3. P0-1 — Completion Admission 完成事实模型错误

## 3.1 问题定义

当前生产 Runtime 将 completion evidence 的一部分责任交给 LLM，并把当前 Agent Run 内大量 successful action 都提升为 completion admission 的必要证据。

关键代码：

- `app/v1/runtime.py`
  - `_column_completion_contract()`
  - 当前可见：`evidence_collection="model"`
- `app/v1/completion_protocol.py`
  - `CompletionContract`
  - `CompletionProtocolStalled`
  - model/runtime evidence collection 逻辑
- `app/v1/services/completion_admission.py`
  - `required_success_ids`
  - `Column completion omitted successful action evidence`
  - `requires at least one successful action evidence`
- `app/v1/execution_ledger.py`
- `app/v1/agent_runner.py`
- `app/v1/agent_tool_execution.py`

## 3.2 真实事故

目标 Task：

```text
project_id=prj_d3b99300e1624232a9c3462e5ea3c571
task_id=tsk_cfc85c0496834a2baae81d9b3eb055f0
```

业务回测实际上已经：

```text
command exit_code = 0
结果文件成功产生
optimization/run_history.json 写入成功
optimization/parameters.json 写入成功
```

但 completion 反复被 Runtime 拒绝：

```text
Column completion omitted successful action evidence
```

Agent 为了满足协议继续制造新的 action：

```text
print("done")
print("checkpoint")
system.noop
...
```

每产生一个新 successful action，Runtime 要求提交的 evidence 集合继续扩大，形成正反馈：

```text
completion reject
→ 新 action
→ 新 evidence
→ required set 变大
→ completion 再 reject
```

最终 Agent 跑到约 31 iterations 后触发：

```text
CompletionProtocolStalled
```

业务成功，但 Task 被判 failed。

## 3.3 根因

当前混淆了三个概念：

```text
执行过的动作
成功执行的动作
证明 Column 业务目标已经完成的证据
```

三者不是等价关系。

例如：

```text
pip install pyyaml
```

可以是一次成功动作，但不是“基线回测完成”的业务证据。

## 3.4 建议修改

### A. Completion ID ownership 完全收归 Runtime

LLM 的 `column.complete` 最好只返回：

```json
{
  "outcome": "...",
  "output": {},
  "summary": "..."
}
```

不要再要求 LLM 提交 Runtime foreign key：

```text
evidence_id
execution receipt id
artifact internal id
```

### B. Completion Evidence 显式分类

建议引入：

```text
CompletionEvidence
├─ context evidence
│  ├─ upstream accepted artifact version
│  ├─ dependency result
│  ├─ review receipt
│  └─ frozen/preloaded fact
│
└─ execution evidence
   ├─ material file mutation
   ├─ business command
   ├─ deployment/external API
   └─ other completion-relevant side effect
```

不要采用：

```text
all successful actions == completion evidence
```

### C. Column definition 应声明 evidence policy

例如：

```text
authoring:
  require_business_effect = true

review:
  allow_context_only = true

deliver:
  allow_upstream_receipt = true
```

不要由 Runtime 用一个全局规则处理全部 Column。

## 3.5 验证场景

必须至少人工/故障注入验证：

1. Column 只读取已 preload context，不执行 capability，也能合法完成；
2. Column 执行 20 个探索动作，但只有 1 个业务 action，completion 不要求提交全部 20 个；
3. LLM 提交错误/伪造 evidence id 不影响 Runtime truth；
4. Runtime 自动生成 completion provenance；
5. completion reject 不会因为无意义 action 而解除 no-progress。

---

# 4. P0-2 — Execution Ownership / Fencing 不成立

## 4.1 问题定义

当前 Task DB 状态有 CAS/lease，但 **执行所有权没有传播到真实副作用层**。

数据库能阻止 stale worker 最终提交 Task 状态，不代表 stale worker 不能继续：

```text
写文件
跑 command
调用 control capability
做 await cleanup
写 terminal artifact
```

## 4.2 关键代码

重点 Review：

- `app/v1/runtime.py`
  - Task claim
  - `LeaseKeeper`
  - `reconcile_await()`
  - `RuntimeSupervisor._loop()`
- `app/v1/services/scheduler.py`
  - `claim_task()`
  - `renew_lease()`
- `app/v1/services/recovery_manager.py`
  - retry / pause / resume / fail / cancel
- `app/v1/capabilities.py`
  - 所有 `write/process/control` capability
- `app/v1/agent_tool_execution.py`
- `app/v1/execution_ledger.py`
- `app/v1/store.py`
  - await handles
  - terminal evidence
  - task state mutations
- `app/v1/conversation.py`
  - Conversation direct capability execution

## 4.3 主要子问题

### A. LeaseKeeper 死亡不会终止旧 Agent

如果：

```text
renew_lease() → SQLite transient exception
```

LeaseKeeper thread 可直接死亡。

旧 Agent 继续跑；lease 到期后新 Worker 可 reclaim。

形成：

```text
Worker A stale but alive
Worker B new owner
```

### B. Capability 执行前没有 fencing validation

当前 capability context 缺少稳定的：

```text
fencing_token
lease_generation
attempt_owner_token
```

所以 stale worker 即使失去 Task DB ownership，仍可能调用 `project.files.write` 等副作用。

### C. Durable Await 没有 claim/lease

`due_await_handles()` 是查询 pending handle，Supervisor 直接 reconcile。

缺少：

```text
claim_await
await owner
lease
fencing
```

多实例/重启/残留 worker 时可能重复：

```text
poll
cleanup
resume
transition
```

### D. waiting Task 释放了 resource ownership

如果 Task waiting 后 lease/resource ownership 被释放，而 await 恢复时不重新经过 Scheduler，可与后来获得同资源的 Task 并发。

### E. pause/retry/fail/cancel 与 claim 不线性化

Control API 普遍存在：

```text
read state
→ race window
→ UPDATE without expected status/version
```

例如 retry 在读取 pending 后，Supervisor 已经 claim 为 running；retry 仍可能把正在执行的 Task 改回 pending 并清 lease。

### F. pause 与 `state_version` 自冲突

如果 running Attempt 持有 version=N：

```text
pause → state_version=N+1
Attempt finish_run(expected=N)
→ 必然 stale
```

但设计意图又是“Column 完成后进入 paused”，两套语义不兼容。

### G. Conversation 绕过 Workflow Scheduler

Conversation 可以直接拥有：

```text
project.files.write
project.command.run
system.*
```

其 project lock 只串行 Conversation，不与 Workflow Scheduler 的 conflict domain 统一。

所以 Scheduler 并不是平台唯一的 side-effect arbiter。

## 4.4 建议修改

需要一个平台级统一 Ownership Contract：

```text
SideEffectAuthorization
{
  project_id,
  task_id?,
  column_run_id?,
  attempt_id?,
  owner_kind,
  owner_id,
  fencing_token,
  resource_domain,
  expires_at
}
```

所有 `write/process/control` 执行前必须验证 token 仍是当前 owner。

建议：

1. `claim_task()` 生成 monotonically increasing `fencing_token`；
2. token 写入 Attempt / execution context；
3. capability side effect 前检查 token；
4. receipt start 与 ownership check 尽量在同一个事务边界；
5. Await 单独设计 claim/lease；
6. Conversation mutation 也必须走同一个 arbiter/resource lock；
7. pause/retry/fail/cancel 全部做 expected-state + expected-version CAS。

---

# 5. P0-3 — Recovery 缺少稳定 Side-effect Replay / Idempotency

## 5.1 问题定义

Ownership 解决“谁能执行”，但 crash/retry 后还必须知道：

> **一个已经真实发生的副作用是否应该再次执行。**

当前 Execution Receipt 主要依赖 invocation scoped key，例如：

```text
agent_run_id:tool_call_id
column_run_id:step:index
```

Recovery 创建新 Agent Run / Column Run 后，同一业务 action 获得新 key，旧 receipt 无法天然 dedupe。

## 5.2 关键代码

重点：

- `app/v1/execution_ledger.py`
- `app/v1/agent_tool_execution.py`
- `app/v1/runtime.py`
- `app/v1/services/recovery_manager.py`
- `app/v1/store.py`
- `app/v1/capabilities.py`

## 5.3 典型失败场景

```text
Agent 调 deploy command
→ 外部部署实际成功
→ receipt commit 前/后 provider 断线
→ Task recovering
→ 新 Agent Run
→ 新 execution_key
→ deploy 再执行一次
```

对于：

```text
支付
部署
发消息
创建远程资源
数据库 migration
不可逆 command
```

这不是简单重复日志，而可能产生真实破坏。

## 5.4 额外代码风险

需要重点核对 recovery 清 receipt 的 namespace 是否与 Agent receipt key 一致。

如果 cleanup 依据：

```text
column_run_id:%
```

而 Agent receipt 实际是：

```text
agent_run_id:tool_call_id
```

则 recovery 对 Agent receipt 的语义并没有真正闭合。

## 5.5 建议修改

必须区分：

```text
Invocation ID   — 一次模型 tool call
Effect ID       — 业务上同一个副作用
```

Runtime 应建立稳定 `effect_key`，例如由：

```text
project + logical task + column logical step + capability + normalized business target
```

生成，或由 Workflow 明确声明。

Receipt 应按 effect lifecycle：

```text
reserved
executing
committed
failed_retryable
failed_terminal
unknown/intervention_required
```

对于不能确认“副作用是否已发生”的 crash，不应自动重复，应进入：

```text
intervention_required
```

或者由 capability 提供 reconciliation 方法。

---

# 6. P0-4 — Runtime Failure 与 Business Failure 混为一谈

## 6.1 问题定义

当前大量异常最终会落到 Task `failed`，但异常来源可能是 Runtime 自己。

目标 Task 已经证明：

```text
业务成功
→ CompletionProtocolStalled
→ Task failed
```

这是错误的事实记录。

## 6.2 关键代码

- `app/v1/runtime.py`
  - `WorkflowRuntime.step()` 大范围 `except Exception`
  - recovering / fail_task_from_exception 分支
- `app/v1/services/recovery_manager.py`
- `app/services/provider_errors.py`
- `app/services/anthropic_client.py`
- `app/services/openai_client.py`
- `app/services/ollama_client.py`
- `app/v1/agent_provider.py`
- `app/v1/completion_protocol.py`

## 6.3 Failure taxonomy 不闭合

代码中即使某处生成：

```text
category="tool_transient"
```

上层如果只检查 `recoverable_llm_error`，最终仍可能直接 terminal fail。

Provider 适配器也要重点看：

```text
Timeout
ConnectionError
DNS
connection reset
HTTP 429
HTTP 5xx
invalid auth
invalid model
```

是否全部映射为统一 Provider Error 类型。

真实日志已经出现原生：

```text
requests.exceptions.ConnectionError
```

## 6.4 建议修改

设计统一：

```text
FailureRecord
├─ origin
│  ├─ business
│  ├─ agent_contract
│  ├─ capability
│  ├─ provider
│  ├─ infrastructure
│  └─ runtime
├─ retryability
│  ├─ transient
│  ├─ permanent
│  └─ unknown
└─ disposition
   ├─ retry_same_effect
   ├─ recover_column
   ├─ blocked
   ├─ intervention_required
   └─ terminal_business_failure
```

Task 的 `failed` 只表示真正的业务终态。

建议另存 Runtime execution state，例如：

```text
running
recovering
blocked_runtime
intervention_required
```

至少增加：

```text
failure_origin
failure_code
failure_disposition
```

---

# 7. P0-5 — 没有可靠的执行终止上界

## 7.1 问题定义

Agent、Workflow、Recovery、Command 都存在理论无限执行路径。

## 7.2 关键代码

- `app/v1/agent_runner.py`
  - 主循环 `while True`
- `app/v1/completion_protocol.py`
  - stalled detection
- `app/v1/runtime.py`
- `app/v1/domain.py`
  - Workflow graph / transition
- `app/v1/services/recovery_manager.py`
- `app/v1/files.py`
  - `subprocess.run()`
- `app/v1/capabilities.py`

## 7.3 已确认问题

### A. Agent 无 hard upper bound

当前真实 Task 已跑到：

```text
31 iterations
```

### B. stalled guard 可以被伪 progress 绕过

只要 Agent 执行一个新的：

```text
print("done")
noop
```

execution progress 变化，stalled state 可被重置。

需要 semantic progress，不是“又执行了一个工具”。

### C. Workflow cycle 无 visit ceiling

允许：

```text
A → B → A → B → ...
```

只要图上存在某条 terminal path，不代表实际执行一定终止。

### D. Recovery 无 retry / elapsed ceiling

transient 可理论无限 recovering。

### E. Command 无 timeout/output cap

`app/v1/files.py` 当前 `subprocess.run(capture_output=True)` 没有可靠硬 timeout。

可能永久占 Worker；无限 stdout/stderr 还会消耗内存。

## 7.4 建议修改

至少增加平台硬保护：

```text
AgentRun:
  hard_wall_clock
  max_tool_effects
  semantic_no_progress_limit

Task:
  max_total_elapsed
  max_column_visits

Recovery:
  max_attempts
  max_elapsed

Command:
  default_timeout
  platform_max_timeout
  max_stdout_bytes
  max_stderr_bytes
  process-tree cancellation
```

注意：不要只加 `max_iterations=20`，否则会把复杂但有真实进展的 Agent 粗暴杀掉。

---

# 8. P0-6 — Supervision / Mailbox / Conversation Recovery 不闭环

## 8.1 关键代码

- `app/v1/runtime.py`
  - `RuntimeSupervisor._loop()`
  - `LeaseKeeper`
- `app/v1/conversation.py`
  - governance dispatcher
  - Conversation job lifecycle
- `app/v1/services/mailbox.py`
- `app/v1/capabilities.py`
  - task reopen/retry/resume control
- `app/v1/store.py`
  - mailbox / conversation jobs / leases
- `app/v1/api.py`
  - health/status

## 8.2 子问题

### A. RuntimeSupervisor 一次异常可永久死亡

如果循环顶层没有 exception boundary：

```text
一次 DB/脏数据异常
→ 唯一 supervisor thread exit
→ 所有 workflow 停止调度
```

### B. Health 不能硬编码 running

必须真实检查：

```text
thread.is_alive()
dispatcher_task.done()
last_successful_tick
last_exception
lease renewal heartbeat
```

### C. LeaseKeeper 也必须自恢复/上报失权

续租线程异常不能只是静默退出；主执行必须立刻获知失去 ownership 并停止新副作用。

### D. Mailbox 被架构要求恢复 Task，但 capability gate 又禁止 control mutation

真实事故中 Mailbox 尝试：

```text
task.reopen
```

被 `ConversationMutationDisabled` 阻止。

建议使用 allowlist，而不是按：

```text
effect_kind in {write, process, control}
```

全部禁止。

Mailbox 至少应可操作已有 Task：

```text
task.inspect
task.retry
task.reopen
task.pause
task.resume
task.cancel
```

但不能扩大工作图：

```text
loop.apply
workflow.publish
create new task plan
```

### E. Conversation dispatcher orphan running job

需要检查：

```text
conversation job running
agent lease expired
```

是否存在自动 reclaim。不能只在 App restart 时处理。

### F. restart 不应把 autonomous supervision 直接永久 fail

Mailbox / scheduled review 应考虑 durable redelivery/requeue，而不是进程重启后统一变 failed。

---

# 9. P0-7 — Capability Execution Truth 可被 LLM 自定义

## 9.1 问题定义

当前 command capability 接受：

```text
success_exit_codes
```

由 Agent 调用参数决定。

关键代码：

- `app/v1/capabilities.py`
  - command schema
  - `accepted = ... args.get("success_exit_codes")`
- `app/v1/files.py`
  - process result
- `app/v1/execution_ledger.py`
- `app/v1/services/completion_admission.py`

## 9.2 真实事故

目标日志中：

```text
exit_code = 1
ModuleNotFoundError: No module named 'yaml'
```

因为 Agent 传入：

```text
success_exit_codes=[0,1,2,3,4]
```

Runtime 最终记录：

```text
ok=true
status=completed
```

随后这个“假成功”还成为 successful action evidence。

## 9.3 原则

机器事实必须由 Runtime 决定。

默认：

```text
exit code 0 = process success
nonzero = process failure
```

如果某个具体工具确实存在：

```text
exit 1 = valid semantic result
```

应该定义在：

```text
registered capability contract
workflow definition
trusted adapter
```

而不是由一次模型 Tool Call 临时声明。

## 9.4 推荐进一步拆分

```text
ProcessResult:
  exited_normally
  exit_code
  timed_out
  killed
  stdout/stderr

SemanticResult:
  success/failure
  classification
```

先保留机器事实，再由可信 contract 做业务解释。

---

# 10. P0-8 — Artifact Provenance 不是 Immutable Ledger

## 10.1 问题定义

当前 artifact 更像：

> `(project_id, path)` 的当前值表

而下游却把它当：

> 历史不可变的 Task output ledger

这是事实模型冲突。

## 10.2 关键代码

- `app/v1/repositories/artifact_repository.py`
  - `accepted_dependency_artifacts()`
- `app/v1/repositories/schema_repository.py`
  - artifact schema / unique constraints
- `app/v1/store.py`
  - artifact insert/upsert
  - dependency artifacts
- `app/v1/runtime.py`
  - `_input_for()` / dependency input
- terminal evidence 相关 store 方法

重点查找：

```sql
UNIQUE(project_id, path)
ON CONFLICT(project_id, path) DO UPDATE
```

如果 upsert 同时覆盖：

```text
id
task_id
run_id
sha256
```

就意味着旧 provenance 被新写入改写。

## 10.3 典型问题

```text
Task A done
→ 写 src/foo.py
→ artifact 归属 A

Task B 后续修改 src/foo.py
→ 同一路径 upsert
→ artifact row 改成归属 B

Task C 依赖 Task A
→ 再查询 A accepted artifacts
→ foo.py 可能已经不存在于 A provenance
```

## 10.4 正确模型

建议拆：

```text
v1_artifact_versions
  id immutable
  project_id
  logical_path
  producer_task_id
  column_run_id
  attempt_id
  sha256
  size
  created_at

v1_artifact_heads
  project_id + logical_path
  → current_artifact_version_id
```

Task dependency 保存的是 immutable version ID，而不是 workspace 当前 path row。

terminal artifact 同样应该 immutable，例如：

```text
.devwerk/terminal/{task_id}/{column_run_id}-{attempt_id}.json
```

CAS 只决定哪个版本成为 canonical terminal evidence。

---

# 11. P0-9 — Loop 不是 Durable / Versioned Runtime Package

## 11.1 关键代码

- `app/v1/loops.py`
  - `LoopCatalog`
  - `_records()`
  - `get()` / list logic
- `app/v1/store.py`
  - loop binding
  - workflow publish
  - `get_project_loop_assets()`
- `app/v1/runtime.py`
  - loop assets injection
- `app/v1/repositories/schema_repository.py`
  - source_loop_key/version/digest

## 11.2 子问题 A：只 pin digest，没有 pin bundle snapshot

Project 保存：

```text
loop_key
loop_version
loop_digest
```

但运行时仍重新读取当前磁盘 Loop。

如果磁盘 Loop 被升级：

```text
Project 绑定 v1 digest
↓
filesystem 变成 v2
↓
旧 Project 再执行
↓
读取 v2
↓
digest mismatch
↓
运行失败
```

这说明所谓 version pin 只是一致性检查，不是 durable versioning。

## 11.3 子问题 B：一个坏 Loop 可以毒死整个 Catalog

如果 `LoopCatalog._records()` 扫描所有目录并在任一 meta 解析失败时整体抛异常：

```text
一个无关 loop.meta 损坏
↓
list_loops() 整体失败
get_loop(other) 也失败
```

Conversation/Workflow 都可能受影响。

必须做到 per-loop fault isolation。

## 11.4 子问题 C：loop.apply 需要原子化

如果流程是：

```text
create workflow plan
publish revision
insert project loop binding
```

分多个事务，任何中途异常都可能产生 half-applied Project。

更危险的是 retry 时如果因为“Project 已存在 Workflow”而拒绝，就形成不可自动修复状态。

## 11.5 推荐设计

Loop apply 时生成 immutable package snapshot：

```text
loop_package_id
loop_key
version
digest
meta snapshot
workflow definition snapshot
assets snapshot
created_at
```

Project/Workflow Revision 只依赖 `loop_package_id`。

磁盘 Catalog 只是：

> 新 Project 创建时的 source registry

而不是旧 Project 每次运行时的事实源。

---

# 12. P0-10 — Task Dependency rerun lineage 可能永久断链

## 12.1 问题定义

Task rerun 后旧失败 Task 可拥有：

```text
resolved_by_task_id = successor
```

但 Dependency Resolver 并没有在所有路径统一 follow successor chain。

## 12.2 关键代码

- `app/v1/services/scheduler.py`
  - dependency admission
  - `_resolve_planned_dependencies()`
  - dispatch eligibility
- `app/v1/services/task_graph_admission.py`
- `app/v1/store.py`
  - rerun / `resolved_by_task_id`
  - task dependency serialization
- `app/v1/repositories/artifact_repository.py`
  - dependency artifact lookup
- `app/v1/runtime.py`
  - dependency context

## 12.3 典型场景

```text
Task A failed
Task B 已经物化，并直接依赖 A.id

rerun(A) → A2
A2 done
A.resolved_by_task_id = A2
```

如果 Scheduler 只对：

```text
task-plan:...
```

这种 symbolic dependency 做 successor resolution，而对已经落库的 `A.id` 只读 `A.status`：

```text
A.status = failed
```

那么 B 永远 waiting dependency。

同时 dependency context/artifacts 也仍可能指向旧 A。

## 12.4 建议修改

建立唯一：

```text
CanonicalDependencyResolver
```

输入任何 Task ID：

```text
follow resolved_by_task_id chain
→ detect cycle
→ canonical current Task
```

以下所有地方只能调用同一个 resolver：

```text
scheduler eligibility
dependency context
accepted artifacts
mailbox/supervision
UI task status
rerun decision
```

不要每个模块自己理解 dependency lineage。

---

# 13. 现有旧 P0 如何合并

为了后续 Review 不重复计算，建议将旧问题按下面方式归并。

## 13.1 Terminal Artifact stale worker race

归入：

```text
P0-2 Execution Ownership / Fencing
+
P0-8 Immutable Artifact Provenance
```

不要只修文件名覆盖；真正问题是 stale worker 仍有副作用能力。

## 13.2 LeaseKeeper thread death

归入：

```text
P0-2 Ownership
P0-6 Supervision
```

## 13.3 Provider ConnectionError 被当 permanent

归入：

```text
P0-4 Failure Taxonomy
```

## 13.4 command timeout/output unlimited

归入：

```text
P0-5 Liveness
```

## 13.5 Mailbox 无法 reopen

归入：

```text
P0-6 Supervision / Recovery
```

---

# 14. 推荐代码 Review 顺序

不要按文件 alphabetical review。建议按执行链 review。

## Phase A — Completion / Execution Truth

依次阅读：

```text
app/v1/agent_runner.py
app/v1/agent_tool_execution.py
app/v1/execution_ledger.py
app/v1/completion_protocol.py
app/v1/services/completion_admission.py
app/v1/runtime.py
app/v1/capabilities.py
app/v1/files.py
```

要回答：

```text
一个 Tool Call 如何变成 execution fact？
谁定义 success？
谁决定它是否属于 completion evidence？
completion 被拒后 Agent 怎样继续？
```

## Phase B — Ownership / Recovery

```text
app/v1/services/scheduler.py
app/v1/services/recovery_manager.py
app/v1/runtime.py
app/v1/store.py
app/v1/execution_ledger.py
app/v1/capabilities.py
```

要画完整 race：

```text
claim
renew
expire
recover
reclaim
retry
pause
cancel
finish
await
```

并在每个状态问：

> 旧 Attempt 是否还能产生副作用？

## Phase C — Durable Truth

```text
app/v1/repositories/artifact_repository.py
app/v1/repositories/schema_repository.py
app/v1/store.py
app/v1/loops.py
app/v1/services/task_graph_admission.py
app/v1/services/scheduler.py
```

验证：

```text
Artifact history
Loop version history
Task rerun lineage
Dependency resolution
```

是否不可变且可重放。

## Phase D — Autonomous Supervision

```text
app/v1/conversation.py
app/v1/services/mailbox.py
app/v1/runtime.py
app/v1/api.py
app/v1/capabilities.py
```

验证：

```text
一个 failure 出现
↓
谁发现
↓
谁有权恢复
↓
恢复失败怎么办
↓
supervisor 自己死了谁发现
```

---

# 15. 建议新增的 Failure-Interleaving Review Checklist

这不是要求“补测试覆盖率”，而是后续人工 Review / 故障注入必须逐项证明架构成立。

## 15.1 Provider

```text
LLM request 前断网
request 已发送但响应未收到
HTTP 429
HTTP 500
DNS failure
connection reset
timeout
invalid API key
invalid model
```

确认每一种：

```text
failure origin
retryability
Task 最终状态
是否重复副作用
```

## 15.2 Capability

```text
side effect 前 crash
side effect 成功后 receipt 前 crash
receipt 成功后 LLM response 前 crash
non-zero exit
command hang
infinite stdout
```

## 15.3 Lease / Ownership

```text
renew_lease exception
lease expires during command
old Worker 在 reclaim 后再次 write
pause 与 finish 同时
retry 与 claim 同时
cancel 与 claim 同时
```

## 15.4 Await

```text
两个 supervisor 同时发现 due await
poll 成功后 crash
cleanup 成功后 crash
resume Agent 前 crash
waiting 期间另一个 conflict Task 开始运行
```

## 15.5 Completion

```text
context-only success
多 exploration action + 一个 business action
completion repeated rejection
伪 action 制造 progress
LLM 提交错误 internal evidence ID
```

## 15.6 Durable State

```text
同一路径被三个 Task 连续修改
旧 Task 的 accepted artifact 是否仍存在
Loop v1 Project 在 Loop v2 发布后是否仍可运行
failed Task rerun successor 后所有 downstream 是否自动解锁
```

---

# 16. 最小可落地修复路线

如果目标不是立即“完美重构”，而是尽快达到 1.0 可稳定运行基线，我建议按下面三个里程碑推进。

## Milestone 1 — Runtime Kernel correctness

必须同时解决：

```text
P0-1 Completion Truth
P0-4 Failure Taxonomy
P0-2 Fencing 基础
P0-3 Effect ID / Receipt 基础
P0-7 Command Truth
```

### 完成标准

目标事故重新跑时必须满足：

```text
真实回测成功
→ Runtime 自动确定 completion evidence
→ 不需要模型枚举内部 ID
→ 不产生无意义 evidence command
→ Task 正确 done
```

并且：

```text
Runtime自身错误 ≠ Task business failed
```

---

## Milestone 2 — Availability / Recovery

```text
P0-5 budgets / timeout
P0-6 supervisor self-healing
Await claim/lease
Control CAS
Conversation mutation arbitration
```

### 完成标准

必须能证明：

```text
任何 worker/supervisor/provider 临时故障
不会让系统静默永久停摆；
不会产生两个有效 side-effect owner；
不会无限占用 Worker。
```

---

## Milestone 3 — Durable Workflow Correctness

```text
P0-8 Artifact immutable versions
P0-9 Loop package snapshot
P0-10 Canonical dependency lineage
```

### 完成标准

历史 Project/Task 必须可长期重放：

```text
后续文件修改不改变旧 Task provenance
Loop 升级不破坏旧 Project
Task rerun successor 能自动修复所有 dependency path
```

---

# 17. 暂不建议做的事情

在上述 P0 修复完成前，不建议优先投入：

```text
大规模拆 Store 文件
UI 重构
复杂 RBAC
沙箱强化
多机高可用
更多 Agent 类型
更多 Loop DSL 功能
纯覆盖率提升
```

这些都无法解决目前最核心的 Runtime correctness 问题。

尤其不要用：

> “测试很多 / 测试通过”

作为某个 Runtime contract 正确的证据。

应该要求：

> 这个 contract 在 Truth / Ownership / Failure / Liveness 四个 invariant 下是否自洽。

---

# 18. 最终架构判断

当前 DevWerk 最大的优点是：

```text
Task / Column Run / Attempt / Agent Run
Workflow Revision
lease / CAS
await handle
execution receipt
recovery
artifact/evidence
```

这些核心概念已经基本齐全，因此不需要重新设计整个产品。

目前主要缺失的是这些概念之间的 Runtime Kernel contract：

```text
                    Agent
                      │
                 intent/outcome
                      │
             ┌────────▼────────┐
             │     Runtime     │
             │                 │
             │ Truth      P0   │
             │ Ownership  P0   │
             │ Failure    P0   │
             │ Liveness   P0   │
             └────┬────────┬───┘
                  │        │
             Capability  Workflow
                  │        │
                  ▼        ▼
             Real Effect  Task State
```

因此下一轮修改的基本原则应该是：

> **LLM 负责做决定，Runtime 负责维护事实。**  
> **Scheduler 不只维护 DB ownership，还必须维护副作用 ownership。**  
> **Runtime 自己的故障不能伪装成业务失败。**  
> **每条执行链最终必须有明确的前进或终止上界。**

只要后续代码 Review 始终围绕这四条原则展开，当前列出的 P0 就可以被系统性解决，而不是逐个打补丁。
