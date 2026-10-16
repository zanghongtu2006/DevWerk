# Kanban Workflow 转移权审查（当前代码）

本文前半部分记录修复前的代码审查及当时 `data/devwerk.db` 的结构，不用任何旧 Project 的运行结果推断现状。当时数据库只读 `PRAGMA quick_check` 返回 `ok`；动态行为由代码路径推演。文末记录本轮实施和新 Project 诊断。

## 修复前：设计中的三层状态

1. **Workflow 有向图**：`app/v1/domain.py` 的 `WorkflowDefinition` 持有 `entry`、`columns`、`done/failed` 终点；每个 `ColumnDefinition.transitions` 把唯一 `outcome` 映射到 `target`。`validate_graph()` 校验 key、目标、可达性和到终点的路径。显式返工环允许存在；“存在终点路径”不保证运行时一定收敛。发布时 `app/v1/store.py::_publish_workflow_revision()` 保存 `definition_json`；Task 固定使用自己的 `workflow_revision_id`。
2. **执行状态机**：`app/v1/states.py` 分别定义 Task、ColumnRun、Attempt、AgentRun 的状态及允许变化。例如 Task `pending → running → pending` 对应跨 Column 推进，最终可到 `done/failed`；ColumnRun `pending → running → succeeded/failed`。它们只检查状态类型变化，不检查 `current_column` 是否与所选 outcome 的图边一致。`control_state`、租约和调度准入又是独立机制。
3. **反馈台账**：`v1_task_feedback` 保存 `responsible_column`、`checks_json`、`state`、`repair_run_id` 等；`open/verifying/resolved` 是 `task_feedback_repository.py` 自行维护的字符串状态，没有列入上述 `StateMachine`。它应描述缺陷和复验证据，而不是成为第二个 Workflow 转移定义。

数据库表与代码相符：`v1_workflow_revisions.definition_json` 存冻结图；`v1_tasks.status/current_column/workflow_revision_id/state_version` 存 Task 当前指针；`v1_column_runs.column_key/sequence/status` 存逐次访问；`v1_events.type/data_json` 存事件；`v1_task_feedback` 存反馈台账。这些表分属同一 Project；本文没有读取旧 Project 的行。

## 修复前：从入口到终点的实际代码路径

1. `app/v1/store.py` 创建 Task 时把 `current_column` 设为冻结 Workflow 的 `entry`，建立 sequence=1 的 pending ColumnRun。
2. `app/v1/services/scheduler.py::claim_task()` 按依赖、WIP、租约和 Task 状态领取任务；`app/v1/runtime.py::WorkflowRuntime.step()` 读取固定 Workflow，按当前 `current_column` 执行 Column。Agent Column 由 `column.complete` 交付 `outcome`，每个 Column 只分配一个 Worker；也支持声明式 capability sequence。
3. `WorkflowRuntime.step()` 校验 output，并用当前 Column 的 `transitions` 找到该 outcome 的**声明目标**，将它记入 `runtime.column_output.transition_target`；目标若为 `done/failed` 则准备终点证据，否则调用 `store.finish_run()`。
4. `store.finish_run()` 在同一数据库事务里结束本次 Run、建立下一次 pending Run、更新 `v1_tasks.current_column/status`、写 `column.finished` 事件。正常应将第 3 步的声明目标原样持久化。

**实际偏差发生在第 4 步之前**：`finish_run()` 先调用 `feedback.settle(db, task, run_id, outcome, next_column, terminal)`，用它的返回值替换图中选出的 `next_column`。因此图边并非最终转移权威。`runtime.column_output.transition_target` 记录声明目标，而 `column.finished.next` 可以记录另一目标；两个记录描述的是不同事实。

## 修复前：`settle()` 承担的职责与确定性错误

`app/v1/repositories/task_feedback_repository.py::TaskFeedbackRepository.settle()` 同时做四件事：生成/核对 `runtime.behavior` 证据、更新全部反馈状态、按最早未解决反馈重新路由、在 Task 到 `done` 时检查终验义务。按反馈路由时，它从责任 Column 或复验 Column 中选目标，再用 `route()` 沿 Workflow 边做 BFS，返回新的下一 Column。这不是声明的 `outcome → target` 查询，而是第二套隐含的转移决策。`allows_unresolved_failures` 和失败终点又使这种覆盖只在部分 outcome 上发生。

代码还有一个与历史数据无关的确定性反例：设 Column A 的 `accepted → B` 已在 Workflow 声明，某条反馈此前已 `resolved`，但其 `responsible_column=A`、复验在 C。A 再次运行并成功提交 `accepted` 时，`settle()` 遍历**全部**反馈，不排除 `resolved`；满足责任 Column 和序号条件后，把该反馈重新写为 `verifying`，并把 `repair_run_id` 改为当前 A Run。C 尚无发生在本次 A Run 之后的复验证据，反馈遂成为最早未解决项；`route()` 将下一步改向 C 的路径。结果是 `accepted` 成功，却没有前往 B。`TASK_STATE_MACHINE` 看见的仍是合法的 `running → pending`，因而不会拒绝这种图边违约。

