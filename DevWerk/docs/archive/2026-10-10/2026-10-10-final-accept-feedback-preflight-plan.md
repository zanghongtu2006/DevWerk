# 最终验收反馈阻塞：证据与最小修复方案

## 本轮黑盒事实

- 新 Project `prj_26a999813752467d96bb6d3dc055d463` 经五轮自然语言讨论后创建了 Workflow 和 Task `tsk_612f15b216244473aa233cb83daae29a`。看板自动走过需求、设计、前后端、集成、系统测试、交付，返修后到达 `accept`。
- `accept` Run `run_96e3f6dbb2af4c94b8066af78d9733d6` 的 Agent 提交了 `delivered → done`，`column.complete` 先返回 accepted；随后 `V1Store.finish_run → TaskFeedbackRepository.apply_completion_evidence` 抛出 `Unresolved Task feedback: feedback_bfd845d6d35a4c389769b83b399e1e1b`。Runtime 把它归为 `runtime_permanent`，Task 停在 `recovering/paused`，没有走该 Workflow 已声明的 `acceptance_failed → integration_review` 返工边。
- 三条未结清反馈分别来自系统测试、后端返修报告和集成复审，均要求 `system_test/runtime.behavior` 的新证据。系统测试 Run 12 的 `python tests/qa-test.py` 回执退出码 0、文本 `5/5 passed`；但随后 Worker 又写入 `tests/acceptance-report.json`。当前 `_behavior_affecting_write` 将其视为后续写操作，`record_column_evidence` 不签发行为证据，因而反馈未结清。此写法是模型手工写报告，不可简单放宽到“写报告不影响验收”，否则会掩盖执行与报告来源不一致。
- `tests/qa-test.py::test_s005_persistence_after_restart` 在重启后登录成功、任意其它响应、甚至重启函数返回 False 时均不抛断言；`main()` 只按异常计失败，因此文本 5/5 是假通过。该文件是测试 Project 交付物缺陷，不能由 DevWerk Runtime 直接相信它的自然语言报告。
- 用户反馈 Job `cjob_9d45ac7017774b74a533bc9f892c05e6` 曾持有规划租约约 25 分钟、128 次工具调用后 `ExecutionBudgetExceeded`，没有完成 Worker 交接；这是独立的监督回合低效问题。不能因为验收错误而把 Main Agent 改造成受限状态机；其方案另行核对 `agent.worker.send` 与待执行 Assignment 边界。

进一步核对：`AgentRepository.send` 实际支持省略 `assignment_id`，把输入持久排队给已有 Worker，在下一次 Assignment 消费。故 `pending` 状态不需要新增 Mailbox 职能或新队列；目前工具描述和 Main Agent 回合指令没有把这条途径说明清楚，也没有把成功的 Worker 交接视作本轮应尽快回复的边界。

## 本次修复边界

1. `app/v1/repositories/task_feedback_repository.py` 增加只读终态预检：按现有 `verified()` 和反馈状态，列出当前 Column 不能补齐的未结清反馈与具体检查。**不**把后写的 JSON/Markdown 报告自动当成通过，不修改已有证据计算和 Workflow 图。
2. `app/v1/agent_tool_execution.py::_complete` 在提交目标为 `done` 时调用预检。若别的 Column 的检查未结清，`column.complete` 返回可见的拒绝回执，说明需选择当前 Workflow 声明的返工 outcome，并保留 Agent 的当前回合供其选择；最终 `finish_run` 的断言仍保留为事务性最后防线。不能把这类业务验收缺口误报为 `runtime_permanent` 后暂停。
3. `app/v1/runtime.py` 的 Column 输入应明确给最终验收 Worker 展示未结清反馈与检查状态，要求其依据已声明的返工边处理；不新增权限、状态位或额外 Agent。
4. 针对以上路径在 `tests/test_worker_selected_acceptance.py` 写回归：有未结清的跨列行为检查时，`delivered → done` 先收到拒绝，可改走已有返工边；证据真正产生后同一反馈可结清并完成。另记录交付物脚本 S-005 假通过，后续黑盒必须以真实 HTTP 行为或独立断言复核，而不能引用其 5/5 文本。
5. `app/v1/capabilities.py` 与 `app/v1/conversation.py` 只补准确的工具协议提示：已有 Task 的执行反馈优先交给当前 Worker；`pending` 且 Worker 已存在时省略 `assignment_id` 排队给下一 Assignment；告知用户“已排队”与“已消费”不同。`app/v1/agent_runner.py` 在成功 `agent.worker.send` 后要求尽快通过 `conversation.reply` 结束当前回合，释放规划租约。只调整交接时机，不禁止通用 Main Agent 直接工具能力，也不增加状态、权限或 Mailbox 对话。

## 验证与后续

先跑最小相关测试，再重启 DevWerk 使修改生效。历史 Task 已暂停，可放弃；新 Project 自然语言讨论至少五轮，创建任务，由看板自动推进。约五分钟间隔看流程状态；遇失败先取精确回执。最后用真实构建和独立 HTTP 断言核对交付，不能把模型生成的 `PASSED` 字符串当通过。

## 实施与回归记录

