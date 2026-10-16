# 黑盒回归监控备忘（测试者工作记录，非 DevWerk 产品需求）

旧项目：`prj_68f94e0f4ba64a1398e48bd61d88270d`；旧 Task：`tsk_c2872f27727049e6baeb3373412e7259`。新测试 Project：`prj_87d10ac45d654f26a933fd84662760e1`，详见 `2026-09-30-vue-java-login-blackbox-retest.md`。新 Project 已五轮讨论、授权及一次恢复；Workflow revision 1，Task 0，规划阻断；**当前无活跃 Task，因此不定时轮询**。现阶段代码方案在 `2026-09-30-login-retest-planning-boundary-plan.md`。

## 当前已证实

- `blackbox-login-*.stdout/stderr.log` 是 2026-09-28 17:24 起本次测试手工启动服务的控制台重定向文件，不属于产品日志。`devwerk.log` 从 2026-09-21 停写，是 PyCharm 持有文件导致按日轮转 `WinError 32`；恢复处理器已加载到 8000 服务，2026-09-29 11:16 起真实追加，不再创建黑盒重定向文件。
- 失败信息可由 `previous_agent_failure` 确定性注入；预算耗尽后的显式 `task.retry` 原先复用旧 Column Run/Assignment，已修为仅对精确 `execution_budget_exceeded` 创建新 Run，真实 Task 验证新 Assignment 从零计数。`project.files.read` 已支持 offset；超大文件结果的直接分页投影已加载并经第 13 次真实 Run 验证，但 Column 范围过大仍是独立问题。
- 当前 Task 已沿 `frontend_development → integration_review → frontend_development` 返修；Review 曾根据旧浏览器报告作出不准确归因，前端 Worker 收到了纠正。浏览器交付文件仍在修改，不在运行期间逐行代审或继续替 Worker 设计实现。
- 2026-09-29 04:32 UTC，第 12 列前端 Assignment 再次在 100 次模型调用后因预算耗尽暂停，之后 3 小时无自恢复。两条 Worker 输入虽排队，却绑定旧 Assignment，恢复后无法消费；一条输入对应的 Conversation Job 因最终答复引用错误工具 ID 而失败，通知没有准确显示投递已持久化。修复方案及代码/单测见 `2026-09-29-blackbox-control-plane-recovery-plan.md`。全局运行 Job/Run/Task 数均为 0，待重启加载并沿现有 Task 恢复。
- 08:13 UTC 正式 Conversation Job 已成功 compact + retry；第 13 列前端 Run 消费两条迁移输入，mailbox 无积压。09:05:31 UTC 再次因 100 次模型调用上限暂停，Task `recovering/paused`。此后没有原样 retry；Workflow 修订的四次 Conversation Job 均未写入新 Plan。详见 `2026-09-29-login-blackbox-system-retrospective.md` 与 `2026-09-29-conversation-objective-effect-contract-plan.md`。暂停期间不做定时轮询；再次开始正式执行后恢复约 3 分钟一次摘要检查。

## 监控节奏和检查点

每约 **3 分钟**读取一次 DB 控制面摘要；间隔中不轮询 Worker 进度。若用户新消息或明确的终态事件先到，提前查看。每次只读：Task `status/control_state/current_column/failure_code`，最近 Column Run 的 `sequence/status/outcome`，Assignment 的 `status/model_calls/tool_calls`，本 Project Conversation Job 的终态，以及 `v1_worker_inputs`、mailbox 的未消费/失败计数。日志只在异常时查小范围，不扫描完整模型 trace。

重点判断：Workflow 是否沿声明边推进；是否在同一组 Column 循环且无新鲜验收证据；失败事实是否进入下一 Worker 的输入；mailbox 是否只通知，不替代 Conversation/Worker 通信；是否出现 Job/Run 长时间无进展、消息积压、预算/上下文阻断。对单次正常耗时的 LLM 调用不作即时干预。

仅在**控制面阻断**、同一返修边重复至少两次且无新证据、确定性失败消息未送达，或 Task 终态时再深入调查和修框架。首先复核冻结 Workflow、反馈证据时序、通知与 Worker 输入链；不要默认归咎交付物或盲目增大预算。每项代码变更先写方案、最小修改、测试，再在安全空闲边界加载。

Task 交付完成后才集中检查前后端登录/防刷、报告真实性、Playwright 浏览器场景、15 分钟解锁的可验证性，并区分交付物缺陷与 Workflow/验收预设缺陷。若 Task 无法交付，记录清楚流程阻断点与证据，不把中间产物写成已交付。
