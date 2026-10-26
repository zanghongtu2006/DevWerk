# Task Plan 提交边界与上下文交接修正案（2026-10-24）

## 问题和边界

一次 Conversation Run 已得到 `task.plan.validate(valid=true)`，但上下文压缩丢失了完整计划及下一步动作，随后反复读取而没有持久化计划和创建 Task。当前 `save` 和 `create` 分两次模型调用；预览校验既增加调用成本，也没有形成可选择的协议约束。本文只调整 Task Plan 的提交路径及其上下文交接，不修改 Workflow 图、Column 路由、任务执行器、Loop 或既有项目数据。

## 实施顺序与代码位置

1. **可选预览开关和一致的服务端校验。** 在 `app/core/global_settings.py::WorkflowGlobalSettings`、`GLOBAL_SETTINGS_FIELDS` 和 `config/global-settings.yaml` 增加 `workflow.require_task_plan_preview`，默认开启，延续当前先预览的使用方式；`app/web/static/pages/settings.js::renderSettings` 沿用现有字段渲染，只把分组标题改为涵盖任务规划的名称。保存后只影响新 Conversation Run，不重启服务。在 `app/v1/agents/agent_run_preparation.py::AgentRunPreparer._capability_context` 将当前 Settings 快照作为布尔值放入 `app/v1/tooling/capability_registry.py::CapabilityContext`；没有 Settings 快照的旧内部调用继续使用原有路径。在 `app/v1/tooling/capability_handlers.py::_task_plan_save` 执行实际校验：开启时要求本 Agent Run 对**相同规范化计划**有成功的 `task.plan.validate` 调用；关闭时允许直接提交，但两种模式都执行完整校验。`_matching_task_plan_preview` 复用现有 Provider JSON 对象参数解码规则，避免原始调用记录的字符串包装造成误拒。`_task_plan_validate` 已有的无副作用检查供 `save` 共用；校验失败返回带 `code` 和 `ValidationDiagnostic(path,message,expected)` 的类型化错误，保存层仍在同一事务内做权威修订/冲突检查。`app/v1/protocols/tools/planning/capability_contracts.py` 定义输入和错误载体；`app/v1/tooling/capability_registration.py` 绑定校验器；`app/v1/tooling/builtin_tool_catalog.py`、`app/v1/tooling/capability_handlers.py::_task_plan_contract`、`app/v1/protocols/context/context_result_contracts.py`、`DEVWERK.md` 的指令改为与开关一致。无关 Tool 的入参、权限不变。

2. **原子保存并创建 Task。** 扩展 `TaskPlanSaveInput`：`start_tasks: bool = False`，`proposed_task_ref: str | None = None`；`start_tasks=True` 且未给 ref 时选计划首个 ref，给出 ref 则验证其属于计划；`start_tasks=False` 时禁止无效 ref。扩展 `TaskPlanSaveReceipt` 为可选的类型化 `materialization` / `started_task_id`，启动时 `next_action=None`，不重复返回完整 Task。`app/v1/tooling/capability_handlers.py::_task_plan_save` 复用 `_task_create`，并由 `CapabilityRegistry.dispatch` 现有控制事务包住计划持久化、Task 图物化、Requirement 绑定及执行回执。`app/v1/repositories/planning_repository.py::create_task_plan` 和 `app/v1/store.py::materialize_task_plan` 的修订及幂等约束保持原样；若其中一步失败，外层事务整体回滚。`app/v1/agents/agent_tool_execution.py::queued_task_entry`、`AgentToolBatchExecutor.execute`，`app/v1/agents/agent_runner.py::AgentExecutionRunner.run`，以及 `app/v1/conversations/conversation_report.py::render_report` 只增加对一步式提交的已创建 Task 回执识别；`app/v1/conversations/conversation.py::ConversationGateway._process` 更新相应提示。旧的 `save`→`task.create` 两步协议继续有效；显式 `start_tasks=True` 仅用于用户已授权启动的场景，不能绕过 `_require_user_planning_turn`、scope 或 active Workflow revision。修复计划仍按现有 `task.successor` 路径处理，不借此扩张行为。

3. **预测压缩与未完成动作保护。** `app/v1/policy.py::ContextPolicy` 增加保守的下一步输入预留量；`app/v1/context/context_manager.py::ContextManager.prepare` 在估算值加预留量接近软阈值时提前压缩，且完整最近一次工具交互优先保留。若固定 Tool Schema 挤压后完整 checkpoint 仍越过硬限，仅缩短 Provider 可见的 checkpoint 引用；`ContextManager._save` 持久化的完整摘要不缩短。若压缩使原本可发送的请求变得更大，则保留未压缩的可发送视图，避免预防机制反而阻塞。`ContextManager._refresh_conversation_authority` 增加从持久化调用记录重建的待启动 Task Plan 提示：有成功 `task.plan.save`、没有相应 Task 物化时，向本轮上下文提供准确的计划 ID 和 `next_action`；绝不自动创建 Task，也不把校验成功误认成保存成功。状态载体定义在 `app/v1/protocols/context/`，避免手拼固定 JSON。上下文保护不能代替数据库提交回执。

4. **回归。** `tests/unit/config/test_global_settings_contract.py` 验证开关默认值/往返/Settings 字段；`tests/unit/workflow/test_task_entry_admission.py` 覆盖 ON 同计划预览、ON 无预览拒绝、OFF 直接保存、保存校验错误路径、原子提交成功/失败回滚/重复调用复用、修订过期拒绝；`tests/unit/agent/test_context_lifecycle.py` 覆盖提前压缩、固定 Schema 底座和压缩后待启动计划提示。先运行相关单测，再运行完整单测。记录实际结果，不把运行中的旧服务或项目当作本次代码的验证。

## 不变量

- `validate` 是只读预览，`save` 才是提交；两者都不以 Agent 文本声明代替实际回执。
- 开关只决定是否要求显式预览，不降低 `save` 的校验，也不改变 API 直连或 Column 权限。
- 原子路径只在明确 `start_tasks=True` 时启动 Task；默认保持旧行为。
- 新代码不引入项目类型、验收场景或特定 Loop 规则。

## 回归中发现的直接边界

- 提前压缩使三个已有 Conversation 单测的完整 checkpoint 把输入推过硬限；已按第 3 项改成仅在必要时缩短 Provider 视图，三项重跑通过。
- `DEVWERK.md` 增加说明后超过现有 3000 字符约束；删去重复措辞，不放宽约束。
- 同一工作区中先前未提交的 `app/v1/context/session_replay.py::replayable_session_messages` 含一个字典解包表达式，触发现有 JSON 源码契约测试；仅改成逐字段赋值，保持输出不变。

## 实施与验证结果

- 上述四项已按列出的文件和方法实施。Settings 开关默认开启；关闭时 `save` 仍执行服务端校验。`start_tasks` 默认为 `false`，原两步路径保留。
- 完整单元测试：`599 passed in 1110.09s`。`python -m compileall -q app/v1 app/core` 与 `git diff --check` 均通过。
- 未重启正在运行的 DevWerk 服务，也未对现有 Project 做黑盒回归；运行中的服务尚未加载本次代码。
