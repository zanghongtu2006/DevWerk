# 单一 Workflow 出边修正：实现与回归记录

对应方案：`2026-10-15-single-transition-minimal-fix-plan.md`。本轮只修正当前版本的路径判定冲突；未改服务进程、数据库中的历史 Project，也未针对软件或调研加入专用规则。

## 实际修改

- `domain.py` 的 `Transition.required_checks` 将正式检查引用与 `outcome → target` 放在同一条边；Workflow 校验检查 key、来源列可达性、重复引用和失效列。读取旧 Revision 时将缺少新字段的列级自动检查、旧 `acceptance_obligations` 规范化到边，规范化后全局义务为空。
- Agent 完成、capability-sequence 完成和 await/resume 最终提交都使用冻结 Workflow 中选定边。只有该边要求的当前列检查会执行；前序列回执由 `finish_run` 在同一事务核验。回执需属于同一 Task、Revision 和有效 Run，返工后的失效条件仍适用。
- `finish_run` 仍拒绝调用方传入与声明边不符的目标。`task.feedback` 不再于 `done` 施加第二终态门槛；反馈记录产生仅含 ID 的 Mailbox 通知，供 Conversation Agent 查看和监督，记录本身不选择另一条边。Task Plan 的 Agent 执行拓扑仍在准入阶段检查，移除了 `done` 时重复检查。
- `workflow.inspect` 与 `workflow.acceptance.configure` 向 Conversation Agent 暴露和编辑各出边的检查引用；旧全局字段仅作为输入兼容并在发布前规范化，不参加运行时判定。

## 已运行回归

- `tests/test_workflow_transition_authority.py`：9/9，通过。覆盖合法目标、反馈不改路由、缺/有边检查回执、无检查边和旧定义转换。
- 完成协议、上下文/验收、反馈与 Worker 回归合计 69/69，通过。
- 域、Runtime、Task 准入、能力协议、Loop 与 P0 Runtime 相关回归：初次 144/147；两处为旧语义断言，已更新；一处 Windows 子进程耗时断言单独重跑通过（3/3）。
- 故障注入中的边检查与恢复关键用例：4/4；await/resume 选定边检查：1/1；能力序列选定边检查：1/1；Workflow 检查编辑和边协议：11/11。`python -m compileall -q app/v1` 通过。
- 最终聚焦重跑（边路由、await/resume、Workflow 检查编辑）：11/11，通过。测试临时目录已清理。

## 尚未验证及实际边界

- Conversation 集成测试在当前沙箱的 Windows `asyncio.new_event_loop()` 创建 socketpair 时阻塞；超时栈停在 Python 标准库 `socket.accept`，尚未进入 DevWerk 对话逻辑。已中止**本轮测试进程**，未碰用户运行中的 DevWerk 服务。这部分不能报为通过。
- `test_p0_assignment_regressions.py` 全文件运行也未完成；已单独重跑并通过其中与本次边检查、重放保护和 await 相关的用例。不能把未完成的全文件运行计入通过数。
- 未进行新的真实 Provider 黑盒 Project。代码保证**已声明**的检查不能缺回执而通过；若 Conversation Agent 漏将用户要求写入 Workflow，或检查脚本本身并未测试其声称的行为，Runtime 不能自行发现。此前“未做页面操作但写成通过”的案例只有在 Workflow 明确把真实页面验证挂到成功边时才会被确定性拦截。
- `acceptance_criteria` 必填规划文字的改名/可选化属于另一份已记录修正案，本轮未实施；它不参与 Workflow 路由。

本轮未提交 Git，工作树中原先已有大量未提交修改；本文件仅记录本轮文件级变化与实际回归结果。
