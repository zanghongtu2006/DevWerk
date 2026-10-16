# 新项目后端列完成协议卡死：证据与修复方案

## 黑盒事实

Project `prj_3c219e3f42d549aeb0e1b84cb2a773ea` 的 Task `tsk_f3f187be76004fe195de5aca10bef52d` 经五轮讨论和第六轮授权，由 Conversation Agent 成功创建八列 Workflow。需求、设计列成功。后端列 Run `run_ec465f63b05342229f0f936f7941f906` 在约 38 分钟后停在 `recovering/paused`，Run 错误 `Column Agent ended without calling column.complete`。

DB 回执显示真实序列：

1. 首次后端 Agent Run `arun_953005ff79184e70946a02d090c2b093` 运行 79 轮、101 次工具调用，Maven compile/package/test 均曾退出 0。它还直接执行了 `java -jar ...` 和 `mvn spring-boot:run` 这类不会退出的服务命令；`project.command.run` 有界超时后清理其子进程，后续独立 `curl` 不可能访问刚被清理的服务。Tool 描述已经明确“foreground service will time out; start/assert/stop in one bounded command”，Worker 仍未遵循。
2. 因多次失败的 HTTP 试探仍留在本列执行账本，`column.complete backend_ready` 被正确拒绝。现有拒绝消息把 **约 3,390 字符的 Spring Boot stdout** 附在 `command timed out` 后，再把真正的“稍后运行成功的行为测试或提交失败/返工 outcome”提示放在尾部。Worker 五次重提完成；`CompletionAttemptGuard` 报 `protocol_permanent`。它误以为旧日志里的 `Admin user already exists` 是 Runtime 缓存的业务失败，未意识到当前失败是前台服务命令超时和后续 curl 连接失败。
3. 监督回合向 Worker 发来“提供简短诊断摘要供我决定重试或修复”的直接输入。第二次 Agent Run `arun_292b5136733f49938ea5b8af7e19f4ce` 再次得到同一完成拒绝，随后只输出了一段自然语言诊断，未调用 `column.complete`。`AgentExecutionRunner.run` 对 Column 无工具文本回复立即抛 `RuntimeError`，Runtime 归类 `runtime_permanent` 并暂停 Task。Worker 不能靠一段普通回复完成 Column；监督请求也不应把它引导成对话 Agent。
4. 首次 Run 还尝试 `taskkill /F /IM java.exe`，退出 128（当时无匹配进程）。这种全局清理命令不能用于验收；本次测试方没有运行该命令。后续对交付的测试只可清理自己启动的准确进程。

## 最小修复

- `app/v1/capabilities.py::_command_run`：失败 ToolResult 的 `error.message` 只给出错误类型、退出码或超时及“stdout/stderr 在 output 字段”，不把完整输出重复写进 error。工具的结构化 output 保留原有截断上限和回执，诊断能力不降低。必要时同步 `system.command.run` 的同型返回，避免另一入口重复问题。
- `app/v1/services/completion_admission.py::CompletionAdmissionService.evaluate`：对未结清失败取有界摘要和失败操作，明确“后来的成功行为测试才能结清失败；前台服务超时意味着需在一个有界命令里启动、请求、结束”，使修复步骤出现在短回复的开头。保持失败必须结清、成功构建不能代替 HTTP 验证的现有规则。
- `app/v1/agent_runner.py::AgentExecutionRunner.run`：Column 模型在无工具调用时若只给文字，先返回一次短的协议纠正并继续当前 Run，要求执行工作或调用 `column.complete`；重复后仍可按原 Runtime 错误停止。不能把文本当完成，也不自动替 Worker 选业务 outcome。
- `app/v1/conversation.py` 的监督说明：发给 Worker 的诊断应指导它继续执行、用本列完整合同提交 `column.complete`；不能要求 Worker 用普通聊天答复 Main Agent。仍使用 `agent.worker.send` 直接交接，Mailbox 只作通知。
- 回归测试精确覆盖：失败命令错误消息不复制大日志；带前台超时的完成拒绝有短、可操作提示；Column 在一次纯文本回复后收到协议纠正并调用 `column.complete` 可继续；反复纯文本仍不能假完成。保持当前 Workflow/Task 状态机不变。

