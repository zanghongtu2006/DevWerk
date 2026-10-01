# P0：恢复中 Task 的 `resume` 与授权入口误导 Agent

第八次黑盒恢复 Job `cjob_f2e5a829094b4a639cbe57cf382a3554` 在 `recovering/paused` Task 上先尝试 `agent.worker.send`，收到泛化的 `TurnUnresolved`；随后调用 `conversation.task.control.authorize(action='task.resume')`，错误地把用户“继续修复同一 Task”绑定成单纯暂停控制。该授权不允许随后发送 Worker 输入或调用 `task.retry`；重复操作触发 `ConversationProtocolStalled`。同时 `RecoveryManager.resume_task` 现可把 `recovering/paused` 改为 `recovering/active`，而这种状态并不会像 `task.retry` 那样重新排队 Assignment。

最小修复分两层：

1. `app/v1/services/recovery_manager.py::resume_task` 拒绝 `recovering` Task 的 resume，要求 `task.retry`。这保证直接 API/内部调用也不能写入无效状态。
2. `app/v1/capabilities.py::_authorize_task_control` 在提交 `task.resume` 的用户回合边界前检查 Task 状态；若为 `recovering`，明确提示使用 `conversation.execution.authorize(request='continue')`，再以 `task.retry` 恢复同一 Task。补充 `conversation.task.control.authorize` 的工具说明，区分普通暂停恢复与 Runtime 失败恢复。`app/v1/repositories/conversation_intent_repository.py::denial` 对未绑定回合的 `task.retry`、`agent.worker.send` 给同样具体的授权路径。

不改变原有 Requirement/Grant 模型、Mailbox 语义或 Task 生命周期。加回归：恢复中 Task 的控制授权被拒且回合仍可绑定执行授权；直接 resume 不改变 Task；普通暂停 Task 仍可 resume。单项提交。
