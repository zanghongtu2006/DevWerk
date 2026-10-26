# Task Plan 校验后的上下文交接修复

## 现象与证据

项目 `prj_45a13d81e46d487bab029d2f967199ca` 的第一章 Task 已完成。用户确认扩展范围后，Requirement 和 Workflow 都成功进入 revision 2；后续两轮 Conversation Run 分别在 `task.plan.validate` 得到 `valid=true`，却没有调用 `task.plan.save` 或 `task.create`，最终重复 `workflow.inspect` 而停滞。后一轮的有效校验发生在 2026-10-24 03:08:11 UTC，03:08:21 UTC 就发生上下文压缩。

`ContextManager.prepare` 按目标比例删除完整工具调用组。Tool Schema 等固定输入已占较大预算时，该目标无法达到，于是循环继续删除最后一组已完成调用，包括刚生成的有效计划参数及其校验回执。摘要只保留部分参数引用；Agent 随后重复重建、核对和校验计划，无法稳定进入保存步骤。这是通用的上下文生命周期问题，与小说内容无关。

## 最小修改

1. `app/v1/context/context_manager.py`：在压缩时，只要仍低于硬窗口，保留最新一组完整的 assistant/tool 调用，优先删除更旧的组。若最新一组本身使硬窗口超限，则保持现有溢出保护。
2. `app/v1/protocols/tools/planning/capability_contracts.py` 与 `app/v1/tooling/capability_handlers.py`：在有效 Task Plan 校验回执中用类型化字段提示下一步使用相同计划调用 `task.plan.save`；无效计划不提供保存提示。校验依然只读，不自动保存、创建 Task 或改变 Workflow。
3. `tests/unit/agent/test_context_lifecycle.py` 与计划工具测试：覆盖固定 Schema 导致目标比例不可达时最近完整调用仍在上下文，以及有效/无效回执的下一步提示。

## 验证

先运行受影响单元测试，再运行相关 Agent/Conversation/Workflow 单元测试。服务重启后在当前项目发起自然语言继续请求，只以真实 `task.plan.save`、`task.create` 和 Task 状态作为结果证据。保留服务与数据现场。
