# DevWerk Runtime P0 复审 — 2026-09-09

## 结论与范围

**当前代码仍存在六项按本项目 v1 发布标准应阻断发布的问题。**

这里的 P0 指：使 conversation → workflow → task → delivery 主链路出现静默漏执行、错误完成判定、无法自行恢复的排队阻塞、过期执行者修改状态，或逻辑阶段无法收敛的架构缺陷。不是说每个请求都会触发，也不把安全、多机部署和成本优化算入本轮范围。

已阅读原始问题清单 `DEVWERK_P0_Runtime_Review_2026-09-08.md`，以当前工作区代码重新验证；未采用 docs 下任何历史测试结果作为依据。下面六项均有本轮独立复现。前五项分别验证实际 Runtime、队列、控制操作或生产完成准入逻辑；异步 capability 使用符合当前注册接口的最小测试 adapter，并非声称当前同步 command 工具会自动走该分支。

本轮只新增复审文档和诊断脚本，未修改生产代码、真实项目文件或历史数据库。没有据此认定指定历史 Task 的失败一定由这六项中的某一项导致，也没有声称穷尽所有缺陷。

## R1 — P0：Await 后的新操作被错误当成历史重放，Task 假完成

代码：

- `app/v1/agent_tool_execution.py:59`：每个工具执行器新建空的 effect_occurrences。
- `app/v1/agent_tool_execution.py:157-162`：按 ColumnRun、参数摘要和本次内存计数生成执行 key。
- `app/v1/runtime.py:914-925`：Await continuation 沿用 ColumnRun，但重新进入 Agent 执行。
- `app/v1/capabilities.py:151-157`：命中 completed receipt 就返回旧结果，不执行 handler。

**触发**：一个阶段先执行命令追加 x，等待 timer，恢复后按任务要求再次执行同一命令追加 x。两次是合法的新业务操作。

恢复后 occurrence 重新从 1 开始，与等待前的命令 key 相同。第二次命令被吞掉，模型收到成功结果，完成准入也没有发现漏执行。

本轮实际结果：

```json
{"task_status":"done","expected":"xx","actual":"x"}
```

**修复方向**：持久化操作意图及 continuation 执行位置。恢复同一未结算意图必须复用身份；模型在 continuation 中新发出的合法操作必须获得新身份。仅按参数去重、重置计数或者重试时一律换 key 都不能同时保证这两个条件。

**修复验收**：正常同参重复调用应执行两次；故障后恢复已结算意图只能执行一次；未结算外部副作用应明确进入待核对状态。

## R2 — P0：无关成功命令可被模型声明为业务失败的修复

代码：`app/v1/services/completion_admission.py:62-92`，尤其 79-85；协议入口 `app/v1/completion_protocol.py:173`。

**触发**：业务交付命令失败后，模型执行一个无关但成功的命令，在 failure_resolutions 中将后者作为前者的修复证据。

准入只验证证据存在、成功、发生在失败之后，以及 capability / effect_kind 相同，没有验证成功操作是否修复原业务失败。所有 project.command.run 都满足后两项相等。

本轮直接调用生产 CompletionAdmissionService：

- 失败：`python deliver.py`，exit_code=1。
- 成功：`python -c "print('done')"`，exit_code=0。
- 模型声明后者修复前者。
- 实际：`{"accepted":true,"failed_delivery_was_reexecuted":false}`。

这是生产完成准入层的确定性复现，未把它描述成一次完整真实项目交付运行。该准入允许未修复的业务失败被清除，足以破坏“执行事实控制完成”的边界；无需恶意模型或安全攻击。

**修复方向**：修复关系必须由操作身份、adapter 的可验证修复协议或 workflow 的业务验收建立；模型的 reason 和同名 capability 不足以证明修复。允许不同命令修复失败时，仍须有针对原失败的独立验收，不能简单禁止所有参数不同的修复。

**修复验收**：失败交付 + 无关成功命令应拒绝完成；确实修复后的重试或有业务验收的替代路径应通过。

## R3 — P0：一次队列领取异常就能让整个项目后续对话无法推进

代码：

- `app/v1/conversation.py:186-215`：先从内存队列弹出 job，settled 默认 True；claim 抛异常后 finally 仍移除 pending 标记。
- `app/v1/conversation.py:138-141`：周期 dispatcher 只接收 enqueue_governance_jobs 返回的新 job。
- `app/v1/store.py:748-753`：已有 queued/running job 时不创建新的治理 job。
- `app/v1/store.py:839-848`：更早的 queued job 阻止后续 job 被领取。

**触发**：claim_conversation_job 遭遇一次 SQLite OperationalError，例如数据库锁等待超时。异常后数据库的 job 仍 queued，但内存队列已丢失它。正常 dispatcher 不重新扫描这些持久化 queued job。

本轮故障注入结果：

```json
{"old_job_status":"queued","in_memory_queue":0,"enqueued_after_dispatcher_tick":[],"new_job_claim":null}
```

后续用户消息不能越过这个旧 job；没有服务内自动收敛路径，需重启恢复或人工介入。外层循环捕获异常并继续运行不能修复丢失的队列项。

**修复方向**：数据库 queued 状态应成为调度来源，周期补领/补入队；只有持久化状态已确认结算时才丢弃内存项。领取异常和取消都必须可恢复。

**修复验收**：分别在领取前、领取事务中、持久化创建后但内存入队前注入异常，恢复后无需重启即可继续处理旧 job 与后续消息。

## R4 — P0：失去 Conversation 所有权后，旧执行者仍可改变 Task 控制状态

代码：

