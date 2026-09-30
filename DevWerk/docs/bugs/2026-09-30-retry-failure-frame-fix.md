# P0：显式重试后 Worker 当前输入丢失上一 Assignment 的失败信息

状态：方案先行。实测 Project `prj_e19f8da8a7264d95aebae7d5eb5ff7c3` 的 Task `tsk_14883764259f4129bcfe4dfef60eeecb` 在 backend_development 达到 Assignment 预算，成为 `recovering/paused/blocked_runtime`。用户恢复对话 Job `cjob_5d54e19cb0ec44ffa8dbb3ae7fe15c7a` 已读取 Task 并调用 `task.retry`；新 Column Run 已创建。Main Agent 尝试 `agent.worker.send`，却把 Session ID 填作 Assignment ID，发送失败。系统不能把新 Worker 得知失败详情寄托于这条可失败的消息。

## 根因

`recovery_manager.retry_task` 对已阻塞 Assignment 建立新 Column Run/Assignment，并把 Task 的 `error` 清空以便继续。`runtime._input_for` 从 Task 当前状态、计划和上游产物组装新 Worker 输入，但没有读取前一个被中断的 Column Run。`AgentRunPreparer.prepare` 只查同一个 `column_run_id` 的 `previous_agent_failure` 与 `unresolved_acceptance_failures`；新 Run ID 因而查不到。Worker 虽可能通过历史 Session 自行发现错误，当前 Assignment 的确定性合同没有它，违背失败信息必须带入恢复交互的要求。

## 最小修复

只在 `runtime.WorkflowRuntime._input_for` 的当前输入中补一个有界 `retry_failure`，条件为 Task 仍携带 `failure_code` 且当前 Column 最近有状态为 interrupted/failed 的旧 Run。字段包括上一 Column Run ID、`failure_code`、旧 Run 的短 `runtime_error`、该 Run 的最近失败验收检查，以及已有 `latest_agent_failure` 的短拒绝信息。不复制完整命令日志、旧 Session 或所有失败历史。不要求 Main Agent 发送 Worker 消息；重试仍保留同一 Worker Session，新 Assignment 仍有独立预算。Task 成功跨列后，现有 `finish_run` 清除 `failure_code`，后续输入不再带旧失败。

在 `tests/test_p0_assignment_regressions.py::test_explicit_retry_after_assignment_budget_exhaustion_gets_new_run_and_budget` 的现有回归里加入断言：即使 `clear_context=True`、没有成功的 `agent.worker.send`，新 Worker 的 `_input_for` 也包含准确的预算失败与旧 Run ID；完成后不污染下一列。保持 `test_context_lifecycle.py` 对同一 Column Run 内恢复的已有检查。相关测试通过后单独提交。

旧服务不热加载修复。新 Project 的恢复测试需验证实际 Worker 的 `current_assignment.input.retry_failure`；主要验收仍是 Kanban 推进并完成任务目标。
