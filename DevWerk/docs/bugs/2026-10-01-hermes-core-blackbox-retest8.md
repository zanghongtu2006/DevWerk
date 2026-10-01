# Hermes 核心分支黑盒复测 8：授权批次纠正后

状态：失败，已复盘。第七次测试在授权的违规批次已被模型纠正为单独调用时误报协议停机；修复 `337ab57` 已通过 46 项相关回归测试。8000 原服务与 8001 隔离服务均无运行 Job/Task 后，关闭两者并在原 8000 端口重启加载全部提交；第七次数据库与 Project 保留为失败证据。

本次用原始七轮登录 Demo 用例创建全新 Project。前六轮逐轮讨论并要求零 Workflow/Task；第七轮授权后重点检查 Grant、Workflow、Task 是否形成可执行的冻结合同，Main Agent 是否让出租约，Kanban 是否推进并最终完成目标交付。出现失败时先核对正式 Job/Task/Agent Run、工具回执与失败信息，不代 Worker 修改交付物。

Project `prj_7bf8884364104924b67987f894163a96`，目录 `data/projects/hermes-core-login-retest8-20261001-061200`。前六轮 Job：`cjob_0dd85230948047f2a63026617d6fff8d`、`cjob_bffe9dd6871440649702c281e5cba2e3`、`cjob_6125fa90f3a24e54a9edc1c21572f65b`、`cjob_ece96616d942488c8205ca7b8f8d429d`、`cjob_cd40a301968047dc8277db19ef039c0c`、`cjob_8a16e5a4b39b484c9011b7a3b7453641`，全部 succeeded，逐轮零 Workflow/Task。第七轮授权 Job `cjob_fb83fc67f76a42d99964ee7d6dd122ba` succeeded，29 模型迭代、35 工具调用；Requirement `req_9c833841353a49ce9295b9fec8ee55bb`、Grant `grant_fa5342242ea94a31aef7d091648ad8e6` 和 Task `tsk_5af7332d5cfc490191b8adeb5345e043` 已创建。Agent 曾因把一列规划成一个 Task 被 `DuplicateTaskIdentity` 拒绝，随后自行改正成单 Task 跨列，`task.plan.save` 与 `task.create` 成功，主动 `conversation.reply` 让出租约。`requirements`、`domain_design` 两列 Worker 已 succeeded，进入 `backend_development`。

冻结 Workflow 的高级验收入口接受了无法由本列创建的脚本：backend `tests/backend_acceptance.py` 与 frontend `tests/frontend_acceptance.py`，后续 delivery/accept 也各有未由此前列承诺创建的 `tests/*.py`。这些脚本在创建 Task 时不存在。这是配置入口绕过脚本所有权规则；单项修复与直接发布入口补查分别见 `538e2f7`、`4e86ed0`，且不热改当前 Task。当时只记录风险，以下再依据正式 Run 回执判断终态。

## 后端实际执行与恢复

后端 Assignment `assignment_c8a0897803424b7f950551f873b24503` 共 70 Agent 迭代、122 工具调用，生成 Spring Boot/H2/BCrypt 代码、JAR 和自有脚本 `backend/tests/backend_acceptance.py`，但与冻结的 `tests/backend_acceptance.py` 不同。自有脚本运行 Spring Boot 时阻塞：其 GET readiness 检查对 404 立即返回失败，随后对仍存活的服务进程调用 `stdout.read()`；`project.command.run` 在 600 秒上限处 `timed_out=true` 并清理进程树，工具错误文本却写作 `command exited with code 0`。回执修复见 `dbe1aea`。后端最终因一小时 `ExecutionBudgetExceeded` 进入 `recovering/paused`，没有进入前端列。

自动监督 Job `cjob_71902ad9321242d8b210e95b2ecebf9a` 读取 Task/反馈并向旧 Assignment 排入 Worker 输入，但没有调用 `task.retry`；它仍以成功的 `work_result` 结束。当前 Task 仍暂停，Worker 输入不会自行调度。确定性完成条件修复见 `714b0f7`。

随后按同一 Task 发出明确恢复请求 Job `cjob_f2e5a829094b4a639cbe57cf382a3554`。Agent 的 `project.files.list('backend/**')` 返回空列表，尽管实际存在源码与 JAR，导致错误的“从零开始”恢复建议；尾部 glob 修复见 `dd39632`。Agent 在未绑定本轮执行授权时发送 Worker 输入，被 `TurnUnresolved` 拒绝；随后误选 `conversation.task.control.authorize(action='task.resume')`，再重复发送同一输入而 `ConversationProtocolStalled`。Job failed，Task 仍 `recovering/paused`。恢复中 Task 的 `resume`/授权入口修复见 `ac16a79`。

第八次测试未交付，原因包含冻结验收路径、后端 Worker 一小时预算用尽、监督未重试及恢复入口误判。每项 DevWerk 代码缺陷独立修复；后端脚本阻塞属于 Worker 生成的交付物问题，仍需在新 Project 验证 Workflow 是否能引导自修与最终交付。服务在测试后已退出；后续以具备数据库写权限的会话重新启动。沙箱内普通 `Start-Process` 无法写入原数据库，不能把该测试环境权限限制误归类为 DevWerk Runtime 故障。
