# Loop 可选与 Kanban 作者工具：隔离黑盒回归

状态：执行中。使用隔离端口 8001、隔离数据库 `data/evals/2026-10-01-loop-optional-blackbox/devwerk.db`，未操作用户的 8000 服务。通过 `/v1/projects` 和 `/v1/projects/{id}/conversation` 模拟 Web 请求；脚本只在测试端发送请求与读取状态，不属于用户交付路径。

## 项目一：恢复已创建的四列 Task

- Project `prj_0d2e7f5068e14db09c0545bcd017b6ef`：六轮仅讨论登录 Demo，再授权创建无 Loop 的四列 Workflow 与 Task `tsk_88749529a9f24c568243c52a5029a50a`。Column 为 `backend_implementation`、`frontend_implementation`、`integration_review`、`acceptance_testing`。
- 后端 Worker 首次因为 `column.complete` 的可选空对象输出格式而停止；修复后第一次恢复又因执行授权之后重复 `task.retry` 被误判而停止。分别由 `e0f6339` 与 `accbdfe` 修复。
- 第二次恢复的后端 Worker 原本可完成 Maven 测试，但四次较早的完成拒绝和后续已修复的命令失败仍使它在第五次提交时被暂停。`90987b3` 收窄拒绝计数重置与 Worker 自选检查的修复规则；53 项相关单测、完整 520 项单测通过。第三次由 Conversation Agent 授权、重试同一 Task 后，后端及前端 Column 均成功，Task 推进到 `integration_review`。
- 集成 Worker 在 `project.command.run` 前台运行 `mvn spring-boot:run`，触发 600 秒命令超时，随后改用 Maven 测试等方式探索验证。该命令选择造成长时间浪费，但集成列最终成功，Task 已推进到 `acceptance_testing`。验收 Worker 也曾尝试 `spring-boot:run`，需继续观察它能否使用有退出条件的检查完成验收；若再失败，按真实失败回执复盘，不能仅凭时间推断 P0。
- `app/v1/process_runner.py::run_command` 在调用完成时回收子进程树，这是现有确定性生命周期；但 `project.command.run` 的公开描述没有交代这一点。验收 Worker 连续尝试“调用一启动服务、调用二访问 HTTP”，服务无法跨调用存在。最小修复方案是只修改 `app/v1/capabilities.py` 的该工具描述：前台服务会超时、子进程不会跨调用保留；如需 HTTP 验收，在同一个有界命令内启动、等待就绪、执行断言并清理，或使用会自行退出的测试命令。保持执行器及权限模型不变，并加工具目录描述回归。当前服务为避免中断两个 Task 暂不重启，该说明将在下一次隔离黑盒启动时生效。

## 项目二：从新 Project 重放同一案例

- Project `prj_d0e210ee28414908ae370984815895b4`：重放完全相同的六轮讨论和第七轮授权。六轮期间无 Task。授权轮的 `workflow.plan.save` 曾四次被 schema/领域契约拒绝，第五次保存成功；Agent 后续调用 `loop.apply`、`task.plan.contract/validate/save` 与 `task.create`，已创建 Task `tsk_df63ca677bc941d08c332ecd5e93fdb6`。该项目选择了 Loop 模板，符合“模板可选”路径；是否顺利派生 Worker 与完成交付仍待回归。
- Agent 在 Task 创建前写了 `SPEC.md`、`CREDENTIALS.md` 两个项目文档，没有写测试脚本。此行为属于当前通用 Conversation Agent 的直接工具权限；不应把“通用 Agent 可写文件”误判为必须预写脚本的旧门槛。
- Task 已依次完成 `requirements`、`domain_design`、`backend_development`、`frontend_development`。第一次后端 Assignment 累计 94 次模型迭代、114 次工具调用后触发 `execution_budget_exceeded`；Runtime 自动新建同一 Column 的下一次 Assignment，并保留 Worker 实例与 Agent Session，后端最终成功。这验证了 Worker 生命周期续接，但该列的重试成本较高，仍需在最终统计中记录。
- `integration_review` 发现后端限流边界错误，通过 `task.feedback.record` 形成责任列 `backend_development`、重验引用 `runtime.behavior` 的持久反馈，并由 Runtime 自动回流后端。源码现为 `MAX_ATTEMPTS=5` 但检查条件为 `>`，仍允许第六次失败；Worker 正在修正。此处是交付物缺陷及预期的反馈/重验路径，不是 Mailbox 对话。
- 首条限流反馈后来由后端修复与重验自动结清，Task 经前端复核回到集成列。集成 Worker 又以新 `dedupe_key=rate-limit-threshold-verified-fixed` 把“阈值已修好、已符合规范”的**确认**调用 `task.feedback.record` 写成一条新的 open 反馈。这是工具语义误用，会造成多余回流；当前还不能断言会无限循环。需要在任务恢复后核对是否实际阻断交付，再决定是否改为“反馈只随声明的 rework outcome 生效”的确定性提交契约，避免用文本关键词判断语义。

