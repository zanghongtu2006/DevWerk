# P0：软件交付的未知回执、mailbox 重试循环与调度停滞

日期：2026-09-15。状态：已复现、未修复。仅审计，不修改代码或数据库。Project `prj_8d5d626356124d43bfca175ebe6db0aa`，Task `tsk_c5b1faa3e2f14c1ba6ee05a4e850597c`。完整结果和消耗见 [交付审计](../DEVWERK_Vue_Spring_Delivery_Audit_2026-09-15.md)。

## P0-A：执行前参数错误被写成未知副作用

首次后端 Worker Run `arun_11e9109c66994d53916d858d7e3b082c` 调用 `project.command.run`：`argv=[mvn,-f,backend/pom.xml,clean,test]`，`cwd` 用项目绝对路径。ProjectFiles.run → resolve 规定相对路径，确定性拒绝：`path escapes project base_dir`。输入解析后的 `cwd` 与 Project `base_dir` 完全相同，但仍不满足相对路径 Contract。

执行回执 `receipt_0c7ecd98549f484f9cc7bfcc63a9743d` 却先进入 `started`，没有 finished/error；下一次 Agent Run 被 `ExecutionReplayUncertain: Previous side effect has no committed outcome` 拦住。这不是需要模型猜测副作用有没有执行的场景：路径校验在 `run_command` 之前发生。应在写入 started Receipt 前做完整前置校验，或把确定性输入拒绝结算为 `rejected_before_effect`，允许 Agent 修正 `cwd` 而不重放外部命令。

## P0-B：阻断消息触发重复 LLM 消费而未解决阻断

43 个 mailbox Conversation Job 产生 207 次 LLM 调用和 2,311,157 个记录 Token（截至 14:18）；同一 Task 有 27 次成功 `task.resume` 和 13 次成功 `task.retry`。这些成功仅改变 Task 控制/调度状态，没有结算原 started Receipt；25 次 Assignment resume 后耗尽 `assignment_max_resumes=25`。24 个后端 Agent Run 因相同未结算副作用失败，9 个随后直接因 budget 失败。该系统把“通知主 Agent”变成了“主 Agent 持续重复无效状态变更”的消费闭环。

需要使 `blocked_runtime/effect_outcome_unknown` 的事件有稳定的治理/结算状态：未结算 Receipt 不可凭普通 resume/retry 重进同一 Assignment；重复同一 failure fingerprint 的 mailbox 不应新建需要模型反复回答的 Job。通知可以保留，不能以通知作为无限调度驱动。

## P0-C：过期租约恢复撞 mailbox 唯一索引，健康状态仍报 ok

14:17 Task/Attempt 35 被置为 running，但 Assignment 为 blocked；Task 租约 14:19 过期。`RecoveryManager.recover_expired_task_leases()` 进入 exhausted 路径时，`_recovery_disposition()` 追加 `task.runtime_blocked` mailbox。`MailboxService.append()` 不是使用新事件，而是按 `(project,type,task,run)` 查最近的 `v1_events`；最近 Event `7598` 已绑定 Mailbox `287`。唯一索引 `idx_v1_mailbox_event` 拒绝再次插入相同 event_id；事务回滚，租约不能恢复。

Supervisor 最后成功 tick 为 14:19:40，`last_exception=IntegrityError: UNIQUE constraint failed: v1_project_mailbox.event_id`。17:46 API 仍给 Task `running/backend_development`，`/v1/health` 却显示 `ok`；这掩盖了无进度状态。17:54 `devwerk.log` 约 172.6 MB，重复 IntegrityError 文本约 24,817 次。

应让 mailbox 与**本次**事件原子关联，或对同一事件进行明确幂等复用；一个 mailbox 唯一约束错误不能让 Supervisor 的租约恢复永久无法提交。健康检查必须反映超过阈值无成功 tick/持续异常，UI 应将过期 running 与 blocked Assignment 展示为需要处理的真实状态。

## 回归关闭条件

1. `project.command.run(cwd=<Project base_dir 绝对路径>)` 在外部执行前明确拒绝，Receipt 为 rejected-before-effect 或不建立，随后用 `cwd='.'` 可继续；不能出现 started/unknown 悬挂。
2. 未结算 Receipt 下对同一 Task 重复通知、resume/retry 不产生持续新的 Conversation LLM Job，也不重复建立 Agent Attempt；能够直接给用户一条确定性阻断摘要。
3. Assignment 累计预算耗尽之后 Task 不伪装 running；过期租约恢复成功提交明确状态，不因 mailbox Event ID 重复而回滚。
4. 对重用同一 Event ID 的 append 施加定向测试，验证唯一索引幂等或新事件关联语义；Supervisor 故障注入后健康 API 为 degraded，日志不会在每个 tick 无限堆栈写入。
5. 保留原始 started Receipt 和 Maven 测试失败证据，不通过删除 DB/任务/产物冒称修复。

