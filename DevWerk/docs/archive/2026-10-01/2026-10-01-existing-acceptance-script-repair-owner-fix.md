# P0：已有验收脚本缺少 Column 修复责任人

## 黑盒证据

Project `prj_4859dd2f5bd74077a313f2b109258fb4` 的 Conversation Agent 在发布 Workflow 前创建 `scripts/run-backend-tests.bat`，后端列只可写 `backend/**` 与 `docs/backend-development.md`。该脚本执行后未新写有效报告；后端 Worker 可运行 26 个 JUnit 测试并在 `backend/` 生成替代验收脚本，但无权修正冻结合同实际调用的 `scripts/` 脚本。正式 `backend_tests` 验收失败后，Worker 错误选择 `requirement_gap`，需求与设计列均确认无缺口，Task 又回到后端列。

## 代码根因与边界

`app/v1/capabilities.py::_validate_acceptance_script_owners` 把 `ctx.files.resolve(script).is_file()` 作为直接通过条件。文件存在仅说明当前能启动，不保证执行 Column 能在失败时修正脚本。冻结验收使 Worker 无法改用 `backend/run-acceptance-tests.bat`。此处是合同准入缺陷；已有 Task 的修订版不会被热替换。

## 最小修复

1. 在共享所有权校验中，不再因脚本已存在而跳过可写责任校验。执行 Column 自身可写脚本，或更早 Column 可写且其验收检查明确引用同一脚本时允许复用；两者均不满足时返回 `BehaviorRunnerScriptUnowned`，要求把脚本放到本列可写范围或给出明确的前序维护列。
2. `workflow.acceptance.runners`、`workflow.acceptance.configure`、`workflow.publish` 已共用该校验，避免增设另一套规则。不要解析脚本内容推断测试质量；正式 Runtime 仍负责验证退出状态、报告新鲜度、场景与断言。
3. 在 `tests/test_context_acceptance_regression.py` 将“已存在即允许”的测试改为“已存在但无人可修正仍拒绝”，并保留本列可写、前序归属、无路径脚本等现有边界。跑目标测试，再跑相关 Workflow/TaskPlan 测试。
4. 单独提交该缺陷；重启服务后从新 Project 重测相同七轮用例，检查是否在授权时创建可维护的合同，再观察 Kanban 是否推进。

## 后续独立判断

`requirement_gap` 被用来报告验收脚本问题，导致无效回流。先用新 Project 验证上述准入修复是否消除该触发条件；若仍发生，应另案约束 rework outcome 与已记录反馈的责任列一致，避免把测试或脚本错误伪装为需求缺口。
