# 首次 Workflow 创建协议修复与回归

## 证据与目标

`prj_318a37e106cc45ecb3d76312913dd62d` 在首版创建轮保存 6 份 Plan、尝试发布 18 次，全部被拒，最终 `ExecutionBudgetExceeded`。`prj_5e36e85805014bc39fbe88ab8ea62d14` 的首次创建先把产物文件名填入 `upstream_outputs`，后又使 Plan walkthrough 的 outcome 与图出边不一致，同时其 `feedback_columns` 要求的能力未在执行列中提供，最终 `ConversationProtocolStalled`。对最新第二份图的只读复核显示，列 key/mode 已对齐，说明继续要求模型重复抄写 Plan 与完整图并不能稳定收敛。

目标是 Conversation Agent 在一次授权执行轮中用内部工具创建**可执行图**，继而保存 Task Plan、创建 Task。图的列、执行器和 outcome→target 仍由 Agent 显式选择；Runtime 不推断业务场景、不补充出边。旧 Plan/Publish 工具继续为 Loop、历史调用和高级配置保留。

## 最小协议变化

1. 新增 `workflow.create` 作为 Conversation Agent 的首选建图入口。参数只要求名称、flow unit、入口、列（key/name/instruction、执行器、出边），以及 Task 输入 schema、预期产物、完成条件和可选 WIP。可选上下文、输入/输出契约与验收检查仍可显式提供。列间 handoff 的 `upstream_outputs` 从**图中指向该列的前驱 key**生成；文件名不会被当成前驱 key。未提供任何验收检查时不自动创建验收环节。
2. 工具把同一份输入生成 `WorkflowDefinition` 和内部 `WorkflowPlan`。Plan 的方法说明由图定义派生；`lifecycle_walkthrough` 和 `self_check` 对该入口可为空，不伪造一份与图竞争的状态机。旧 Plan 数据仍能读取；发布只检验 Plan 列 key/mode 与图的一致性及图能到 `done`，不再以演练文本的 outcome 路由否决图。
3. 发布前仍运行现有图、能力、Task 合同、反馈能力与 JSON Schema 校验。失败返回原校验错误，不发布半成品。若调用者显式要求 `feedback_columns`，必须在对应列开放 `task.feedback.record`；新入口默认不声明该要求。
4. Conversation Agent 工具列表只暴露单次建图入口；旧 `workflow.plan.save`/`workflow.publish` 保持 Registry 和外部调用兼容。更新主 Agent 指示为 `workflow.create` 负责创建和改版，`loop.apply` 仍可选。成功回执必须包含真实 Workflow revision ID，Task 工具后续引用此 ID。没有给 Agent 特定项目的流程模板。

## 代码与测试点

- `app/v1/domain.py`：精简作者输入模型；Plan 的 walkthrough/self-check 设为可选，仅保留旧数据兼容。
- `app/v1/capabilities.py`：`workflow.create` schema、图/Plan 构造、共同发布入口；旧工具不删除。
- `app/v1/store.py`：移除以文本 walkthrough 再次路由的重复校验，保留列 key/mode 与可达终态校验。
- `app/v1/conversation.py`：主 Agent 首次创建与改版使用一个工具；观察成功回执时计入新能力。
- `tests/test_capability_contract.py` 和相关合同测试：单次创建成功、无前驱文件名污染、显式错误不静默修补、旧发布路径兼容、图是唯一状态机。

先跑聚焦回归，再因代码变更重启服务，以全新 Project 完成至少五轮自然语言讨论；读取真实工具回执、活动图及 Task，不能把 Agent 的文字声明当作成功。若黑盒仍失败，记录独立原因并继续修正，不能宣称稳定。

## 当前回归记录

- `tests/test_capability_contract.py`：47/47 通过；新增 `workflow.create` 三项聚焦测试验证单次工具调用发布两列图、从出边推导前驱 key、非法图不发布且回滚 Plan、后注册的通用能力出现在新工具 schema。
- `tests/test_provider_contract.py`：23/23 通过；真实授权轮 `test_conversation_grant_creates_initial_workflow_in_one_call` 通过；Scope 的同 Requirement 错误提示与授权轮聚焦测试 2 项通过。
- `tests/test_project_supervision.py`：23 项通过、1 项失败。失败项预期旧的 `ExecutionBudgetExceeded` 文本，而本分支已有的重复失败调用保护先返回 `ConversationProtocolStalled`；这项断言与本次新建图工具无关，未为此更改产品行为。
- `compileall app/v1` 与 `git diff --check` 通过。DevWerk 因代码变更重启后 `/v1/health` 为 `ok`。
- 全新 Project `prj_6681907a508f4ac38b5e41c7636a4788` 已开始自然语言黑盒测试；截至目前前两轮只讨论文章目标和内容框架，未声称已建图。后续以五轮以上对话、真实工具回执、活动 Workflow 与 Task 为准记录结果。

## 黑盒 A：首次创建结果

Project `prj_6681907a508f4ac38b5e41c7636a4788` 经五轮自然语言对话：前四轮由 Agent 讨论目标、篇幅、例子及资料核查；第五轮用户授权交付，没有指定列结构。创建 Run `arun_aaf044fae2364877ad13b4a948597e15` 依次调用 `conversation.execution.authorize`、`workflow.create`、`task.plan.save`、`task.create`、`conversation.reply`。`workflow.create` 前四次分别因空 transitions、把终态写成 Column、指向未知 `terminal`、再次空 transitions 被拒；第五次成功发布 `wfrev_4f9806acc89f450db2ff1ad1b8d27f28`，随后创建 Task `tsk_c43032b77587439bb121621f7e39ecff`。Board 确认活动图非空且 Task 数量为 1，Job 为 `succeeded`，Agent 回复只称 Task 待执行。

