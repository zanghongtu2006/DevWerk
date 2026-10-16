# 已交付任务的后续修复：授权范围与结果回执修复方案

## 证据和边界

新项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 完成了六轮自然语言讨论，Conversation Agent 创建了 Workflow 和 Task，Task 经八列自动推进到 `done`。后续用户要求为同一交付的验收脚本复测缺陷创建独立修复 Task；Job `cjob_47ae1478285a4c169de14e53f9375558` 却在约 20 分钟、118 次工具调用后，没有创建 Task，最终显示旧 Task“已交付”。旧 Project 不作为后续黑盒通过标准。

数据库和工具回执显示：Agent 以 `conversation.execution.authorize(request=begin,new_scope=true)` 建立新 Requirement `req_649f8717cd144e34a4b43881ca9e1997`；它随后将新 TaskPlan 指向旧 Requirement 的 Workflow Revision，`task.plan.save` 因范围不匹配被拒；再调用 `project.scope.revise` 时，`ConversationIntentRepository.mutation_denial` 仅接受 `extend` grant，因此报 `ScopeExtensionRequestMissing`。最后 `conversation.reply(work_result, task_ids=[旧 done Task])` 被接受，回复渲染器忽略了 Agent 自述，仍显示旧交付“已交付”。这是假成功，不能视为修复排队。

## Hermes 对照

- `D:/workspace/github/hermes-agent/tools/registry.py` 的 `get_definitions` 把可用工具 schema 直接交给主 Agent，`dispatch` 对工具结果统一归一化和报错；`tools/kanban_tools.py` 为看板操作提供结构化工具，明确区分 orchestrator 与 Worker。工具契约是可操作入口，不要求 Agent 猜 DB 或 shell 命令。
- `agent/tool_executor.py` 保留工具调用的 ID、参数与结果轨迹；`agent/turn_finalizer.py` 对失败、异常结束分别给出结果状态。这支持一个原则：最终“完成”应受当前回合实际工具回执约束，而不能从历史 Task 状态推断。
- DevWerk 已具备内部 Workflow/Task 工具和持久 Conversation Agent；不移植 Hermes 的通用框架、MCP 或另建权限层。此次仅修正工具契约与回执边界。

## 修改设计

1. `app/v1/capabilities.py` 中 `conversation.execution.authorize` 与 `task.successor` 的工具描述，以及 `app/v1/conversation_intent.py` 的主 Agent 指令：明确三条合法路径。已有范围内的修复用 `continue` + 原 Requirement；需要扩展原范围用 `extend` + `project.scope.revise`，其返回新 Workflow Revision；真正独立的新 Requirement 用 `begin`，若复用当前 Workflow，须先完成范围接管再保存 TaskPlan。终态 Task 不能原地编辑，修正原 Task 身份用 `task.successor`，独立 Task 用新 `proposed_task_ref` + `task.create`。这些是工具协议说明，不是固定业务状态位。
2. `app/v1/repositories/conversation_intent_repository.py::mutation_denial`：允许本回合新建的 `begin` Requirement 对当前 Workflow 做**一次必要的** `project.scope.revise` 接管，且只能修订 grant 所绑定 Requirement；沿用 `scope_repository.py::revise` 的“旧范围 Task 必须全部终态”校验和冻结历史规则。这样 Agent 选了独立新需求时仍可在同一用户回合完成 Workflow/Task 创建，不需用户再发一轮 `extend`。`bind_revised_scope` 仍更新 grant revision，不另建状态来源。
3. `app/v1/capabilities.py::_check_workflow_scope` 给出当前 grant 与 Workflow 所属 Requirement 的具体 ID 和合法下一步。不要放宽 TaskPlan 的范围校验，也不要允许跨 Requirement 直接复用旧 Workflow。
4. `app/v1/conversation_report.py::render_report`：执行回合引用旧的终态 Task 时，必须有**本回合针对该 Task 的成功生命周期收据**（如 `task.rerun`/`task.successor`；后者应引用新 Task），否则拒绝把旧 Task 作为本轮工作结果。普通 status 回合仍可查看旧 Task；执行回合的文件写入等效果可按真实收据报告，但不能借旧 Task 的 `done` 冒充新任务已完成。`work_result` 仍从工具回执渲染，不采信自由文本。