## 测试环境隔离注意

两个 Project 的文件目录与数据库对象独立，但同一台 Windows 主机上的 Java 进程和默认 HTTP 端口并不隔离。项目一集成 Worker 曾按进程名清理 Java；因此若并发时项目二的 Java 测试失败，必须先排除测试相互干扰。下一轮完整黑盒应串行运行，不能把并发资源冲突误报为 DevWerk 流程故障。

## 待验证

1. 两个 Task 的 Column 转移、失败反馈、责任列重验、Mailbox 通知及最终状态。
2. 交付完成后，再核对 Vue/Java 登录、防刷、401/429、两条 API 与无登出端点等约定；区分流程缺陷与交付物质量问题。
3. 记录无效工具调用、模型/工具调用数与长命令的成本。仅在有阻塞证据时再改代码，保持小范围提交。

原项目验收 Worker 的有界脚本曾报告“5 次失败后未见 429”，后续独立检查发现第 1–5 次为 401、第 6–7 次为 429，与已批准的“允许五次失败，封锁时 429”相符。脚本自身的阈值断言需要修正；截至观察点没有持久化错误 Task 反馈，也不能把该脚本误判当作后端缺陷。

## 验收列完成阻塞与修复方案

项目一的验收 Worker 后来执行了包含 8 个 HTTP 场景且成功退出的有界命令，却在 `column.complete` 连续五次被旧探索命令 `mvn spring-boot:run -q` 的失败回执拒绝，Task 转为 `recovering/paused`。`90987b3` 的“后续成功行为命令修复探索失败”分支只对 `required_capability=project.command.run` 生效；该 Workflow 的 `acceptance_testing` 没有这一标记，所以实际独立验收列落在规则外。Worker 随后尝试给自身记录 `runtime.behavior` 反馈，也被正确拒绝，因为该引用只属于 Plan 明确列出的行为列。这是完成协议覆盖面缺陷，不是后端交付失败。

最小修复：`app/v1/services/completion_admission.py::evaluate` 对**所有无固定 acceptance checks 的 Column**采用相同的探索命令规则：只有晚于最后一条未解决命令失败的、成功且非已知非行为的 `project.command.run` 回执，才能解除先前的命令失败；非命令动作失败、固定检查失败、只读/noop/清理命令仍阻止成功。保留执行历史与最终证据核验。`completion_protocol.py` 的工具说明与拒绝提示同步表述，不新增状态位或列特例。增单测覆盖无 `required_capability` 的独立验收列、固定检查、清理命令和时间顺序。新项目仍在运行，服务重启应等它达到安全边界；之后由 Conversation Agent 恢复同一 Task，再跑新 Project 串行回归。

项目二第二次集成列也因同类旧准入规则连续五次拒绝而暂停。此时两个 Task 均为 `recovering/paused`，已在安全边界重启隔离服务以加载 `d587d88` 与新的工具说明，之后先恢复项目一，再恢复项目二，避免并发 Java 进程干扰。

## 跨 Attempt 行为证据丢失

项目一在新协议下恢复后，验收 Worker 成功提交，但最终 `done` 被 `Required behavior evidence missing: backend_implementation` 拒绝。后端 Column Run `run_62de721c13f54f38b295bc0d973e0b61` 共有五个 Attempt；第 4 个 Attempt 成功执行 `mvn test`，第 5 个 Attempt 仅提交 `column.complete`。完成准入从整个 Column Run 的 ledger 看到成功命令，`TaskFeedbackRepository.record_column_evidence()` 却只查最后 Attempt，所以没写入后端 `runtime.behavior`。同一事实在两个准入层使用了不同生命周期边界，属于 P0 假阴性。

