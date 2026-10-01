# P0：高级验收配置允许没有报告参数的行为命令

第九次黑盒 Project `prj_4859dd2f5bd74077a313f2b109258fb4` 的冻结 Workflow 除了未拥有的直接 `.bat` 脚本，还把内联 PowerShell 空场景报告生成命令作为 system_test 行为验收，把只检查报告文件存在的命令作为 delivery/accept 行为验收。三者均没有把各自 `report_path` 作为独立命令参数。`workflow.acceptance.runners` 已强制这一点，高级 `workflow.acceptance.configure` 与直接发布/TaskPlan 准入未统一，直到 Column 执行才会发现报告缺失或无场景。

本次只统一一个可确定的入口合同：`app/v1/services/task_plan_compiler.py::acceptance_diagnostics` 对声明 `report_path` 的行为命令要求该路径作为 `argv` 独立参数；返回 `BehaviorRunnerReportArgumentMissing`。`app/v1/capabilities.py` 的高级配置及直接发布把该诊断视为无法发布的合同缺陷，与现有 `NonBehaviorCheck` 同层处理。Runtime 仍负责验证报告新鲜度、结构与每个场景的真实结果；这里不尝试静态解析 PowerShell 或证明脚本语义。

更新现有测试 fixture，使其显式携带报告参数；加入回归验证内联报告路径、完全缺失的报告参数均被拒，而合法独立参数可准入。保持 Workflow 自由度和其他命令格式不变。单项提交。
