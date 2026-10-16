# P0：已授权的重复调用拖垮已保存 TaskPlan 的交付

状态：方案先行，证据为 `prj_5d92846e04ae45f1857068d86f78405d` 的正式授权 Job。范围只修同一用户 Job 内已经提交的执行授权重放；不改首轮用户授权、Requirement 范围或 TaskPlan 验证。

## 现象与代码根因

Agent 已正确保存 `tplan_72a1460dae424c3bb803af797ee47556`，`task.plan.validate` 真实返回 valid=true。接下来的批次包含 `conversation.execution.authorize(request=begin)`、`project.inspect` 和同 Requirement 的 `requirement.select`。`app/v1/agent_tool_execution.py::execute` 无条件要求任何授权工具独占一批，因此三个工具全部产生协议错误且均无副作用；后续重复相同失败调用触发 `ConversationProtocolStalled`。`app/v1/capabilities.py::_authorize_execution` 仍按当前 work_intent 重建完整 resolution；授权已经写入后，新参数可能不同而被 `TurnAlreadyResolved` 拒绝。Provider 在 grant 后已收起授权工具，但模型可从同 Run 早先消息重建调用，因此仅隐藏工具不足以保证进展。

## 最小修复

`_authorize_execution` 首先读取当前 Job 的已提交 `v1_turn_resolutions`。若其 phase 是 execution，直接返回**原始** `turn_contract`、Requirement、work_intent 和 execution_grant_id，加 `already_authorized=true` 与“本次入参没有更改权限，请继续使用原 Grant”的明确字段；不建立第二 Grant、不更新 revision、不调用 `resolve`，即使模型本次改了 scope_summary/request 也不会扩权。若已提交的是 discussion/control，仍拒绝升级。首次授权仍走原验证和单独提交规则。

`AgentToolBatchExecutor.execute` 仅在当前 Job 尚未提交 execution boundary 时要求 `conversation.execution.authorize` 独占；已提交 execution 后的重复授权是只读重放，允许与检查/现有授权范围内合法工具同批。`conversation.reply`、首次授权、Task 控制授权继续独占。Dispatcher 的实际 grant/Requirement/revision 校验保留；其它批量业务工具仍各自审计，错误的 Requirement 选择不能扩展该 Grant。

测试在 `tests/test_conversation_contract.py` 注入真实 Agent Loop：首次授权单独提交，随后模型故意批量重放改变参数的 authorize + inspect + 同 Requirement select，要求三者回执不产生协议停机、原 Grant/Requirement/intent revision 不变，后续文件写入/事实报告完成。`tests/test_conversation_intent_contract.py` 保留直接 `conversation.turn.resolve` 已提交边界不可升级测试，并验证 discussion 不能通过重复 authorize 升级。独立提交。

## 新 Project 验收

服务空闲后重启；全新 Project 原样复用六轮讨论加最后明确授权。检查 TaskPlan 保存后能真正调用 `task.create` 并进入 Column；检查新冻结命令均为专用报告脚本，Worker/Task 沿 Kanban 推进。只在新的实测阻塞时继续最小修复，停止添加与交付无关的能力。