## 验证

先运行针对性单元测试和相关完成协议/监督测试。服务仅在修改完成后重启，使新代码生效。旧 Project 可保留为失败证据；新 Project 再走五轮自然语言讨论、授权创建 Workflow/Task，约五分钟检查看板进度，并用真实构建和独立 HTTP 行为核对最终交付。

## 实施记录（进行中）

- `project.command.run` 和同型的 `system.command.run` 失败消息已改为有界摘要，结构化 `output.stdout/stderr` 原样保留。超时回执直接说明“前台服务必须在同一有界命令内启动、检查、停止”；完成拒绝对错误正文取前 300 字，避免 Spring Boot 日志把修复提示淹没。
- Column 只返回纯文本时，Runner 现在给出短的完成协议纠正并继续当前 Run，最多两次；仍未 `column.complete` 则沿原错误路径停止。监督提示改为纠错输入，不请求 Worker 用聊天消息回复诊断。
- 针对命令失败、完成拒绝和一次纯文本后正确完成的 6 项专测通过；相关组合测试正在执行。未降低未结清失败/行为证据门槛，也未改任何 Workflow 路由。
- 完整相关组合（capability、completion evidence、Worker-selected acceptance、Project supervision）`74 passed`。增加纯文本重复三次仍不得假完成的用例。首次代码补丁把失败消息辅助函数误插入 `_loop_apply` 中部，静态检查发现后立即移到该方法的 `return` 后；最终相关组合测试覆盖了 Loop 调用并通过。
- 仅重启识别到的 DevWerk uvicorn 进程（原 PID 28484）；8000 健康检查 `ok`、Supervisor/Gateway `running`。新 Project `prj_9887385cae194d82a4a09632559d0108` 已创建并开始第一轮自然语言讨论，旧失败 Project 保留为证据但不恢复。
- 新 Project 前两轮 Job 均成功、未创建任务。第一轮由 Agent 主动梳理边界；第二轮确认锁定/持久化后，它又提出失败计数重置、锁定提示、账号来源和登录结果等问题。第三轮自然语言已确认这些边界，继续讨论接口与测试；未替它写 Workflow/Task API 参数。
- 2026-10-11 续测：新 Project 完成五轮自然语言讨论，第六轮授权后，Conversation Agent 调用 `loop.inspect`、`loop.apply`、`task.plan.contract`、`task.plan.validate`（首检指出 EntryEvidenceMissing，修正后通过）、`task.plan.save`、`task.create`；Workflow `wfrev_056b76bcd9e749e88c83d7ffd8ca8cac` 为八列单 Agent 有向图，Task `tsk_faffe78d85c04be0a689b9cadbeddbfa` 自动完成 `requirements`、`domain_design`，进入 `backend_development`。最终交付仍待观察，按约五分钟间隔检查。
- 同一 Task 的后端列曾运行带前台 Java 服务的 `project.command.run`，600 秒超时后得到有界失败回执；Worker 随后继续修正并成功调用 `column.complete`。`backend_development` 与 `frontend_development` 均已成功交接，Task 当前进入 `integration_review`。这次超时没有造成 Task 永久暂停；仍需观察系统测试和验收。
- 集成审查首次 Worker 曾因未结清失败命令而被 `column.complete accepted` 拒绝，形成 `CompletionProtocolStalled`；Runtime 随后保留 Task 并派生新的本列 Worker。新 Worker 完成修正，`integration_review` 最终成功，Task 自动进入 `system_test`。未手动改写状态或调用交接 API。
- `system_test` 首次真实运行 `scripts/acceptance/run_tests.py` 因 Windows GBK 输出符号异常退出；修订后再次真实运行，报告为 0/9，通过门槛没有误报。Worker 持续尝试跨命令启动 Spring 服务，但 `project.command.run` 会清理子进程，这类调用的成功退出码不能证明服务仍在。测试列尚未完成。已用自然语言向 Conversation Agent 反馈阻塞，请它依现有 Worker 监督工具推动本列修复或按图返工；未替其修改状态或交付代码。
