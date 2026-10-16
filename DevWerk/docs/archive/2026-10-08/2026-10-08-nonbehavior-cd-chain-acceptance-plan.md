# 前端列用 `cd && dir` 绕过行为验证

## 现场证据

- 全新项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 第二轮 `frontend_development` Run（序号 13）没有运行 Vue 构建；其成功命令只有 `cmd /c cd frontend && dir dist`，输出仅列出旧构建目录。`column.complete` 随后被 Runtime 接受，该列进入 `succeeded`。
- 冻结 Workflow Plan 把 `frontend_development` 列列入 `required_behavior_columns`。`app/v1/services/task_plan_compiler.py::known_nonbehavior_command` 已把单独的 `dir` 识别为非行为验证，但对 `cd frontend && dir dist` 仅解开外层 `cmd /c`，未剥离无副作用的切换目录前缀，因而误判为可接受命令。
- 后续 `integration_review` 列实际运行了 `npm run build` 并成功，交付物未因此必然坏掉；缺陷在于 Column 自身的冻结行为证据约束被绕过，不能以别列偶然补跑掩盖。

## 最小修复

1. 只在 `known_nonbehavior_command` 中识别简单的 `cd`/`chdir`/`pushd` 加 `&&` 前缀，继续分析链尾命令。`cd frontend && dir dist`、`cd frontend && type report` 必须拒绝；`cd frontend && npm run build` 与真实测试命令仍允许。保留原有明确拒绝规则和 Runtime 有向图，不引入新调度状态。
2. 在已有 `tests/test_context_acceptance_regression.py` 或独立小测试中覆盖两种 Windows argv 形态和合法构建反例，并跑相关完成协议、Workflow 测试。
3. 为使本次黑盒测试满足严格判定，需要让同一 Task 的前端列在加载补丁后实际执行 Vue 构建，再由看板推进后续列直至 `done`。若当前 Task 已终态，则本轮记录为门槛缺陷暴露，修复后另起新 Project 验证，不篡改历史终态。
