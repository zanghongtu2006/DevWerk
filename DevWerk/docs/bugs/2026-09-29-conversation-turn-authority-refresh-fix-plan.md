# Conversation 已授权执行回合的可见权限快照失效

## 真实阻断

Project `prj_68f94e0f4ba64a1398e48bd61d88270d` 的 Workflow 修订 Job `cjob_b55c2f8901994e1b8fab56debae428c3`（请求保存 Plan 并发布 revision）最终失败，没有任何新 revision/Task。它先错误地 `conversation.turn.resolve(execution_request=begin)`，收到已存在 Requirement 的 `RequirementConflict`；随后 `project.scope.inspect`，再以 `execution_request=extend` 正确解析为 `phase=execution`、取得 grant。其 AgentRun 的 `capabilities_json` 明确含 `workflow.plan.save`、`workflow.publish`、`workflow.acceptance.configure`，Provider 在 execution 阶段也不筛掉这些工具；但模型连续声称这些工具不可用，从未实际调用。

根因在 `app/v1/conversation.py::ConversationGateway._process`：用户 Job 开始时 `turn_requirement()` 按设计返回 `None`，`turn_contract.phase` 尚为 `unresolved`，故 `graph_mutation_allowed=false`，并把该值及空 Requirement 写进本轮 `authoritative_project_state`。同一 AgentRun 后续通过 `conversation.turn.resolve` 持久化 execution grant，工具调度 `CapabilityRegistry.dispatch` 能按最新 contract 动态重建权限，**模型看到的原始用户状态帧却仍写 `task_graph_mutation_allowed=false`**。`ContextManager` 当前只刷新 Column 的失败事实，没有刷新 Conversation 的授权事实。这是模型错误推断的直接诱因，且违反“交互层必须确定性带入事实”的要求；模型把“当前尚未授权”的旧状态当成“工具不存在”，用户收到虚假的阻断原因。

## 最小代码方案

在 `app/v1/context_manager.py::ContextManager.prepare` 对 Conversation User Job 的当前 `authoritative_current_request` 帧增加 `_refresh_conversation_authority`，在每次 Provider 调用前从持久 `v1_conversation_jobs.turn_contract_json`、绑定 Requirement revision、当前 Requirement 状态重新生成：`authoritative_project_state.turn_contract`、`requirement`、`conversation_job.task_graph_mutation_allowed`、`turn_authority.task_graph_mutation_allowed`。只有 `phase=execution`、该 Job 原始 `start_task=true`、绑定 Requirement 与当前 revision 均 active 时才标 true；未解析、讨论态、关闭/过期 Requirement 标 false。不要改变工具目录、dispatch 授权、Grant、mailbox 或 Workflow 发布规则；这里只修模型可见投影。保持首个未解析请求只能看到 `conversation.turn.resolve`，解析完成后才展示执行权限。保留既有 `turn_authority.instruction` 文字。

单测在同一 AgentRun 中模拟用户回合初始 unresolved→持久 `conversation.turn.resolve(execute/continue)`，分别读取前后两次 `ContextManager.prepare` 的当前状态帧：初始 false/无 Requirement，解析后 true/当前 Requirement ID/phase=execution；再验证讨论态仍 false、执行 Grant 过期或 Requirement 关闭时也 false。既有 Conversation/Context 回归应通过。修改前文档已保存；修改后仅在全局无运行 Job/Run/Task 时重启 8000，并通过正式 Conversation API 重试 Workflow 修订。不能把模型最终自述当作验证，须核对真实 `workflow.plan.save`/`workflow.publish` 回执和新 revision。

## 实施与验证

已在 `ContextManager.prepare` 深拷贝 Provider 消息后、内存投影前刷新当前请求的 durable `turn_contract`、`work_intent`、Requirement 与两个权限标记。工具目录和真实 dispatch 授权未改。新增同一 Run 未解析→执行 Grant→Requirement 关闭的回归；相关 Context/Conversation 合同 **68 passed**。原失败 Job 的 `capabilities_json` 原本含写工具却没有调用，这次修复只纠正模型看到的旧权限状态，不保证模型必然选择发布工具。实服务加载及黑盒复测待安全边界完成。

全局无运行 Job/Run/Task 时重启 8000 服务，健康接口 `ok`、supervisor running、监听 PID 35148。正式 Conversation API 已受理重试 Job `cjob_8088d30d3a1f444db3dfa3e4d8eef7de`；未把受理当成发布成功。原 Job 的 resolve/工具回执核对：第一次 begin 与当前 Requirement 目标冲突，第二次 extend 成功；模型之后从旧 `turn_authority=false` 推断没有写工具，虽 `capabilities_json` 实含这些工具。没有真实 `workflow.plan.save`/`workflow.publish` 调用。
