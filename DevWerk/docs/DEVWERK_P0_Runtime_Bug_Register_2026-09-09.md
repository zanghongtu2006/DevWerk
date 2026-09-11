# DevWerk Runtime Bug 登记簿 — 2026-09-09

**2026-09-10 真实项目续修：** 六章小说扩写至十二章暴露的 E1–E6 / A–E 已完成代码修复。最终全量 **309 passed / 242.07s**，完整 Conversation Gateway 与真实项目 DB 副本的新增六章任务图验证通过。R7/R9 本次重新打开部分已完成本地验证；生产数据未修改，真实模型尚未交付新增六章。见 [范围扩展实施记录](DEVWERK_Scope_Extension_Implementation_2026-09-10.md)。字数硬校验 F 延后；旧 297 项结果仍保留为历史快照。

**上一轮实施记录：R1–R9 核心修复曾通过 2026-09-10 的本地全量回归，297 passed / 280.07s。具体落地、验证和限制见 [实施记录](DEVWERK_P0_Runtime_Implementation_2026-09-10.md)。当前重新打开的项目见上方续评及下方总表。本地通过不等于真实模型或生产项目已完成发布验收。**

编号沿用 [复审报告](DEVWERK_P0_Runtime_ReReview_2026-09-09.md) 的 R1–R6，不按聊天中的排列重新编号。配套文档：[详细修改方案](DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09.md)。

本次已根据用户明确的“持续 Main Agent + 可持续 Leaf Worker + 单职责 Column”目标修订。参考 [Hermes 源码分析](DEVWERK_Hermes_Architecture_Review_2026-09-09.md)。原六项复现事实不改变；修复范围改为 Agent / Session / Assignment，新增架构缺口不冒称具有与原六项相同的故障注入证据。

## 1. 评估与证据口径

P0 指本项目 v1 发布阻塞：错误完成、漏执行、主链路无法自行恢复、过期执行者改变状态或逻辑阶段无法收敛。不包含安全、多机部署及费用优化。

证据来自当前源码与独立故障复现，未采用 docs 下历史测试结果。复现入口为 `scripts/review_p0_runtime_2026_09_09.py`，使用临时 SQLite、临时项目和确定性模型响应，没有调用真实模型。

R2 在生产完成准入服务边界复现；R5 使用正式 capability 接口注册最小异步 adapter；其他项覆盖实际 Runtime、Gateway 或控制 handler。六项不是对指定历史 Task 根因的直接归因。诊断脚本退出码 0 表示诊断完成，不表示缺陷已修复。

## 2. 总表

| ID | 级别 | 缺陷 | 直接后果 | 状态 | 修改包 |
|---|---|---|---|---|---|
| R1 | P0 | Await 后操作身份碰撞 | 新操作被跳过，Task done | 代码修复 / 本地回归通过 | N3 |
| R2 | P0 | 模型声明的修复关系缺少约束 | 未修复失败通过准入 | 代码修复 / 本地回归通过 | N4 |
| R3 | P0 | 内存队列丢项，持久化队列不补领 | 项目后续对话一直排队 | 代码修复 / 本地回归通过 | N2、N5 |
| R4 | P0 | 控制提交缺少发起者 fencing | 旧 Conversation 改写 Task | 代码修复 / 本地回归通过 | N0、N4 |
| R5 | P0 | Await 结算与 ledger 脱节 | 已成功异步操作不能完成阶段 | 代码修复 / 本地回归通过 | N2、N3 |
| R6 | P0 | 预算只覆盖单次 AgentRun | 同阶段无进展反复 Await | 代码修复 / 本地回归通过 | N5 |
| R7 | P0 架构阻塞 | Conversation 事件回合不能扩展工作图 | 无法根据执行反馈自主拆分/修复 | 已修复：范围修订与两层回合状态刷新，完整 Gateway/全量回归通过 | N1、A–E |
| R8 | P0 架构阻塞 | Task 内 final_text 摘录代替持续 context | 跨相关 Task 无稳定私有会话契约 | 核心实现 / 本地回归通过 | N0、N2 |
| R9 | P0 架构阻塞 | 实例/分配/消息生命周期未分离 | 无法统一持续调度、定向消息与恢复 | 已修复：扩写不关闭、关闭不退休、显式后继和旧上下文保留，回归通过 | N0、N2、A–E |

## 3. R1 — 新调用被识别为历史重放

**不变量**：相同参数的新意图应执行；恢复同一个已持久化意图不重复执行。

**触发**：Agent Column 追加 x → timer Await → 恢复后按任务要求再次追加 x → 成功完成。

**期望**：文件 xx，两条不同操作意图各自结算。

**实际**：`REPEAT_AFTER_AWAIT {"task_status":"done","expected":"xx","actual":"x"}`。

**根因**：`app/v1/agent_tool_execution.py:59,157-162` 的内存 occurrence 在新 AgentRun 重置；相同 ColumnRun + 参数 hash + occurrence 产生旧 key；`app/v1/capabilities.py:151-157` 返回旧 receipt。

**影响边界**：跨恢复边界的写入、命令和控制调用。参数相同不能证明意图相同；本复现不需要崩溃。

