# 已交付 Project 改造 Workflow 时的 Requirement 绑定死锁

## 事实与根因

- Project `prj_42ab1012ccf343689ca798a3ceec6f0e` 的旧 Task `tsk_bad2634c66cd46ef91271b83985f114c` 已 `done`；当前 Workflow Revision `wfrev_0ac32e3aad5c4ef8a7883aae312c524e` 的 `v1_workflow_scopes.requirement_id` 为 `req_666f7598719d4ed09331835b0bc0b6c1`。
- 用户在消息 55 明确要求复用当前 Project、重新设计 Workflow；Conversation Agent 却以 `begin` + `focus=new_scope` 固定了新的 Requirement `req_281d3af2a49945b9b42f340f59982204`。后续消息 57、59 的 `extend` Grant 都绑定新 Requirement，状态为 `pending_scope_revision`。
- `conversation_intent_repository.py::denial()` 在该 Grant 下只允许用新 Requirement ID 调用 `project.scope.revise`，其他创建/选择/发布工具一律返回 `ScopeRevisionPending`。但 `scope_repository.py::revise()` 又要求新 Requirement ID 与旧 Workflow 的 Requirement ID 相同，返回 `ScopeConflict: The active Workflow belongs to a different Requirement`。两边条件互斥，没有可成功的工具调用序列。Agent 最终虚构“项目管理员后台”解锁步骤；下一轮又重复失败操作直至 `ConversationProtocolStalled`。DevWerk 没有另一个高权限 Main Agent 或用户必须使用的管理员后台。
- 回归继续暴露第二道阻断：`WorkflowPlan` 按 Project 内容哈希复用。接管后若新 Workflow 沿用同一个流程方法，`workflow.plan.save` 会取回旧 Plan ID，而 `workflow.publish` 的 `_bind_requirement()` 把该不可变 Plan 的旧 Requirement 所有权当成新 Workflow 的所有权，报 `Work belongs to a different Requirement`。真正执行范围在 Workflow Revision 上，复用当前 Revision 已采用的不可变方法时不应改写旧 Plan。
- 这不是多 Column 路由或旧 Project 污染，而是**同一 Project 的新授权范围接管已结束 Workflow 后继续规划**缺少合法过渡。旧 Workflow/Task 的历史必须保持冻结，活跃 Workflow 可发表新 Revision 并绑定当前授权 Requirement。

## 最小修复

1. 在 `app/v1/repositories/scope_repository.py::revise()` 中保留用户发起、当前 Grant、版本比较、未知效果阻断与原子事务。若当前 Workflow 属于另一 Requirement，仅在旧 Requirement 的 Project Task 均为终态（`done`/`failed`）时允许新 Requirement 接管并创建新的 Workflow Revision。若旧任务仍在执行或待执行，明确拒绝，避免活跃交付被切换。旧 Workflow Revision、Loop binding、Requirement 与 Task 全部保留，不改历史引用。
2. `project.scope.revise` 成功后沿用现有流程：新 Workflow Revision 绑定新 Requirement 修订版本，Grant 由 `pending_scope_revision` 变为 `active`。Conversation Agent 才能使用既有 `workflow.plan.save` / `workflow.publish` / `task.plan.save` / `task.create` 工具完成新的工作图和 Task。无需管理员操作、直接 DB 写入或新状态机。
3. 收窄工具说明：当前授权 Requirement 可以在旧交付结束后接管同 Project Workflow，返回新 Revision 后再发布定制图并建 Task。任何非终态旧 Task 则继续阻断。不得在 Web 暴露调试细节或添加新 UI。
4. `_bind_requirement()` 仅对 `v1_workflow_plans` 加一条窄例外：若该 Plan 正是当前 Workflow Revision 使用的方法，且 Revision 已绑定本轮授权的 Requirement/Revision，则允许复用并保留 Plan 历史所有权。任何其他旧 Requirement Plan 仍拒绝。

## 回归

- 隔离 DB 测试：旧 Requirement 的 Task `done` 后，新 Requirement 的 `extend` Grant 调用 `project.scope.revise` 成功；旧 Task 仍指向旧 Workflow/Requirement，新 TaskPlan/Task 绑定新 Workflow/Requirement。
- 同一隔离测试覆盖接管后复用同内容 WorkflowPlan、重新发布修改过的 Workflow Definition，确认旧 Plan 所有权不改写，新 Workflow 与 Task 归当前 Requirement。
- 旧 Task 非终态时跨 Requirement 修订被拒绝，当前 Workflow 不变；同 Requirement 修订和既有小说扩写回归保持通过。
- 完成代码回归后，仅为加载修改而必要重启 DevWerk 服务；不终止其他进程。再使用本 Project 的 Conversation Agent 正常用户轮次验证能修改 Workflow、创建新 Task。若新图或 Task 方案自身存在独立错误，单独记录，不扩大本次代码修改。

## 执行记录

- `tests/test_scope_extension.py` 与 `tests/test_conversation_contract.py`：43 passed。新增参数化测试覆盖旧 Task `done` 与 `pending` 两条路径，并实际走通接管、复用不可变 WorkflowPlan、发布修改过的 Workflow Definition、保存 TaskPlan、创建新 Task。
- 为加载修复，仅重启经进程命令行确认的端口 8000 DevWerk 实例；项目 API 返回 200。
- 已通过该 Project 的公开 Conversation API 提交一轮继续执行请求，Job `cjob_a5787e5f47d045af8e61aed15c5e3779`，用户消息 ID 61。
- 该 Job 已 `succeeded`：`project.scope.revise` 产出 `wfrev_26d9e511ccff4ff7ad80499df814cabf`；Conversation Agent 随后两次发布新版，最终当前 Workflow 为 `wfrev_7347cae79dfd461f888f988af8ac3914`，绑定新 Requirement `req_281d3af2a49945b9b42f340f59982204` 修订 2。`task.plan.save` 和 `task.create` 成功，创建 `tsk_d8d5b2d260cc4d648d99ba344ee15cc7`，Runtime 已将其置为 `running`。旧 Task `tsk_bad2634c66cd46ef91271b83985f114c` 仍是 `done`，仍绑定旧 Workflow/Requirement。
- Agent 在这轮中先出现了未授权调用、不可达 Column、Plan 参数及 ID 混淆等拒绝，但后续自行修正并完成创建；这些拒绝没有导致新的权限死锁。本次验证仅证明 Workflow 修改和 Task 创建链路恢复，不将正在运行的 Task 算作交付完成。
