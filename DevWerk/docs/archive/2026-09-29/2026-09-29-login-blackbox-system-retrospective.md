# 登录防刷黑盒回归：系统级复盘与下一阶段边界

Project `prj_68f94e0f4ba64a1398e48bd61d88270d`；核心 Task `tsk_c2872f27727049e6baeb3373412e7259`。本文件汇总控制面、Kanban、Context/Memory、通知与交互、验收预设的关系；细节与逐轮回执见 `2026-09-28-vue-java-login-blackbox-test.md`、`2026-09-29-blackbox-control-plane-recovery-plan.md`、`2026-09-29-browser-workflow-boundary-and-evidence-plan.md`。**截至当前，测试没有交付完成，不应宣称通过。**

## 已证实的系统结果

- 已按用户要求完成至少 5 轮讨论，再由 Conversation Agent 建立 Workflow 并运行同一 Project。需求是 Vue + Java 登录/防刷简单 Demo；多个 Column 已形成代码、测试和文档。第 13 次 `frontend_development` Run 于 09:05:31 UTC 因 Assignment 100 次模型调用上限暂停，Task `recovering/paused`。第 9、12 次前端 Run 也各耗尽 100 次。没有第四次原样 retry。
- 先前预算恢复后的 pending Worker 输入绑定旧 Assignment，无法被新 Assignment 消费，属于确定性控制面 bug。已在新 Assignment 创建事务中按同 Task、同 Column、同 Worker、前驱 `execution_budget_exceeded` 精确转移；旧 ID 被接管后拒绝继续投递。真实第 13 次 Run 已消费两条迁移输入。mailbox 仅存事件通知，75 条均 `acknowledged`，没有让 mailbox 承担 Worker 指令传输。
- Conversation Job 已实际 `agent.worker.send` 后最终 `conversation.reply` 失败时，旧失败通知没有报告已排队输入。已把成功的 `agent.worker.send` 计入持久进展，并明确区分“排队”和“消费”。真实 Worker 输入已确认，不依赖模型口述。
- `project.files.read` 增加 `offset/next_offset` 与大文件直接分页投影；第 13 次 Run 未再使用 `agent.result.read` 追完整文件回执，说明该局部成本缺口被消除。但它仍进行了 69 次文件读取、34 次命令（26 次失败），其中 36 次读同一约 25 KB 浏览器脚本。**分页不是任务拆分**；单个前端列承担大量浏览器联调时，独立 context 的收益仍不足以保证完成。
- Conversation Agent 的用户回合开始于 unresolved，初始模型可见 `task_graph_mutation_allowed=false`；执行 Grant 后真实 dispatch 权限更新，但旧状态帧不刷新，导致模型声称 Workflow 写工具不存在。已按持久 turn contract、Requirement revision 在每次 Provider 请求前刷新权威状态。相关 Context/Conversation 测试 68 passed。
- 后续一次 execute Job 没有任何真实业务副作用，却引用旧暂停 Task 的状态以 `work_result` 结束为 succeeded。已增加执行回合最终报告准入：必须存在本 Run 成功的 write/process/control 回执；状态/讨论回合不受影响。相关 Conversation 测试 37 passed。该防线阻止“空执行成功”，不能强迫模型完成某个特定用户动作。
- `devwerk.log` 停于 9 月 21 日的原因是 Windows 上旧进程占用日志文件，按日轮转 `WinError 32` 后写入失败。恢复型日志处理器已在真实服务验证 9 月 29 日继续追加；`blackbox-login-*.stdout/stderr.log` 是本次测试手工重启时创建的控制台重定向，不是产品日志。当前服务采用 Temp 控制台输出文件，8000 健康，避免再在项目数据目录制造同名测试日志。

## 当前真正阻断：流程预设与交付脚本，不是 mailbox

冻结 revision 4 的 `frontend_development` 强制检查是 Vitest，`accept` 才拥有最终浏览器验收。但 `integration_review` 根据早于源码修改的空浏览器报告将脚本启动问题归咎当前前端代码，反馈要求前端修脚本并附带下游 `accept/final_browser_acceptance` 检查。实际执行中，前端 Worker 在同一列反复承担 Vue、Playwright 测试源、服务启动、报告生成和跨端诊断；这超出了按 Column 拆独立上下文的原意，且反馈证据时间顺序不足。现有反馈路由按 Column/Run 屏障检验后续 formal check，但反馈**生成时**没有自动保证其文字归因基于最新源码/命令。只有再次复现无新证据回退，才应考虑增加反馈新鲜度代码约束；本次先修改 Workflow 预设。

