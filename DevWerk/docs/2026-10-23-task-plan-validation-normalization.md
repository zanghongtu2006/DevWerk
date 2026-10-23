# TaskPlan 预检使用原始输入的阻塞

2026-10-23 小说回归 Project `prj_6fde95ef827b4add8c6db2c6b6727fa1`：上下文修复后，Conversation Agent 成功发布 `novel.production` Workflow，但 0 个 Task。首个 `task.plan.validate` 将章节编号以字符串 `"1"` 提交。共享编译器已按动态 `input_schema` 将其规范为整数；预检随后却把**原始** TaskPlan 送进图准入，得到 `Task admission values cannot be compared with 'lte': '1', 6`。保存路径先编译再图准入，预检与保存语义不一致。Agent 后续重复读取，触发 `ConversationProtocolStalled`。

最小修复：`_task_plan_validate` 在共享编译诊断通过时使用 `compile_task_plan` 返回的规范化副本执行后续 entry evidence、graph admission 和身份检查；诊断不通过时不以原始数据继续做可能误导的图比较。只修正预检与保存的一致性，不改小说模板、Workflow 或容错范围。新增定向回归，证明可规范的数字字符串在预检与保存一致；非法字符串仍被合同校验拒绝。之后启动全新 Project 复测自然语言建任务。

实施：修改 `app/v1/tooling/capability_handlers.py` 的 `_task_plan_validate`，共享编译器与保存路径一致地提供后续图准入输入；诊断已有错误时避免再对原始数据产生噪声。`tests/unit/workflow/test_loop_contract.py` 新增跨预检与保存的回归。定向 1 passed；完整小说 Loop 合同 22 passed。真实 Project 复测另行记录，不将单元测试等同黑盒通过。