最小修复：`record_column_evidence` 以 Column Run 为检索范围，取最后一个真正的成功行为命令；若其后有成功的 `project.files.write`，证据作废，要求再测试。回执仍绑定当前完成 Attempt，但 output 保留实际 Agent Run/Tool Call 来源，避免伪造来源。`settle` 在终态检查前对已有成功且缺回执的行为列按同一规则补记，以恢复受此缺陷影响的历史 Task；显式固定检查完全不走补记。单测覆盖跨 Attempt 成功、后续源码写入使证据失效、终态补记与固定检查隔离，再恢复原 Task。

跨 Attempt 修复的完整回归为 523 通过、1 失败。失败用例 `test_different_successful_operation_does_not_recover_failed_column_action` 指出：无固定检查时，泛化为“任意后续成功命令”会错误地把 `python -c print('different operation')` 视为修复。后续补丁只收紧**自动清除旧失败**的判定：成功命令必须是从 argv 可识别的测试/验证入口（如 Maven test、pytest、test/verify/acceptance 脚本），且晚于最后失败；普通打印、noop、清理仍不算。该规则不改变 Worker 对任意项目命令的执行权限，也不改变首次成功证据或 Workflow/Task 作者能力。原项目的 `acceptance-test.ps1` 和后端 `mvn test` 均在可识别范围内。

## 项目一终态复核：授权约束在规划交接中丢失

项目一 Task `tsk_88749529a9f24c568243c52a5029a50a` 已经沿四个 Column 到达 `done`，说明 Workflow/Task 创建与调度链条可运行；但交付不符合用户确认的范围。第六轮用户明确说不要 logout 或其它端点，第七轮授权又重复说不要 logout。实际后端 `AuthController` 提供了 `POST /api/auth/logout`，Vue 也调用它并显示登出按钮。验收报告只测了正向的 login/me 与限流，共 8 项，没有检查额外端点或额外 UI。

证据链：授权 Grant `grant_34ec37dc30a540948439206e49e4f555` 的 `scope_summary` 只写“两条端点”，没有保留原授权消息的排除语句；Task Plan 的 `readiness.non_scope=[]`，验收标准也没有排除项；后续前端、集成、验收 Column 输入都没有出现 logout 字样。故这是交接上下文缺失与验收范围遗漏，不能把 `done` 等同于符合目标。

最小修复设计：

1. 执行 Grant 已有不可变 `source_job_id`，原始授权用户消息也已保存在对应 Conversation Job；无需新增快照字段或表列。`task.plan.contract` 读取并展示当前 Grant 的原文，提醒 Agent 将明确排除项写入现有 `readiness.non_scope` 与验收标准。
2. 在 `runtime.py::WorkflowRuntime._input_for`，对引用该 Grant 的 Task，从已有记录读取**创建该 Task 前最新一次使用同一 Grant 的用户请求**作为 `authorized_scope.user_request`；若与最初授权不同，同时附上 `original_user_request`。送入每个 Column，独立于可选择省略的 Goal/Task Context；Worker 提示要求按原始授权检查交付，发现与 Task Plan 冲突时不能默默采纳缩水后的 Plan。无 Grant 的原有 Task 保持原输入。历史 Grant 无需迁移。这也覆盖后续用户授权继续或修复同一 Requirement 的情况。
3. 单测检验 Grant 原文不可被摘要取代、每个 Column 看到相同原文、无 Grant 的 Context 不变。随后从 Web 对话让 Conversation Agent 对此已完成但越界的 Task 创建明确的修复 Task，验证 Kanban 再次推进并验收；另一个 Loop 项目在隔离服务里继续恢复，最终对两条路径做终态复核。

修复 Task 黑盒进展：Conversation Agent 通过现有工具创建 `tsk_1c7cb102c72040b89203be82be6531f0`，`repair_of_task_id` 正确指向原 `done` Task；该 Plan 的 `non_scope` 和验收标准也明确排除 logout。Task 在 `pending/backend_implementation`、`scheduling.state=admitted`、无依赖和失败码的状态停留约七分钟。Conversation Agent 随后错误地把正常的 `pending` 当作已确认故障，先尝试 `task.retry`/`task.resume`，再自行 `task.cancel`，使修复 Task 进入 `failed`，之后又反复尝试创建新 Plan。离线复制同一数据库，将该 Task 恢复为 `pending` 后，`SchedulerService._dispatch_eligible()` 返回 true，`runnable_task_ids()` 返回它；隔离服务 `/v1/health` 的 Supervisor 线程存活且 tick 无异常。仍需查明进程内为何未领取 Task，不能仅凭健康接口推断它正常调度。

