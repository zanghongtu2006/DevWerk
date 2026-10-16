# 排查：实际页面未验证，Task 仍被标记 `done`

## 范围

只读核对 `data/devwerk.db` 中 Project `prj_e7f1da2db0f940e28bcd4a065352da51`、Task `tsk_b17a9101a04b4099afc08990a40694bd` 的 Workflow 快照、Column Runs、Agent Messages、Tool Invocations，以及该 Project 生成的需求、脚本与验收报告。另以 Project `prj_87e0c377f385460da4c257d600f699e3` 对照模板文字加强后的行为。没有运行产品测试、修改旧 Project 或改动核心代码。

## 可核对的事实链

1. Task Plan 的 `readiness.acceptance_criteria` 写有六条路径的真实执行要求；Task 输入给出六条场景，`docs/requirements.md` 的六行验收表同时列出**预期响应**和**预期页面提示**。同一需求要求前后端实际构建并运行、每条路径有截图或日志。项目生成的 `docs/test-plan.md` 更明确写明前后端联调、前端启动和每条路径的页面截图。
2. 持久化 Workflow 有 `requirements → domain_design → backend_development → frontend_development → integration_review → system_test → delivery → accept → done`。八列的 `acceptance_checks` 全为空，Workflow `acceptance_obligations` 也为空。`system_test` 的 `tests_passed` 是指向 `delivery` 的普通 outcome，`accept` 的 `delivered` 指向 `done`。
3. `system_test` Agent 确实读取 `docs/requirements.md` 和前端源码，但其消息明确计划“Full system E2E test with real HTTP calls”。它未通过 `project.files.read` 读取 `docs/test-plan.md`。最终 `scripts/acceptance/test-e2e-full.ps1` 只启动后端，通过 `curl.exe` 向 `127.0.0.1:8080/api/login` 发 JSON HTTP 请求；没有启动前端、浏览器交互或页面截图步骤。实际最终回执为脚本退出码 0、21/21 HTTP 断言通过，包含真实约五分钟等待。这个成功回执可证明后端场景，不可证明页面提示已显示。
4. `docs/test-report.md` 把上述 HTTP 结果写成“所有 6 条验收路径测试通过”，并称其为 E2E。`system_test` 最终 `column.complete(tests_passed)` 的结构化 `output` 是 `{}`，没有向下游交接“已验证/未验证”逐项记录。`delivery` 读取了上游结果，检查前端构建产物和后端/API 运行，未产生页面操作回执，报告仍继承“E2E 通过”。
5. `accept` Agent 通过 `project.files.read` 读取需求、需求追踪矩阵、交付报告、脚本及前端源码，但未直接读取 `docs/test-plan.md` 或 `docs/test-report.md`，也未执行 `project.command.run`。需求追踪矩阵同样明确逐路径列出“预期页面提示”，并把页面状态错误归给前端返工列。Agent Message 自制的逐项核对表却只把六条 HTTP 返回码分别标为通过；没有核对页面提示、前端启动或页面截图。之后写 `FINAL_ACCEPTANCE.md`，将六条路径全部标为真实通过，提交 `column.complete(delivered)`；该调用一次成功，Task 到 `done`。其结构化输出同样只列各场景的后端 `code` 预期/实际，没有页面提示的核对字段。
6. 数据库中此 Task 的 `v1_acceptance_results` 为 0 行，`v1_task_feedback` 为 0 行。`CompletionAdmissionService.evaluate` 只校验已声明的检查和运行回执；没有声明检查时，不会比对页面行为。`TaskFeedbackRepository.apply_completion_evidence` 在无反馈且无 Workflow obligations 时直接返回。`V1Store.finish_run` 验证 `delivered → done` 是合法边，于是提交终态。

## 责任边界与根因

Workflow 图没有自主判断“测试足够”；判断来自 `system_test`、`delivery`、`accept` 三个 Column Agent。首个语义错误发生在 `system_test`：把“接口 HTTP 六路径”命名为“完整 E2E 六路径”。`accept` 独立读取原始需求和脚本后仍复用同一个结论，漏掉需求表的页面提示维度。两列原有指令已经提到真实用户操作与未验证场景，但没有稳定防止该错误。`21/21` 是对**脚本自己定义的 21 项**的成功统计，不代表需求的所有维度都被覆盖。

Runtime 没有通用的“需求文字推断测试充分性”功能；本 Workflow 也没有任何冻结的执行检查/终态义务，所以 Runtime 只保证合法路由和工具协议，不会识别 HTTP 回执与页面行为的语义差异。**即使给当前脚本简单套一层 `acceptance_check`，它仍会以退出码 0 通过**：问题不仅是缺少正式检查，还在于检查对象自身只覆盖 HTTP，无法代表页面交互。`readiness.acceptance_criteria` 不参与此判定，其命名/可选性另见 `2026-10-14-readiness-completion-notes-followup.md`。

模板文字加强后的对照 Project `prj_87e0c377f385460da4c257d600f699e3` 中，各列检查仍为空、obligations 仍为空；`system_test` 依然尝试 `tests_passed`，却没有页面操作回执。该次调用被拒绝的直接原因是未清偿的失败命令回执，**不是**页面证据缺失，因此不能算该问题已由模板文字修复；该 Task 当前没有到 `done`。

## 下一步设计边界

不要通过恢复 `acceptance_criteria` 的强制语义或在 DevWerk 核心添加“必须浏览器测试”等项目类型规则来掩盖问题。应继续评估现有 Workflow `acceptance_checks` / `acceptance_obligations` 如何表达本次任务真正要求的执行证据，以及在没有可执行检查时 Column Agent 如何明确报告“未验证”而不是提交成功。形式检查只能证明确实运行了其声明的能力；对于不透明脚本，单一退出码不能证明其覆盖了需求的全部维度。最终修正须分别验证“缺项被阻止”和“合格证据可正常交付”，且不改变不需要独立验收的 Workflow。