`record()` 仅以 `(task_id, dedupe_key)` 去重，不按同一缺陷语义去重；`settle()` 按 `rowid` 取最早未解决项。只要旧反馈被重开或重复记录，后面的工作就可能被持续延后。图验证允许显式返工环，但这里产生的是**反馈台账隐式抢路由**，与 Column 选择的 outcome 不一致。现有 `task.feedback.record/list` 也不提供目标 Column 接收、拒绝、协商责任的动作。

## 为什么写成这样：提交历史与状态名

Git 历史能确认代码的形成过程，不能证明任何人的主观意图。2026-09-21 的 `docs/DEVWERK_Repair_Evidence_Token_Fix_2026-09-21.md` 在 R2/R3 方案中要求持久缺陷、责任 Column、复验义务，并写明“当前列提交完成时，在同一个事务中决定沿既有边继续、返修或复验”。2026-09-22 的 `cd2b2a7` 首次新增 `TaskFeedbackRepository.settle()`，同时把它接到 `store.finish_run()`；该版本已经按最早未解决反馈选择责任/复验 Column、用 BFS 寻找图内路径，再返回一个**不同于声明 target** 的下一 Column。可见这种改写是初版反馈机制的架构选择，不是近期偶然引入的一条条件分支。设计方案把“沿图中的某条合法边”误当成“沿本次 outcome 声明的边”，两者并不等价。

之后 `8c69b75`（09-27）补复验收据的时序约束，`f521053`（09-30）增加显式返工 outcome 不受旧反馈覆盖的例外，`7cf92c8`、`08c4505`、`ac97c50`、`3a37ec1`（10-01 至 10-02）叠加 Worker 自选行为证据、终态验收和失效判断。这解释了当前方法为何同时含反馈生命周期、路径规划、例外分支和终态检查。没有一次后续修改取消最初的目标改写，也没有为 `resolved` 加终态保护。

`settle()` 里看似相近的名字其实属于不同层：`task.status` 与 `v1_column_runs.status` 是 `states.py` 的通用运行状态；参数 `terminal` 是通用的 `done/failed` 终点；`feedback.state` 的 `open/verifying/resolved` 是反馈机制自己的通用字符串状态，未纳入 `StateMachine`；`outcome`（例如某模板的 `accepted`）是各 Workflow Column 自定义的业务出口；`allows_unresolved_failures` 是每条 Transition 的通用配置位。方法本身没有写死软件研发 Column 名，因此会影响任何启用此反馈机制的 Workflow；软件交付模板只是较容易触发它。

## 修复前结论与应保持的转移不变量

当前实现不是单一的 Kanban 状态机，而是“Workflow 有向图 + 多个执行状态机 + 反馈路由器”叠加。分层本身可以成立；问题是反馈路由器取得了修改下一 Column 的权力，且已解决反馈能被重新打开。这违反应有的不变量：**一次成功的 `column.complete(outcome)`，其下一 Column 必须等于该 Workflow revision 中 `(current_column, outcome)` 的唯一 target**。若完成条件不满足，应在接受 `column.complete` 之前明确拒绝并给出缺口；若需要返工，Column 应选择声明的返工 outcome。反馈可携带缺陷和复验证据，但不能在成功提交后暗改图边。Mailbox 继续只做通知。

这属于 Kanban 核心控制流缺陷，应按 P0 对待。最小修复方向不是重写整个引擎：先把 `settle()` 的反馈状态处理、终验证据检查与路径决策分开，禁止反馈返回或覆写 `next_column`；已 `resolved` 的反馈不得因普通再访自动重开。为同一 Workflow revision 增加不变量测试：每种成功 outcome 的持久化 `current_column` 与 `column.finished.next` 都等于声明 target；覆盖已有已解决反馈、未解决反馈、显式返工和终点四种情况。此文是代码审查结论，尚未实施修复。

## 本轮实施边界（代码修改前冻结）

