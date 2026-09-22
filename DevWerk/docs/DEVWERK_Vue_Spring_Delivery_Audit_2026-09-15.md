# Vue + Spring Boot 登录安全项目交付审计

审计快照：2026-09-15 17:54（北京时间）。只读审计，不修改 DevWerk、数据库或测试项目。项目 `prj_8d5d626356124d43bfca175ebe6db0aa`，唯一交付 Task `tsk_c5b1faa3e2f14c1ba6ee05a4e850597c`。运行中的数值以此快照为准。

## 结论

**未交付，也不能验收。** Task API 返回 `running/backend_development`，但后端 Assignment 已 `blocked`，当前 Attempt 35 停在 `running`，Task 租约在 14:19 已过期。17:46 查询时最近业务活动为 14:18；Supervisor 的最后成功 tick 为 14:19，之后反复抛出 mailbox 唯一索引错误。`/v1/health` 仍显示 `ok`，与真实调度停滞不符。

Workflow 确实来自 `software.ddd_delivery` 1.2.1，只有一个端到端 Task。`requirements` 和 `domain_design` 成功；`backend_development` 未成功；`frontend_development → integration_review → system_test → delivery → accept` 均未开始。项目中没有 Vue 前端、交付 README、最终验收或可证明通过的完整运行结果。

## 阶段与产物

| 阶段 | Runtime 结果 | 可核实证据 |
| --- | --- | --- |
| requirements | succeeded，1 Attempt | 已写 `docs/requirements.md` 和 `docs/requirements-traceability.md`。 |
| domain_design | succeeded，1 Attempt | 已写领域设计、架构、API 契约和测试计划。 |
| backend_development | 表面 running，实际 blocked/停滞 | 后端 Maven 工程与 Java 源码、测试文件已写；未完成该 Column 的成功回执。34 个 Agent Run failed；34 个 Attempt interrupted，Attempt 35 仍 running。 |
| frontend_development 及其后续五列 | 未进入 | 无 `frontend/` 工程；无 Vitest、集成审查、系统测试、delivery、accept 结果。 |

后端阶段写的 `docs/backend-development.md` 声称“实现完成”，但这只是 Agent 文件内容，不能覆盖真实测试与 Column 状态。

### Maven 测试证据

`backend/target/surefire-reports/TEST-*.xml` 记录 **56 个测试：38 通过、14 个错误、4 个断言失败、0 跳过**。两个 `system.command.run` 的 `mvn.cmd test -f backend/pom.xml` 执行回执均为 exit code 1。14 个 Controller 错误的共同根因是 Spring ApplicationContext 无法加载：`AuthenticationService → SecurityConfig → JwtAuthenticationFilter → AuthenticationService` 构造依赖环。4 个断言失败集中于 Lockout/RateLimit 的时钟窗口测试；测试配置在运行后替换静态 `Clock`，已经构造的 Service 仍持有旧 Clock，不能用这几个失败冒称安全规则已验证。后端类文件存在只说明编译曾进行，不能证明应用能启动。

安全质量风险（尚未进入独立审查）：`application.yml` 硬编码 JWT 签名密钥，且 H2 Console 打开并被 `SecurityConfig` 放行为公开路径。当前是本地演示，但仍需在验收前明确配置与暴露边界。

## 模型/API 与 Token 消耗

来源：`data/devwerk.db` 的 `llm_usage`、`v1_conversation_jobs`、`v1_agent_runs`、`v1_agent_assignments`，按此项目/Task ID 过滤。Provider 为 MiniMax-M2.7（Anthropic 兼容接口）。最后一条 LLM 记录发生于 14:18:51；到 17:54 未见新的模型调用。

| 消费者 | 回合/Job | LLM API 调用 | 输入 Token | 输出 Token | 记录的输入+输出 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4 个用户 Conversation Job | 4 | 18 | 127,539 | 11,720 | 139,259 |
| 由 mailbox 触发的 Conversation Job | 43（40 成功、3 失败） | 207 | 2,224,530 | 86,627 | 2,311,157 |
| Column Agent（全部列） | 36 Agent Run；21 次实际模型调用 | 21 | 126,227 | 36,714 | 162,941 |
| **总计** | 47 Conversation Job + 36 Column Agent Run | **246** | **2,478,296** | **135,061** | **2,613,357** |

