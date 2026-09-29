# 黑盒登录测试：浏览器验收阻断与 Workflow 边界调整方案

Project `prj_68f94e0f4ba64a1398e48bd61d88270d`；当前 Task `tsk_c2872f27727049e6baeb3373412e7259` 保留为 revision 4 的原始测试证据。这里记录流程层面的复盘及下一轮可追踪方案，不宣称交付完成，也不直接修改测试 Project 的源码。

## 实测结论

1. 第 13 次 `frontend_development` Run 于 2026-09-29 09:05:31 UTC 再次因 `execution_budget_exceeded` 中断，Task 为 `recovering/paused`，Assignment 用满 100 次模型调用、115 次工具调用。该 Run 有 69 次 `project.files.read`（其中 `verify_browser.py` 36 次）、34 次 `project.command.run`（26 次失败）、7 次文件写入。新 `project.files.read` 分页投影已加载，未再出现 `agent.result.read` 的额外分页循环；但重复读取一份约 25 KB 的浏览器脚本和大量失败命令仍消耗预算。此前第 9、12 次前端 Run 也各用满 100 次调用；额外预算不能证明流程可交付。
2. Worker 输入链与 mailbox 不是当前阻断源：两条旧 Assignment 输入已迁移并由第 13 次 Worker Run 消费，所有 75 条 mailbox 通知已确认。Conversation 的 compact + retry 均有真实成功回执。Worker 收到最新错误后确曾修改并重跑脚本。第 13 次先遇 Python 子进程以 Windows GBK 解码 Playwright UTF-8 输出失败，再遇 `frontend/backend/...` 错误 JAR 路径，最后恢复到“后端 120 秒未就绪”，并在预算到 100 时暂停。
3. 在 Task 暂停后独立启动同一 JAR，**持续排出 stdout**，57.2 秒内 8080 端口就绪，启动日志约 6.2 KB，证实 JAR 和 Java 环境本身可以启动；临时 Java PID 37440 已核对并停止，8080 不再监听。`verify_browser.py::start_backend` 以 `stdout=PIPE, stderr=STDOUT` 启动 Java，但 `wait_for_backend_ready` 在 120 秒内只探端口、不读取该 PIPE，也不检查子进程是否提前退出。Windows 管道被启动日志填满后阻塞 Java 是**高可信推断**，但脚本未保存原始 Java 输出，不能把它写成已由原命令直接证实。下一轮修复须改为持续排出子进程输出或写入有界日志文件，并在子进程退出时立即返回具体错误；然后重新运行同一命令确认。
4. 冻结 Workflow revision 4 的 `frontend_development` 强制检查仅 `frontend_vitest`，最终 `final_browser_acceptance` 在 `accept` 列；但第 11 次 `integration_review` 根据旧浏览器报告记录前端 BLOCKER，`checks_json` 同时要求前端 Vitest 和下游 `accept/final_browser_acceptance`。旧报告早于脚本修改且“脚本缺少后端启动逻辑”的归因不符当时源码。Workflow 在实际使用中把浏览器测试脚本开发、服务编排、Playwright 运行和最终验收挤进前端开发返修；这与按 Column 切独立 context 的目标不符，并使审查反馈反复打回同一大列。
5. 验收预设还存在语义冲突：`final_browser_acceptance.purpose` 写 Playwright B-01~B-06，`scenario_ids` 却为 S-01~S-12；现有脚本主体通过 HTTP API 生成 S 场景，尚未证明浏览器交互，而 `frontend/e2e/login.spec.js` 将 B-06 的 15 分钟等待标为 skip。Runtime 只可验证声明的 ID、报告新鲜度、逐项 expected/actual 和测试源哈希，无法从报告格式推断实际是否使用浏览器。不能放宽 Runtime，也不能把 skipped 当通过。原始产品要求是 15 分钟后解锁；后端可用可控时间的行为测试证明它，浏览器 B-01~B-05 覆盖真实 UI。B-06 若保留为浏览器场景，必须明确可在 600 秒单命令限制内验证的真实策略，否则从浏览器预设中删除并保持后端 15 分钟解锁验收。