## 回归和验收

- 单测分别覆盖：新 `begin` Requirement 在一个回合接管已终结旧 Workflow 并保存/创建 Task；旧 Task 未终结时拒绝接管；普通 `extend` 路径保持不变；旧 done Task + 本轮 `requirement.create` 不能报告“已交付”；status 查看旧 Task 与真正的本轮 Task 创建仍可报告。
- 先运行相关单测。服务加载代码后，用公开 Web API 新建 Project，至少五轮自然语言讨论，由 Conversation Agent 自行决定 Workflow/Task；只观察其调用工具、Task 被看板驱动到 `done`、编码产物能编译及符合 Workflow 验收约束。监测间隔约五分钟，不实时轮询；旧失败 Project 不纳入通过判断。
- 若新黑盒仍无法创建或推进，记录确切回执及最小补丁后再重测；不补充此次目标外的能力。

## 实施与验证记录（进行中）

- 已修改上述三个边界：`begin` 新 Requirement 允许在创建回合同步接管旧 Workflow（旧 Task 未终结仍被拒）、工具描述给出 `continue/extend/begin` 的操作路径、执行回合禁止旧终态 Task 冒充本轮新工作。
- 新增同回合接管/任务创建、未完成旧 Task 拒绝接管、旧完成 Task 结果归因回归。针对性 3 项通过；连同现有范围与回复用例共 13 项通过。首次运行 pytest 使用沙箱系统临时目录时全部因目录不可写而报 setup error，切到工作区临时目录后通过。
- 2026-10-09 新建公开 API 黑盒 Project `prj_26a999813752467d96bb6d3dc055d463`。前五轮讨论均成功且未创建任务；第六轮自然语言授权交付，Job `cjob_3ba6dc29c9aa4b9c919a4ba509561563` 已受理。后续按约五分钟间隔记录工具效果与看板终态。
- 第一次约五分钟检查：授权 Job `succeeded`，真实 Workflow Revision 为 `wfrev_d4b72d8453744e54b66b2d3f59dfb652`，Task `tsk_612f15b216244473aa233cb83daae29a` 已创建并自动运行到 `domain_design`。回复“任务计划已保存；登录防刷演示交付：待执行”仅表示排队，实际状态以 Task/Column 记录为准。
- 第二次约五分钟检查：`requirements` 与 `domain_design` 的 Column Run 均成功，Task 自动进入 `backend_development/running`；仍是同一 Task，没有人为路由或重复创建。
- 额外回归：`tests/test_conversation_intent_contract.py` 独立运行 38 passed。与新路径及范围/回复相关的 13 项亦通过。一次组合测试在个别较长用例处被中断，不能把该次中断计为通过。
- 第三次约五分钟检查：`backend_development` 成功结束，Task 自动进入 `frontend_development/running`。当前无停滞或错误路由。
- 第四次约五分钟检查：Task 仍在 `frontend_development/running`，后端列 39 次工具调用后成功。前端列已写文件并执行命令，最近两次 `project.command.run` 回执成功；当前 Run 尚未提交完成，最新工具回执约七分钟前，可能处于较慢模型请求，先留一个检查窗口，不主动干预或强行判定卡死。
- 下一次检查：前端列与 `integration_review` 都已成功，Task 自动进入 `system_test/running`。此前的模型等待未变成流程阻塞；仍无手动路由或重复 Task。
- 系统测试阶段首个检查点：Task 仍在 `system_test/running`，该列有真实命令进展；`mvn test -f backend/pom.xml` 的工具回执退出码 0，并核对了 Python/Java/Maven 环境。自动化 HTTP 行为验证尚未看到通过收据，故不能认定系统测试完成。
- 下一检查点仍在 `system_test/running`。最近工具回执停在 `mvn test` 通过约七分钟；较早写根目录 `verify.py` 被本列 `writable_paths` 正确拒绝，三次带双重转义绝对 cwd 的命令也被拒，随后相对路径命令成功。等待模型下一步自行在 `tests/**` 或 `e2e/**` 写验收，不因这些已纠正的参数错误改代码。
- 随后系统测试列在允许的 `tests/verify.py` 写出脚本并运行；最新 `project.command.run` **整体退出码 0，但脚本正文写明 `TOTAL: 3/5 passed`、`S-004 FAIL`、`S-003 SKIP`**。Shell 在 Python 后执行 `Stop-Process`，丢失了 Python 的退出码。脚本的 S-001 又把“第三次失败仍是 PASSWORD_ERROR、第四次才 ACCOUNT_LOCKED”写成通过，与用户确认的第三次触发锁定相冲突。此刻 Task 仍在系统测试列，不能据命令退出码判定验收成功；观察本列是否识别并反馈，若错误放行则按回执定位 Runtime/流程缺陷。
- 再下一检查点：Task 仍在 `system_test/running`。Worker 已重写 `tests/verify.py`，但该文件仍明确以第 1–3 次 `PASSWORD_ERROR`、第 4 次 `ACCOUNT_LOCKED` 作为 S-001 通过条件；尚无改后成功的 HTTP 运行回执。继续给本列一次自纠机会，避免在 Worker 尚运行时误判为交付。
- 又一检查点：Worker 重新运行同一 PowerShell 启动服务/运行脚本/停止服务复合命令，约十分钟后工具回执 `command timed out`，Task 仍在 `system_test/running`。这次没有被误放行；但常驻 Java 进程和脚本混在一个命令里造成验证脆弱、时间浪费。当前还没有反馈或回修路由，下一步观察 Worker 能否拆分或提交准确失败。
- 超时后约五分钟检查：Task 仍在系统测试列，最新工具回执仍是该超时命令；尚无 `column.complete` 或反馈。服务日志显示模型请求日志本身含完整 prompt 且发生 Windows 编码日志异常，可能放大 I/O；目前尚不能据此断言 Worker 已卡死，先观察一个模型请求窗口。
- 代码核对修正超时假设：`app/v1/process_runner.py::run_command` 已在直属进程退出后短暂排水并关闭 Windows Job，`tests/test_p0_runtime_regressions.py` 已覆盖子进程继承输出管道场景。因此**不能**把本次十分钟超时直接归因于子进程持有管道；更符合现有证据的是复合 PowerShell 命令/其中 Python 脚本未按预期终止。第二次同类命令亦超时，输出停在 Java 服务启动后的请求日志，没有新的脚本结果。Worker 随后写出 `scripts/acceptance/run-tests.bat`，Task 仍在系统测试列，等待其尝试可有界的运行方式。
- 脚本静态检查发现 `scripts/acceptance/run-tests.bat` 包含 `taskkill /f /im java.exe`，会杀死所有 Java 进程，不能作为本机验收命令执行。已通过公开项目对话提交明确用户反馈，Job `cjob_eab8996e42644b54994830ce7e447412`：要求只清理本次启动的进程、传播 Python 退出码、按第三次失败锁定修正断言，并沿现有 Workflow 重测。未由测试方执行该脚本或直接修改交付文件。
- 该用户反馈 Job 已成功：Conversation Agent 先尝试直接 `project.files.write(scripts/acceptance/run-tests.bat)`，Runtime 因活跃 Workflow 独占工作区而拒绝；随后正确调用 `agent.worker.send` 将反馈排队给当前测试 Worker，回复只报告“Worker 输入已排队；Task 执行中”。它没有使用 Mailbox 当对话通道。Worker 输入中又要求测试列修改后端配置/Java 代码，但本 Workflow 的 `system_test` 可写范围不含 `backend/**`；若确有后端缺陷，应由测试列报 `tests_failed` 并沿图回修，而不是在测试列越权改实现。下一检查点核对 Worker 是否遵守列职责。
- 下一检查点：测试列正确以 `tests_failed` 完成，记录后端 `>=`/`>` 锁定边界和可配置时长缺陷，Task 自动回到 `integration_review/running`；没有把工具退出码 0 误判为通过。测试脚本已改为第三次失败即锁定。但 `run-tests.bat` 仍用 `wmic` 模糊查找并杀死所有命令行含 `login-anti-brush` 的进程，可能误伤其他 Project，且仍传 `--skip-s003` 跳过解锁。已通过公开对话追加反馈，Job `cjob_9d45ac7017774b74a533bc9f892c05e6`，要求只清理本次启动的确切 PID，且快速配置下真实执行解锁 HTTP 场景，不将跳过项计为通过。
- 约五分钟后：`integration_review` 已按 `backend_changes_requested` 路由到 `backend_development`，新后端 Run 为 `pending`；第二条用户反馈 Job 仍 `running` 并持有 Project 规划租约，Worker 暂未开始属于预期调度。没有跨列抢路由。
- 又一检查点：反馈 Job 仍 `running`，Task 继续 `pending/backend_development`。Conversation Agent 在回合内读写了项目脚本、后端模型和测试文件（Run 仍等待规划租约）；这在通用 Agent 权限内，但会让 Worker 的后续实现与 Main Agent 写入交织，需要最终重新构建与验收。新脚本不再全局按镜像名/命令行搜索杀进程，改用 `netstat` 取 8080 监听 PID；然而如果 8080 被其他服务占用，它仍可能把旧进程当本次启动者并杀死。检查时 8080 无监听者，但脚本的进程归属仍需完善；不要把端口 PID 等同于启动回执。新脚本取消 `--skip-s003` 并保存 Python 测试退出码。
- 再约五分钟检查：反馈 Job 运行约 16 分钟、工具调用约 70 次，Task 仍 `pending/backend_development`。Main Agent 继续直接改后端并多次尝试不存在的 `backend/mvnw.cmd` 与双重转义 cwd，工具明确返回 `Executable not found`/`path escapes project base_dir`；项目此前已成功用 `mvn -f backend/pom.xml`。这是明显的规划租约持有时间与 Token 低效，目前尚非状态机阻塞。后续若不能自行收敛，需考虑让 Main Agent 把反馈交给当前列后尽快结束回合。
- 再次检查：Job 已约 105 次工具调用，Task 仍 `pending/backend_development`，但仍有工具进展。Main Agent 已经直接改后端源码/测试和验收批处理，并尝试 `system.command.run` 测试，多个参数错误后尚未结束回合。`app/v1/capabilities.py` 仅在 Task `running/waiting` 时阻止 Main Agent 直接写/跑命令；Task 在规划租约下为 `pending`，所以该回合允许了 Main Agent 与后续 Worker 交织修改。不能简单把 `pending` 全部加进禁令，否则初始规划回合中创建 Task 后补需求快照也会被阻止；更稳妥的下一步是主 Agent 在已有排队 Task 收到用户反馈时尽快转交 Worker 并结束回合，避免重复实现与 20+ 分钟租约占用。
- 下一检查点：第二条反馈 Job 最终 `failed/ExecutionBudgetExceeded`，约 128 次工具调用，期间写入脚本和后端文件但未有 `agent.worker.send`、`conversation.reply` 成功收据。它释放 Project 规划租约后，同一 Task 的 `backend_development` 自动从 `pending` 进入 `running`。这证明状态机未卡死，同时证明“活跃 Task 反馈由 Main Agent 直接长循环处理”会导致用户反馈 Job 失败、高 Token 成本和无可靠交接；需要在不取消 Main Agent 通用能力的前提下为现有 Task 监督回合设清晰的结束/交接契约。
