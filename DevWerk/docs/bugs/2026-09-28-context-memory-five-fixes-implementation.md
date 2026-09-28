# Context / Memory 五项缺陷修复记录

方案：[五项缺陷代码修复设计](2026-09-27-context-memory-five-defects-code-fix-plan.md)

执行原则：每项缺陷只在对应独立提交中改动其源码、测试和本记录；后一项不得混入前一项提交。使用当前服务源码、隔离数据库和注入模型验证。真实外部模型效果另行评估。

## F1 — 恢复 ledger 与模型输入分离

实施范围：

- `agent_models.py::AgentRunSpec.runtime_ledger` 接收内部完整证据。`runtime.py::_execute_agent` 从历史 invocation 重建完整 ledger，Await 补充回执去重后交给 AgentRun；不再把它放入 `context`。
- `agent_run_preparation.py::prepare` 把完整 ledger 留给工具执行与 completion，Column system envelope 去掉动态 context；当前 Assignment 只写一次有界 `execution_evidence` 视图。仍接受旧调用方放在 `context.action_ledger` 的形式，但不把全文送入 Provider。
- `context_projection.py::project_ledger` 只输出有限数量的状态、ID、错误和来源哈希，优先列出未解决失败。`agent.evidence.list` 用持久化 invocation ID 分页列出遗漏证据，完整结果继续由 `agent.result.read` 回读。
- 恢复历史先按持久化消息逐条过滤旧的 runtime Assignment frame，再进行 replay，避免它与真实 Worker 输入合并后失去可识别边界。
- Await 生成的临时恢复证据在新 AgentRun 下变成真实 invocation；该回执 effect_kind 仍为 `read`，不错误重置“连续无执行进展 Await”的保护。

覆盖用例：4×110 KB 合成日志恢复，完整 ledger 与有界模型消息分离；目录分页和结果回读；同 Assignment 多次重新装配；Await 完成回执与 repeated Await budget；36 轮大命令输出与 Provider overflow retry；完成协议和旧 Assignment 测试。

验证结果：`tests/test_architecture_conformance.py`、`tests/test_context_lifecycle.py`、`tests/test_p0_assignment_regressions.py`、`tests/test_agent_recovery_and_fencing.py`、`tests/test_context_acceptance_regression.py` 共 **70 passed**。目录中命令退出码与 Runtime 失败判断一致；随后补跑的命令失败目录、大日志恢复及 Await 回执 3 项 **3 passed**。初次扩大回归曾发现“反复 Await 无进展”被恢复回执误计为进展；修正 effect_kind 后重新运行整组通过。

## F2 — Session 交接摘要

待实施。

## F3 — checkpoint 精确覆盖

待实施。

## F4 — Memory 范围绑定

待实施。

## F5 — Memory 预算与分页

待实施。
