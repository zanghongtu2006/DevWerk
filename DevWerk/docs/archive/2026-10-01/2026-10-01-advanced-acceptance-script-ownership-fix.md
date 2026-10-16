# P0：高级验收配置绕过脚本所有权校验

状态：方案先行。第八次黑盒 Project `prj_7bf8884364104924b67987f894163a96` 的授权 Run 已创建 Requirement/Workflow。模型先向 `workflow.acceptance.runners` 提交不完整场景，被 schema 拒绝；随后改用 `workflow.acceptance.configure` 分列发布验收检查，`ready_for_task_plan=true`。其中 backend/frontend 检查调用 `tests/backend_acceptance.py`、`tests/frontend_acceptance.py`，而 DDD Loop 的两列分别只允许写 `backend/**`、`frontend/**`；delivery/accept 也引入本列及前列未承诺创建的独立 `tests/*.py`。这些脚本当前不存在。第六次黑盒已证明这类冻结路径会阻断后续执行。

## 根因

提交 `c764172` 只在简化入口 `_workflow_acceptance_runners` 校验脚本所有者。高级入口 `_workflow_acceptance_configure` 修改并发布同一个 Workflow revision，却没有调用该规则，造成同一冻结合同的两个入口验收标准不一致。`task.plan.validate` 只检查行为检查和场景覆盖，不核对可创建脚本。不能依赖模型一直选择简化入口。

## 最小修改

将现有脚本所有权检查抽到 `app/v1/capabilities.py` 的私有共享函数，针对 Workflow definition 中可识别的 Python/Node/PowerShell/shell 脚本行为检查执行。`_workflow_acceptance_configure` 在发布前、同一事务内检查修改后的完整列集合：脚本已存在，或本 Column 可写，或与更早 Column 有权创建且已配置的脚本完全相同。未声明 `writable_paths` 的 Column 保持原本不限路径语义。`_workflow_acceptance_runners` 仍校验专用脚本格式、报告参数、场景，并调用同一个配置入口，不重复维护所有权逻辑。高级入口其他非脚本命令保持现有语义；不重构 Workflow 图、权限或验收报告格式。

在 `tests/test_context_acceptance_regression.py` 加回归：高级配置的 backend `tests/*.py` 被 `BehaviorRunnerScriptUnowned` 拒绝且 revision 不变；合法的 backend 自有脚本以及 delivery/accept 复用前序 system_test 脚本可以配置。保留简化入口相关测试，并运行相关验收/Workflow 合同测试后独立提交。

当前第八次 Project 不热改冻结 revision；观察授权 Run 是否自行纠正，再用新 Project 原样重测。交付目标仍是最终 Task 通过 Kanban。

## 入口补查（`538e2f7` 之后）

复核发现直接 `workflow.publish` 也能发布相同的脚本验收合同。将同一个私有校验函数加入 `_workflow_publish`，使简化配置、高级配置、直接发布三个入口一致。增加直接发布的拒绝与 revision 不变测试。此补查不改变已经运行的第八次 Task 冻结版本。
