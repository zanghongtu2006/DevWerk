# 验收列行为证据契约未显式呈现，导致完成协议停滞

## 现场证据

- 全新项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 的 Workflow Plan 把 `accept` 列纳入 `task_contract.required_behavior_columns`。该列提交 `delivered` 前必须在本列取得一次真实、成功的 `project.command.run` 行为验证收据；既有 `system_test` 收据不能替代本列要求。
- `accept` Worker 的指令要求读取证据并写 `FINAL_ACCEPTANCE.md`，但发给 Worker 的运行 envelope 只展示 `completion_checks`，没有展示 `required_capability` 和“文件查看、打印不属于行为验证”的规则。Worker 因此调用 `cmd /c type FINAL_ACCEPTANCE.md` 与 `cmd /c echo ...`，两次都 exit 0，却连续收到笼统的“requires a successful project.command.run receipt”。
- `app/v1/services/completion_admission.py` 实际用 `known_nonbehavior_command` 排除了这些命令；拒绝逻辑正确，错误文字未说明“已经有成功命令，但它们不是行为验证”。`CompletionAttemptGuard` 在五次被拒后使 `accept` Run 中断、Task 进入 `recovering/paused`，实际阻塞交付。

## 最小修改

1. `app/v1/agent_prompt.py::build_run_envelope`：仅对存在 `completion_contract` 的 Column，在 envelope 中列出每个 outcome 的 `required_capability` 和 `evidence_requirement`；当要求 `project.command.run` 时，明确要求本列运行与任务相关的测试或验收命令，并说明 `type`、`echo`、`dir` 等非行为命令不满足。只传递 Runtime 已有契约，不新增权限、状态或 Workflow 规则。
2. `app/v1/services/completion_admission.py::CompletionAdmissionService.evaluate`：保留当前严格拒绝。若已有成功的指定能力收据，但均被 `known_nonbehavior_command` 排除，返回具体原因和可操作的纠正提示；若完全没有指定能力收据，继续区分“未执行”。不把文件存在、打印或失败命令当作通过。
3. 测试覆盖：在现有 prompt/完成协议测试中验证 `required_capability` 可见、非行为命令拒绝理由明确、真实测试命令通过。运行相关单元测试，不修改其他状态机逻辑。

## 恢复与黑盒复查

- 等当前 Conversation Job 到达安全边界后加载补丁；通过公开 Project 对话让 Conversation Agent 在同一 Task 上恢复 `accept`，实际重新执行与 Task 相关的测试命令后提交。用户反馈同时要求处理文件型 H2 与文档不一致，并在配置变更后重测。
- 最终以 Task 到 `done`、相关命令退出码 0、交付文档与配置一致为通过；若 Agent 仍无法恢复，记录新的实证再针对该点修复，不扩大架构。
