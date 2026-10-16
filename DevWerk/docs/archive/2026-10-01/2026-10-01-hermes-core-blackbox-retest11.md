# Hermes 核心分支黑盒复测 11：验收脚本责任目录补齐后

状态：授权后的规划失败，未启动 Task。2026-10-01 15:18（Asia/Shanghai）重启 8000 服务，加载 `560d423` 模板修复及之前的验收准入代码。Project `prj_7d43d4e7c0c54a4bb602604ace8ac769`，目录 `data/projects/hermes-core-login-retest11-20261001`。复用第九次测试完全相同的六轮讨论和第七轮授权输入；测试方未直接修改 Project 交付物。

六轮讨论 Job `cjob_5d549654824a482a85b229d3f9c373d7`、`cjob_9278829dcda44c688dad07cf9e4dfaac`、`cjob_446a05a3352449998183f5838ed62cdc`、`cjob_da11e223c46140ef8a7aaaae6bef316f`、`cjob_08be489023e04609bbf8aa39e0505b3e`、`cjob_5ce1cb86eab843a7a409cbcc6562627b` 均 succeeded；每轮 Workflow/Task 数量均为零。同一个 Conversation Agent Session `ca_1fa07c453d154c90b6956a97a5418d66` 贯穿六轮及授权轮。讨论内容最终确认 `/api/me`、第三次失败即 429、单 Vue 视图等修正。

第七轮授权 Job `cjob_25ff611695c146c5bf74b86412791284` 最终 failed；其 Agent Run `arun_199cbce69cbf4e0193ba43dc108a3833` 自身 succeeded 并给出未完成回复。该 Run 共 51 次模型迭代、76 次工具调用，其中 17 次工具调用失败，21 次 `project.files.write`。没有 Task、Column Run 或 Worker Assignment；不能据此评价 sub-agent、Mailbox 或交付质量。当前仅保留一个由 `loop.apply` 产生的 8 列 Workflow，其第 2 版只配置了 backend/frontend 行为检查，不具备 TaskPlan 准入条件。

## Conversation Agent 做了什么

1. 正确遵守前六轮只讨论的边界，在明确授权前没有创建 Workflow/Task；授权后调用 `conversation.execution.authorize(begin)`。
2. 用固定原生工具 `loop.inspect`、`loop.apply` 应用 `software.ddd_delivery`，写需求追踪文档，并通过 `workflow.acceptance.runners` 成功配置 backend/frontend 两列。没有直接操作数据库。
3. 多次读取 `workflow.inspect`、`task.plan.contract`、`task.plan.validate`；这些工具已经返回合法 Column key、Task input schema、验收缺口、entry-evidence 候选和 `valid=false`。
4. 写了多份 PowerShell 验收 runner，尝试给 system_test/delivery/accept 等列配置检查，并多次尝试保存 TaskPlan。最后回复称现有 8 列 DDD Workflow 对小 Demo 过重，想换为 4 列；它错误调用了只针对新用户扩展请求的 `project.scope.revise`，被 `ScopeExtensionRequestMissing` 拒绝，最终报告新建 Task 数为 0。

## Conversation Agent 没做什么，以及直接证据

