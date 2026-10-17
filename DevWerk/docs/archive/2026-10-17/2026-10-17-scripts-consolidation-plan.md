# scripts 目录收敛方案（2026-10-17）

## 范围

只整理 `DevWerk/scripts/` 中独立的开发诊断/评测脚本及相关说明，不改应用运行时、Workflow/Loop 协议或测试断言。Web → Conversation Agent → Workflow 的正常交付路径没有脚本依赖。

## 逐项判断

| 文件 | 处理 | 依据 |
| --- | --- | --- |
| `review_p0_runtime_2026_09_09.py` | 删除 | 日期型 P0 诊断探针，明确声明不是通过型回归。其重复 await、累计预算、持久队列、失败命令、旧租约和异步回执问题已有 `test_p0_assignment_regressions.py`、`test_p0_runtime_regressions.py` 的确定性用例。 |
| `verify_scope_extension_snapshot.py` | 删除 | 固定使用历史小说 Project ID 和章节 7–12 的快照模拟；`test_scope_extension.py` 已覆盖六章续写到十二章、旧 Task 不变和新 Task 创建。 |
| `eval_conversation_intent.py` | 删除 | 针对早期登录讨论/启动边界的专用多轮评测；现有 `test_conversation_intent_contract.py` 覆盖讨论、执行授权、状态回合和业务副作用。保留的端到端评测也会经过讨论、启动和创建 Task。 |
| `eval_worker_repair.py` | 删除 | 针对计算器的专用双轮故障注入；`test_repair_notification_contract.py::test_two_cross_column_repair_cycles_use_real_files_checks_and_same_workers` 覆盖同一关键合同。保留的端到端评测涵盖交付后修复。 |
| `llm_preflight.py` | 删除 | 单次“回复 OK”调用，仅测试当前 Provider 连通性，未被运行时、测试或 README 引用；配置/适配器合同已有 `test_provider_contract.py`，端到端评测可验证真实 Provider。 |
| `eval_software_context.py` | 保留 | 当前唯一完整的隔离真实 Provider 交付与返修评测；`test_software_context_eval.py` 直接导入其等待/恢复逻辑，服务 README 也引用该入口。保留输出目录及 `--resume` 隔离校验，不扩展场景。 |

## 验证

1. 删除前确认没有源码/测试导入或启动脚本引用被删文件，且历史 P0 诊断关键合同在现有测试中有对应覆盖。
2. 删除后确认 `scripts/` 仅剩 `eval_software_context.py`；清除 README 中对“scripts/ 多个 operational helpers”的过宽表述。
3. 运行 `test_software_context_eval.py` 及上述合同回归；执行 `compileall`、Markdown 链接检查和 `git diff --check`。
4. 单独提交并推送当前功能分支，不修改 v0.1.0 Release 标签。

## 实际结果

- `scripts/` 从 6 份收敛到 1 份，仅保留 `eval_software_context.py`；源码、启动入口与当前 README/测试无被删除脚本的引用。
- 保留脚本直接单元测试：3 passed。旧 P0、scope、讨论授权和跨 Column 修复对应的聚焦合同回归：10 passed。
- 全套测试收集仍为 538 项；`python -m compileall -q scripts tests` 与 `git diff --check` 通过。
- 首次在沙箱内运行聚焦 pytest 无输出并被停止；随后在普通进程环境运行上述 13 个测试全部通过。此次未调用真实 Provider，也未修改测试断言或应用运行时。
- 本次创建的三个具名 pytest 临时输出目录已清理。
