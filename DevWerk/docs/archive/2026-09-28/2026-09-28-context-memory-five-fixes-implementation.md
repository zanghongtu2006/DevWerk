# Context / Memory 五项缺陷修复记录

方案：[五项缺陷代码修复设计](../2026-09-27/2026-09-27-context-memory-five-defects-code-fix-plan.md)

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

实施范围：

- `AgentRepository.session_handoff` 按 Project / Session / Worker / Requirement 取最新 Session snapshot。`AgentRunPreparer._append_session_history` 在当前 Assignment 历史前加入有来源、有限大小的交接 reference；当前合同仍由唯一 current frame 提供。
- `agent.worker.replace` 返回新 Session 的 snapshot ID；`agent.context.compact` 对 idle Worker 的检查移入写事务，并返回 snapshot ID / revision。
- `context_projection.project_handoff` 对大摘要输出 hash、引用与部分连续原文，标明 `content_complete=false`；短摘要可完整送入。完整存储内容未被覆盖。
- `agent.context.read` 保持原消息目录调用方式，新增 `snapshot_id` 或 `source_message_id` 精确分页。Repository 验证叶子 Worker 的 Session 归属；`slice_source_text` 的 next_offset 按实际返回字符递进，读取中 hash 改变时拒绝继续。

覆盖用例：successor 实际 Assignment 输入包含交接内容；长中文摘要分页可原样重组且旧 Worker 无法读取继任 Session；空闲 Worker 摘要进入下一 Assignment，旧消息可按 ID 精确读取；保留旧 snapshot/session_history 查询行为。

验证结果：`test_scope_extension.py`、`test_context_handoff.py`、`test_persistent_agents.py`、`test_agent_recovery_and_fencing.py` 及 mailbox 通知断言共 **31 passed**。针对性的 successor / idle compact / 长中文分页与旧查询路径先行运行 **13 passed**。所有模型入口使用隔离注入模型，未调用外部 Provider。

## F3 — checkpoint 精确覆盖

实施范围：

- `ContextManager` 只把完整 assistant/tool 批次的真实持久化消息 ID 写入 v2 `coverage_json`，连续区间只合并相邻 ID；多次压缩累计先前的精确覆盖集合。归档失败、缺少来源 ID 或失去执行权时不提交 checkpoint。
- `AgentRepository.session_history` 对 v2 checkpoint 只排除覆盖集中的消息。旧 checkpoint 无精确集合，保守回放原始 Assignment 消息，避免沿用 `through_message_id` 误删仍有效的指令；下一次压缩可生成 v2。
- Worker mailbox 消费事务直接返回刚写入 AgentRun 的 `source_message_id`；当前 Assignment frame 也保留真实 ID。`session_replay` 合并相邻自然语言消息时保留各自的 source ID，供继续压缩与审计使用。

覆盖用例：六条有效 Worker 输入夹杂另一 Agent 消息后压缩/重启；连续两次压缩集合累计；旧 checkpoint 原文回放并升级；Worker 消费 ID 与落库行一致。扩大回归涵盖上下文生命周期、恢复 fencing、持久化 Agent 与架构协议。

验证结果：相关测试组 **66 passed**。未调用外部 Provider。

## F4 — Memory 范围绑定

实施范围：

- `MemoryScopeContext` 显式承载 Project、Task、冻结 Workflow 修订与 Conversation Session 身份。自动 selector 缺少身份时，required 抛出范围错误，optional 在 manifest 记录 `scope_unavailable`，不会把 `scope_id=None` 解释成全 Project 同类记录。
- `WorkflowRuntime._input_for` 从 Task 绑定冻结修订；Conversation 从已持久化 Job 绑定主会话。Agent 工具上下文继承真实 Task/Conversation 身份，Worker 私有 Session 不作为 Conversation Memory 范围。
- `project.memory.search/write/supersede` 默认从真实执行上下文解析范围；显式 task/workflow/conversation ID 验证 Project 归属，写入预校验先于执行回执。`project.memory.read` 保留按引用读取旧数据，并标记未绑定旧记录。Project 管理性 Store facade 保持兼容，未自动迁移历史文件。
- Memory manifest 带选中记录的 `scope_id`；未绑定旧 workflow/task/conversation 记录不会混入自动 selector。

