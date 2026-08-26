# Loop、Workflow 与 Task Plan 解耦设计（V1）

## 目标

DevWerk V1 将可复用工作方法与一次具体工作拆分为三个稳定事实：

```text
Loop asset -> Workflow Plan + Workflow Revision
User objective -> Task Plan -> Tasks -> Scheduler -> Column Runtime
```

Loop 定义如何工作；Task Plan 定义这一次做什么；Task 固定引用创建时选择的 Workflow Revision。

本文是 V1 对 Loop、Workflow Plan、Workflow Revision、Task Plan 与 Task 归属关系的架构事实；其余核心文档继续定义通用 Agent、Column Runtime、状态机与监督语义。

## 领域边界

### Workflow Plan

Workflow Plan 是 Loop 中的方法说明，包含：

- flow unit 与完成定义；
- 可复用 Column 职责及生命周期演练；
- Task 输入、上下文、产物和验收契约；
- 默认 WIP 与调度原则；
- 评审、返工和干预原则。

Workflow Plan 不包含具体 Task、Task 编号或 Task 依赖。
Workflow Plan 的 Task Contract 可以声明与领域无关的依赖形状约束，例如按 Task 输入中的整数序号形成严格线性前驱；它约束 Task Plan 的合法结构，但不保存本次项目的具体 Task。

### Workflow Revision

Workflow Revision 是不可变的可执行 Column 有向图。它引用同一次发布所依据的 Workflow Plan，但不引用任何 Task Plan。后续 Revision 只影响新建 Task。

### Task Plan

Task Plan 是针对当前用户目标形成的不可变工作组合，包含：

- 绑定的 `workflow_revision_id`；
- 具体 Task 标识、标题、输入和 readiness；
- Task 间依赖与冲突域；
- Agent 使用约束、评审范围和重试范围。

同一 Workflow Revision 可以被多份 Task Plan 使用。

### Task

Task 通过 `task_plan_id + task_ref` 从 Task Plan 中实例化。标题、输入、依赖、冲突域和 readiness 以 Task Plan 为唯一来源，避免 Provider 在 `task.create` 时重复抄写和漂移。

## 生命周期

1. Conversation Agent 通过 `loop.list`、`loop.inspect` 选择 Loop。
2. `loop.apply` 校验参数并只创建 Workflow Plan、初始 Workflow Revision 和 Loop 来源记录。
3. Conversation Agent 根据用户本次目标保存 Task Plan。
4. `task.create` 先预检完整 Task Plan，再在一个事务中实例化全部 Task、初始 Column Run、依赖和调度记录，并返回指定 Task；任一预检或写入失败时物化数量为零。
5. Scheduler 根据 Task Plan 依赖与 Workflow Plan 的 WIP 默认值调度。
6. Runtime 从 Task 自身、Task Plan 和固定 Workflow Revision 构建输入，不读取 Loop 的固定任务组合。

执行型 Conversation Run 从开始即获得 Loop、Workflow Plan、Task Plan 和 Task 工具；顺序合法性由持久化边界校验。因此 Agent 可以在同一轮完成 `loop.apply -> task.plan.save -> task.create`，但无法绕过 Loop 自由发布项目的初始 Workflow。

## V1 接口

- `workflow.plan.save` / `GET|POST .../workflow-plans`：保存和读取可复用方法计划；
- `workflow.publish` / `POST .../workflow-revisions`：依据 `workflow_plan_id` 发布不可变 Revision；
- `task.plan.save` / `GET|POST .../task-plans`：保存和读取绑定 Revision 的具体任务组合；
- `task.create` / `POST .../tasks`：仅接受 `task_plan_id` 与 `proposed_task_ref`。

## 确定性校验

- Workflow Column 必须与 Workflow Plan 的阶段声明一致。
- Loop 参数声明 Project Workflow 的稳定绑定；Task Contract 只声明每个工作单元变化的输入，两者不得拥有同名字段。
- Task Plan 必须引用同项目存在的 Workflow Revision。
- Task Plan 的依赖必须是无环图，且只能引用同一计划内的 Task。
- Task Plan 必须满足 Workflow Plan Task Contract 声明的依赖形状；线性依赖契约要求序号连续，首个 Task 无前驱，其余 Task 只依赖紧邻前驱。
- 每个 Task 输入必须满足 Workflow Plan 的 Task Contract。
- Task 的 Agent 使用约束和精确输入绑定必须与固定 Workflow Revision 兼容。
- `loop.apply` 和 `task_plan.save` 分别形成独立、明确的持久化边界。

## Project 与 Task 参数所有权

Loop bindings 是 Project 采用该工作方法时确定的稳定事实。Column Runtime 将其作为只读 Project Context 提供：

```yaml
project:
  id: prj_...
  name: example
  description: ...
  base_dir: ...
  loop:
    key: novel.production
    version: 1.0.0
    digest: ...
    bindings: {}
```

Task input 只保存当前工作单元变化的事实。Loop 加载时比较 `parameter_schema.properties` 与 `task_contract.input_schema.properties`；存在交集即拒绝该 Loop，防止 Project 与 Task 形成两个事实源。可由 Project 事实推导的值不重复存入 Task，例如小说最终章由 `chapter_number == project.loop.bindings.chapter_count` 判定。

## 有界 Context

Column Context 只预加载显式声明的少量文本 Artifact。所有 glob 共享版本化 Runtime Policy 中的字符总量与文件数量边界；重复路径只读取一次，非 UTF-8 文件跳过并记录日志。软件工程 Loop 不预加载整个仓库，Agent 使用 `project.files.list`、`project.files.search` 和 `project.files.read` 按需定位源码。

## V1 非目标

- Loop 市场、远程安装、签名和依赖解析；
- Memory Contract；
- 用户侧 Workflow 编辑与合并；
- 多 Workflow 产品能力；
- 复杂 Baseline 数据库与审批系统。

## 监督上下文投影

用户发起的 Conversation 回合可以获得完整的规划视图。由 Task 终态、Mailbox 或定时复核触发的监督回合只装载当前 Workflow、Task 摘要和本次触发事实；需要诊断时再通过能力按需读取状态与证据。`task.inspect` 和 `event.list` 提供紧凑的状态、运行元数据与事件索引，不重复回放已经持久化的 Runtime Context 或大段 Conversation Progress 内容。