**关闭条件**：同参新意图执行两次；已结算意图恢复只执行一次；结果不明的副作用不盲目重放。断言真实文件/调用次数和 Task 结果，不能只测 key 不同。

## 4. R2 — 无关成功操作被接受为修复

**不变量**：修复关系由执行事实和受约束的规则确定，不能由模型选择两个 evidence ID 确定。

**触发**：交付命令 exit 1，后续无关 print 命令 exit 0；completion.failure_resolutions 声称后者修复前者。

**期望**：拒绝成功完成，交付失败仍 unresolved。

**实际**：`UNRELATED_COMMAND_REPAIR {"accepted":true,"failed_delivery_was_reexecuted":false}`。

**根因**：`app/v1/services/completion_admission.py:62-92` 只验证顺序、成功、相同 capability/effect_kind。不同 project.command.run 仍满足这些条件。

**影响边界**：完成准入的替代修复通道，不要求恶意模型。本轮证明准入服务错误接受，没有把此探针描述为真实项目完整交付运行。

**关闭条件（Hermes 阅读后修订）**：无关成功不能消除 required 验收失败；普通 Agent 可以修正命令/参数并通过当前冻结的 Assignment 验收；探索性已知失败不永久污染完成状态；pending/unknown 不被隐瞒。禁止模型临时把 required 失败改为 probe。操作相似或 retry 关系是诊断关联，不是业务验收证明。

## 5. R3 — 一次领取异常造成队列缺项

**不变量**：数据库恢复可用后，持久化 queued job 必须能被重新发现，内存不能是唯一入口。

**触发**：队头 claim 注入一次 SQLite OperationalError，恢复 DB，创建后续消息，执行正常调度扫描。

**期望**：无需重启，旧 job 先运行，后续消息随后运行。

**实际**：`ORPHAN_QUEUE {"old_job_status":"queued","in_memory_queue":0,"enqueued_after_dispatcher_tick":[],"new_job_claim":null}`。

**根因**：`app/v1/conversation.py:186-215` 提前弹出并清除 pending；周期 enqueue 不补扫 queued；`app/v1/store.py:839-848` 又按顺序阻挡新 job。

**影响边界**：同项目会话和治理推进，不声称必然全服务停机。持续 DB 故障无法保证推进，但 DB 恢复后必须自动推进。

**关闭条件**：领取异常、任务取消、持久化创建后未入队、分页积压均可自动补领；竞争消费者不能重复合法执行。

## 6. R4 — 旧 Conversation 的控制写入生效

**不变量**：控制提交同时验证发起者所有权和目标对象版本。

**触发**：旧 owner 通过前置检查，receipt 领取后租约失效并由新 owner 接管，再执行旧 task.resume。

**期望**：ownership lost；目标 Task、状态事件、mailbox、projection 均不改变。

**实际**：`STALE_CONTROL {"reported":"ExecutionOwnershipLost","old_job_status":"failed","task_control_after_stale_handler":"active"}`。

**根因**：`app/v1/capabilities.py:171-179,1995-2001`；`app/v1/services/recovery_manager.py:450-461`。后置检查保护不了已提交 handler；目标 Task CAS 不等于发起者有效。

**影响边界**：已确认 task.resume。其他控制、记忆、规划写入属于实施盘点范围，不逐项冒称为新的已复现 Bug。

**关闭条件**：过期 owner 零业务写入；当前 owner 正常写入；DB 状态、事件及 receipt 原子结算；事务中不持锁调用模型或长时间外部命令。

## 7. R5 — Await 成功后 ledger 仍停留在 awaiting

**不变量**：保留调用历史，同时让完成准入采用当前结算事实。

**触发**：异步 process capability 返回 awaiting → poll 成功 → Agent 恢复后 column.complete。

**期望**：原操作 completed，不重复启动外部任务，正常完成。

**实际**：`COMPLETED_AWAIT_STALE_LEDGER {"external_receipt":"completed","task_status":"waiting","control":"paused","exception":"AttributeError: 'NoneType' object has no attribute 'get'","completion_rejections":0}`。

**根因**：`app/v1/runtime.py:907-912` 更新 receipt，`731-756` 从旧 invocation 恢复 ledger；`execution_ledger.py:120-129` 混淆 pending/failed；`completion_admission.py:102` 对 None 调用 .get。

**影响边界**：正式支持的异步 capability 生命周期。普通 timer await 本身不是充分触发条件。

**关闭条件**：成功、失败、重复回调、反序回调、结算后恢复前崩溃均有正确结果；仅修判空但仍错误拒绝成功不能关闭。

## 8. R6 — Await continuation 绕过逻辑阶段上限

**不变量**：Await、provider retry 和 worker 更换不能给同阶段无限新额度。

**触发**：相关次数上限均为 1，模型只发出 timer Await，每次唤醒再次 Await，连续恢复六次。

**期望**：有限次后停止新模型调用，进入明确 budget/no-progress 监督状态。

**实际**：`AWAIT_BUDGET_BYPASS {"status":"waiting","control":"active","v1_column_runs":1,"v1_column_attempts":1,"v1_agent_runs":7,"v1_await_handles":7}`。