**根因补证**：`SchedulerService.claim_task()` 在 Conversation Agent 持有 Project 规划租约时明确拒绝 Worker 领取 Task；`conversation.py::_process` 也已把“排队后立即 `conversation.reply` 结束本轮，Worker 在租约释放后启动”写进 Agent 的权威上下文。该 Agent 创建 Task 后继续轮询，自己延长租约，因而制造了七分钟 `pending`，随后错误取消。Supervisor 与准入条件并未坏，进程内调度受规划租约阻挡是设计行为；真正缺陷是“排队后的本轮结束”仅靠自然语言约束，未在工具协议上落实。

针对这一缺陷的小范围修复：在 `agent_runner.py` 发现 Conversation Agent 成功调用排队类工具（`task.create`、`task.successor`、`task.retry`、`task.reopen`、`task.rerun`、`task.resume`）后，将下个模型请求限定为 `conversation.reply`；`agent_provider.py` 只暴露该回复工具；`agent_tool_execution.py` 拒绝同轮追加的其它工具调用。被拒绝的排队操作不触发强制结束，已有监督唤醒的自动回复保持原样。回归测试确认 Task 创建后不会继续轮询/取消、正常回复释放租约，随后 Supervisor 可领取 Task；测试覆盖 Provider 不遵守工具选择的兜底拒绝。无需修改 Scheduler 或 Worker 生命周期。

协议回归 77 通过。旧测试轮最后因持续规划、反复提交无效修复 Plan 耗尽 Agent 调用预算而失败；它留下的修复 Task 已是 `pending/paused`。隔离服务加载新协议后，通过新的 Web 对话明确请求恢复**同一** Task，观察其结束本轮并释放租约后由 Supervisor 领取；不以再创建 Task 掩盖这次失败。

恢复请求随后成功，Conversation Agent 结束本轮，Supervisor 领取同一 Task；`backend_implementation` 成功。`frontend_implementation` 在生成模型输入前报 `ContextExhausted(required_context_overflow)`，Task 转为 `recovering/paused`。该 Column 的冻结输入约 230 KB，其中 `reference_artifacts` 约 214 KB；自动预载的第一个文件 `frontend/dist/assets/index-*.js` 就有 121 KB，后面还包括 `frontend/node_modules` 中的包源码、`.map`、安装元数据。`ContextSelection.artifact_globs` 为 `frontend/**/*` 时，`ProjectFiles.existing_texts` 只按 glob 和字符数取文件，不区分项目源文件与生成物；最多 200,000 字符的预载在这个模型下估计为 152,147 tokens，超过 129,025 的输入预算。此溢出和 Task 逻辑无关，属于 P0 上下文装载缺陷。

最小修复：仅在 Runtime 的**自动参考文件预载**中排除常见依赖/构建目录（`node_modules`、`dist`、`target`、`build`、`.venv`、`venv`、`.next`、`.cache`、`coverage`）；`project.files.read/list/search` 保持原能力，Worker 需要时仍可按路径读取。实现为 `ProjectFiles.existing_texts` 的可选排除目录参数，由 `WorkflowRuntime._input_for` 传入，不改变显式注册的 accepted/working Artifact 或现有 ContextSelection 契约。测试验证 `frontend/**/*` 预载只带源码、普通文件接口仍可读生成物，并恢复同一 Task 重验。

本地对同一 Task、同一前端 Column 用新预载规则重新构造输入，JSON 长度从约 230 KB 降到约 56 KB；预载文件为 `frontend/index.html`、`package.json`、`pnpm-lock.yaml`、Vue 源码和 Vite 配置，不再含 `dist` 或 `node_modules`。相关文件/上下文测试 4 通过。隔离服务已加载补丁，并从 Web 对话请求重试原 `recovering/paused` Task，终态待验证。

另一个独立的重试边界问题在此过程中暴露：前端 ContextExhausted 的监督唤醒调用 `task.retry(task_id)`，未带 `column_key`；`RecoveryManager.retry_task()` 默认把目标设为 `workflow.entry`，于是已经成功的后端列被重跑。此不是前端模型上下文修复所需，也增加了工具/模型成本。最小改动是**非终态 Task 的默认 retry 目标改为当前 Column**；确需从 Workflow 入口重跑仍可显式提供 `column_key=workflow.entry`。在现有两列 Runtime 测试中模拟第二列恢复，断言默认 retry 留在第二列并且第一列不重跑。`task.reopen` 的终态后重新进入流程语义不改。