- `app/v1/capabilities.py:134-173`：所有权检查与 handler 调用分离，handler 后检查已太晚。
- `app/v1/capabilities.py:179-188`：effect_guard 覆盖 receipt 结算，不能撤销已经独立提交的 handler。
- `app/v1/capabilities.py:1995-2001`：task.resume 不向 store 传递发起 Conversation 的所有权条件。
- `app/v1/services/recovery_manager.py:450-461`：更新事务校验目标 Task 版本，不校验发起者的 Conversation fencing 身份。

**触发**：旧 Conversation 通过前置检查；领取 receipt 后租约过期并由新执行者接管；旧执行者继续执行 task.resume。

本轮在这个确切间隙进行故障注入，真实 handler 执行结果：

```json
{"reported":"ExecutionOwnershipLost","old_job_status":"failed","task_control_after_stale_handler":"active"}
```

表面上调用已报失去所有权，任务实际上已经被旧执行者从 paused 改为 active，可被 scheduler 执行。目标 Task 的 CAS 与发起者所有权检查不能相互替代。

**修复方向**：将发起者 fencing 条件传入所有控制状态写入，在同一个提交事务内验证；盘点其他控制及状态写入 handler。不能只再加一次 handler 前检查或只保护 receipt 更新。

**修复验收**：在检查后、handler 前强制接管，旧执行者的控制写入必须零生效；新 owner 的合法操作正常生效。

## R5 — P0：异步副作用已成功，恢复 ledger 却仍读取旧 awaiting，完成时崩溃

代码：

- `app/v1/runtime.py:907-912`：外部成功后将原 receipt 改为 completed。
- `app/v1/runtime.py:731-756`：恢复 ledger 读取原 tool invocation 的 result，没有关联 receipt 最新结算事实。
- `app/v1/execution_ledger.py:120-129`：未 completed 的副作用计入未解决项。
- `app/v1/services/completion_admission.py:99-105`：旧 awaiting 结果中的 error 为 None，继续调用 .get，触发 AttributeError。

**触发**：Agent capability 返回 awaiting；poll 成功；Agent 恢复后正常提交 column.complete。这里使用最小异步 process adapter 覆盖正式支持的扩展协议，普通 column.await timer 本身不是该故障的充分条件。

本轮结果：

```json
{"external_receipt":"completed","task_status":"waiting","control":"paused","exception":"AttributeError: 'NoneType' object has no attribute 'get'","completion_rejections":0}
```

外部工作确实已完成，但 Runtime 无法正常完成该阶段。即使只修 None 解析，旧 awaiting 仍会成为未解决操作并拒绝完成，所以这不是单纯判空问题。

**修复方向**：建立 invocation → operation/receipt 的稳定关联；ledger 使用当前已结算的执行事实，并保留历史事件。显式区分 pending、failed、completed；修正可空 error 解析。

**修复验收**：异步成功后无需重复启动外部操作即可完成；异步失败保留失败；重复恢复不改变结算事实。

## R6 — P0：反复 Await 可以绕过逻辑阶段的全部执行上限

代码：

- `app/v1/runtime.py:727-729`：每次新建 control 都重新计算 wall deadline。
- `app/v1/agent_runner.py:38-54`：AgentRun 重新初始化迭代及工具调用计数。
- `app/v1/runtime.py:880-885`：恢复后再次 Await，继续复用同一 ColumnRun / Attempt，不消耗新的阶段访问次数。

**触发**：模型在每次 timer 恢复后又发出 column.await，没有业务进展。不是指某个正常外部操作等待时间较长。

将 agent_max_iterations、agent_max_tool_calls、max_column_visits、max_recovery_attempts 均设为 1，首次执行后连续恢复六次，实际：

```json
{"status":"waiting","control":"active","v1_column_runs":1,"v1_column_attempts":1,"v1_agent_runs":7,"v1_await_handles":7}
```

这些单次/单对象计数均未超限，但同一逻辑阶段不断唤醒模型并扩充历史，不能保证小任务结束或向监督层交付明确失败。P0 理由是任务收敛性与后续工作推进，不是 token 费用。

**修复方向**：把累计执行预算、连续无进展恢复计数和期限放到持久化 ColumnRun/Task 层，覆盖所有 continuation；长期外部等待与反复发起新等待应分别处理，不能每次唤醒都获得全新额度。

**修复验收**：无进展 re-await 达到限制后进入明确终态或监督；正常等待不消耗模型执行时间；有业务进展的有限 continuation 可继续。

## 对原清单的判断

原文的完成真实性、稳定副作用身份、ownership/fencing、监督队列恢复和执行有界性，当前仍不能视为关闭。R1、R2、R5 还说明 receipt、invocation、ledger 和 completion 之间没有形成一致的执行事实链。

本轮没有新增证据证明原文其余每一项都仍为 P0，也没有把“本轮未复现”当成已彻底修复。

建议修复次序：

1. R1 / R2 / R5：先统一操作身份、执行结算与完成准入，避免继续产生错误完成记录。
2. R3 / R4：补齐持久化调度恢复和提交时所有权边界。
3. R6：补齐跨 Await 的逻辑阶段预算与无进展退出。

每组仍应遵循：先写具体方案与不变量，再改代码，再进行对应故障回归。当前不能仅凭已有正常流程测试作为 v1 发布依据。

## 本轮复现方式

脚本：`scripts/review_p0_runtime_2026_09_09.py`。

从仓库根目录执行：

```powershell
.\venv\Scripts\python.exe scripts/review_p0_runtime_2026_09_09.py
```

脚本使用临时 SQLite 和临时工作目录、确定性模型返回、一个最小异步 adapter；没有请求真实模型服务，没有读取历史测试报告。tests.helpers 仅用于构造合法 workflow/plan，不引用其测试结论。

脚本输出六项观察值；退出码 0 仅表示诊断完成，**不是缺陷已修复或回归通过**。该脚本已在本轮从保存路径执行，输出与上文一致。

