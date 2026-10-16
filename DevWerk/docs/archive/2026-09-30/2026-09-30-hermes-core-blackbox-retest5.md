# Hermes 核心分支黑盒复测 5：报告脚本合同后

加载提交 `c6af5e4`，无在途 Job/Run/Task 时重启服务。新 Project `prj_5d92846e04ae45f1857068d86f78405d`，目录 `data/projects/hermes-core-login-retest5-20260930-193743`。通过正式 HTTP API 原样发送相同七条用户消息（六轮逐轮讨论、最后明确授权）；前六轮逐项检查 Workflow/Task 为零。执行中只观察 Kanban 是否能推进和交付；每约三分钟观察一次，终态再看质量。不代主 Agent 建计划或修改交付物。

| 轮次 | Job | 结果 |
| --- | --- | --- |
| 1（讨论） | `cjob_ab8e77d361d542f2834338a257eef02c` | succeeded；Workflow 0，Task 0。 |
| 2（讨论） | `cjob_ab34bebb476a4c538cfcbb90117e9dc8` | succeeded；Workflow 0，Task 0。 |
| 3（讨论） | `cjob_451cab182e0c4b9abd350c00a8258d4b` | succeeded；Workflow 0，Task 0。 |
| 4（讨论） | `cjob_50246c693db4414888dc087cf9f1524d` | succeeded；Workflow 0，Task 0。 |
| 5（讨论） | `cjob_a65f5a8a63fe4e2da98de9d00701eaab` | succeeded；Workflow 0，Task 0。 |
| 6（纠偏讨论） | `cjob_f6b7d7ef9f514d26be8098b98ec0415a` | succeeded；Workflow 0，Task 0。 |
| 7（明确授权） | `cjob_d76ac245116e456cb72337e5044f021f` | failed；Grant、Workflow、已验证的 TaskPlan 均已落地，Task 0。 |

授权轮 18 个持久工具回执：一次 `workflow.acceptance.runners` 因 Maven/npm 非专用报告脚本被明确拒绝，模型随后改为 Python 脚本并成功配置四个 Column；旧高级入口成功补足最终验收，`task.plan.validate` 最后返回 valid=true，`task.plan.save` 成功，计划 `tplan_72a1460dae424c3bb803af797ee47556`。因此新的报告脚本准入发挥作用，规划合同不再阻止 Task 创建。后续模型一次批量提交 `conversation.execution.authorize(request=begin)`、`project.inspect`、`requirement.select`（指向同一个已授权 Requirement）；`AgentToolBatchExecutor` 将整批拒绝为 `ConversationTurnProtocolError`，模型重复原样失败操作后 Job 停于 `ConversationProtocolStalled`，未调用 `task.create`。冻结原 Grant 没有被更改。独立修复方案见 `2026-09-30-committed-authorization-batch-replay-fix.md`。