重试默认目标补丁完成；Runtime、文件、监督相关回归 42 通过。此补丁尚未加载到正在执行黑盒的隔离进程，但该黑盒的下一次恢复请求已显式给出目标 Column，因此不会依赖默认值。

隔离服务重启时监督唤醒已把后端列重新启动，启动策略又暂停了该进行中的 Assignment；这次中断是测试进程重启时机造成，不能归因于上下文补丁。随后用户模拟的“重试前端”对话被 Conversation Agent 错判为讨论，只检查 Task 并回复，未执行恢复；该轮没有任何 Task 状态变更。下一轮需显式恢复原 Task，再验证前端是否成功。环境操作以后须先确认无运行中的 Column 再重启，避免人为制造新中断。

显式指定 `frontend_implementation` 的恢复轮随后成功；前端与集成列均完成，源码已无 logout。最终 `acceptance_testing` 的 Task 终态检查却报 `Required behavior evidence missing: integration_review`。集成 Worker 实际执行成功 `mvn test -Dtest=LoginIntegrationTest -q`、`mvn test -q` 和 `npm run build`，之后写入 `integration/VERIFICATION_REPORT.md` 再调用 `column.complete`。`TaskFeedbackRepository.record_column_evidence()` 以**最后任意一次** `project.files.write` 为行为证据失效点，因此把“测试后写验收报告”错误当作“测试后改源码”，丢掉三个成功命令的回执；`settle()` 复核时同样无法补记。

最小修复：`record_column_evidence` 只把可能改变行为的后续写入当作失效点。明确的验证报告（文件名含 `report` 且后缀为 `.md`）不使先前命令失效；未知路径和源码/配置写入仍保守地要求重新验证。单测覆盖“测试→报告→完成可保留证据”与既有“测试→源码更改必须重验”；再恢复同一 Task 的验收列。此处不修改 Workflow Contract、不伪造测试回执，也不把所有文档写入一概视为安全。

补丁的 Worker 选定验收回归 9 通过。对当前隔离数据库制作只读逻辑副本后，以真实集成 Run/Attempt 运行新 `record_column_evidence()`，成功补记 `runtime.behavior` 回执；无须重跑已经通过的集成测试。确认无运行中 Task/Conversation Job 后，才能重启隔离服务并恢复原 Task 的验收列。

恢复原验收列后又暴露 `Required behavior evidence missing: backend_implementation`。后端此前成功 Run 已有真实 `mvn test -q` 回执；但前端溢出后的监督唤醒曾无参数 `task.retry`，错误回到后端。该多余后端 Run 只尝试了三次失败的 Maven 命令，未写入任何文件，随后因测试服务重启而 `interrupted`。`TaskFeedbackRepository.verified()` 固定选同名 Column **最后一次访问**，即这个无产出的中断 Run，因而屏蔽先前成功 Run 的有效回执；`settle()` 的失效序号也把这次访问计入。重试默认目标补丁可防未来多余回流，但历史 Task 仍需正确识别无效访问。

最小修复：在 `task_feedback_repository.py` 共用一个“最近有影响的 Column Run”判定：`succeeded` Run 或有成功、可能改变行为的写入的未完成 Run 是影响点；只有失败命令/只读操作、没有成功写入的 `interrupted/failed` Run 不作证据失效点。`verified()`、终态补记与 invalidation barrier 使用同一规则；若中断 Run 确实改过源码，仍拒绝旧回执。测试覆盖成功验证→无写入中断访问→保留旧回执，以及成功验证→中断访问写源码→拒绝旧回执。修好后从原验收列继续，不改 Task Plan 或绕过证据门槛。

该证据判定补丁已通过 70 项相关回归测试。对隔离数据库副本按实际后端、集成和验收 Run 重算，后端与集成均找到真实测试回执，终态判定返回 `done/done`；下一步在隔离服务加载补丁并恢复原 Task 验证实际流程。

全量单测 529 通过、2 个 `ResourceWarning: unclosed database` 收集期错误；两个受影响用例分别单独重跑均通过，暂无证据指向本补丁的功能失败。相关目标测试 68 通过，新增 continuation 原文测试另行通过。
