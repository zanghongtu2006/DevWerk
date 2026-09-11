# P0 修复实施与验证记录

后续真实项目的范围修订、误导性身份错误和虚构执行声明已另行修复：最终全量 **309 passed / 242.07s**，真实项目副本可保留原六个 Task 并新增第 7–12 章。完整实现、证据与真实模型尚未交付新增章节的边界见 [范围扩展实施记录](DEVWERK_Scope_Extension_Implementation_2026-09-10.md)。下文保留上一轮实现及 297 项测试快照；字数硬校验按用户决定延后。

本文件对应 [Bug 登记簿](DEVWERK_P0_Runtime_Bug_Register_2026-09-09.md) 的 R1–R9，以及用户确认的 [Hermes 修订方案](DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09.md)。流程为先已有方案、再修改代码、再执行回归。未采用 docs 中以前的测试结论作为当前通过证据。

状态：R1–R9 核心代码修复已落地；2026-09-10 最终全量回归 **297 passed / 280.07s**，退出码 0。该运行包含本次最后补充的消息终态处理、真实 Worker 进程退出恢复和提示词修订。真实模型/生产历史需求验收及方案取舍见第 7–8 节。

## 1. 实际结构

```text
Project Main AgentInstance
  └─ active Requirement / revision
      └─ Workflow → Task → ColumnRun
          └─ Assignment（一次逻辑阶段的工作与验收契约）
              ├─ leaf Worker AgentInstance
              ├─ private ContextSession
              ├─ physical AgentRun(s)
              └─ persisted Operation → execution receipt
```

Main 与 Worker 都执行真实 AgentCore 模型/工具循环。Column 仍只执行一个 leaf Worker；没有把多代理讨论、评审、投票循环塞进 Column。跨角色协作由独立 Column/Task 和 Main 的结果反馈回合组织。

Worker 完成 Assignment 后释放执行占用，保留身份与 ContextSession。同一 Requirement 下明确指定相同 `executor.worker_key` 可跨相关 Task 复用 Worker；默认键包含 Task ID 与 Column key。新 Requirement 不按同名角色隐式复用上下文。`worker_role` 保留在冻结的 executor 契约中；实例的执行边界角色是 `main` / `leaf`。

## 2. R1–R9 落地映射

| 编号 | 修复内容 | 主要代码 | 回归入口 |
|---|---|---|---|
| R1 | assistant 来源位置与 tool index 形成持久 Operation；参数相同的新意图独立执行；恢复先补未交付响应；模型操作及 Runtime 验收的不确定回执阻止盲重放 | `agent_runner.py`、`agent_tool_execution.py`、`execution_recovery.py` | `test_p0_assignment_regressions.py`、`test_agent_recovery_and_fencing.py` |
| R2 | 同 capability 的无关命令不能豁免失败；固定 acceptance_checks 在 Runtime 执行；恢复完成提交时重新验收 | `domain.py`、`completion_protocol.py`、`services/completion_admission.py` | 固定交付检查、修正命令、noop、提交中断后产物变化 |
| R3 | 周期补扫全部 durable queued Jobs；claim 失败不丢内存条目；异常回调不自旋；Worker 定向消息不被 Main 吞走 | `conversation.py`、`store.py`、`services/mailbox.py` | claim 故障后同一 Gateway 完成前后两个请求、定向投递 |
| R4 | DB control 的 owner 检查、业务写入、receipt、事件在同一事务；失败返回也回滚；Worker generation 与 Task lease 双重检查 | `capabilities.py`、`repositories/agent_repository.py` | 两连接接管竞争、迟到旧 owner、部分写入后失败回滚 |
| R5 | Invocation 保留历史；ledger 从当前 receipt 恢复 completed/failed；pending 单独拒绝完成；nullable error 不再崩溃 | `runtime.py`、`execution_ledger.py`、`services/completion_admission.py` | 异步启动→poll 成功→Column 完成 |
| R6 | Assignment 累计模型/工具/恢复次数；无进展 Await 收敛；重试按逻辑 ColumnRun 计；Main 规划动作受 Requirement 总量约束 | `policy.py`、`agent_runner.py`、`services/recovery_manager.py` | 跨 Await 不能刷新模型预算、连续无进展 Await 停止 |
| R7 | Main 事件回合可在有效 Requirement 内保存计划、创建后续 Task；Task 记录需求与反馈来源；相同反馈/引用防重复创建 | `conversation.py`、`capabilities.py`、`repositories/agent_repository.py` | Worker 完成事件触发 Main 新建 repair Task |
| R8 | 私有 Session 保存多轮真实消息；相关 Task 可复用；等待历史可恢复；有边界的历史窗口、显式摘要和源记录分页 | `agent_run_preparation.py`、`session_replay.py`、`repositories/agent_repository.py` | 跨 Task 上下文、旧 Session 迁移、窗口摘要保留 |
| R9 | 独立实例、Assignment、占用、生命周期、定向消息；领取 Task 时预留同一 Worker 的单写者资格；终态明确结算无法消费的绑定消息 | `repositories/agent_repository.py`、`services/scheduler.py`、`services/mailbox.py`、`states.py`、`store.py` | Worker 跨 Task 持续、并发领取、retired/closed 不复活、完成与 steer 的两个事务顺序 |

