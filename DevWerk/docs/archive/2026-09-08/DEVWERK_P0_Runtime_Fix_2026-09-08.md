# DevWerk v1 发布前 Runtime 修复方案

本轮以工作区当前代码为基线，保留已有未提交修改。目标是 Web 对话→Loop/Workflow→小任务→执行→交付闭环，不扩大到权限、计费、沙箱或多机高可用。原 Review 的推演需要落实为可验证的执行约束，不能以既有测试数量代替正确性。

最终状态（2026-09-09）：本轮代码修复与确定性回归完成，265 passed；真实数据库副本迁移、只读事故回放完成。未重启线上服务或重新执行目标项目，历史 Task 仍为 failed。以下实施顺序与中间失败记录保留为修复过程，最终证据和恢复入口见文末。

## 事故核对

只读检查 `data/devwerk.db`：`tsk_cfc85c0496834a2baae81d9b3eb055f0` 在 `analyze` 失败；Column Run 为 `run_9d297b903a774428a9c6e07b23a9a81a`，Agent Run 为 `arun_c33282190bfc44bb9830add8003e1e5c`，31 iterations、36 tool calls，异常为 `CompletionProtocolStalled`，类别为 `protocol_permanent`。这支持“运行协议失败”，不能据此判断业务失败，更不能直接把历史 Task 改成 done。

代码确认 `_column_completion_contract()` 使用 model evidence，Admission 强制包含所有 successful action IDs；相同拒绝通过新增工具调用可改变 progress。这构成原 Review 描述的反馈循环。

## 修改设计与验收

| 问题 | 本轮实现方向 | 验证依据 |
|---|---|---|
| P0-1 完成事实 | Runtime 收集 receipt；模型只需 outcome/output/summary；默认允许纯上下文阶段完成，Workflow 可显式 required/forbidden；拒绝次数有独立上界 | 多动作、纯上下文、旧 evidence 参数、伪造 ID、重复拒绝 |
| P0-2 执行所有权 | state_version 作为 fencing generation；传播 Task/Attempt ownership 到能力调用和命令取消；控制操作 CAS；Await claim、续租、资源保留 | stale write、租约异常、pause/finish、重复 await |
| P0-3 重放 | Invocation 与逻辑 effect 分开；恢复复用确定完成的 effect，未知副作用阻塞而不盲目重放；保留 ledger | 已完成 effect 后 provider 断线、执行中断 |
| P0-4 失败分类 | provider/tool/infrastructure transient 有界 recovering；协议、运行一致性及永久错误进入 recovering+paused，标记 blocked_runtime/intervention_required；failed 留给明确业务终态 | 连接异常、契约失败、恢复耗尽 |
| P0-5 活性 | Agent 时间/调用上界、Column visits、恢复次数/时间、命令超时/输出上限/进程树终止 | 挂起 provider、无限输出、超时进程、Workflow 环路 |
| P0-6 监督 | supervisor/dispatcher tick 隔离异常；真实 health；治理轮只开放已有任务恢复工具 | DB 单次异常、Mailbox reopen、discussion-only 限制 |
| P0-7 命令事实 | 命令 exit code 成功规则由平台定义，模型不能把 exit=1 改成成功；失败 receipt 保存原始输出 | 非零退出与模型自定义 success_exit_codes |
| P0-8 工件历史 | 追加版本和不可变内容快照；工作路径是当前位置，历史事实按版本读取；terminal 路径包含执行身份 | 多任务覆盖同路径、历史 hash 和内容 |
| P0-9 Loop 持久化 | apply 原子事务；持久化包快照含 assets；Catalog 单个损坏隔离；旧 binding 仅在 digest 一致时回填 | apply 故障回滚、磁盘升级/删除、坏 meta |
| P0-10 依赖谱系 | 所有 ID 经统一 successor resolver，检测环与跨项目链；调度和 context/artifact 共用 | 已物化下游依赖 failed A，A2/A3 done 后解锁 |

## 兼容与恢复原则

继续使用现有 Task/Column/Attempt/Agent 状态体系；运行阻塞通过 `recovering`、`control_state=paused` 和结构化失败字段表达，避免额外引入一套前端未知状态。业务 `failed`、取消、显式 rework 仍遵循声明的 Workflow。

对普通 shell 命令无法凭参数 hash 宣称 exactly-once：恢复时只复用已提交 receipt；执行后尚未提交的结果必须要求核对。重复业务步骤、下一次 Column visit 和显式新任务不能被错误去重。

历史数据库与项目产物保持原状；本轮使用独立临时数据库进行迁移、故障注入与回放。目标任务恢复应保留历史失败记录，经 Conversation 的 task.reopen 能力从失败阶段恢复，运行确认后才能宣告交付。无法从旧工作目录找回已覆盖文件时，不伪造历史快照。

