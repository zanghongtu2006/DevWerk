# 两轮软件交付/返修与无效 Token 消耗修复设计

日期：2026-09-21。状态：代码修复完成，最终全量回归 370 项通过，真实模型两轮故障注入返修验证通过；尚未重跑原 Vue/Spring 项目的完整产品验收，未部署或重启生产服务。本文替代“首 Column 成功即可认为软件交付闭环通过”的不足口径；不覆盖或删除前次实际测试记录。实施细节与实际结果见 [实施记录](DEVWERK_Repair_Evidence_Token_Implementation_2026-09-21.md)。

## 1. 范围和已核实证据

输入是 09-16 human-team 软件交付记录与 09-20 completed-task repair 记录，项目 `prj_df4616276dab4235ae178242fcc7fdc6`。本次只读核对生产 `data/devwerk.db`，不启动旧队列、不修改该项目历史。源码、日志和数据库是事实依据；报告中的产品缺陷与框架故障分开处理。

SQLite 当前合计：Conversation 183 次 / 1,823,413 recorded tokens，Column 120 次 / 802,102 tokens；合计 303 次 / 2,625,515 tokens。与两报告相加一致。缓存输入单列，不混为价格或重复加入 total。报告两阶段 Mailbox 合计 83 次 / 746,291 tokens，是确定性通知本可避免的历史开销基线，不将此反事实直接当作新版本实测节省。

运行中的 Mailbox Job `cjob_d0a751e2704b4822befec41d961f8e5a` 尚未回填 agent_run_id，但关联 Run `arun_91273c644de24b97a5ca1ceedff21900` 有持久工具回执：retry、Worker lifecycle、Workflow publish、cancel、rerun 等确实成功。用户 revision-4 请求仍 queued。因此不能只读 Job 的最终统计字段判断无调用。

返修 frontend 的同一 Assignment `assignment_573af161a81640438d03c2e501ec8e3d` 多次以 1 次模型调用、0 次工具调用结束。代码在任何旧 Session 历史存在时就删除新的完整输入附件，同时重放旧 Assignment 原始 assistant 完成轨迹，放大了“以前已完成”的错误理解。

## 2. 问题分层

| 编号 | 已确认问题 | 修复位置与不变量 |
| --- | --- | --- |
| R0 | Mailbox 被当作新的执行授权，抢占同项目用户请求 | Gateway 确定性通知 reducer；事件不授予写入、规划、调度权限；用户排队优先 |
| R1 | 新 Assignment 继承旧完成叙述并缺少完整新输入 | 同 Worker/Session 保留归档；当前 Assignment 回放与跨 Assignment 参考分离 |
| R2 | 缺陷只能发给存活 Assignment，没有持久返修对象 | Task 级持久缺陷、责任 Column、复验义务；已结束 Worker 可再次受派 |
| R3 | 缺失证据或未解决缺陷仍能 done | 持久验收义务、Runtime 收据、终态事务检查；Markdown 声明不是验证收据 |
| R4 | terminal Task 对应错误冻结 revision，rerun 仍复制错误版本 | 显式 successor 选择已校验的新 TaskPlan，保留前驱，禁止隐式迁移原 Task |
| R5 | Job 正常结束被当作业务操作成功 | report blocked 对应明确未完成的 Job/result 状态，保留部分成功效果 |
| R6 | 反复抄写宿主已有 ID/revision/evidence，引发协议轮次 | 宿主绑定当前消息/版本默认值，保留显式冲突拒绝；不能据此猜测执行意图 |
| R7 | 每回合重复全计划、全 Workflow、旧原始工具轨迹 | 有界当前状态投影、近期可见对话、按需历史/详情读取；保留真实持久来源 |
| R8 | 命令路径校验拒绝被误记为副作用结果未知 | 确定性命令预检放在效果收据开始之前，执行后未知结果仍保守处理 |

Origin 未实现、浏览器用例未覆盖、Chromium 未安装属于交付物/环境缺陷；Runtime 不靠自然语言识别“Origin”或“E2E”来修正代码。框架修复目标是让已接受的行为形成不可静默省略的执行义务、缺陷进入实际返修、复测证据决定终态。Loop 提供软件测试职责和模板，具体命令由项目确定。

## 3. 通知与调度

Mailbox 不是第二个主 Agent。项目 Conversation Agent 继续是唯一主 Agent，自动 Workflow transitions 由 Runtime 执行。

### 3.1 用户补充的明确边界（09-21）

Mailbox 是被动通知机制，不是 Agent，也不是 Agent 间的通信代理。小说 review、代码测试报告、其它交付反馈只作为原始通知内容和来源引用保存、展示；Mailbox 不解释反馈、不组织对话、不选择接收 Worker、不生成下一步指令。通知 acknowledged 仅证明投递完成，不表示缺陷修复、测试通过或失败 Job 已解决。