1. `app/v1/store.py::finish_run` 在事务内再次核对 `(current_column,outcome)` 的冻结图边及 `terminal`，不接受任何调用方传入另一目标。该处是唯一持久化 `current_column` 的正常完成路径。
2. `app/v1/repositories/task_feedback_repository.py` 删除 BFS `route()`、`task.feedback_routed` 事件和从反馈决定下一 Column 的返回值。反馈完成处理只更新缺陷状态、复验证据并在 `done` 时拒绝未解决的义务；非终点的成功 outcome 保持其声明路径，即使反馈尚未解决。返工只能由显式的 Workflow outcome 选择。反馈登记只校验责任/复验 Column 属于同一冻结 Workflow，不再要求图上存在自动寻路路径。
3. 在现有 `app/v1/states.py` 增加反馈状态及合法状态变化，作为 Kanban Runtime 状态目录的一部分；已 `resolved` 的同一反馈不能自动回到 `verifying`。不新建状态服务、路由器或第二套调度层。反馈复验与终态检查仍由原仓库负责，但不能写 `current_column`。
4. 新增最小回归：图边权威（含旧已解决反馈和未解决反馈）、显式返工、终态未解决反馈拒绝、已解决状态稳定、反馈状态非法变化拒绝。调整只依赖旧隐式路由的测试预期，不削弱证据检查。
5. 定向测试通过后再清理诊断环境：确认活动进程与 `data` 目标绝对路径，停止 DevWerk，移除本次重测的活动数据库与日志及旧测试 Project 工作区，保留源码和本轮文档；启动服务，用全新 Project 做自然语言讨论和 Workflow/Task 交付诊断。观察按五分钟间隔，记录实际阶段/事件/交付结果；失败后先复盘，不靠旧 Project 推断新运行。

## 本轮实施与验证记录

- `TaskFeedbackRepository.route()`、`settle()` 的目标覆盖及 `task.feedback_routed` 事件已删除。反馈只更新生命周期和证据；到 `done` 时仍检查未解决反馈及冻结验收义务。
- `V1Store.finish_run()` 在持久化前核对冻结 revision 的 `(current_column, outcome) → target` 与 `terminal`，任何传入不同目标的调用均拒绝。已有已解决反馈不会重开；`open → verifying → resolved` 纳入 `states.py` 的状态机，`resolved` 是终态。
- 保留 Task、ColumnRun、Attempt、AgentRun 等各自状态机：它们描述不同实体的生命周期；Workflow 图是下一 Column 的唯一权威，没有增加调度器或第二套路径状态。
- 全量回归首次运行：545 通过，2 个测试因公共状态目录新增 `feedback` 而失败；同步测试契约后，相关 9 个测试通过。图边权威、显式返工、终态拦截及状态终结均有定向覆盖。`git diff --check` 无错误。
- 诊断环境：服务停止后删除旧 DB、旧测试 Project 工作区和备份；旧 `data/logs/devwerk.log` 被外部进程锁住，无法删除或截断。新服务将日志写入独立的 `data/logs-diagnostic`，不会读取该旧日志。新建 Project `prj_c6e9f88b8f574e0bb9c35e56b58f4654`，后续黑盒运行结果待记录。

黑盒诊断沿用用户指定的最小交付案例：至少五轮自然语言对话，讨论 Vue 前端、Java 后端、登录及防刷、不使用 cache 的简单 demo；由 Conversation Agent 自行决定合理实现并创建 Kanban Workflow/Task，不由测试调用自动化建板 API。通过标准为 Task 沿冻结 Workflow 到达 `done`、交付符合讨论确定的验收要求且前后端能够编译。监测 Agent/Workflow 的状态间隔约五分钟；若阻塞，先读取本次 Project 的事件、Run、DB 行和新日志，再作最小修复并新建 Project 重测。

本次已完成五轮自然语言对话。前四轮处于纯讨论；第五轮用户授权开始后，Conversation Agent 通过内部能力应用 `software.ddd_delivery` Loop，发布 revision `wfrev_e7622009f5d84e6bae9948109c786814` 并创建 Task `tsk_c0a90ec00cef483aa0c3e1c9f3fd208f`。第一轮监测：`requirements`、`domain_design` 的 Run 均 `succeeded`，Task 在 `backend_development/running`；尚不能宣称完整交付通过。

后续五分钟间隔监测：`backend_development` 在持续写文件、调用命令，未进入恢复/失败；随后 Run `succeeded`，Task 正常推进到 `frontend_development/running`。此时未见反馈改写图边或重复抢路由。

### 首次黑盒阻塞与单点修正（修改模板前记录）

`frontend_development` 最终在第 4 个 Run 因 `ExecutionBudgetExceeded: Agent wall-clock budget exhausted` 进入 `recovering/paused`。本次日志显示该 Worker 曾超过 60 次模型迭代、上百次工具调用，反复尝试启动 Java 后端并用 HTTP 探测；其上下文估计约 86k，未发生 Provider 上下文溢出。`project.command.run` 的现有工具描述已明确子进程在调用结束时清理、服务启动与探测须在一个有界命令内完成。真正的职责冲突来自冻结的 `frontend_development` 指令要求“真实命令验证前后端契约”，而后续 `integration_review`、`system_test` 已负责联调和行为验证。Agent 在前端列花费大部分预算处理后台服务生命周期，未按列边界及时交付。

