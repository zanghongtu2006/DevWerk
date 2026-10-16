# Tests 按用例归并方案（2026-10-16）

## 目标与基线

从 `main` 派生 `codex/test-case-suite-consolidation`，仅调整测试代码与测试目录文档。当前 `tests/` 有 44 个 `test_*.py` 模块、502 个顶层测试函数；`pytest --collect-only` 收集 538 个参数化用例。文件按历次修复阶段逐渐增加，相关用例散落于“P0 regression”“completion”“context”“conversation”等模块，跨测试模块导入辅助函数，查找场景边界成本较高。

本轮以**被验证的行为场景**为单位归并到约 25 个模块；保留现有 538 个收集用例及其断言，暂不因名称相近删除测试。只有能证明覆盖完全等价的重复用例才可另行删除。应用代码、Loop 和协议保持不变。

## 归并映射

| 场景 | 目标模块 | 并入的现有模块 |
| --- | --- | --- |
| Project 文件与命令解析 | `test_files_contract.py` | `test_command_resolution.py`, `test_project_search_bounds.py` |
| Column 完成协议与有向边验收 | `test_completion_protocol.py` | `test_completion_evidence_guidance.py`, `test_worker_selected_acceptance.py`, `test_workflow_transition_authority.py` |
| Assignment 上下文连续性 | `test_context_lifecycle.py` | `test_context_checkpoint_replay.py`, `test_context_handoff.py` |
| 项目记忆范围和预算 | `test_memory_scope_budget.py` | `test_memory_scope_binding.py`, `test_memory_workcell_contract.py` |
| Conversation 回合意图与工具回复 | `test_conversation_intent_contract.py` | `test_conversation_partial_effects.py`, `test_conversation_reply_tool.py` |
| Conversation Session 与运行索引 | `test_conversation_contract.py` | `test_conversation_session_run_index.py` |
| Worker 身份、Assignment 与执行隔离 | `test_p0_assignment_regressions.py` | `test_agent_recovery_and_fencing.py`, `test_persistent_agents.py` |
| Task 计划保存和入口准入 | `test_task_entry_admission.py` | `test_task_plan_compiler.py` |
| 工具 Registry 与 MCP 桥接 | `test_capability_contract.py` | `test_mcp_tool_bridge.py` |
| 项目监督与失败可见性 | `test_project_supervision.py` | `test_failure_transparency_contract.py` |
| 全局配置与日志 | `test_global_settings_contract.py` | `test_logging_contract.py` |
| 仓库架构与发布资产 | `test_architecture_conformance.py` | `test_release_assets_contract.py` |

保留独立模块的场景包括 Web API、Workflow/Loop 定义、Runtime 执行与恢复、Provider、Store、范围延续和软件上下文评估。这些模块各自拥有足够独立的被测边界，继续合并会产生过大的单文件或模糊职责。

## 迁移约束

1. 保留每个测试函数、参数化标记、fixture 与断言；以场景注释分隔原有测试段，避免大量逻辑改写。
2. 更新跨测试模块辅助函数的导入路径，处理合并后的同名导入（尤其 `turn` 和 `AgentRunSpec`）。确认没有同名顶层测试/辅助定义覆盖。
3. 更新 `docs/v1-test-contract.md` 为新的用例地图；提供 `tests/README.md`，使新增测试按行为场景进入现有模块。
4. 使用收集清单比较迁移前后测试名称和参数化数量，检查删除的 19 个模块没有遗漏用例。运行聚焦测试和完整测试，记录与发布基线的差异。

## 验收

- 测试模块数由 44 降至 25；收集数保持 538，测试函数名称与参数化标识保持一致。
- `pytest --collect-only`、Python 编译和文档链接检查通过；应用代码无变更。
- 全量回归记录实际结果。当前发布基线是 535 passed、3 failed（提示词长度一项，Conversation 回复原话两项）；本次整理不借删除或跳过测试掩盖这三项。