## 续修实施顺序（先方案、再修改、最后回归）

以下内容先于续修代码落地，当时补丁尚未完成验收；最终验收记录位于文末。

1. **完成协议**：保留失败动作检查，不采用“任意后续成功覆盖失败”的放宽规则。拒绝反馈应带具体 capability/arguments，提示成功重试该操作或走声明的失败/rework outcome；Runtime 自动关联同一操作的成功重试。移除已废弃的 success-ID 全量校验残留。历史任务第 9 个调用 `python -c "import yaml; print(yaml.__version__)"` 曾失败，而主回测在第 13 个调用成功；回放要覆盖诊断修复，不能直接宣告第一次 completion 已可通过。
2. **所有权收尾**：Await 持有资源直到等待结束，恢复前领取租约；恢复、cleanup、settlement 必须受同一 generation 约束。补齐 Conversation 直接写文件/运行进程与 Scheduler 的互斥，以及 Conversation lease 丢失后的工具阻断。文件写入用短事务将校验、落盘和记录串联。命令启动和取消都检查执行权。
3. **重放收尾**：自动恢复保留同一 Column Run，Attempt 追加；副作用 key 稳定于这一 Column visit。明确完成可复用，started/unknown 不自动重放。恢复上下文必须包含已执行 ledger。显式重试、Await continuation 和后续 visit 分清边界，不能把重新执行错误当成已执行。
4. **监督与活性收尾**：验证 LeaseKeeper 异常撤销旧执行；修复 Await 异常的非业务终态记录；检查 supervisor/dispatcher 健康状态、队列上界与 orphan 状态恢复；恢复次数/时间耗尽后只阻塞一次并通知。
5. **持久事实收尾**：验证工件历史版本读取、hash 对应旧内容；Loop apply rollback 测试使用真实合法 bindings；Catalog 坏 metadata 隔离；依赖谱系覆盖多代 successor、已物化边、环和缺失节点。旧包无法匹配原 digest 时保留明确阻塞，不填入新包冒充历史。
6. **回归**：先运行新增 P0 故障场景，再运行原有契约套件。只更新与新规范冲突的断言，保留业务失败、错误传播、未授权讨论轮不能修改等检查。测试过程中不同时修改生产文件，避免 inspect/source 读取到不同版本。最后用真实数据库副本/只读记录回放事故，记录没有运行的真实外部步骤。

## 中间验证记录（非最终验收）

第一次补丁回归 221 passed / 8 failed：包含 Await 尚未领取租约、旧失败分类/exit-code 契约断言，以及运行测试时编辑文件导致的一处 source introspection 错位。后续 Await 路径已补 claim 和续租，尚待最终全量回归。

新增 P0 场景已有 12 项通过；Loop 两项测试输入误用了目录名/空 bindings，须先修正夹具，再验证真正的 rollback 与持久化行为。原 Architecture/Loop 子集共 35 项通过（不含上述两项失败）。

## 验证记录

2026-09-08 续修回归：247 passed / 3 failed（94.03s）。三项待更新的旧断言分别是：覆盖后的工件必须排除（现已有旧内容快照）、Provider retry 必须新增 Column Run（现应追加 Attempt）、失联 receipt 必须 failed（现应保留 started/未知结果）。更新时增加对应旧字节、Attempt 数和禁止盲重放的检查。

只读源库备份至临时 SQLite 后执行新 schema：`integrity_check=ok`，`foreign_key_check=0`，291 个现有工件均迁移为版本记录；目标 Loop assets 可读取（0 个），目标 Task 仍为原 failed。副本清理出现 WinError 32，确认现有 `with store.connect()` 只结束事务、不关闭 SQLite 连接。新增修复项：普通连接退出上下文时关闭，嵌套事务借用连接不关闭 owner；以文件可删除和原子 Loop rollback 同时验证，避免引入提前关闭/提交。

下一轮额外验收：真实副作用完成后 Provider 断线，恢复不重复追加文件；未知 receipt 恢复不启动命令；Await 领取互斥和资源保留；Workflow 环路达到 visit 上界转运行阻塞。

上述新增回归已 24 passed（12.49s）。收尾复查增加以下具体修复后再统一回归：