## 3. 执行身份与恢复语义

最终复查补充的 R1/R2 修复方案：Runtime 固定验收不由模型 Operation 直接派发，因此验收命令已经发生效果、但 receipt 尚未结算的窗口需要独立检查。每次 Worker turn/恢复派发前，检查同 ColumnRun 的 Runtime 验收 receipt；started/awaiting 不允许新模型或重新验收，转为明确的未知效果阻塞。已结算验收在完成提交恢复时仍需重新验证工作区。回归在真实追加文件命令返回后、receipt 结算前注入故障，断言恢复后只追加一次。

新 Operation 的标识为 `op:<source_agent_run_id>:<assistant_sequence>:<tool_index>`。assistant 消息和整批 Operation 在同一事务持久化后才执行工具。参数 hash 用于诊断和保守的相同操作修复关联，不再用于判断两条不同消息是否是同一次执行。

恢复区分以下窗口：

1. 意图尚未持久化：没有工具效果，可重新请求模型。
2. 意图已保存、尚未执行：按原 Operation 派发。
3. 效果 receipt 已结算、响应尚未记录：读 receipt 补响应，不重跑命令。
4. 工具响应已保存、模型下一次请求失败：恢复 Session 与当前事实，然后让模型发出新意图；新发出的相同命令仍是新操作。
5. Await 已接受但 Handle 尚未提交：恢复已接受的等待，补建 Handle。
6. 完成已获准但 Task 提交中断：重新执行固定验收，再提交结果。
7. started 且无可确认结果：以 `effect_outcome_unknown` 阻塞；新 Task 和 Main 直接写入不能绕开该项目的未知效果。不会把未知结果伪造为失败后自动重试。

旧版本没有 Operation 的未完成记录由 `execution_recovery.py` 保守识别。只接受唯一可关联的旧 receipt；跨 Await 的旧参数 hash 可能属于另一次意图时要求核对。新 AgentRun 标记 `operation_protocol=1`，避免把新协议的恢复响应再次误认成旧意图。

## 4. 完成准入与普通 Agent 修正

新字段 `ColumnDefinition.acceptance_checks` 由发布的 workflow 冻结进入 Assignment contract。当前支持同步 `project.command.run` 与 `project.files.read`，例如：

```json
{
  "acceptance_checks": [
    {
      "key": "delivery_tests",
      "capability": "project.command.run",
      "arguments": {"argv": ["python", "-m", "pytest", "tests/test_delivery.py"]}
    },
    {
      "key": "required_report",
      "capability": "project.files.read",
      "arguments": {"path": "delivery/report.md"}
    }
  ]
}
```

命令必须真实验证目标；读取文件只证明该文件能被读取，不代表其业务内容正确。内容正确性应由固定测试或独立 Review 阶段判断。验收不能引用模型临时选中的“成功 ID”来跳过失败，也不声称能自动理解任意命令之间的语义等价。

有固定检查时，已确定结束的探索性失败保留在日志，但不永久污染成功状态；Worker 可以改路径、参数、实现，再通过固定检查。pending/unknown 不被这些检查豁免。没有固定检查的旧 workflow 保留保守兼容：无关成功不能消掉失败；需要替代验收方法时由 Main 显式发布 workflow/plan 修订。未自动改写旧 workflow 的业务验收标准。

## 5. 生命周期与消息

最终回归期间补充的 R9 修复方案（先记录、后修改）：Assignment 绑定消息可能在最后一次模型请求开始后才入队，完成后没有可消费它的后续 turn。终态提交需在同一事务把这些未消费消息标记 failed，保留 `consumed_by_run_id=NULL` 和失效原因；不绑定 Assignment 的消息继续等待下一次分配。retire/需求关闭同样结算剩余消息。发送和重新投递时检查目标状态，拒绝已终结分配或 retired 实例。回归覆盖“发送先于完成”和“完成先于发送”两个事务顺序，以及退休和重投递。

已实现的 Main 能力：`agent.worker.list`、`agent.worker.inspect`、`agent.worker.send`、`agent.worker.lifecycle`、`agent.context.compact`、`requirement.list/create/select/close`。Worker 自动获得只能读取自己历史的 `agent.context.read`。