原实现中 `agent.worker.send` 将 steering 写入 `v1_project_mailbox`，混淆了通知与执行输入。本次拆分为独立 `v1_worker_inputs`：由有权限的用户回合显式提交，或 Workflow 已定义的交接提供输入；绑定 Worker/Assignment，保留幂等、消费回执和生命周期 fencing。不会因输入入队而启动 Worker，不允许 Column 内创建多 Agent loop。正常 Column 间的报告传递继续使用 Workflow artifact/context contract，不经 Mailbox 中转。

启动迁移原拟复制旧消息并将旧 pending/delivered/received 标记失败，自动审批因批量改写历史状态拒绝了该部分。本次改用只新增表的方案：旧 Mailbox 行不修改、不自动消费、不重新投递 Worker 指令，保留查询审计。若部署前存在未消费旧输入，需要另行审查后显式转入新通道；不能将其当作已消费。此项不阻塞独立新通道、只读核查和隔离测试。

通知结果保留事件 ID、Task/Column/Assignment 来源及完整持久 payload 引用；界面摘要可限长，不能将报告内容丢弃。普通完成通知不必刷屏，但必须可查询。已结束 Assignment 的返修属于 Task 持久缺陷与新 Assignment 调度，不通过给旧 Worker 发送 Mailbox 消息绕过生命周期。

普通完成、失败、阻塞及计划复查事件统一先做确定性归并：读取已持久 Task/Column 状态，生成必要的简短用户通知，完成 mailbox received→acknowledged 和 Job 结算，不调用模型、不发布 Workflow、不 retry/cancel/rerun。失败消息保留原错误与待处理事项；不把失败自动解释为用户授权的新修复范围。Runtime 自身已有的有界恢复和图内返修继续执行。

统一 Registry 对 event/scheduled turn 的业务 mutation 拒绝；不能仅依赖 Gateway 不调用模型。用户 Job 优先于尚未开始的通知 Job；旧运行事件恢复后按新通知路径结算，不重新跑旧规划循环。所有消费者保持相同 lease/fencing/ack 原子规则。事件投递本身不制造递归通知。

验收：同样 8 个 Column 完成和终态消息产生 0 次模型调用；失败通知亦不修改图；重复投递不重复通知；用户请求不等待模型化 Mailbox 循环。

## 4. Worker 生命周期与上下文

Worker 身份、Session 归档、历史源码来源继续持久。新 Assignment 不清空 Worker，也不把上一 Assignment 的原始 assistant/tool completion 当作正在继续的对话。

当前 Assignment：重放自身完整成对工具轨迹，支持 await/retry；跨 Assignment：提供有界、带 Task/Column/Assignment 来源的历史摘要和检索指针。通过 agent.context.read 按需查原文。当前任务/列/版本/Assignment 放在独立权威输入中；只有同 Assignment 已有 Run 时才使用输入增量投影。新 Task 或有向返修第一次进入时提供完整选定上下文和持久缺陷。

验收包括新 repair Task 复用同 Worker/Session但不接受旧完成、同 Assignment 恢复仍保留工具回执、review→implementation→QA 至少两个实际循环。

## 5. 持久返修与验收义务

引入 Task 级 defect/handoff：稳定 ID、来源（用户 Job 或当前 Column Run）、责任 Column、说明、required 标记、复验 check keys、创建时序、修复/验证记录。写入使用幂等 key，不能仅给旧 Assignment 发消息。

用户反馈和后续 Column 的反馈都能建立缺陷。活跃任务的当前执行需通过现有 lease/generation fencing 安全交接；不能在旧 Worker 仍可写时直接跳列。返修沿已声明有向图到责任 Column；缺陷和复验要求随 Task 输入提供。已完成 Task 保持不可变，建立新 successor 承接旧缺陷及新计划。

required defect 不能靠模型提交 resolved=true 关闭：必须有缺陷之后、责任实现之后的成功冻结复验收据。返回实现会使下游验收证据失效；到达 done 必须在同一提交事务检查没有未解决 required defect，且所有必需验收义务都有当前有效 Runtime 收据。跨 Task、跨 revision 或旧 generation 收据不能满足当前义务。

Workflow/TaskPlan 要把可执行验收清单持久为结构化对象，绑定责任 Column 与 check key。初始软件 Loop 可以是模板，创建实际软件 Task 前必须实例化实施、QA、delivery、accept 的非空验证契约。项目接受的 E2E/运行探针义务不得因改 revision 被静默移除；允许显式用户修改需求，不允许 agent 用报告替代 required 执行。

框架无法从任意 shell 命令数学证明测试覆盖正确。测试清单与需求来源、执行收据、缺陷状态需要分别呈现；`--list`、源码查找或读取“通过”文档不能声明为行为执行证据。软件 Loop 要用可执行集成/浏览器及启动/停止检查验证交付，而不是把这些落在 prompt 里。

## 6. 冻结 revision 的修复后继

`task.rerun` 保留原 revision 的原语语义，描述必须清晰。新增显式 successor 路径接收 predecessor + 新 TaskPlan/ref；前驱必须 terminal，新计划经过统一编译/入口/范围检查；一次原子物化并记录来源。原 Task、原 Column 回执和原 Workflow 不改写；禁止将 active Workflow revision 偷换到旧 Task。任务工作目录复用现有 Project，Worker 记忆仍按第 4 节隔离 Assignment。