- 租约反复失联、外部作业明确可恢复失败，同样按 Task 的失败 Attempt 数/经过时间停止自动重试。轮询本身的网络/工具异常不证明外部作业失败，保留 pending Handle，记录有界重试 checkpoint；不得重新提交外部作业。耗尽后 paused，并允许 resume 继续原 Handle。
- Await 已取得外部结果后的 Agent 暂时故障也保留已提交 checkpoint；阻塞时保留 waiting 和 Handle 以支持恢复，不能留下 recovering Task 对应的孤儿 pending Handle。完成 Await receipt 与 Conversation Job 的最终落库补所有权检查。
- 缺失、跨项目和有环的 successor 链仅阻止依赖它的 Task，不能让无关 Task 全部停止调度；调度入口和展示采用同一解析规则。
- 命令进程树回归增加父进程提前退出、子进程仍持有输出句柄的场景；若发现残留，补齐进程组生命周期后再验收。
- `auto_resume_previous_tasks=false` 的启动暂停也保留 waiting Task 和 pending Handle，仅暂停调度；恢复时继续等待原外部作业，不能取消 Handle 后重提作业。

修改前基线：本机隔离配置运行现有测试，229 passed（87.18s）。这只是用于识别回归的基线。修复后的场景和实测结果在实施结束时补充。

## 2026-09-09 中断后续修

已核对：上一轮最后测试为 28 passed / 2 failed（19.81s），两个失败分别是启动暂停把 waiting 改成 pending，以及 Windows 父进程退出后子进程继续写入 `late.txt`。当时修改调用被套餐限额拦截，两个修复均未落盘。

