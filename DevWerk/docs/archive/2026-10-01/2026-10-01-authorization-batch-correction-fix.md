# P0：授权调用从违规批次改为单独提交仍被误判为重复失败

状态：方案先行。隔离黑盒 Project `prj_0e951359e1e14f4a95b8abcfb82bb11e`（8001，`data/devwerk-retest7.db`）六轮讨论全部成功且零 Workflow/Task。第七轮用户明确授权；模型首次同批调用 `conversation.execution.authorize` 与只读 `loop.inspect`。Runtime 按独占调用规则拒绝整个批次，回执写明“单独提交授权，批次没有效果”。模型下一步照做，使用完全相同的授权参数单独调用，却被 `repeats_failed_operation` 当作同一失败操作，立即报 `ConversationProtocolStalled`。本次仅 2 次模型迭代、3 次工具调用，无 Requirement/Grant/Task 创建。

## 根因与修复边界

`app/v1/agent_tool_execution.py` 正确要求未授权的 `conversation.execution.authorize` 单独提交，防止同批其他操作越过授权边界。`app/v1/execution_ledger.py::repeats_failed_operation` 却只比较 capability+参数及是否失败，未区分“这个调用本身失败”与“它所在批次因调用组合无效而整体未执行”。后者下一次改成独立调用时，是有效纠正而非无进展重复。

只在重复失败检测中忽略具有 `ConversationTurnProtocolError`、`rejected_before_effect` 且明确写着 `This batch had no effects` 的旧批次回执；真实参数/权限失败继续防重复。独占批次规则保持不变，完全相同的违规批次仍由 `CompletionGuard.observe_rejection` 拒绝。修改限于 `app/v1/execution_ledger.py`；回归测试放在 `tests/test_conversation_contract.py`，模拟先同批授权+只读、再按回执用同参数单独授权、随后做实际交付。核对只有一次 Grant/Requirement；相关测试通过后独立提交。

重启隔离服务加载修复，新 Project 原样重放七轮，不在当前失败 Project 上人为注入 Grant。验收继续以 Kanban 交付目标为准。