## 7. 协议与 Token 控制

turn.resolve 仍由同一个 Agent 判断 discuss/execute/control，不新增分类 Agent、不使用关键词规则。宿主默认补入当前不可变 user_message_id、当前 intent revision 和当前用户来源引用。显式提供不同 ID/revision/quote 仍校验，不把权限错误自动修正成授权。

工作结果的 tool receipt 引用由宿主提供可用 ID；普通讨论不重复交付状态。blocked 表示业务阻塞，成功提交一个报告不代表报告中的业务已成功。展示字段区分对话处理结束与操作结果。

Conversation 当前已经排除了旧原始工具回执，但仍重复注入全量计划和可见对话；Worker 存在跨 Assignment 原始轨迹回放。这两类分别处理。状态提供 ID、revision、状态、简短目标、未解决事项和可检索入口；完整 Workflow/计划/旧对话按需读取。保持当前消息、未解除用户约束和持久 WorkIntent 不被摘要覆盖。记录投影前后字符量、模型调用数、失败工具数；不以字符估算冒充计费 token。

## 8. 实施次序与验证

1. 先落地本设计；修复通知 reducer/权限、用户优先、Assignment 投影、宿主协议字段和命令预检；以已有事故数据在隔离 DB 中重放验证零副作用与调用预算。
2. 实施 Task 持久缺陷、责任路由、冻结证据义务和终态闸门，补充 corrected-revision successor；更新软件 Loop 和工具说明。
3. 运行新增测试及完整 tests，按当前测试契约无 skip/xfail。至少两个真实文件改变→失败检查→有向返修→重跑检查链，记录 Task/Column/Worker/Session/Assignment/收据来源。
4. 真实模型验证在隔离项目中进行；不复活被停止的生产队列。按新测试契约记录实施构建、测试、浏览器及运行证据；无法完成的项目不得标通过。
5. 对比模型调用/recorded tokens/cache inputs、Mailbox 计数、协议拒绝率与上下文大小。确定性 reducer 的 0 LLM 调用可以单独证明；整体 token 节省必须来自实际新运行，不能用历史比例推算后声称已省下。

## 9. 发布边界

本轮不手改原项目交付物或历史任务状态，不默认部署/重启生产。修复后的生产历史恢复需在明确操作时检查 queued user Job 与旧 active event Run。文档记录代码、测试和未完成项；不能用“第一阶段已完成”掩盖缺陷返修/完整交付仍未验证。

## 10. 落地协议补充

- `task.feedback.record`：`task_id`、幂等 `dedupe_key`、`responsible_column`、`description`、非空 `checks[{column,check_key}]`。反馈只能引用本 Task 冻结检查。所有此类反馈都是必须解决的缺陷；一般评论走交付物或通知，不创建可忽略的阻塞记录。
- `v1_task_feedback` 的 open→verifying→resolved 由 Runtime 执行；没有面向模型的 resolved=true 接口。当前列提交完成时，在同一个事务中决定沿既有边继续、返修或复验；不在活跃 Worker 写文件时直接改列。新 Assignment 读取持久反馈。
- `v1_acceptance_results` 保存检查的 Task、Workflow revision、Column Run、Attempt、执行键、结果和 Agent Run。`workflow.acceptance_obligations` 引用 column/check_key，并通过 `invalidated_by` 指定哪些列的再次执行使旧收据失效。
- 软件 Loop 1.3.0 的 `task_contract.required_behavior_columns` 包括 backend_development、frontend_development、system_test、delivery、accept。每列必须至少有一个 `evidence_kind=behavior`、非空 purpose 的命令检查及终态义务。实现列检查由本列重新执行失效；下游 QA/交付/验收由任一实现列重新执行失效。Loop 模板可以先建立，未实例化检查不能保存可执行 TaskPlan。
- `TaskPlan.repair_of_task_id` 明确同一逻辑任务的终态前驱；`task.successor(task_id,task_plan_id)` 指定修正计划。后继继承未解决反馈，不修改前驱；不能删除前驱验收义务或缩小失效范围。`task.rerun` 继续明确表示原计划原 revision 重跑。
- corrected successor 复用完整计划物化与入口证据校验，依赖图一次提交；重复请求必须匹配同一前驱才可复用。软件 Loop 的 `feedback_columns` 要求 review/QA/delivery/accept 声明持久反馈工具；当前列收到冻结检查目录，解决“通用机制存在但实际 Worker 无法调用”的集成缺口。
- 真实模型首轮暴露：Worker 自行尝试 pytest，而冻结测试使用 unittest；前者失败被错误地当作必须修复的交付义务，造成多轮无效操作。因此实现列也要求冻结验收，避免仅在最后 QA 有验证契约。检查通过只能解除探索失败，不能替代仍失败的冻结验收。
- 模型显式引用已经由相同操作重试解除的真实失败，仍按“失败存在、后续成功、同 capability/effect/operation hash”校验；不再因为它已不在 unresolved 集合而拒绝。虚构引用、异操作和先于失败的成功仍拒绝。
