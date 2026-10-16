# Hermes 核心分支黑盒复测 7：隔离服务加载四项 P0 修复

状态：失败，已复盘。第六次 Project 的第二次 backend_development Assignment 当时在原 8000 服务运行，未中断它。为并行验证新提交，另启 8001 本地服务，单独使用 `data/devwerk-retest7.db` 与 `data/logs/retest7/devwerk.log`；Project 基础目录独立。第一次启动尝试把日志文件改名，因代码要求文件名必须为 `devwerk.log` 而退出；改用隔离日志目录。此用例与第六次相同：前六轮逐轮讨论且不得建立 Workflow/Task，第七轮明确授权同一个 Vue 3 + Java 17 Spring Boot/H2 登录与防刷 Demo；随后观察 Kanban 是否完成交付。

加载提交：`fb368e1`（同 Run 规划状态刷新）、`c764172`（验收脚本所有权）、`c37a946`（重试失败信息带入 Worker）、`d1c8343`（排队后主 Agent 让出租约）。每项已经独立提交。测试记录按 Job/Task/Column、失败恢复、验收结果追加；不会直接替 Worker 修改 Project 交付物。

Project `prj_0e951359e1e14f4a95b8abcfb82bb11e`，目录 `data/projects/hermes-core-login-retest7-20260930-222029`。六轮讨论 Job 依次为 `cjob_bb4a304688bb494496eaccd20e61cd5b`、`cjob_901dbc40615142c2a683150f735e356d`、`cjob_5a1d207f95214630b91cd76de22a0656`、`cjob_0c89dec0aafc4931bc266aaabd7face4`、`cjob_f7bf3c37c76349edad4624a6ec74e4da`、`cjob_0321865e8d0d4c1d8c67439a0a4f5fc8`；各自 succeeded，逐轮确认零 Workflow/Task。

第七轮授权 Job `cjob_53eb62ca180d4443a202870151c0b18f` 在 2 次模型迭代、3 次工具调用后 failed，`ConversationProtocolStalled`，没有 Requirement/Grant/Task。首次同批提交 `conversation.execution.authorize` 与 `loop.inspect`，Runtime 正确拒绝整个批次；模型随即用原参数单独提交授权，重复失败保护误判而终止。根因与单独修复见 `2026-10-01-authorization-batch-correction-fix.md`。此 Project 不补写授权或 Task；新版本重启后用全新 Project 原样重测。
