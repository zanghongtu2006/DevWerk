# Hermes 核心分支黑盒复测 2：监督交接修复后

分支 `feature/hermes-conversation-core`，加载提交 `8ae8c4f` 与 `fa63c91` 后，端口 8000 服务健康。上一轮失败 Project `prj_b2ff5d6dae2243828f5d6ae34a2b3ccf` 保留。新 Project `prj_f3ec6ffb98ce4b87ad9109da63b78e0d`，目录 `data/projects/hermes-core-login-retest2-20260930-144633`。创建前全局没有运行中的 Job、Task、Agent Run；数据库中其它旧 Project 有 pending Task，但并非本轮在途执行。

本轮严格复用上一轮 Project 的七条原始用户消息（六轮讨论含一次纠偏，最后单独授权），不修改交付物、不代 Agent 建 Workflow/Task。依次从 `v1_conversations` 读取旧用户消息内容，通过正式 HTTP Conversation API 发给新 Project：讨论轮 `start_task=false,mode=discuss`，授权轮 `start_task=true,mode=auto`。前六轮验证零 Workflow/Task，授权后观察真实 Grant/Workflow/Task/Column/Worker Session 和 MailBox。执行期约三分钟观察一次，先验证流程能否交付；终态后才审查源码质量和前后端测试。若再发生 P0，先记录证据与方案，再小步修复、独立提交、下一新 Project 复测。

第一轮 Job `cjob_0f50704de50144db9555d82a6dc100d6` 已提交，后续记录逐项补充。

| 讨论轮 | 旧消息 ID | 新 Job | 结果 |
| --- | --- | --- | --- |
| 1 | 777 | `cjob_0f50704de50144db9555d82a6dc100d6` | `discussion`，0 Task，Workflow 404。 |
| 2 | 779 | `cjob_deaed16effb945539ae18c20f5e47168` | `discussion`，0 Task，Workflow 404。 |
| 3 | 781 | `cjob_3bd957f7622d4612a0c940f0bbe2203b` | `discussion`，0 Task，Workflow 404。 |
| 4 | 783 | `cjob_aafcb2fb5f17456c920881ced66ed407` | `discussion`，0 Task，Workflow 404。 |
| 5 | 785 | `cjob_81e6cb0c559f42f680877722ad624633` | `discussion`，0 Task，Workflow 404。 |
| 6（纠偏） | 787 | `cjob_5679aa9234d5464a884bb967092cb61e` | `discussion`，0 Task，Workflow 404。 |

第五轮公开文字摘要再次出现未请求的 `/api/auth/status` 与独立路由；第六轮纠偏在同一 Session 下成功，但这仍暴露重复的自由文本语义漂移。最终是否污染执行以授权后 TaskPlan/代码为准。

授权轮复用旧消息 789，Job `cjob_5a6e6a6fbdd047be8c56c44c992f5cb3`，正式 API `start_task=true,mode=auto`，明确创建并交付一个端到端 Task。等待真实回执与 Column 状态。

约 15:11 北京时间：Job `running`，`phase=execution`，Grant `grant_d9ef1f45b74a458394751b85eaf5e64a`，Workflow 已创建但 Task 尚未创建。最近工具回执包含 TaskPlan 缺少冻结 `system_test/delivery/accept` 行为验收、Task 输入缺 `requirements_path`，以及对已建 Workflow 再用 `loop.apply`、已授权后再次 `conversation.execution.authorize` 的拒绝。Agent 仍在读取回执并调整，暂不判定 P0；需观察其能否完成 TaskPlan 准入。此处与监督修复无关，是原本较重的规划合同在真实模型上的摩擦。

最终结果：授权 Job `failed`，错误为 `ConversationProtocolStalled: Conversation Agent repeated an identical failed tool operation without changing its arguments: conversation.execution.authorize`。已有 Grant、Requirement、Workflow，但 Task 数量为零；因此没有进入 Column Worker，属于规划控制面 P0 阻塞，而非交付物编译失败。53 次工具调用中 `agent.result.read` 占 32 次，目标主要是约 19 KB 的 `workflow.inspect` 回执；首次 `conversation.execution.authorize` 成功，后续变更参数再次授权被 `TurnAlreadyResolved` 正确拒绝，最后一次与 `loop.inspect` 批量提交因协议拒绝，重复失败操作触发停机。`workflow.acceptance.configure` 两次验证失败后成功，随后 `task.plan.save` 分别因 `requirements_path` 缺失及行为验收不完整失败；Agent 未能继续修好 Plan。失败期间未绕过授权，也未创建虚假 Task。独立修复方案见 `2026-09-30-conversation-planning-control-plane-fix.md`。
