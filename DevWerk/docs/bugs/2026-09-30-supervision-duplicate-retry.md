# P0：同一监督通知重复 retry，消耗恢复额度

## 实测证据

Hermes 核心分支黑盒 Project `prj_b2ff5d6dae2243828f5d6ae34a2b3ccf` 的唯一 Task `tsk_80a31d6420134d6e8cc785296a4d040d` 在 `backend_development` 的 Assignment `assignment_26226757201645d8ba613eaafc6f80ea` 用尽 100 次模型请求预算，02:25:58 UTC 进入 `recovering/blocked_runtime`，失败码 `execution_budget_exceeded`。同一 Project Agent Session 的监督 Job `cjob_d64c161b4acb45e0bbb210466588aa1d` 已读取 Task/Workflow/失败回执，并于 02:26:19 UTC 调用 `task.retry(clear_context=false)`，创建 sequence 4 的 `pending` 后端 Column Run。它于 02:28:09 UTC 再次成功调用 `task.retry`，新增第二条 `task.retry_requested` 事件，但仍只有同一个 sequence 4 pending Run。两次成功回执都计入 `V1Store.bind_supervision_job` 的全 Task 自动恢复上限，下一次真实失败可能无法自动唤醒主 Agent。

本次运行时的完整 Task 仍属 pending，旧 Assignment blocked，新 Worker 生命周期 available，调度是否推进需继续观察。重复 retry 不应被认作第二次真实恢复尝试。

## 代码根因

1. `app/v1/repositories/conversation_intent_repository.py::_supervision_denial` 只验证 Grant、Task ID、`clear_context=false`；没有核对 Task 当前状态，也没有检查当前通知 Job 是否已成功恢复一次。
2. `app/v1/services/recovery_manager.py::retry_task` 对 `pending` Task 仍接受同列 retry：只有 `done/failed/running/waiting` 被拒，状态机允许 `pending -> pending`。该调用会更新 Task 版本、发事件和 mailbox，即使没有增加有效 Column Run。
3. `app/v1/store.py::bind_supervision_job` 仅在绑定一个**新**监督 Job 时统计历史成功 retry/reopen；已绑定的 Job 内没有次数闸门。因此第二个调用进入持久回执，耗尽两次上限。模型第一次错误调用 `task.resume` 被权限拦截，这部分符合预期，不是根因。

## 最小修复方案（保持现有架构）

先在 `ConversationIntentRepository._supervision_denial` 对 `task.retry/reopen` 增加两个动态条件：

- 对同一 `conversation_job_id`，查询已提交的 `v1_tool_invocations` 与其 `v1_agent_runs`，若已有一次成功的 `task.retry/reopen`，拒绝后续恢复调用，返回清楚的 `SupervisionRecoveryAlreadyRequested`，并引导 Agent 检查 Task、结束通知回合。只算成功回执，失败参数可更正，不消耗机会。
- `task.retry` 仅在本监督事件的 Task 当前为 `recovering` 时可用；`task.reopen` 仅在 `failed` 时可用。对 `pending/running/waiting/done` 拒绝，避免一个已排队的 Task 再次发 retry 事件。回执中的 Task ID 与原用户 Grant、Requirement revision 仍按现有规则核对。

为防止将来其它调用路径也把同列 `pending -> pending` 当恢复，`RecoveryManager.retry_task` 增加同列 pending 的无副作用拒绝或幂等返回约束：至少不能重复生成 `task.retry_requested` 事件与 MailBox 通知。用户控制路径仍可明确要求切换目标列；该行为须沿用现有准入和审计，不在本修复中重设计。考虑到 `CapabilityRegistry.dispatch` 在事务中二次调用 denial，以上状态/回执检查须在持久效果前执行；同一个模型响应的串行工具调用也要覆盖。

`V1Store.bind_supervision_job` 的全 Task 上限改为统计**发生过成功恢复的不同监督 Job**，而非原始工具调用条数。这样历史同 Job 双回执只算一次真实监督机会，且上面的单 Job 闸门阻止新数据重复。保留最多两个独立失败通知周期，不能借普通 mailbox Job 或 user Job 伪造额度。

## 回归范围与通过条件

扩展 `tests/test_project_supervision.py`：首次监督 retry 成功、Task 变 pending 且只产生一个新 Column Run/事件；同 Job 第二次 retry（同参数或不同 `column_key`）被拒，成功恢复回执数仍为 1；失败调用更正后仍可成功一次；未来真正再次 `recovering` 的**新**通知 Job 保留第二次机会。另在 RecoveryManager 测试中验证正常用户对同列 pending retry 不会追加虚假事件。保持已存在的授权隔离、Worker context 保留、最多两次真实恢复测试通过。

修复前让当前黑盒服务继续至明确终态，记录其它可能问题；在无运行中的 Job/Task/Run 边界重启加载新代码，之后以新 Project 重跑同一五轮以上讨论和登录防刷交付案例。修复、专项测试、提交各自可追踪，`devwerk.zip` 不参与。

## 实施与验证

已按上述边界修改 `ConversationIntentRepository._supervision_denial`、`RecoveryManager.retry_task` 与 `V1Store.bind_supervision_job`，不调整 Column 结构或 MailBox 职责。新增同 Job 一次恢复、失败动作不耗机会、pending 同列无重复事件、历史双回执按一个 Job 计额度的回归。仓库 venv 运行监督/Assignment 专项 **24 passed**；Conversation 意图、合同与调度扩大回归 **79 passed**。代码尚未加载到原 8000 服务，原黑盒数据未改写。

当前 Project 的 sequence 4 后端 Assignment 也在 03:03:36 UTC 用尽 100 次模型请求，Task 保持 `recovering/blocked_runtime`；自动通知因旧实现把同一 Job 的两次 retry 算满额度，仅做确定性播报。第二个 Assignment 曾把完整后端 Maven 失败从 9 failures/2 errors 改进到 6 failures/0 errors，但未收敛；最后仍在专门调试第三次失败锁定后的持久化。当前交付物 `AntiBruteForceService.createLockAndThrow` 在同类内部调用中声明 `REQUIRES_NEW`，Spring 代理事务不会因 self-invocation 生效，随后抛异常回滚锁记录，是 `isLocked` 仍为 false 的具体实现原因。该问题属于 Worker 交付失败，不应用放松验收掩盖；后续需评估主 Agent 是否能在同一授权范围内修订 TaskPlan/派生有界修复工作，而非单纯重复同列预算。
