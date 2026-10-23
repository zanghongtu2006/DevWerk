# `workflow.create` 首次建图五次尝试排查

## 证据

Project `prj_6681907a508f4ac38b5e41c7636a4788` 的第五轮 Conversation Run 为 `arun_aaf044fae2364877ad13b4a948597e15`。只读核对 `data/devwerk.db` 的 `v1_agent_messages`、`v1_tool_invocations`，并对照 `app/v1/capabilities.py` 与 `app/v1/domain.py`：

| 调用序号 | 最后一列的写法 | 工具结果 |
| --- | --- | --- |
| 2 | 把 `done` 放进 `columns`，且 `transitions=[]` | 输入契约拒绝：出边不能为空 |
| 3 | 仍把 `done` 放进 `columns`，补 `terminal → terminal` | 图校验拒绝：`done` 是保留终态 key |
| 4 | 改成普通 `accept` 列，但出边目标写 `terminal` | 图校验拒绝：未知目标 `terminal` |
| 5 | `accept` 列改为 `transitions=[]` | 输入契约再次拒绝：出边不能为空 |
| 6 | `accept` 列的 `success → done` | 发布成功：`wfrev_4f9806acc89f450db2ff1ad1b8d27f28` |

前四次回执均为 `rejected_before_effect`。同轮随后 `task.plan.save` 与 `task.create` 成功，生成 Task `tsk_c43032b77587439bb121621f7e39ecff`。因此没有发生四个半发布版本或持久化冲突，也不是授权、Task 参数、Scope 或 Provider 错误。

## 根因

Agent 在收到每次校验错误后局部修改参数，但始终将「最后执行列」和「终态」混为一谈。正确表达是：最后一个普通 Column 有非空出边，其目标是保留的 `done` 或 `failed`；终态自身不在 `columns` 中，不需要 executor 或出边。

顶层 `workflow.create` 工具说明已经写明 `done`/`failed` 是终态目标，不是 Column，故这不是代码悄悄改变路由。Agent 未稳定遵循这条规则。不过当前实际传给模型的嵌套 JSON Schema 里，`WorkflowAuthoringRequest.columns` 只有生成的标题；`WorkflowAuthoringColumn.transitions` 只有最小长度；`Transition.target` 只有普通 key 正则。这三个紧邻填写点没有表达终态规则。已有 `_bind_workflow_authoring_contract` 增强的是旧 `WorkflowDefinition` schema，未增强新 `WorkflowAuthoringRequest` schema。顶层文字与字段所在位置相距较远，使这种参数误用更容易发生。现有校验正确拒绝了非法图；主要改进点是作者协议的局部可读性，而非放宽图校验或自动补边。

## 历史核对：终态何时从 Column 分离

这里需要修正一个不完整的归因：本次 Agent 所犯的概念错误与 DevWerk 自己曾经采用过的旧模型相似。不能只说它「凭空混淆」；不过本次 Run 没有读取旧源码或旧设计记录，不能据此断定旧模型污染了它的上下文。

1. Git 提交 `6baaac8`（2026-07-17 18:46 +08:00）的初版设计只要求 Workflow 有 `done`/`failed` 两个终点，尚未把它们明确定义为 Column 之外的哨兵。
2. `90e2b54`（2026-07-17 20:55 +08:00）修改设计：`done`/`failed` 是独立 terminal sentinel，不在 `columns`，无 executor，也不产生 Column Run；运行到终态应通过显式边完成 Task 终结。
3. 但是后续 `f1a4851`（2026-07-17 22:37 +08:00）的 V1 代码仍是旧式模型：`ColumnDefinition.terminal: done|failed`，Workflow 要求恰好一个 `done` 和一个 `failed` **终态 Column**；终态列无 executor、无 transitions。设计与代码当晚并未对齐。
4. `09aabd8`（2026-08-16 11:46 +08:00）才把代码改到哨兵模型：删除 `ColumnDefinition.terminal`，新增 `WorkflowDefinition.terminals`，保留 `done`/`failed` key，不准它们出现在 `columns`；每个执行 Column 要有出边，边的 target 可以是执行 Column 或哨兵。该提交也加入定向返工、会话及恢复等 Runtime 改动。提交说明没有单独解释终态分离的个人动机；设计文档能支持的工程原因是区分「要执行并产生 Column Run 的阶段」与「由图边原子结束 Task 的状态」。当前 `app/v1/store.py::finish_run` 正是按这一分支执行：非终态边创建下一 Column Run，终态边直接更新 Task 状态并写事件/mailbox。
5. 新 `workflow.create` 是本分支 2026-10-19 的**未提交**改动，沿用 2026-08-16 起的哨兵语义，但最初没有把该语义写到嵌套参数字段上。它没有再次改变终态模型。2026-10-20 本文所述补充字段说明同样未改模型。