覆盖用例：两个 Workflow 修订与旧 Task 冻结读取；Project/Task/Conversation 选择；缺身份 optional/required；真实 capability dispatch 的默认写入、搜索与跨 Project 拒绝；旧未绑定记录按引用读取。

验证结果：`test_memory_scope_binding.py`、`test_memory_workcell_contract.py`、`test_scope_extension.py`、`test_runtime_contract.py`、`test_conversation_contract.py`、`test_conversation_reply_tool.py`、`test_architecture_conformance.py` 共 **79 passed、1 failed**。唯一失败 `test_persistent_column_worker_uses_same_tool_loop_and_declared_contract` 仍断言 F1 前的固定能力列表，实际在 F1 已增加 `agent.evidence.list`；与 F4 范围绑定无关，本项提交不混入该测试维护。未调用外部 Provider。

## F5 — Memory 预算与分页

实施范围：

- `ContextPolicy` 增加 ledger、handoff、Memory 总视图、单记录和候选上限。`MemoryManager.build_context` 限制自动候选数与合计字节数，保留文件 hash、revision、scope、来源 reference 与 `content_complete`；core 只载入 active 条目。manifest 记录截断和省略原因，部分来源提示按引用读取。
- Conversation 与 Column 均按 Runtime policy 构造初始有界 Memory。Column 若收到 Memory，自动获得 `project.memory.read/search` 两个只读工具；不增加写入能力。
- `ContextManager.prepare` 每轮从有 hash 的真实 Memory 来源重建摘要，按当前 tools、必要输入、估算比例与模型窗口分配；校准上升和 Provider 超窗重试时重新缩减。仍保留当前合同、Worker 输入和必要结构。旧工具批次按完整配对压缩；必要输入本身超限时报告 `required_context_overflow` 及各组件大小。
- `project.memory.read` 增加 offset、max_characters、expected_sha256，`MemoryManager.read_page` 基于现有可替换 Store 接口提供连续中文页；版本变化返回 `source_changed`。`project.memory.search` 每页最多返回八条有来源的短视图，带 `search_next_offset` 继续检索。工具结果二次投影保持页连续性、重算 next_offset；极小预算下可省略重复元数据，不跳字。
- 历史对话参考、handoff 摘录和执行证据在必要时继续缩减；来源 ID 与回读入口保留。对当前 Worker 指令或用户请求不进行静默裁剪。

覆盖用例：230 KB core 的 Main/Column Provider frame；Memory、工具 schema、ledger 和 artifact 混合输入；Provider 首次超窗后的更小重试；中文页经 512 字节工具投影完整拼接与变更检测；搜索目录有界；必要输入超窗明确报错；替换 Store 接口；handoff/ledger 小窗口投影；来源在装配后变更不混用版本。

验证结果：Memory、Context、Conversation、Runtime 相关回归 **144 passed、1 deselected**；唯一排除的是 F1 加入 `agent.evidence.list` 后未更新的旧固定能力列表断言，与 F5 无关。最后收紧中文分页投影并加入搜索 offset 后，F4/F5 与 checkpoint/handoff 组合回归 **25 passed**。测试均使用隔离数据库和注入模型，未调用外部 Provider。

## 回归断言维护（独立于 F1–F5 修复提交）

`test_runtime_contract.py::test_persistent_column_worker_uses_same_tool_loop_and_declared_contract` 的固定能力列表仍是 F1 前的值。仅把已存在的 `agent.evidence.list` 加入期望，不改变运行时代码；单独提交，避免混入 F5。

最终完整单元测试：**442 passed**（`pytest -q --disable-warnings --maxfail=1`）；隔离数据与注入模型，不调用真实外部 Provider。历史生产任务未自动恢复或重跑；真实模型任务的交付效果仍需发布后按原有操作验收。