- 没有形成可启动 TaskPlan。第 17 次工具调用返回 `ready_for_task_plan=false`，明确列出 system_test、delivery、accept 三列缺少行为验收；第 38、44 次 `task.plan.validate` 也都返回 `valid=false`。它仍多次调用 `task.plan.save`，分别传入 JSON 字符串而非对象、Task input 多余 `contract` 字段、错误的 exact-input 指针或错误的 entry-evidence 类型，均被 schema/准入拒绝。
- 没有正确利用 Column 的可写目录。先为五列写根目录 `scripts/*.ps1`，再配置到后端/前端目录；后续又将集成审查、系统测试、交付、验收 runner 放到 `integration-review/**`、`system-test/**`、`delivery/**`、`accept/**`。这些路径与相应 Loop 的 `writable_paths` 不符。修改模板增加 `scripts/delivery/**`、`scripts/accept/**` 也没有让此 Run 自动选用这些路径，因此继续逐个放宽目录不是可靠修复方向。
- 没有把实现交给 sub-agent，也没有进行真实构建/测试。尽管 `launch_validation=deferred` 允许未来由 Column Worker 创建验收入口，它仍提前写 21 次 Project 文件；本次没有 `project.command.run` 调用，Project 中也没有后端应用或前端应用源码。后端 runner 当前输出的场景 ID 是 `BACKEND-AC`，与冻结的 `AC-*` ID 不同，且测试源 SHA 为 `N/A`。即便强行启动，正式验收仍需 Worker 修复这些脚本。此处仅是静态合同风险，未发生 Column 验收执行。
- 没有形成更适合本需求的初始 Kanban。`DEVWERK.md` 指示“没有合适 Loop 时不得自行创建初始 Workflow”；`loop.apply` 是初始 Workflow 的唯一路径，`workflow.publish` 要求先有 Loop 创建的 Workflow，且列 key 必须与 Workflow Plan 完全一致。现有本地软件 Loop 只有 8 列 DDD 交付和 GitLab DevOps 方案。它后来意识到 8 列过重，但不能在同一已授权需求中通过现有简单工具直接改成 4 列初始流程；`project.scope.revise` 不是该用途。

## 与目标的差距判断

当前 Conversation Agent **已经有通用 Agent 的基本形态**：跨轮 Session、71 个当前可用工具、文件/命令/记忆能力和固定 Kanban 原生能力；本次没有权限层级或数据库直写问题。差距主要是 **Kanban 规划接口与流程边界设计**，并叠加工具契约与模型调用质量问题，而非缺少一个“更高 Main Agent”：

1. 固定 API 存在，但没有面向主 Agent 的短而完整的 Kanban 作者路径。`loop.inspect/apply`、`workflow.acceptance.runners/configure`、`task.plan.contract/validate/save`、`task.create` 需要连续正确组合；初始 Workflow 又只能从文件系统 Loop 产生。这限制了项目经理按需求自由设计流程。
2. `software.ddd_delivery` 的 Task Contract 在 Task 创建前强制 backend、frontend、system_test、delivery、accept 五列都有冻结的可执行行为命令、报告路径和场景覆盖。它让尚未启动任何 Worker 的主 Agent 提前设计或编写未来各列的测试入口，导致 76 次工具调用耗在规划与脚本路径上。上下文压缩已运行，但不能代替可执行的简洁规划路径。
3. `workflow.acceptance.runners` 当前工具说明仍写“an existing Project script is also valid”，与 `bbec693` 后“已有脚本也须有 Column 修复责任人”的真实规则矛盾；这会误导 Agent。工具反馈通常只报告第一个不合规脚本，复杂批次需要反复尝试。
4. Agent 也有明确判断错误：已收到 `valid=false` 仍保存 TaskPlan，重复调用 `authorize(begin)`，最后把同一已授权目标的 Workflow 修正误用为“新范围扩展”。这些错误不应靠再增加目录补丁解决；应在规划 API、验证前置和清晰的同范围修订路径上处理。

## 分析边界与后续决策

本文件只复盘，不修改运行代码。此 Project 尚未触发任何 Kanban Worker，因此不能推出 Mailbox、Column 协作或验收 Runtime 本身失败。下一步应先决定：让 Loop 保持可选模板，并通过固定、可发现的 Kanban 作者工具支持同一授权需求下创建/修订初始 Workflow；让主 Agent冻结需求、阶段职责和验收目标，把具体可执行检查交给对应 Column Worker 构建并由 Runtime 确定性验收。决定后再制定独立方案并修改代码，而不是继续为模型碰到的路径逐项加白名单。
