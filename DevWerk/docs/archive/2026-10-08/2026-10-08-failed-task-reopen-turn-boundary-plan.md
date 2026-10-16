# 失败 Task 的恢复请求被误绑定为 resume 控制回合

## 现场与根因

- 新项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 的交付列因前台常驻启动命令超时，以 `execution_failed` 进入终态 `failed`。用户消息 145 明确要求继续同一 Task、补齐启动/健康/停止验证。
- Conversation Job `cjob_3a55c6ea795d4908a64b9a5f3f213797` 首先成功调用 `conversation.task.control.authorize(action=task.resume)`，把整轮锁成 `targeted_control/task.resume`；此后 `task.reopen(column_key=delivery)` 被 `TurnControlScopeMismatch` 拒绝，再尝试 `conversation.task.control.authorize(action=task.reopen)` 又因 schema 仅支持 pause/resume/cancel 被拒。Job 失败，Task 没有恢复。
- `app/v1/capabilities.py::_authorize_task_control` 只对 `recovering` 拒绝错误的 resume；`failed` 没有同类拒绝。`app/v1/repositories/conversation_intent_repository.py::resolve` 的直接 `conversation.turn.resolve` 路径也缺少状态校验。工具说明和主 Agent 提示没有明确区分 `failed→task.reopen` 与 `paused/recovering→task.retry`。

## 最小修复

1. 在 `ConversationIntentRepository.resolve` 提交 `targeted_control` 前，按现有 Task 状态拒绝 `task.resume` 用于 `recovering` 和 `failed`：分别给出 `conversation.execution.authorize(request=continue)` 后调用 `task.retry` 或 `task.reopen` 的可操作错误。保留 `task.resume` 在真正暂停 Task 上的语义，不给控制工具增加新 action。
2. `app/v1/capabilities.py::_authorize_task_control` 沿用同一状态判断；更新 `conversation.task.control.authorize`、`task.reopen` 的描述，以及 `app/v1/conversation_intent.py::TURN_INSTRUCTION`，使 Agent 在调用前可见失败 Task 的恢复协议。`conversation_intent_repository.py::denial` 对未授权的 `task.reopen` 与 `task.retry` 一并提示先获取当前用户执行授权。
3. 在 `tests/test_conversation_intent_contract.py` 增加失败 Task 的回归：错误 resume 不提交回合边界；正确 `execution.authorize(continue)` 后同一 Task 可 `task.reopen`。再跑现有意图、监督和恢复相关测试。

## 黑盒继续

无其他运行 Task/Job 时重启加载补丁，再通过公开项目对话发一条自然语言继续请求；仅恢复这个失败 Task，不新建 Task。约每五分钟检查真实 `task.reopened`、交付、最终验收和终态。交付列原先错误运行常驻脚本的问题另以实际有界验证收据判断；本次修复只处理恢复授权边界。