- 已实施终态反馈只读预检及 `column.complete` 拒绝回执；保留事务终态校验，没有放宽 `runtime.behavior` 或将报告文件自动当测试证据。Runtime 原本已向 Worker 传入完整 `task_feedback`，只补充了终态检查提示。
- 已明确 `agent.worker.send` 的 pending Assignment 用法；已有 Task 的反馈回合优先交给当前 Worker。用户回合向仍在 `pending/running/waiting` 的 Task Worker 成功排队后，下一模型请求要求 `conversation.reply`，释放规划租约；恢复/失败 Task 及自动监督回合不走此快捷结束路径。
- `task.feedback.record` 已明确只记录新缺陷，成功的修复/审查报告写在本列 output，避免像本次后端及集成列那样把成功报告再登记为新的未结清义务。
- `tests/test_worker_selected_acceptance.py` 新增端到端案例：终态缺证先拒绝，Worker 选择现有返工边，返工行为回执结清反馈后再完成。`tests/test_project_supervision.py` 新增用户回合 pending Worker 交接条件。相关三组测试 `35 passed`；此前专测 `13 passed`。一次测试用例最初因测试提交的 output 不满足既有 schema 被拒，修正测试数据后通过，非产品缺陷。
- DevWerk 仅重启识别到的 `uvicorn app.main:app` 进程（原 PID 37028），8000 健康检查通过。新黑盒 Project `prj_3c219e3f42d549aeb0e1b84cb2a773ea` 已创建，正在自然语言讨论。
- 在首次重启后追加了上述缺陷工具说明，需在正式启动新 Task 前再次重启 DevWerk 以使说明生效。此时新 Project 仍在讨论，无 Worker Task。
- 新 Project 前五轮自然语言讨论 Job 均成功，仅讨论、未创建 Task：依次明确账号锁定策略，预置账号与 H2 文件持久化，HTTP 接口格式，A1–A8 验收场景及工作流交接。第五轮回复将实际八列 `software.ddd_delivery` 模板误描述为简化五列，并把 Conversation Agent 写入执行列；第六轮授权时已用自然语言要求它先检查真实 Loop 协议、自己选列并创建 Task，不由测试方替它构造 API 参数。
- 第二次仅重启 DevWerk 进程以加载补充工具说明（原 PID 28376），健康检查 `status=ok`、Supervisor 和 Gateway 均 running。第六轮授权 Job `cjob_dd84be8e865f4174848bc69af6841071` 已受理；等待约五分钟检查 Task 与 Workflow 真实回执。
- 补充组合测试 `test_conversation_intent_contract.py + test_scope_extension.py` 在约 48 个已输出用例之后长时间无新输出，已中断，不能计作通过；相关 35 项与单独的报告写入证据 4 项仍是本次有效回归结果。
- 第六轮后约五分钟检查：授权 Job `succeeded`，真实 Workflow Revision `wfrev_85ac5a694be64628a90b49a3ac4351cc`、Task `tsk_f3f187be76004fe195de5aca10bef52d` 已由 Conversation Agent 创建。发布的 Workflow 是实际八列模板（requirements、domain_design、backend_development、frontend_development、integration_review、system_test、delivery、accept），并含集成/测试/验收返工边；此前五列示意没有污染真实看板。需求和设计 Run 均成功，Task 自动进入 `backend_development/running`。回复“待执行”是排队语义，真实 Task 已在执行。
- 六轮用户回合对应的 Agent Run 工具调用数为 `3, 0, 0, 0, 4, 10`；授权回合 9 次模型迭代/10 次工具调用后成功排队并结束，没有自行实现交付物或等待 Worker。Task input 冻结 A1–A8。此模板仍使用 `required_behavior_columns` 的 Worker 自选行为命令，未配置固定 `acceptance_checks`；最终质量须独立检查脚本断言语义与真实 HTTP 结果。
- 下一五分钟检查：Task 仍为 `backend_development/running`，Run 3 无错误；没有异常返工或路由变化。按下一个窗口继续观察，不干预正在执行的 Worker。
- 再下一检查：后端列仍运行且租约心跳更新，累计 36 次工具调用；最后实际工具回执为约八分钟前的成功文件写入。当前可能在较慢的模型请求，尚无失败或状态机卡死证据，留一个窗口观察，不实时轮询或强行重启。
- 再五分钟检查：Task 仍在后端，但工具回执已由 36 增至 49，最新模型消息与工具调用都在最近两分钟内；此前空档是慢请求而非流程卡死。继续让单列 Worker 自行完成。
- 下一检查点后端仍运行，工具调用数保持 49、最近一次为成功的 `project.command.run`（约七分钟前）。仍没有失败回执或状态变化；已知 Provider 单次请求超时配置为 600 秒，此时先等下一窗口，不按空档本身重启服务。
- 下一窗口工具调用增至 67、消息增至 114，最近工具回执就在检查点附近；第二次慢请求也自行恢复。Task 继续在同一后端 Run，无重启/重复分派。
- 再下一检查点后端 Run 已执行约 33 分钟，累计约 100 次工具调用，最新回执在检查点附近，心跳正常。看板仍单列推进但后端执行成本偏高；先不把慢实现误判为路由故障，若达到 Agent 的一小时墙钟上限再依据失败回执处理。
