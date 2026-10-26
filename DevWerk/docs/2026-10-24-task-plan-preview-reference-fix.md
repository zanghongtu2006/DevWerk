# Task Plan 预览引用提交修正案（2026-10-24）

## 黑盒证据与责任边界

新 Project `prj_90d3ca61b97a4f1da13a889f9f45cc54` 的 Conversation Agent 读取描述性 Loop 后，用 `workflow.create` 独立发布了 Workflow。Task 阶段两轮均阻塞：多次 `task.plan.validate(valid=true)` 后，模型在 `task.plan.save` 重新生成不同的计划对象；原始 Assistant Tool Call 已有差异，Runtime 没有改写。当前 `workflow.require_task_plan_preview=true` 要求相同规范化计划，因此保存返回 `TASK_PLAN_PREVIEW_REQUIRED`，重复后触发 `ConversationProtocolStalled`。这是通用 Task Plan 提交协议问题，不属于小说 Loop。

## 最小修改

1. `app/v1/protocols/tools/planning/capability_contracts.py`：`ValidationReceipt` 增加只在 `valid=true` 时给出的 `validated_plan_ref`（规范化输入计划的内容哈希）。`TaskPlanSaveInput` 接受 **二选一**：完整 `plan`，或 `validated_plan_ref`；保留 `start_tasks` 和 `proposed_task_ref`。类型层拒绝同时传两者或都不传，原完整计划调用继续有效。
2. `app/v1/tooling/capability_handlers.py::_task_plan_validate`：校验成功后返回该引用。`_task_plan_save`：引用模式只从同一 Project、同一 Agent Run 已提交且 `valid=true` 的验证回执取回当时的类型化 `TaskPlan`；核对哈希与回执，再执行现有 Workflow scope、入场校验、持久化与原子 Task 创建。设置关闭时，完整计划可直接保存；引用模式仍必须指向真实有效的预览。旧的同计划预览匹配路径保留。
3. `app/v1/tooling/builtin_tool_catalog.py`、`_task_plan_contract`、`DEVWERK.md`：告诉 Agent 在预览成功后传短引用提交，避免重新生成大型动态 JSON。只修改现有 Tool 描述和计划提示，不新增 Tool、状态表、Loop 规则或 Workflow 路由。
4. `tests/unit/workflow/test_task_entry_admission.py`：覆盖有效引用原子创建、无效/别的 Run 引用拒绝、旧完整计划模式、设置开关。`tests/unit/tooling/test_capability_contract.py`：核对输入输出 Schema。聚焦测试后跑相关 Conversation/Context 测试；完整套件视运行时间与剩余风险执行并记录真实结果。

## 不变量

- 引用只代表当前 Run 的有效预览，不是永久授权，也不能跨 Project/Run 使用。
- `save` 仍重新执行权威校验和原事务；预览通过不等于 Task 已创建。
- 模型不能通过错误对象、缺失回执或过期 Workflow 以引用方式绕过现有入场规则。
- 修改不依赖小说、文件路径或特定 Loop。

## 实施与聚焦验证

已按上述文件和方法落地。`task.plan.validate(valid=true)` 返回内容引用；`task.plan.save` 可只传该引用与启动选项，从同一 Agent Run 的已验证记录取回原计划，再走原有权威校验与事务。保留完整计划旧调用。新增回归覆盖有效引用、错误引用、跨 Run 不可用以及 Workflow 换版拒绝。Task 入场、Tool 契约和 Agent 指令聚焦测试共 **84 passed**；`compileall` 和 `git diff --check` 通过。尚待重启后新 Project 黑盒复测。

## 新 Project 黑盒复测

代码测试后，仅停止经过 PID、命令行和 8000 端口三重核实的 DevWerk 进程，使用 `startup.bat` 在 8000 重启。健康检查确认 Supervisor 和 Conversation Gateway 运行。保留旧失败 Project 及数据库记录；新建 `prj_81e052766ad44cdaa070c6852f1ea202`，用相同的两章悬疑小说场景重测。首轮自然语言 Job `cjob_42c4330013e54490a3f356f148bb2779` 成功，Agent 实际调用 `loop.inspect` 和 `loop.list`，未创建 Workflow/Task。第二轮 Job `cjob_a86b46a1f25d405fb66997beec2e2d87` 成功，仍只讨论故事方向。第三轮 Job `cjob_1605aa344f164baba2bb36450a598580` 已提交，用户明确了创作选择和两章交付要求；后续结果待记录。

第三轮已成功：初次 `workflow.create` 参数被拒后修正并发布 revision `wfrev_b071065296c045958fc9761fa9e5ff05`，`source_loop_key=null`。Agent 初次直接 `task.plan.save` 被当前预览设置拒绝；随后 `task.plan.validate(valid=true)` 回执带有 `validated_plan_ref`，再以该引用调用 `task.plan.save` 成功，实际创建并启动 Task `tsk_1b8fae46659b40fabdff97ebdeb35d86`。这证明修复针对的提交阻塞在真实模型调用中解除，但 Task 尚未到终态。第四轮自然语言状态询问 Job `cjob_60d778bc9dd4466c8c68587df7e5d72a` 已提交。

后续结果：该 Task 经 `baseline → chapter1 → chapter2 → review` 四个 Column Run，均成功，最终状态 `done`。项目内已生成 `设定基线.md`、`第01章.md`、`第02章.md`、`审查说明.md`。第五轮 Job `cjob_1dab2b5f9ea44a349e334ff5dfea0657` 成功，Conversation Agent 读取实际文件和 Task 状态后，明确报告三处未修的连续性硬伤，并说明虽然 Task `done`，当前正文暂不宜作为续写基线。审查文件也明确要求作者 Column 定向修改，但 Agent 自行设计的 review Column 以 `success → done` 结束；该图的 `rework` 只指向 `chapter2`，而问题跨设定和两章。此为 Workflow 完成口径与审查结论不一致的测试发现，不能把 `done` 等同于成稿质量通过。第六轮用户已同意返工，Job `cjob_d814f49ab03d4922826f4c24b7b5bd59` 已提交，观察 Agent 如何安排后续工作。