`llm_usage` 还记录 8,177,818 个 `cached_input_tokens`，这是 Provider 返回的独立字段；不把它直接再加到 2,613,357 的 `total_tokens` 或推断收费金额。246 次调用中 245 次成功、1 次 Provider 400。此审计中的“API 调用”特指被用量表记录的 LLM 请求；`api_requests` 表对此项目没有行，不能用其虚构 Web/API 请求数量。

Column 消耗可按 Column Run 时间归属：requirements 3 次/14,226 Token；domain_design 4 次/55,566 Token；backend_development 14 次/93,149 Token。**88.4% 的全部记录 Token 来自 mailbox 对话**，不是写后端代码本身。Conversation 43 次 mailbox Job 中发生 27 次成功 `task.resume`、13 次成功 `task.retry`，但都没有消除未完成外部执行回执。后端 Assignment 累计 14 次模型调用、38 次工具调用、25 次 resume，触及其 `assignment_max_resumes=25`；34 个失败 Agent Run 中 24 个为同一个 `ExecutionReplayUncertain`，后续 9 个为 `ExecutionBudgetExceeded`。这是重复状态消费而非 34 次有价值的重新开发。

## 失败传播过程

1. 13:40 后端 Worker 开始；写了后端源码、JUnit 测试和“实现完成”文档。
2. 13:44 调用 `project.command.run` 时把项目**绝对路径**作为 `cwd`，而 ProjectFiles API 只接受项目内相对路径。请求输入中的 `cwd` 恰好等于 Project `base_dir`，但按该 API 契约仍非法。`files.py:run()` 在执行前拒绝它；外层却已经插入 `status=started` 的执行回执 `receipt_0c7ecd98549f484f9cc7bfcc63a9743d`，之后没有结算。于是 Runtime 错把可立即纠正的参数错误升为“外部副作用结果未知”。
3. 后端 Assignment 被 `effect_outcome_unknown` 阻断。每次 mailbox 通知激活 Conversation Agent；它对同一个 Task 做 `resume` 或 `retry`，但旧回执仍是 started，Worker 一恢复即再次失败。由此形成阻断 → mailbox → LLM → resume/retry → 再阻断的重复消费。
4. 25 次 Assignment resume 后出现 `ExecutionBudgetExceeded`。其中一次 Conversation Job 自行运行 Maven，真实 Surefire 报告证实测试失败；但该做法并不能让原 Column 成功，也没有解除原始回执。
5. 14:17 最后一次 `task.resume` 把 Attempt 35 置为 running，Assignment 却已经 blocked。14:19 租约过期；Supervisor 的过期租约恢复路径调用 `task.runtime_blocked` mailbox append，没有为这次 append 建立对应新事件，取回之前的 Event `7598`。Mailbox `287` 已使用该 event_id，唯一索引 `idx_v1_mailbox_event` 因而抛 `UNIQUE constraint failed: v1_project_mailbox.event_id`。事务回滚，Task 不能从 expired running 进入 recovering；每个 tick 重复异常。

17:54 的 `data/logs/devwerk.log` 已约 172.6 MB，`sqlite3.IntegrityError` 文本约 24,817 次，并继续增长。此时尚未继续消耗 LLM Token，但磁盘写入与调度停滞仍在发生。P0 细节见 [独立 Bug 记录](bugs/2026-09-15-software-delivery-replay-mailbox-scheduler-stall.md)。

## 审计判定与恢复边界

- 可接受事实：Loop 正确选中，需求/设计列成功，后端文件及失败测试证据完整。
- 不可接受声明：后端完成、软件可运行、自动化测试通过、前端完成、Task 已交付。均没有真实证据。
- 不建议再次盲目 `task.resume`/`task.retry`；原 started Receipt 与 blocked Assignment 必须先经正式结算/可判定的失败处理，且 Supervisor 唯一索引异常必须修复，才能谈恢复。保留项目 DB、Surefire 报告与产物供复核。
- 本审计没有停止服务或改写任务状态；服务停机以阻止日志持续增长已单独向用户请求许可。