## 下一轮流程方案（保持 DevWerk 架构）

先保留 revision 4 当前 Task 的暂停与失败证据，不做第四次原样 `task.retry`。由该 Project 的 Conversation Agent 在**同一 Requirement** 下保存修订 Workflow Plan 并发布新 revision，原 revision/Task 不改。新增一个单 Agent 的 `browser_harness` Column，位于前端实现后、集成审查前，职责仅为 Playwright 测试源、浏览器脚本、前后端测试启动和真实报告；它可读取后端但不修改后端。`frontend_development` 只负责 Vue 实现与 Vitest；`integration_review` 只审查新鲜的当前代码/测试回执，不以旧报告推断当前缺陷。`browser_harness` 自身有浏览器检查或明确失败 outcome，失败可沿声明边交给对应开发列，不能在一个 Column 内启动多 Agent 循环。最终 `accept` 仍独立复跑冻结的浏览器验收；后端 15 分钟解锁由后端行为测试证据承担。修订场景 ID、测试源和验收责任时必须与原需求快照一致，不新增登录功能或绕过验收。

发布新 revision 后，经 Conversation Agent 将暂停的旧 Task 以准确理由显式置为 failed，再读 `task.plan.contract`，按新 revision `task.plan.validate → task.plan.save → task.successor` 创建同一逻辑交付工作的后继 Task。新 Task 从工作流入口运行，但已完成的前后端内容通过 Project 文件与 Worker 独立 context 复用；各 Column 只验证自己的职责。保留两个 Task 的 ID 和结果，不把旧 revision 的失败改写为通过。Conversation 操作若被拒，先记录持久回执、停止该步，不直接操作数据库或用 mailbox 传指令。

## 后续代码修复判据

当前再次阻断主要由测试交付脚本与 Workflow 预设导致，**不新增 DevWerk 核心补丁**。若修订 Workflow 仍把不同 Column 的反馈送错、对旧报告允许再次登记为当前事实、消息丢失、Column 无法合法路由，才按实际回执设计最小代码修复并补单测。普通命令重复失败是否需要 Column 级停滞保护，需要先确认连续相同失败且无其他成功进展；第 13 次 Run 中 Worker 曾自行改变命令，不能仅凭失败总数判定平台死循环。整个新 Workflow 到 `done` 后，再集中评估 Vue/Java 登录与防刷的产品质量和验收预设，区分交付缺陷与流程缺陷。

## 执行记录

通过正式 Conversation API 提交 Job `cjob_b55c2f8901994e1b8fab56debae428c3`，仅授权读取现有 Workflow/Plan、保存修订 Plan 和发布新 revision；明确禁止本轮新建、失败或重试 Task。API 返回 queued 只表示受理，是否真正发布以持久工具回执和 revision 为准。

该 Job 失败，未发布新 revision，Task 状态不变。原因不是 registry 缺工具：AgentRun 目录包含 `workflow.plan.save`/`workflow.publish`，但执行 Grant 之后模型可见 `task_graph_mutation_allowed` 仍为回合开头的 false。交互层投影缺陷及最小代码修复见 `2026-09-29-conversation-turn-authority-refresh-fix-plan.md`。修复加载后提交重试 Job `cjob_8088d30d3a1f444db3dfa3e4d8eef7de`，结果待核对。

`cjob_8088...` 得到 execute Grant，却重复 resolve，随后把只读检查与 resolve 混在同一批而被协议拒绝；最后仅引用旧暂停 Task 成功结束，未执行本轮 Workflow 修改。交互层空结果准入缺口及单测见 `2026-09-29-conversation-execution-result-admission-plan.md`。修复加载后改为分步：Job `cjob_7769bf0e524e4d91962979ab34e6044a` 仅请求保存 Plan；它在取得 Grant 后由 Provider `overloaded_error (529)` 失败，没有任何新 Plan/Revision。该 529 属外部 Provider 暂时不可用，按约 3 分钟间隔重试一次，不在高频轮询或改 DB 绕过 Conversation 路径。
