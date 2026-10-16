# P0：冻结验收脚本没有可执行的 Column 所有者

状态：方案先行；证据为 `prj_e19f8da8a7264d95aebae7d5eb5ff7c3` 的冻结 Workflow 与当前 DDD Loop 写入边界。此修复独立于会话状态刷新；不修改正在运行的测试 Project。

## 对交付的影响和根因

第七轮已创建 Task。冻结的五个行为检查分别调用 `scripts/backend_acceptance.py`、`scripts/frontend_acceptance.py`、`scripts/e2e_acceptance.py`、`scripts/delivery_acceptance.py`、`scripts/final_acceptance.py`。这些脚本目前都不存在；DDD Loop 的 backend Column 只可写 `backend/**`，frontend 只可写 `frontend/**`，system_test 只可写 `e2e/**`、`tests/**`、`scripts/acceptance/**` 等，delivery/accept 不可写验收脚本。因此 Task 即使启动，相关 Worker 也不能按冻结合同制作这些脚本。

`app/v1/capabilities.py::_workflow_acceptance_runners` 仅检查脚本在 Project 范围、扩展名以及命令中包含报告路径，未核对是否有 Column 可以在执行到该检查时创建脚本。`task.plan.validate` 因而允许无法交付的检查进入冻结 Workflow。这是 Task 入口合同缺口，不是 Worker 执行质量问题。命令运行器不审计命令产生的所有文件副作用；不能把绕开 Column 声明的写入边界当作正常交付路径。

## 最小改动

只收紧简化入口 `workflow.acceptance.runners`：在编译检查前读取当前 Workflow revision 的 Column 顺序和各 Column `metadata.writable_paths`。若脚本文件已经存在，可以引用；否则脚本必须属于本 Column 的可写路径，或与一个更早 Column 已配置的验收脚本完全相同，且该更早 Column 有权创建它。没有声明 `writable_paths` 的自定义 Column 沿用原本的不限路径语义。一次调用分批提交和同批提交都适用。任何无人能创建的新脚本，明确返回 `BehaviorRunnerScriptUnowned`。保持 `workflow.acceptance.configure` 原有高级入口语义，不修改 Workflow 图、Worker 权限和产物生成方式。新的 Project 应在规划时纠正路径；旧冻结 Task 不自动变更。

Delivery/accept 可以重用 system_test 先生成的脚本，以不同报告路径运行各自行为断言；若执行所需验证不同，main Agent 可以在 Task 开始前亲自创建脚本，或让前序有写权限的 Column 制作对应入口。复用并不放宽产品文件写入边界，也不允许拿构建成功当行为验收。

## 回归与黑盒验收

更新 `tests/test_context_acceptance_regression.py`：已有 runner 编译测试改成各 Column 自己可写的脚本，delivery/accept 复用 system_test 脚本；增加对无主 `scripts/*.py` 拒绝、跨 Column 复用、已有脚本允许的断言；保留目录越界、缺报告参数、伪 runner 等已有测试。运行相关测试与 `test_loop_contract.py`，然后独立提交。

运行中的第六次 Project 先观察到终态并记录；之后加载新代码，启动同一七轮登录用例的新 Project。验收标准是生成可由 Worker 实现的冻结验收检查、Task 各 Column 能推进、交付目标最终通过。若新 Project 仍失败，只针对实测阻塞复盘。
