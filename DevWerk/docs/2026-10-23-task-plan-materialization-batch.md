# 已保存 TaskPlan 的多次 task.create 同批拒绝

小说黑盒 Project `prj_e3eca7efeb4d41e094906a9deea81c51` 的第三轮：`task.plan.validate` 最终有效、`task.plan.save` 成功，但模型同批提交同一 Plan 的三个 `task.create`，`AgentToolBatchExecutor` 在副作用前全部拒绝，错误为 `Queue one Task action alone...`，三次重复后 `ConversationProtocolStalled`。真实 Task 数为 0。Agent 最终说计划已保存是事实，说任务未启动也是事实。

`V1Store.materialize_task_plan` 一次调用会原子创建完整 Plan 的全部 Task，后续同 Plan 不同 `proposed_task_ref` 读取已有 Task。因而同一批针对同一 Plan 的 `task.create` 是可安全幂等处理的；现有“一批只能一个”限制比领域语义更窄，直接阻断六章 Task 图。

最小修复：仅允许 Conversation 同一批全部为 `task.create` 且 `task_plan_id` 相同的调用；任何混入其他 Task 控制或 Tool 的批次仍拒绝，已排队后仍只允许 `conversation.reply` 释放规划租约。保持一次计划原子物化与调用回执，不改变调度架构。增加批处理回归，证明同 Plan 多引用只创建一次完整图；不同 Plan/混合工具继续走原拒绝分支。之后新 Project 复测。

实施：`app/v1/agents/agent_tool_execution.py` 仅放行同一计划的 `task.create` 批次。新增真实 Conversation Gateway + AgentCore 定向测试，同批引用两章，确认仅物化一个有序 Task 图且两条调用均成功。该测试在允许 Windows asyncio 建立本地 socketpair 的环境下 **1 passed**；受限沙箱中的两次尝试卡在事件循环创建，已中断，不计为代码失败。真实 Project 复测另行核对。
