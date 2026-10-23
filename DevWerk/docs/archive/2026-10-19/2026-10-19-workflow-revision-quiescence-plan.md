# Workflow 换版与 Task 静止边界：最小修改方案

## 目标与不变量

同一 Requirement 内，Conversation Agent 可以根据用户的改图请求检查 Task、选择等待/拒绝/请用户确认停止执行，然后保存新 Plan 并发布完整新图。Runtime 不决定废弃哪些 Task，只在提交新版时保证旧版没有活跃执行，并保证退役版本不会再次派发 Task。旧 Task 与旧修订的历史绑定不改写；需要继续的工作由 Conversation Agent 在新版重新规划并创建 Task。不得将未完成 Task 伪装成 `done`。

## 当前代码缺口

- `app/v1/store.py::_publish_workflow_revision` 只校验新图并切换活动指针；没有检查旧版 Task/Worker 是否静止。
- `app/v1/services/scheduler.py::_dispatch_eligible` 和 `claim_task` 不要求 Task 所绑定的 Workflow 修订仍是活动修订；换版后旧版 pending Task 仍可能被派发。
- `app/v1/capabilities.py::_task_pause` 只接受单 Task 的 `targeted_control` turn。用户授权 Conversation Agent 修改 Workflow 的普通执行 turn 中，Agent 无法暂停正在运行的 Task 以准备换版。
- `app/v1/conversation.py` 的主 Agent 指令未说明换版前的 Task 决策与需用户确认的废弃操作。

## 实施步骤

1. `app/v1/store.py::_publish_workflow_revision`：在提交新版的同一个事务内，先拒绝 Project 中任何修订仍有活跃 Task/Worker 或未决副作用，再以活动修订为边界检查旧版 Task。只允许已 `done/failed`，或仍 `pending` 且位于旧版入口列、没有 Column Attempt/Assignment 的 Task。其余状态返回带 Task ID/状态的通用诊断；同时用旧活动修订 ID 比对以阻止提交期间的并发换版。该约束适用于显式 `workflow.publish` 与同一内部发布入口，不新增专用 API。
2. `app/v1/services/scheduler.py`：所有 pending/recovering Task 的派发资格与最终 claim 都要求其 Workflow 修订为 Project 当前活动修订。旧版入口 pending Task 换版后保留记录但停止派发；Conversation Agent 可按需取消旧 Task，并在新版创建对应工作。`app/v1/repositories/planning_repository.py` 与 `app/v1/store.py` 的 Task Plan 保存/Task 实体化也拒绝向退役修订新增工作，但已实体化计划的幂等回执可继续返回旧 Task。
3. `app/v1/capabilities.py::_task_pause`：在已有 `targeted_control` 路径之外，允许用户发起、已取得当前 Requirement 执行授权的 Conversation Agent 暂停同 Requirement 的 Task；保留原有跨 Requirement 与非用户 turn 拒绝。`task.cancel` 仍只可操作非运行 Task。
4. `app/v1/conversation.py`：简短说明改图前检查 Task；对运行 Task 可等待、解释无法立即换版或请求用户确认废弃，不能把暂停请求当作 Worker 已停；经用户同意后暂停并观察，再取消旧 Task。对旧版未启动的入口 pending Task，新版发布后只保留历史记录，不自动迁移；需要继续则取消旧 Task，在新版保存带 `repair_of_task_id` 的 TaskPlan 并通过 `task.successor` 接续。
5. `app/v1/repositories/conversation_intent_repository.py`：当 `continue` 授权下误调用 `project.scope.revise` 时，错误回执明确区分“Requirement 范围扩展需 `extend`”与“同一 Requirement 仅改 Workflow 图应继续保存 Plan 并发布”。不得只提示改用 `extend`，以免将图修订引向 Scope 分支。

## 回归

- Store：活跃 Task/Attempt 阻止换版，未启动的入口 pending 允许，已完成 Task 允许，活动修订与旧记录保持一致；失败发布不改变活动指针。
- Scheduler：退役修订的 pending Task 不可 claim；新版 Task 可正常 claim。
- Capability：Conversation Agent 在授权执行 turn 中可暂停同 Requirement Task，异 Requirement/未授权被拒；运行 Task 仍须实际退出后才能取消。
- 原有 Workflow/Task/Conversation 相关测试与 `git diff --check`。只针对通用生命周期行为，不加入任何场景专属内容。

## 实施与验证结果

已按上述步骤实现。`workflow.publish` 的共同 Store 入口在同一提交事务中检查 Project 全部修订是否有活跃 Task/Worker/未决副作用，再检查当前活动修订中未终结的 Task 是否为从未执行过的入口 pending；失败不切换活动指针。成功发布后旧修订 `active=0`，旧 pending 保留历史但调度器不再选择或 claim；Task Plan 保存与 Task 实体化也拒绝退役修订。已实体化 Task Plan 的幂等返回保留。Conversation Agent 的用户授权执行轮可以暂停同 Requirement Task，但运行中的暂停请求仍不构成静止证据。错误回执现在明确指出同一 Requirement 的改图路径，避免仅建议错误地转向 `extend`。

定向测试覆盖运行中、暂停请求中、已运行后回到入口 pending 的拒绝，未启动入口 pending 的冻结与新版 successor 接续，旧版终态 Task 的允许发布，以及执行授权/跨 Requirement 暂停边界。首次定向 5 项通过；相关 Conversation Intent、Loop、Task 入口和上下文套件共 120 项通过；追加 Task Plan/实体化保护后，Loop、Task 入口和 P0 Runtime 套件共 79 项通过；最终相关 Scope/暂停定向 5 项及发布校验 3 项通过。`compileall app/v1` 与 `git diff --check` 通过。完整 Scope 套件前 13 项通过后有旧用例长时间无输出，仅中止该 pytest 进程；不能计为全套通过。

范围审查：本次新增逻辑只根据 Project、Workflow 修订、Requirement 授权、Task 状态/Assignment/Attempt 和版本绑定运作，没有项目类型或内容类型判断。未再启动黑盒 Project；因此这轮证明 Runtime 生命周期规则和工具路径可用，不声称模型已经在真实长对话中稳定完成自主换版。此前黑盒暴露的语义选择与复杂图参数失败仍需下一次独立验证。检查时 8000 端口无 DevWerk 进程，本次没有停止用户服务。