严格说，**执行阶段与终态的职责区分在 2026-07-17 22:37 的旧代码中已经存在**：旧终态 Column 无 executor、无出边。变化的是表示方式：终态**移出** `columns` 成为独立哨兵。这个表示方式最早写入设计是 **2026-07-17 20:55**，落实到运行代码是 **2026-08-16 11:46**。本次 5 次调用的直接拒绝原因仍是当前参数不满足当前协议；历史设计/实现过渡和新工具参数说明缺口是应承认的背景与协议责任。

## 最小修改方案

只在 `app/v1/capabilities.py` 的新建图工具 schema 上补充通用字段说明：`columns` 只包含执行列，最后一列仍须有出边；`WorkflowAuthoringColumn.transitions` 每列至少一条，最后一列可指向 `done`/`failed`；`Transition.target` 只能指已声明 Column key 或保留终态 `done`/`failed`，不存在泛化的 `terminal` key。保留现有图结构、校验、授权、Runtime 和 Task 逻辑，不自动猜测/生成出边。增加一个 schema 合同测试，确认这些说明确实出现在实际注册的 `workflow.create` 工具参数中。

完成后运行聚焦回归。此修改仅降低模型误填概率，不能用单次样本保证首调用必然成功；若要证明黑盒稳定性，需要新 Project、自然语言对话及真实回执的重复观察，不能把本轮既有的第五次成功重算为修复后验证。

## 修改与回归

已在 `app/v1/capabilities.py` 为新 `WorkflowAuthoringRequest` schema 单独绑定上述三处说明；原先只服务于旧 `WorkflowDefinition` 的 `_bind_workflow_authoring_contract` 保持不变。`tests/test_capability_contract.py` 核对实际注册工具参数中存在相应描述。`tests/test_capability_contract.py` 47/47 通过，`git diff --check` 与 `compileall app/v1` 通过。

修改后按服务身份核对了 8000 端口所属的 DevWerk uvicorn 父子进程，完成代码所需重启；重启后的 `/v1/health` 返回 `ok`。尚未以新 Project 黑盒测量首次调用成功率，不能将字段说明改善等同于模型行为的确定性保证。

## 深入排查：为何恰好绕了四次

`AgentRunPreparer.prepare` 将 Registry 的工具 schema 直接送到 Provider；Anthropic 兼容路径会展开 `$ref`，但保留字段描述。该 Run 暴露了 102 个可用工具，`workflow.create` 在其中；旧 `workflow.publish`/`workflow.plan.save` 没有暴露。本次没有调用 Loop，也没有读取旧源码。前四轮用户/Agent 自然语言对话也没有出现 `terminal`/`done`/`failed` 图定义。因此不能把此次误填具体归因于旧工具、Loop 或历史代码被加载；更直接的证据是新工具的参数形状与错误反馈。

调用 2：Agent 提交 `columns=[research,draft,done]`，把 `done` 作为带 Agent executor、空出边的列。`CapabilityRegistry.validate_arguments` 先执行 JSON Schema；由于 `transitions.minItems=1`，它在 `_workflow_create` 和 `WorkflowDefinition.validate_graph` 之前拒绝，**只提示空出边，没有指出更根本的保留 key 冲突**。Agent 的下一条文字明确说“终端列也需要声明 transitions”，于是局部补边。

