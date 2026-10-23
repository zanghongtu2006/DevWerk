# 首次 Workflow 发布耗尽执行预算：通用修复方案

## 复现与根因

新 Project `prj_318a37e106cc45ecb3d76312913dd62d` 经五轮自然语言讨论后授权建图。Conversation Agent 的创建轮最终 `ExecutionBudgetExceeded`：112 次工具调用、6 次成功保存 Workflow Plan、18 次 `workflow.publish` 均失败；活动 Workflow 与 Task 都不存在。具体回执见同日黑盒复测记录。

失败不只是模型偶然写错一个字段。当前 `workflow.plan.save` 要求 Agent 生成详尽方法 Plan，接着 `workflow.publish` 又要求它再次生成包含执行器、上下文、输入输出契约与状态边的完整 `WorkflowDefinition`。同一事实在两次巨大调用中重复，且两者必须精确对齐。Agent 在 Plan keys、executor mode、upstream key、Runtime input root、outcome 等交叉约束上反复失败，后期还把整个图序列化成字符串提交。准确的错误回执未能使其在预算内收敛。

对照先前成功的 Project `prj_b6c7a8b0c5204fe1a94c63dc55fcd520`：首次创建轮也曾出现 6 次 `workflow.plan.save` 和 4 次 `workflow.publish` 失败，第 5 次发布才成功，共 35 次工具调用。故“两阶段、字段重复”的不稳定性在本次改动前就存在；本次更严重，不能仅凭一次运行认定某个新增 if 分支直接造成首次创建失败。

本分支在失败测试之前增加了 `workflow.plan.save` 的“保存并不发布”回执、延长了主 Agent 关于换版和 Task 静止的说明；`workflow.publish` 当时仍要求完整 `WorkflowDefinition`。新增的 Store 静止校验只有合法图进入发布事务才会执行，而失败轮的 18 次调用均在参数/模型校验阶段被拒，因此它不是本次错误的直接来源。提示与回执变长可能影响模型选择，但目前无法从单个运行证明因果。

## 修改边界

保留 Workflow Plan、完整 `WorkflowDefinition`、图状态机、Store 发布入口、Task 生命周期与旧版静止校验。增加一个**简短但显式的发布输入**：Agent 引用已保存 Plan，只提交图的入口、每列执行器配置和出边。Runtime 从 Plan 已有列职责与上下文边界构造完整定义，并调用现有发布校验；不猜测或隐式增加任何出边，不硬编码项目类型、浏览器或文章流程。完整图输入继续可用。该路径只减少重复描述，不代 Agent 决定 Workflow 路由。

## 代码点

1. `app/v1/capabilities.py` 的 `workflow.publish` schema 增加与 `workflow` 互斥的 `flow` 形态：`workflow_plan_id`、`flow.entry`、`flow.columns[]`。每项仅含 `key`、`executor`、`transitions` 和可选 `instruction`。`executor` 沿用 agent/capability_sequence 现有结构；`transitions` 沿用显式 outcome→target，不自动增加 rework 或 done 边。工具描述说明：已有 Plan 时优先使用 `flow` 减少重复；两种形态都走相同校验。
2. `app/v1/capabilities.py::_workflow_publish` 在 `flow` 路径加载引用 Plan，按 Plan column keys/顺序填入 `name`、职责指令与默认 ContextSelection，使用传入 executor 和 transitions 生成 `WorkflowDefinition`，并继续经 `canonicalize_workflow_capability_arguments`、`Store.publish_workflow`。如果 Plan 列集、执行模式、引用上下游、可达终态或工具可用性不合法，原有验证拒绝，不静默更正。所有列都需要显式执行器和至少一个出边。
3. `app/v1/conversation.py` 对主 Agent 的工具指导仅新增一句：保存 Plan 后，优先传短 `flow`，使用 Plan 中完全相同的 column keys/modes，并显式列出每条出边；如需要完整的上下文/契约配置，使用原 `workflow` 形态。
4. `tests/test_capability_contract.py` 增加正反例：多列 agent 图可经短输入发布并与原有图校验对齐；不匹配 Plan、缺列、缺出边、错误执行器模式继续拒绝；完整图路径保持兼容。测试只覆盖通用协议，无文章或浏览器特例。

## 回归与黑盒

先跑聚焦测试和相关 Workflow/Conversation 套件，再重启 DevWerk 以加载代码。新建 Project 自然语言测试首版创建与 Task 调度，读取真实活动图和任务，而非只依据答复。若在预算内仍失败，应记录新调用证据，不能以本修复宣称黑盒通过。

## 实验结果与处理

曾按上述设计试作短 `flow` 输入。聚焦 Workflow 发布测试 13 项与 Conversation 系统提示测试 1 项通过；`compileall`、`git diff --check` 通过。较宽的 Capability/Conversation 组合套件运行超过两分钟无新输出，仅终止 pytest 进程，没有计作全套通过。

服务重启时发现原进程使用的全局 Python 缺少 `uvicorn`，第一次重启进程立即退出；改用本项目 `venv/Scripts/python.exe` 后 `/v1/health` 已恢复 `ok`。未改动产品日志路径。

新 Project `prj_5e36e85805014bc39fbe88ab8ea62d14` 完成五轮自然语言讨论后授权创建。Agent **没有使用**新短 `flow`，仍提交完整 `workflow`：第一次被拒于 `fact_check` 的 `context.upstream_outputs` 错填文件名 `fact_list.md`；第二次被拒于 Plan 的 `draft` walkthrough outcome 为 `completed`，而图中同列仅声明 `passed/rework`。随后它原样重复失败操作，保护机制以 `ConversationProtocolStalled` 结束；看板仍为空，Task 0。短协议未在真实 Agent 行为中解决问题，故已撤回该实验性产品代码与测试，不作为已完成修复。本文保留为方案及失败实验记录，下一步设计须先解释为何 Agent 仍选择完整图，以及 Plan walkthrough 与图出边的重复约束是否应存在。