浏览器脚本实际失败链为 GBK 解码 UTF-8 Playwright 输出、错误 JAR 路径、后端 120 秒未就绪。Task 暂停后同一 JAR 直接运行且持续排出 stdout，57.2 秒内 8080 就绪；临时诊断 Java 进程已停止。脚本用 `stdout=PIPE` 启动 Java，却在等待端口期间不读 PIPE，也不检测早退；输出管道填满阻塞启动是高可信推断，需要下次在 Worker 路径以新鲜后端日志/命令验证。脚本主体还主要运行 HTTP API 场景，而冻结检查文字称 Playwright B-01~B-06，实际 `scenario_ids` 却是 S-01~S-12。B-06 的 15 分钟等待被测试文件标为 skip；不能把它当通过，后端自动解锁应由可控时间的行为测试覆盖。Runtime 对报告结构、freshness、source hash 和 expected/actual 逐项严格拒绝，无法凭报告 schema 证明实际测试技术；这里首先是验收预设和测试源审核职责问题，不能放宽 Runtime 迁就假报告。

## 修订 Workflow 的尝试与新的交互可靠性问题

按“前端实现→单 Agent 浏览器 harness→集成审查→QA→交付→独立验收”设计了最小 revision 方案，保留同一 Requirement 与原 Task 证据。通过正式 Conversation API 多次尝试：第一次因旧权限快照被模型误判写工具不存在；第二次模型重复 resolve、混批只读工具，最后空执行被误报成功；第三次 Provider `overloaded_error (529)`；第四次 Job `cjob_fca23f8e3bb444c4bf827da6e37ee78d` 正确取得 Grant、成功 `workflow.inspect`/`workflow.plan.list`，却没有尝试 `workflow.plan.save`，声称 recovering Task 禁止修订 Workflow Plan。`planning_repository.create_workflow_plan` 与 `_require_user_planning_turn` 不以 Task recovering 为禁令；这是没有工具拒绝回执支持的模型判断。当前 DB 仍只有原 `wfplan_98872984b1b54c55bdd7bf6195803fc1` 和 active revision 4，Task 未被 fail/retry/successor。执行回合空结果准入如今会把这种未完成明确标为失败；不会隐性推进 Kanban。

这说明用户的核心预期——Conversation Agent 通过结构化 Workflow 稳定组织长期任务——尚未由本轮黑盒证明。现有代码提供了单 Agent Column、Worker 生命周期、独立 context、通知/指令分离和严格验收，但模型在规划工具协议上的稳定性仍是发布阻断。四次修订 Job 合计 52 次模型请求、953,196 input tokens、31,303 output tokens（`llm_usage` 按 Job 时间窗统计），仍没有新 Plan；重复提示已经不是合理的测试策略。继续把每次模型误判都写成特殊 prompt 补丁，容易污染通用 Agent、扩大范围且不能保证下一轮。通用**目标—副作用合同**的代码位置、分步实施和限制见 `2026-09-29-conversation-objective-effect-contract-plan.md`。它必须适用于新 Requirement、Workflow 修订、TaskPlan、Worker 控制等通用动作，不能为本测试硬编码 `browser_harness` 或改 Web 显示调试信息。现有“执行回合至少一项真实副作用”是最低准入，不等于这个完整合同。

## 下一步执行边界

当前停止原样重试前端 Task，也停止重复提交同一 Workflow Plan 提示。保留 8000 服务与暂停 Task 现场、所有 DB/日志/回执。先评审上述通用目标—副作用合同的最小可实现形式及 Workflow 场景的单测，再按“文档→代码→测试→安全重启→黑盒”迭代；其间不直接改测试 Project 源码绕过 Worker，也不把 mailbox 扩成通信代理。Workflow revision 修订成功后，再决定是否显式 fail 旧 Task 并用新 revision 创建 successor；旧失败测试结果始终保留。新流程若最终到 `done`，再集中审查交付物质量，区分产品缺陷与流程/验收预设缺陷。