调用 3：仍保留 `done` 列，只将其出边改为 `terminal → terminal`。空出边校验通过后，`WorkflowDefinition.validate_graph` 才报告 `done and failed are reserved terminal sentinel keys`。错误是 Pydantic 的原始 `ValidationError` 文本，未给出具体正确写法。Agent 再局部重命名该列。

调用 4：列变成 `accept`，但仍保留 `terminal → terminal`。图校验报告 `unknown transition target 'terminal'`，未列出合法终态值。Agent 随后认为“终端列不应声明到 terminal 的转换”，又把出边删空。

调用 5：`accept.transitions=[]`，再次命中最先执行的 `minItems` 错误。直到调用 6 才写出 `accept: success → done`。这不是工具随机成功/失败；**相同输入始终会得到相同拒绝**，不稳定发生在作者协议允许表达相互矛盾的概念、校验只报告当下首个错误、Agent 按局部错误修补而没有一次性回到完整图契约。前四次回执均 `rejected_before_effect`，没有半发布。

字段描述补丁已到达 Provider-facing schema，但它仍是自然语言提示：`Transition.target` 在机器约束上仍只是匹配 key 正则的任意字符串，`WorkflowAuthoringColumn.key` 也仍允许 `done`/`failed`，所以该补丁不能保证首调成功。102 个工具的总量可能增加注意力负担，但本次回执不能证明它是直接原因；不应为此削减通用 Agent 权限。

## 下一步建议：只收紧建图入口

1. 仅修改新工具的作者输入模型（`app/v1/domain.py::WorkflowAuthoringColumn` 等）：Column key 在 JSON Schema 与服务校验中排除保留值 `done`/`failed`；新增有判别字段的目标类型，明确区分 `{kind:"column", key:"review"}` 与 `{kind:"terminal", status:"done"}` / `failed`。最后执行列仍显式声明出边。外部 Loop、旧 `workflow.publish` 和持久化的 `WorkflowDefinition` 保持原形。
2. `app/v1/capabilities.py::_workflow_create` 仅将该目标对象转换为现有图的字符串 target，并在转换前对未知 Column key 返回短而可操作的错误（含允许的列 key 或终态值）。不猜测业务边、不自动把 `terminal` 改成 `done`、不自动补空出边。
3. `tests/test_capability_contract.py` 覆盖 Provider-facing schema 的判别结构、两种目标的发布、保留 key、未知目标和空出边均无副作用。`tests/test_scope_extension.py` 中使用该新入口的合法样例同步更新。随后新建 Project，以至少五轮自然语言交互观察首调用的真实成功率及 Task 创建。单一样本成功只能证明链路可用，不能宣称概率意义上的绝对稳定。

这一步是建图工具的通用输入契约收紧，不涉及 Workflow 状态机、调度、Mailbox、Column Worker 或业务场景优化。

## 收紧协议的实现与回归

上述三点已在本分支落地。`WorkflowAuthoringTransition.target` 现在是判别联合；`WorkflowAuthoringColumn.key` 同时经服务端字段校验和实际注册的 JSON Schema 排除 `done`/`failed`。`_workflow_create` 将 typed target 转换成原有图的字符串边，并对未知 Column key 返回具体可执行的错误；原持久化图、旧 `workflow.publish`、Loop 不变。之前只加说明的补丁仍保留，但不再作为唯一约束。

对历史五次调用的关键反例进行静态/合同回放：`done` Column 即使同时带空出边，也先在 `workflow/columns/0/key` 被拒；裸字符串 `target="terminal"` 在 `workflow/columns/0/transitions/0/target` 被拒；正确的 typed Column/terminal 边能发布。错误调用均不产生 Workflow 或 Plan。Provider-facing schema 显示 `target.kind` 的 `column|terminal` 判别值及各分支必填项。`tests/test_capability_contract.py` 48/48、`tests/test_provider_contract.py` 23/23、授权创建聚焦测试 1/1 通过；`compileall app/v1` 和 `git diff --check` 通过。

服务因代码变更重启后 `/v1/health=ok`。全新黑盒 Project `prj_84c507c41ab444d393b4e3fa38528471` 已提交第一轮自然语言对话，后续将以至少五轮对话及真实工具回执核对首次建图、Task 创建；当前不得提前声称黑盒成功。
