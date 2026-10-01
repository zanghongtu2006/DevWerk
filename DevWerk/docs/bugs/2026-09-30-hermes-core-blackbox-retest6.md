# Hermes 核心分支黑盒复测 6：已授权重放修复后

加载提交 `995ca50`，无运行 Job/Run/Task 时重启服务。新 Project `prj_e19f8da8a7264d95aebae7d5eb5ff7c3`，目录 `data/projects/hermes-core-login-retest6-20260930-200237`。经正式 HTTP API 原样发送相同七条消息：前六轮逐轮讨论并验证零 Workflow/Task，第七轮明确授权。执行中约三分钟查看 Kanban，不代 Agent 写交付物；以完成 Task 目标为唯一成功条件。

| 轮次 | Job | 结果 |
| --- | --- | --- |
| 1（讨论） | `cjob_746ec3b627c3467993c63b80694dfdc4` | succeeded；Workflow 0，Task 0。 |
| 2（讨论） | `cjob_9f6e7887377b4decb61ff8a47ef8b538` | succeeded；Workflow 0，Task 0。 |
| 3（讨论） | `cjob_2d2127dedff74aa0b3767dfe97b95dcb` | succeeded；Workflow 0，Task 0。 |
| 4（讨论） | `cjob_3a0a0c97fdf4401c843b54a446eaec7e` | succeeded；Workflow 0，Task 0。 |
| 5（讨论） | `cjob_87bbd8adcf974fb4a87b404aeab6516d` | succeeded；Workflow 0，Task 0。 |
| 6（纠偏讨论） | `cjob_b6528b9ca1c04d9d86aae5bc4416d506` | succeeded；Workflow 0，Task 0。 |
| 7（明确授权） | `cjob_4f97227d9ca341dda9c1dbb24ba3d5aa` | `failed`：100 次迭代、136 次工具调用，`ExecutionBudgetExceeded`。但已创建 Task `tsk_14883764259f4129bcfe4dfef60eeecb`，Task 由 Supervisor 独立推进。 |

## 已观察到的交付阻塞

授权 Run 在第 61 次工具调用创建 Task，随后继续重复授权、Loop apply、TaskPlan/Requirement 文件操作，尝试重复建 Task；重复身份、文件收据错误占用后续预算。`app/v1/context_manager.py` 的初始 `authoritative_project_state` 在同一 Run 中未随 Workflow/TaskPlan/Task 变更刷新，是已确认诱因；修复见 `2026-09-30-live-planning-state-refresh-fix.md`，提交 `fb368e1`。新代码尚未用于这个 Project。

Task 冻结的五个验收检查引用 `scripts/backend_acceptance.py`、`scripts/frontend_acceptance.py`、`scripts/e2e_acceptance.py`、`scripts/delivery_acceptance.py`、`scripts/final_acceptance.py`；没有 Column 可在其声明的文件写入边界内创建这些路径。入口校验修复见 `2026-09-30-acceptance-script-ownership-fix.md`，提交 `c764172`。当前旧 Task 不会被修改；继续观察它的真实结果，再启新 Project。

首次后端 Assignment 在 94 次模型迭代、141 次工具调用后耗尽累计预算，Task 转为 `recovering/paused/blocked_runtime`，前两列已成功。再次通过同 Project 正式对话 API 明确授权恢复（Job `cjob_5d54e19cb0ec44ffa8dbb3ae7fe15c7a`），只要求 Agent 自己读取系统失败记录。Agent 执行 `task.retry`，建立新的后端 Column Run；但 `agent.worker.send` 把 Session ID 当作 Assignment ID，被拒绝。新 Worker 的 `current_assignment.input` 实测没有上一轮预算失败详情。修复见 `2026-09-30-retry-failure-frame-fix.md`，提交 `c37a946`。

恢复 Job 在成功排队后继续反复读项目文件，持有 Project 规划租约，调度器拒绝启动待执行 Worker；最终 Job 自身也在迭代/工具预算上限失败，Worker 随后才启动。修复见 `2026-09-30-conversation-handoff-yield-fix.md`，提交 `d1c8343`。由于 Worker 已启动，没有为了加载这些提交而重启服务。

当前交付物还出现独立的产品偏差：后端 `ClientIpFilter` 和 `LoginService` 读取 `X-Forwarded-For`，与用户第 4、6 轮确定的“只用直连地址，不信任 X-Forwarded-For”相反。黑盒测试不代 Worker 修改项目文件；观察后续审查、测试、验收列能否发现并反馈。

## 终态补录

第二次 `backend_development` Run `arun_6af12b9482a44374bcfbd601e74fe8ab` 后来以 95 次迭代、116 次工具调用再次耗尽 Assignment 累计预算。Task `tsk_14883764259f4129bcfe4dfef60eeecb` 留在 `recovering/paused`，错误为 `ExecutionBudgetExceeded: Assignment cumulative model/tool/resume budget exhausted`；未进入 frontend/test/delivery。该 Project 没有完成交付，后续新 Project 复测，不把旧 Task 的冻结 Workflow 热改。
