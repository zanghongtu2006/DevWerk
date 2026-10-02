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
