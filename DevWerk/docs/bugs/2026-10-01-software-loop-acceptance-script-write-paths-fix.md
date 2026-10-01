# P0：软件 Loop 的验收脚本责任范围不完整

## 复测证据

Project `prj_c691d974eb954980b31c490dd40925f4` 六轮讨论均 succeeded、零 Workflow/Task。第七轮已获得用户授权，但 `workflow.acceptance.runners` 首先拒绝把 Maven 命令当作结构化验收 runner；主 Agent 写入根目录 `scripts/*.ps1` 后，被可修复责任校验拒绝；再尝试把后端、前端脚本放入对应可写目录，却将交付脚本指向 `backend/scripts/verify-delivery.ps1`。`delivery` Column 的模板可写范围只有 `README.md`、`docs/delivery-report.md`、`startup.*`、`shutdown.*`，没有独立验收脚本的合理位置。高级配置拒绝后，模型重复完全相同的调用，授权 Job 以 `ConversationProtocolStalled` 结束，Task 尚未创建。

## 修改边界

保留每列一个 Worker、既有 Workflow 节点与验收合同，不放宽脚本责任校验。仅在 `loops/ddd-software-delivery/loop.json` 为需要行为验收且当前没有脚本目录的 `delivery`、`accept` 列分别增加 `scripts/delivery/**`、`scripts/accept/**`。`system_test` 已有 `scripts/acceptance/**`，后端和前端已有各自目录。主 Agent 可以在规划时把脚本放进相应目录，Worker 在本列失败时也能修正它。既有 `startup.*`/`shutdown.*` 交付文件范围保持原样。

在 `tests/test_loop_contract.py` 加模板边界测试：各必需行为验收列都有本列可写的脚本位置，并保持目录彼此独立。运行该测试及验收/TaskPlan 相关测试。单独提交并从全新 Project 重测同一七轮用例。

若主 Agent 在具备明确目录后仍重复同一无效调用，应单独复盘重复失败处理或工具反馈；不要通过放宽验收合同、伪造场景报告或人工改写 Project 交付物来绕过。