实例生命周期为 available / suspended / retired，占用由 active_assignment_id 表达。Assignment 为 running / waiting / blocked / completed / failed / cancelled。正常完成和已结算失败释放占用；等待保留逻辑工作；暂停、取消、耗尽重试的管理转换递增 generation 并释放相应占用。retired 不隐式恢复。

发送消息只意味着持久入队。消息进入 Worker 的持久 user 消息与标记 consumed_by_run_id 在同一事务完成，同时记录 mailbox delivery。消费不代表业务执行完成，需结合 Assignment/result/后续操作检查。空闲 Worker 的消息在下一次合法 Assignment/turn 消费，不在后台偷偷启动无 workflow 的业务循环。

旧 mailbox 行继续投给 Main。带 recipient_agent_id 的消息只投给相应 Worker。Main 不能把 Worker steer 当作自己的监督事件确认掉。

## 6. 预算和上下文边界

Assignment 默认沿用现有 `agent_max_iterations=100`、`agent_max_tool_calls=300`，累计统计全部物理恢复 run；另有 `assignment_max_resumes=25`、`assignment_no_progress_awaits=3`。旧 ColumnRun 创建新 Assignment 时导入已有 AgentRun 计数。物理 run、命令时限仍保留，外部等待不占据 Python 运行线程。

Requirement 默认最多 1000 次成功规划动作，避免通过连续创建新 Task 绕过单阶段收敛限制。新业务目标只能由用户回合建立；事件回合继续已有有效需求。反馈来源与任务引用有持久记录，防止重复投递导致重复工作。

Worker 历史窗口默认 80000 字符，完整源记录仍留在数据库。摘要覆盖游标只能由空闲 Worker 的明确压缩操作推进；过长摘要拒绝保存，摘要不因后续历史窗口裁剪而丢失。模型可按消息游标读回原文。当前工作契约重新进入当前 turn，旧 Task 的历史说明不能改写新 Assignment。

## 7. 数据迁移与历史 Task

采用增量建表/加列。**不 DROP、不重建旧 v1_agent_sessions，不删除旧消息、invocation 或 receipt。** 新表为 v1_agent_context_sessions，通过 legacy_session_id 显式连接同一旧 Task/session_key 的历史；不猜测跨 Task 身份归属。

已经在 `data/devwerk.db` 的只读备份产生的临时副本上执行初始化迁移：

| 表 | 迁移前 | 迁移后 |
|---|---:|---:|
| v1_tasks | 53 | 53 |
| v1_agent_runs | 415 | 415 |
| v1_agent_sessions | 96 | 96 |
| v1_execution_receipts | 2317 | 2317 |

这些数字是本次实际读取与迁移验证，不能替代全量内容一致性检查或真实模型端到端验收。另有临时数据库测试比较旧 Session 行逐字段不变，并验证旧上下文仍进入新 Worker。

指定历史 Task `tsk_cfc85c0496834a2baae81d9b3eb055f0` 只读状态仍为 failed，阶段 analyze，错误是 CompletionProtocolStalled。历史工具结果还显示默认 Python 缺少 yaml、绝对 cwd 被路径规则拒绝，以及多次手工 evidence_ids 完成提交被拒绝。现有新协议由 Runtime 收集证据；非零命令退出仍不伪装成功。没有把历史 Task 的状态改成 done，没有更改它的外部项目环境，也没有自动重新执行它。

代码生效需要使用更新后的应用实例。若重跑该历史需求，应先明确项目实际 Python/依赖环境和验收方式，再通过现有 rerun/reopen 语义执行新的工作；历史失败和原回执应保留。

## 8. 验证边界与待批准扩展

相对最初方案，本次 v1 的具体实现取舍：

| 设计项 | 当前实现与限制 |
|---|---|
| Worker 创建/重新分配 | 首次合法 Column Assignment 创建实例；后续通过相同 Requirement 下的 worker_key 复用。恢复同一 Assignment 使用原 Worker；没有提供活动 Assignment 热迁移到另一 Worker 的工具。 |
| 需求版本 | Assignment 冻结 requirement_revision 并在提交时校验。当前显式工具支持创建、选择、关闭需求；新的目标建立新 Requirement，没有提供原地修改活动需求并自动迁移旧 Tasks 的工具。 |
| 时间与收敛 | 持久累计模型/工具/恢复次数提供终止边界，物理 run 和子进程另有限时；没有实现跨进程精确累计活动秒数。Requirement 采用反馈去重与规划动作总上限，不能称为完整语义循环识别。 |
| context 压缩 | 保存完整源消息，提供字符窗口、显式有游标摘要和分页读取；没有实现自动语义摘要、自动 session rotation 或精确 token 预算。 |
| Web 展示 | 保留现有对话/看板执行入口和状态展示；新增 Worker 控制使用 Main 工具。专用 Worker HTTP/UI 操作未纳入当前实现，见下文审批记录。 |
| 发布验收 | 本地自动回归覆盖具体故障窗口；方案 H01–H26 仍是发布验收清单，不能将测试总数等同于全部组合场景或真实在线模型已验收。 |