**根因**：`app/v1/agent_runner.py:38-54` 局部计数；`app/v1/runtime.py:727-729,880-885` 重建 deadline 并复用 ColumnRun/Attempt。

**影响边界**：连续无进展模型唤醒。不把正常长期外部等待或费用高本身登记为 P0。

**关闭条件**：预算跨恢复持久化；轮询同一操作不等于启动新模型；达到上限后不多派发一次；有进展的有限 continuation 能交付。

**修订范围**：累计限制属于 Assignment/逻辑 Column，不属于 Worker 的整个生存期。Worker 可以长期 idle、保留 context 后接收新的合法分配；主代理无进展反复创建同原因修复 Task 也需要收敛。

## 9. R7 — Main 根据结果继续规划被 user-turn 条件阻断

**性质**：相对于此次明确的产品目标的 P0 架构冲突，源码路径确认，未标记为新的端到端故障注入结果。

**证据**：`app/v1/conversation.py:300-324` 仅用户回合允许图变更；`app/v1/capabilities.py:1883-1931` 明确拒绝非 user_initiated AgentRun 的 loop.apply、plan.save、workflow.publish、task.create。

**场景**：用户已要求交付需求，Worker 返回需新增修复/拆分的工作，Main 被结果事件唤醒，却不能创建后继 Task，只能等用户再发消息或操作已有 Task。

**建议**：Main 的规划能力由角色、有效需求范围、版本和 owner 决定，结果/监督事件可以触发它；leaf 永远没有扩图/再委派权。变更原因和计划修订持久化，重复事件不能无限扩图。

**关闭条件**：H01/H02/H23/H24。非用户事件驱动 Main 合法续规划；leaf 拒绝；重复/过期结果不重复创建或复活工作。

## 10. R8 — 持续 Worker 的独立 context 缺少可靠模型

**性质**：新目标下 P0 架构缺口。当前有 session，不应描述为完全没有 Agent 或上下文。

**证据**：`store.py:2218-2223` 按 project/task/session_key 定位；`2307-2338` 仅选最新成功与最新结束 run 的 final_text/error，排除 waiting；`agent_run_preparation.py:175-185` 将其拼入 prior history。

**场景**：D 完成 T1，之后 T3 需要 D 基于原决策、工作记录和新评审继续。目前跨 Task 会话不复用，终结文字摘录没有覆盖游标/完整恢复关系。

**建议**：增加稳定 WorkerInstance，session 不再以 Task 为身份边界。持久消息 + 有覆盖范围摘要 + 未覆盖尾部 + 当前 Assignment 输入；保留 waiting checkpoint，兄弟 Worker 不共享完整 context。

**关闭条件**：H03/H04/H09/H25。同需求明确复用有效、上下文隔离、摘要恢复可靠、旧表约束迁移可验证。

## 11. R9 — 生命周期、当前分配和消息未成为独立契约

**性质**：新目标下 P0 架构缺口，依据当前数据结构和调用路径；不把尚未支持的接口冒称为已运行后崩溃。

**证据**：`domain.py:107-110` 的 AgentExecutor 只有 kind/capabilities；`runtime.py:660-725` 直接执行 Column AgentRun；`schema_repository.py:170-177` 的 session 没有 Worker 实例和工作分配关联。现有 mailbox 主要服务 Project Main，尚未形成 Worker accepted/consumed/acted 消息契约。

**场景**：无法统一追踪 idle 后持续接活、主代理 turn 结束但子任务继续、发给 Worker 的消息是否消费、同会话两个分配竞争与 retire 后迟到结果。

**建议**：AgentInstance / ContextSession / Assignment / AgentRun 分离；定向扩展现有 mailbox；实例单 writer + Assignment generation；一个 Column 一个 leaf，跨代理协作只能在 workflow/Main 层表达。

**关闭条件**：H05–H08/H10/H18/H22。身份不因 run 结束消失，消息不丢，跨代提交不生效，idle 不占资源。

## 12. 状态维护与关闭模板

状态流：已确认 → 方案已 Review → 实施中 → 待回归 → 已关闭。再次复现则重新打开。

每项关闭记录必须包含：实现版本/主要文件、失败用例由红转绿的证据、故障注入位置、历史活跃数据是否迁移或 hold、剩余限制。

九项代码修复记录已归档于 [实施记录](DEVWERK_P0_Runtime_Implementation_2026-09-10.md)：第 2 节逐项关联实现文件与回归；第 3–6 节说明具体契约；第 7 节说明只读备份迁移验证和历史 Task；第 8 节列出取舍及未批准 HTTP 扩展；第 9 节给出最新运行结果及故障注入入口。实现版本为当前工作区，未创建 Git 提交。

R1–R6 仍保留“已复现故障”的证据级别，R7–R9 保留“新目标下源码架构缺口”的来源。前述源码行号属于修复前定位；当前代码通过实施记录中的文件及测试函数定位。方案 H01–H26 的完整发布场景及真实模型交付验收不以本地测试总数代替，尚未执行的项目不标成通过。
