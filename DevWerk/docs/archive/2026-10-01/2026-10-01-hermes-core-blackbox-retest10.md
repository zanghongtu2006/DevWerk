# Hermes 核心分支黑盒复测 10：已有验收脚本修复责任准入

状态：授权阶段失败，已复盘。8000 服务于 2026-10-01 11:29（Asia/Shanghai）重启，加载 `bbec693` 等提交。相同的六轮讨论及第七轮授权输入直接从复测 9 的用户消息复用；本次不直接修改 Project 交付物。

Project：`prj_c691d974eb954980b31c490dd40925f4`；目录：`data/projects/hermes-core-login-retest10-20261001`。六轮 Job `cjob_a369462aa0e54ca8ba8dcd76294b7f5c`、`cjob_351c723dfae34d70ad2a969e0d775b10`、`cjob_0a03f37642f14963b2258e199fd165fd`、`cjob_facd44ff792d4f2ebf1d412f2438dcaa`、`cjob_e1a635f7b96d400fadc88af35c282155`、`cjob_c5304aa2a5924bb58f1b08461ed50404` 均 succeeded，每轮后 Workflow 和 Task 数量均为零。第七轮授权 Job `cjob_b0c92eebaebf49f5931bca68cfcb3f8e` failed，`ConversationProtocolStalled`；仅形成 `loop.apply` 的初始 Workflow，无 Task。

工具顺序：首次 `workflow.acceptance.runners` 用 Maven 命令充当结构化行为报告入口，被拒；主 Agent 写了根目录 `scripts/*.ps1`，第二次 runner 因没有本列修复责任而被正确拒绝。随后高级配置把后端、前端入口移动到各自目录，却把 `delivery` 的脚本指向 `backend/scripts/verify-delivery.ps1`，被 `BehaviorRunnerScriptUnowned` 拒绝；模型又重复相同配置，重复失败保护终止该 Job。这不是第九次未检出冻结脚本的问题，修复后的准入生效了。

本次发现模板缺口：`delivery` 和 `accept` 都是必需行为验收列，却没有独立验收脚本的可写目录；`system_test` 已有 `scripts/acceptance/**`。按 `2026-10-01-software-loop-acceptance-script-write-paths-fix.md` 最小补齐模板，提交 `560d423`，相关测试 27 项通过。需从新 Project 用相同七轮输入复测；本 Project 不补救为成功。