测试使用真实 SQLite、实际 Runtime/Gateway、真实本地子进程效果与故障注入；模型响应和一个异步 adapter 为确定性替身。没有宣称已跑通真实在线模型或用户外部量化项目的全链路交付。

验收中断回归还在独立 Python Worker 进程执行真实文件追加后调用 `os._exit(73)`，跳过异常处理和清理；父进程经正式过期租约恢复后，检查仍只有一次文件效果和一条 started receipt，Worker 被阻塞而非重跑。这一用例覆盖真实进程退出，不只是异常替身。

新增 Worker HTTP 控制端点被自动审批拒绝，理由为“未见认证或授权保护，且包括不可逆 retirement，暴露范围未明确授权”。因此当前实现没有加入这些端点，Main 工具接口和内部生命周期服务已经落地。这是额外 HTTP 扩展的阻塞，不是把内部生命周期修复留为空接口。

可供用户批准的具体扩展范围：

| 拟议端点 | 行为 |
|---|---|
| GET /v1/projects/{project_id}/agents | 查询实例及执行占用 |
| GET /v1/projects/{project_id}/agents/{worker_id} | 查询 Assignment、消息及消费状态 |
| POST /v1/projects/{project_id}/agents/{worker_id}/messages | 持久发送 steer，返回 202 accepted；不声称已执行 |
| POST /v1/projects/{project_id}/agents/{worker_id}/lifecycle | 修改空闲实例 available/suspended/retired；retired 不可逆 |

以上 HTTP 扩展未部署、未加入当前 API；如果继续，需要明确批准访问范围。没有通过其他入口间接新增这些被拒绝的远程端点。

## 9. 本次回归记录

执行环境：Windows / 项目 `venv\Scripts\python.exe`；使用仓库当前代码和 pytest 临时数据库。完整命令：

```powershell
.\venv\Scripts\python.exe -m pytest -q --disable-warnings --maxfail=3 --junitxml=test-results/p0-runtime-2026-09-10.xml
```

最终运行结果：**297 passed，0 failed，280.07 秒，退出码 0**。机器可读报告为 [JUnit XML](../test-results/p0-runtime-2026-09-10.xml)。中间失败的报告已由最终成功运行覆盖；未把某次历史通过数算作最后代码的验证结果。`git -c core.whitespace=cr-at-eol diff --check` 同时通过。

本轮回归确实发现并修正了文件写入 preflight 的错误变量引用、Main 提示词超过既有长度契约、Worker 能力断言仍沿用旧会话模型，以及测试以违反唯一约束的方式伪造重复事件的问题。重复事件测试现在先使 Main 在已创建 Task 后回复失败，再走真实 mailbox.redeliver 流程。

重点故障注入入口：

| 测试 | 故障位置与断言 |
|---|---|
| `test_committed_effect_replays_after_missing_answer_without_reexecution` | receipt 已提交后，在保存 Operation 响应或 invocation 处中断；恢复后文件效果仍一次。 |
| `test_unsettled_runtime_acceptance_effect_is_not_reexecuted` | Worker 进程在验收命令完成、receipt 提交前退出；过期租约恢复不重复命令，保留未知效果。 |
| `test_completion_recovery_rechecks_workspace_after_interrupted_task_commit` | 已接受完成后 Task 提交中断；修改产物，恢复时重新验收并拒绝错误交付。 |
| `test_control_transaction_serializes_takeover_and_rejects_late_old_owner` | 两个真实 SQLite 连接竞争控制权；事务有效期间接管等待，接管后的旧 owner 零写入。 |
| `test_main_uses_worker_completion_to_create_scoped_repair_task` | Worker 结果驱动 Main 创建 repair；Main 回复中断后同事件重投不重复 Task；同 Worker/session 消费后续消息并完成。 |
| `test_late_assignment_input_settles_without_fabricating_consumption` | 消息先入队后结束，与先结束再发消息两个顺序；前者明确 failed 且没有 consumed ID，后者被拒绝。 |

没有启动或重启用户正在使用的应用实例，没有把生产数据库标记成测试通过，没有重跑指定历史需求。上述代码级修复与发布/真实项目验收的边界见第 7–8 节。