实施顺序：先保留 startup hold 下的 Task/Column/Attempt waiting 状态和 pending Handle，再用原 Handle 验证 resume；Windows 命令增加 Job Object，启动器在收到握手前不执行目标命令，先加入进程组再启动目标，关闭 Job 时回收整棵树。目标 argv 保持参数数组，命令退出码由启动器原样返回，不引入 shell 拼接。依据 [Windows Job Objects 官方文档](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。随后运行 P0 场景、全量契约测试、真实数据库副本迁移/事故回放，并写明未实际执行的外部项目步骤。

首轮续修 P0 回归 30 passed（20.12s），全量 257 passed / 2 failed（140.91s）。接着修正 startup hold 的历史兼容边界：仅有 pending Handle 的 waiting Task 保留等待，否则按旧规则暂停至 pending；失去租约的终态写入测试应在 prepare 阶段即断言拒绝，并保留落库 CAS 检查。复查还发现 RuntimeExecutionError 使用 `category` 而 Await 读取 `error_category`，补统一属性并验证结构化 transient ToolResult 仍可恢复。符号依赖的旧单代 SQL 改用现有 successor resolver，补三代 successor 的调度、context 和 artifact 一致性测试。

上述相关回归 65 passed（43.86s）。最后检查升级重放兼容：比较历史 command receipt 参数时也规范化已废弃的 success_exit_codes 和默认 cwd，避免成功旧 receipt 被误判为参数冲突；未知结果仍禁止重放。Capability handler 返回后、提交 receipt 前再次检查执行权，write/process handler 已被调用后若抛出异常或返回不合法结果，保留未知结果，不把“已经发生的副作用但输出校验失败”误记为可重试失败。补对应故障注入后冻结生产文件，跑最终全量回归。

## 最终实现与验收

| 范围 | 已落地的行为 | 核心文件 |
|---|---|---|
| P0-1 | Runtime 自动关联执行事实；默认允许基于已加载上下文完成；旧 evidence_ids 不再控制事实；未修复动作反馈精确操作；completion 拒绝次数独立有界 | `completion_protocol.py`、`execution_ledger.py`、`services/completion_admission.py`、`runtime.py` |
| P0-2 | generation/有效租约约束能力、文件写入、进程启动和最终提交；Await 排他 claim/续租；等待保留资源；Conversation 与同 Project Workflow 执行互斥 | `execution_control.py`、`capabilities.py`、`store.py`、`services/scheduler.py`、`conversation.py` |
| P0-3 | 自动恢复复用 Column Run 并追加 Attempt；副作用 key 与 Agent Run ID 分离；恢复加载历史 ledger；已完成 receipt 可复用，未知结果阻塞；旧命令参数规范化兼容 | `agent_tool_execution.py`、`runtime.py`、`store.py` |
| P0-4 | 可恢复异常有界重试；协议/永久/一致性错误暂停执行并记录运行失败；业务 failed 保留给声明的业务终态或显式取消 | `services/recovery_manager.py`、`runtime.py` |
| P0-5 | Agent 时间/轮次/工具次数、Column visits、恢复次数/时间、命令超时/输出受限；Windows Job Object 回收子进程，父进程提前退出也不遗留副作用 | `policy.py`、`agent_runner.py`、`process_runner.py`、`windows_job.py` |
| P0-6 | Supervisor 单轮异常后继续；容量限制覆盖执行与 Await；lease 续租异常撤销执行；orphan Conversation 一次性结算；治理轮可用任务恢复工具；health 返回实际存活状态 | `runtime.py`、`conversation.py`、`store.py`、`api.py` |
| P0-8 | 工件版本追加保存，内容按 SHA 快照读取；同路径覆盖不改写旧 Task 事实；终态文件路径唯一 | `repositories/artifact_repository.py`、`repositories/schema_repository.py`、`store.py` |
| P0-7 | success_exit_codes 不再定义成功，非零退出、超时、输出截断均失败；保留真实命令输出 | `capabilities.py`、`files.py`、`process_runner.py` |
| P0-9 | Loop apply 原子事务；binding 保存包及 assets；旧 binding 只在 digest 匹配时回填；坏 metadata/重复 key 隔离；SQLite 上下文关闭连接且嵌套事务不提前提交 | `loops.py`、`store.py`、`repositories/schema_repository.py` |
| P0-10 | 直接及符号依赖使用同一 successor 解析；多代 successor 的调度、上下文和工件一致；坏链只阻塞有关任务 | `services/dependency_resolver.py`、`services/scheduler.py`、`repositories/artifact_repository.py` |

完整命令，在 `D:\workspace\DevWerk\DevWerk` 执行：

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

最终结果：**265 passed in 189.72s**，无 failed/skip/xfail。其中 `tests/test_p0_runtime_regressions.py` 包含 36 个场景（含参数化），覆盖真实子进程与文件副作用、Provider 断线、旧 receipt、未知结果、失联 worker、Await 恢复与启动暂停、工件旧字节、Loop 回滚及多代依赖。原有 API/Web、Provider、业务 failed/rework、讨论轮权限边界等契约一并通过。

真实源库使用 SQLite `mode=ro` 打开，仅备份到临时目录后执行迁移。迁移结果为 `integrity_check=ok`、`foreign_key_check=0`，291 个当前工件对应 291 个迁移版本；临时数据库及目录清理成功。源库与项目文件未被迁移测试修改。

事故回放使用原 Agent Run 的执行顺序及原 `analyze` 契约：第 4 次调用的旧 false success 被按非零退出纠正；第 13 次调用已成功修复对应回测命令。第 19 次 completion 的 `continue_to_tune` 不再遇到 omitted-success-ID 校验，但仍被一个未解决的 PyYAML 诊断失败拒绝。追加一条**模拟的同操作成功重试记录**后 admission 通过，下一 Column 为 `tune`。没有运行实际回测或安装依赖，因此该回放不能作为真实项目交付证据。

### 默认执行上限及边界

`V1RuntimePolicy.execution` 默认：Agent 3600 秒、100 iterations、300 tool calls；每个 Column 最多 25 次 visit；自动恢复最多 5 次且恢复时间窗口最多 3600 秒；单命令 600 秒，stdout/stderr 各最多 1 MiB。达到上限转运行阻塞，由拆分任务、调整计划或显式恢复继续推进，不生成业务成功/失败结论。

Await 网络故障保留 pending Handle，停止自动重试时保留 waiting+paused；resume 继续原 Handle。仅声明外部作业失败才结算外部失败。无 Handle 的旧 waiting 状态在启动暂停时回到 pending+paused，避免保留无法恢复的等待。

已有 Provider 同步网络请求不能由 Python 强制杀死；本轮让 Runtime worker 按 deadline 退出并阻止迟到工具执行，网络线程仍依赖 Provider 请求自身超时。Windows 命令树与调用同生命周期，长期后台服务不应通过无限阻塞命令维持。互斥覆盖同 Project 的已声明资源与直接执行，不宣称任意 shell 对跨 Project 外部资源实现 exactly-once。旧文件已被覆盖且没有内容快照时，保留缺失/不匹配事实，不恢复不存在的旧字节。

### 目标历史任务的恢复入口（未执行）

新代码加载后，在原 Project 的 Conversation 中恢复原任务，并让 Agent 先核对现有回测产物和诊断结果。对应已有能力为：

```json
{
  "capability": "task.reopen",
  "arguments": {
    "task_id": "tsk_cfc85c0496834a2baae81d9b3eb055f0",
    "column_key": "analyze",
    "clear_context": false
  }
}
```

该操作新建本阶段 Column Run，保留原失败历史和工件；它不等同于自动恢复同一 Attempt，也不承诺跨显式 reopen 去重所有命令。实际验收应观察 `analyze → tune` 及后续验收/交付终态，并核对产物，而不是直接修改 Task 为 done。本轮未调用该能力、未重启真实服务、未执行外部项目任务。
