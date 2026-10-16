# Tests 用例归并验证（2026-10-16）

## 结果

在 `codex/test-case-suite-consolidation` 分支，`test_*.py` 模块由 **44** 个收敛到 **25** 个。迁移前后均收集 **538** 个参数化用例；逐项比较测试函数名与参数标识，缺失 0、新增 0。测试函数体的 AST 完全重复组为 0，因此本轮保留所有断言和用例。

应用代码、Loop 和配置没有变更。删除的 19 个测试模块已按行为场景并入 12 个现有模块；对已删除模块的跨文件导入为 0。`python -m compileall -q tests` 及文档/测试 diff 空白检查通过。

## 回归

- 关键归并模块：**143 passed**。
- 最终完整测试：**535 passed、3 failed**，耗时 371.35 秒；与归并前发布基线的失败集合完全一致。
- 三项既有失败：`test_platform_policy_is_compact_project_manager_identity`（3110 字符超过 3000 阈值）；`test_report_uses_actual_pending_state_not_model_claim` 与 `test_gateway_continues_cancelled_requirement_and_reports_six_created_tasks`（回复引用了未经验证的模型原话）。本轮未改应用代码或相关断言。

导入整理阶段曾误删一处嵌入式子进程脚本的辅助函数导入，导致一项额外失败；恢复该导入后单项测试通过，最终完整回归也回到上述基线。验证在 Windows 非沙箱环境运行，因为沙箱限制 TestClient 所需的本地 socketpair。测试数据库和输出位于忽略的临时数据目录，并在记录结果后清理。