图的三列是 `research → author → accept → done`，`research` 执行器使用可见 Chrome MCP，`author` 使用项目文件/命令工具，`accept` 使用文件读取与度量。`upstream_outputs` 分别是空、`research`、`author`，没有文件名误填为前驱。此次证明失败回执可使 Agent 在同轮修正并完成创建；**四次拒绝说明还不能仅凭这一例声称首次创建已经充分稳定**。已新建独立 Project `prj_03f04040f32b4f55acd1d05bec95f962` 做重复性复测；不复用失败历史 Project。

Task `tsk_c43032b77587439bb121621f7e39ecff` 已由 Runtime 启动到 `research` 列。第一次 Column Run `run_67e1b4f141fc4c2b8f2e58ec759cc951` 约十分钟后以 `ExecutionBudgetExceeded: Assignment cumulative model/tool/resume budget exhausted` 中断，Task 曾进入 `recovering`；下一次间隔检查时又显示 `running@research`。这属于建图后 Worker 执行范围，不能把 Task 已创建误报成产物已交付，也不能在本次仅凭状态推断是否最终可完成。

该首次 Research Run 记录 116 次工具调用，其中 `agent.result.read` 38 次、可见 Chrome 导航 35 次、页面快照 27 次；另有新建/选择页面及项目文件搜索。所有回执 `ok=1`，但未在 Assignment 预算内完成该列。后续间隔检查仍在 `research` 的 `running/recovering` 间变化，未观察到进入 `author`。这是一项独立的 Worker 进展问题，本文件不把它归因于首次建图协议，也不声称文章交付完成。

## 黑盒 B：独立场景

Project `prj_03f04040f32b4f55acd1d05bec95f962` 改用 IT 值班交接模板，不需要浏览器或外部资料。前两轮讨论了目标和输入边界；第三轮用户询问“哪些环节需要单独核对”时，Conversation Agent 提前调用 `project.files.write` 写出 `shift_handoff_template.md`，没有发布 Workflow。第四轮用户指出这只是未验收草稿，Agent 承认提前写文件并建议核查流程。第五轮用户明确要求已有文件作为草稿，由看板 Workflow 和 Task 自行核查、修订与交付，不要求人类逐项验收。此轮仍在执行窗口，结果待实际工具回执核对。提前写文件不能算本次建图成功。

第五轮 Job 最终 `succeeded`，Run `arun_b6f27310b61c4ae6889475f9274b196e` 先读取草稿、检查 Scope、取得执行授权，随后**首次** `workflow.create` 即发布 `wfrev_5021b57a8c874c1aa9bb150a967de0c7`；接着 `task.plan.save` 与 `task.create` 均成功，Task 为 `tsk_02fc692b9bb4486c92c42339bd334fe2`。Board 同时证实活动图和 1 个 Task。图为 `review(all_passed→deliver, needs_revision→revise) → revise(revision_done→review) → deliver(delivery_complete→done)`。这是第二个不依赖特定业务模板的首次创建成功案例，且建图仅一次调用。

静态审查也发现一个交接风险：`revise` 指令称输入为上一轮 `review` 的不通过项列表，但该列显式传入的 `context.upstream_outputs=[]`；`revise` 的能力列表只有项目文件读写，没有反馈读取工具。当前版 `workflow.create` 只有在 `context` 整体省略时才从图导出前驱 key，显式空列表会覆盖这一默认。它可能导致返工分支无法读取失败原因。先观察 Task 的实际行为，再考虑仅在通用紧邻列上下文派生规则上修复，不加入值班模板专属逻辑。

约五分钟后的真实 Task 状态：Project B Task `tsk_02fc692b9bb4486c92c42339bd334fe2` 已到 `done`，未触发 `needs_revision` 分支；Project A Task 仍 `recovering@research`，失败码 `execution_budget_exceeded`。因此 B 证明一条实际完成路径，但不能证明返工分支的交接可用。

### 交接规则的最后一处小修正

新建图入口本来承诺从图导出相邻前驱输出；若 Agent 提交 `context` 对象却把 `upstream_outputs` 留空，该承诺失效。仅修改 `app/v1/capabilities.py::_workflow_create`：对空或省略的前驱列表，填入当前列在图中的直接前驱 key；显式非空列表仍保持原样，让作者能够精确选择上下文。不解析业务文字、不推断工件路径、不新增路由。同步更新 `app/v1/domain.py::WorkflowAuthoringColumn.context` 字段说明，并在 `tests/test_capability_contract.py` 增加显式空列表的回归测试。该修补不改变已发布版本；重启服务后只影响新发布图。

上述小修正已落地。回归测试使用显式 `context={include_project:false, upstream_outputs:[]}`，确认保留其他上下文选择并补入直接前驱 key；`workflow.create` 聚焦 3/3 通过，能力/Provider 合同合计 70/70 通过，Scope 聚焦 2/2 通过，`compileall`、`git diff --check` 通过。服务因代码变更重启后 `/v1/health=ok`。最后这处规则修改尚未另开第三个五轮 Project 黑盒；黑盒 A/B 属于修改前版本，不能误称它们验证了最后的派生行为。

Project B 的 Task 随后真实到 `done`，项目目录存在 `shift_handoff_template.md`。Column Run 依次为 `review → revise → review → revise → review → deliver`，六次均 `succeeded`；说明原图在这次样本中即使未载入 `upstream_outputs` 也通过文件重读完成了返工。它不否定静态的上下文缺口，但证明一次完整交付路径。Project A 的资料核查 Task 仍停留在 `recovering@research`，属于独立的浏览器核查执行预算问题，本次未修改该 Worker/预算机制。