最小修正只改 `loops/ddd-software-delivery/loop.json` 的前端 Column 指令：要求真实执行前端构建/定向测试、核对已冻结 API 契约；把启动后端的端到端联调明确留给后续集成/系统测试列；前端就绪后尽快提交 `frontend_ready`。不更改 `project.command.run`、Runner、预算、状态机或 Workflow 图边。当前 Task 固定旧 revision，先向 Conversation Agent 请求一次恢复，以验证管理链路；本次修正仅供下一全新 Project 重测。

一次恢复已自动发生：Run 5 再次进入 `frontend_development`，随后因 `npm` 写 `C:\Users\hongt\AppData\Local\npm-cache` 被当前 Codex 沙箱拒绝而形成未解决的失败收据；Worker 重复提交相同 `column.complete`，被已有协议保护拦下，Task 变为 `column_completion_protocol_stalled/recovering/paused`。这是本次诊断服务所处的沙箱环境与默认 npm 缓存位置冲突，不能直接归因于用户正常启动环境中的 DevWerk。下一步在测试服务进程设置可写的 `NPM_CONFIG_CACHE` 后重启，重新尝试恢复；不为测试沙箱的用户目录权限额外重构产品。首次向 Conversation Agent 的恢复请求传了 `start_task=false`，按现有协议这明确禁用控制动作，因此它只能讨论，不能执行 `task.retry`；应使用 `start_task=true` 重试该管理动作。

服务重启并设置可写缓存后，允许控制动作的 Conversation Agent 请求确实触发了 `task.retry_requested`（Run 5 的第 2、3 次 Attempt）；无需重做前两列或后端。最后 Worker 因 Windows PowerShell 执行策略拦截 `npx.ps1`，选择冻结图声明的 `execution_failed → failed`，未发生反馈抢路由。对现有前端项目直接运行 `npm.cmd run build` 则成功（Vite 5.4.21，67 个模块，退出码 0），说明产品代码至少可以构建；失败是命令入口选择，不是前端不可编译。下一全新 Project 前，在现有 `project.command.run` 工具说明中补一句 Windows 应直接调用 `npm.cmd`/`npx.cmd` 而非通过 PowerShell 执行脚本；不修改命令执行器、Windows Job 或 Workflow 状态机。此提示由实际工具契约提供，适用于所有 Column，避免在各模板重复维护平台知识。

第二次诊断前，按目标目录边界删除首次诊断 DB、Project 工作区及独立日志目录，保留 `data/evals` 和可复用的 npm 下载缓存。重启后的服务带可写 `NPM_CONFIG_CACHE`，读取了新的 Loop 指令与工具说明。新 Project 为 `prj_81c5a3a514434143ad995260c528e02f`，开始相同的五轮自然语言讨论；结果待记录。

本次复测期间用户进一步明确观察重点：前后端独立开发前是否由合理的设计/架构阶段固定同一 API 契约；若执行阻塞，Conversation Agent 能否据需求调整 Kanban。此后测试消息使用 `mode=auto,start_task=true`，不给出“不能开始”的反复限制，观察 Agent 自行选择启动时机与合法 Workflow。合理的 Workflow 结构均可接受，判据仍是最终交付。

第二次测试第四轮 Conversation Agent 自主创建 Workflow/Task；图边为 `requirements → domain_design → backend_development → frontend_development → integration_review → system_test → delivery → accept → done`，设计先于两端开发。第五轮解释反馈时，它错误声称 `task.feedback.record` 可让 Runtime 自动退回责任 Column。代码检查发现 `app/v1/capabilities.py` 该工具的描述仍写有旧行为“Runtime routes only at a safe Column boundary”，与本轮移除 `settle()` 改路后的实现相矛盾；这是遗漏的运行协议，不只是模型随机表述。最小修复是同步工具描述：反馈仅持久化缺陷和复验义务，下一 Column 始终由当前 Column 提交的 outcome 对应冻结图边决定；返工必须选声明的返工 outcome。无需加入新的路由器、状态或权限层。当前运行中的测试服务仍持有旧工具目录，先观察本轮结果，同时通过自然语言纠正 Agent 的错误认识；新服务启动后验证修正后的目录。

用户纠正后，Agent 读取了本次冻结 Workflow 图，确认 `frontend_development.backend_contract_issue → backend_development` 以及集成审查返回前/后端的显式边；未改写已有图。`domain_design` 已生成独立的 `docs/api-contract.md`，包含请求字段、HTTP 状态及不同防刷错误响应；它先于后端与前端执行。当前 Task `tsk_211fbe620312410aa9c2e26292e9d1ff` 在 `backend_development/running`。Conversation Agent 的文字回复仍把已完成的 `domain_design` 说成“正在执行”，属于状态快照不准；当前未见它影响调度或实际交付，先继续观察，不因此扩展修复范围。
