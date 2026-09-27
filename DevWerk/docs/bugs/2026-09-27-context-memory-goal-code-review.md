# 多 Agent、Context 与 Memory 目标：当前代码审阅

日期：2026-09-27

## 1. 结论

**目标已经可以拆成可执行的验证项，但当前代码尚未完整达成目标，不能判定为“已经完成”。**

已经实现的是主干机制：Project Conversation Agent 主持规划，Workflow 调度单 Agent Column，Worker / Assignment / Session 分离，工具结果投影、上下文 checkpoint、独立的通知与 Worker 输入通道，以及受验收回执约束的交付与返修流程。

尚未闭合的是长任务最关键的部分：**恢复后上下文仍然有界、交接知识真正送达、压缩前后约束一致、Memory 按当前工作范围选择且有预算。** 本次在当前工作树上用隔离数据复现了五项缺陷。它们不是 README 用词问题，也不能通过已有测试名称或历史通过率证明已经解决。

“比独立 Agent loop / harness 更精准”是待验证的效果目标。代码可以证明具备哪些控制机制，不能单独证明在相同交付质量下减少了无关上下文、重复工作或总 Token。当前评估入口也没有完成这种对照实验。

## 2. 审阅范围与证据边界

- 对象：`D:\workspace\DevWerk` 当前工作树，包含尚未提交的代码改动；Git HEAD 为 `91a07d4b6b5a4c4ed01c6e4f22dd4a7dd8e3c1b3`。HEAD 本身不代表本次审阅的完整代码。
- 阅读实现、相关测试源代码；**未使用 docs 中的历史测试记录作为完成证据**。
- 新增离线诊断脚本，用临时数据库和临时 Project 运行真实仓储、上下文装配与压缩代码；没有调用外部模型，没有修改生产数据库。
- 本次为 review，没有修改生产源码、README、Web 或运行时指令。
- 下文“复现”指本次重新执行的离线实验；模型 Token 数若来自估算器，会明确标为估算，不能当作 Provider usage。

本次诊断材料：

- [离线复现脚本](D:/workspace/DevWerk/DevWerk/data/evals/review-context-memory-20260927/probe.py)
- [本次机器可读结果](D:/workspace/DevWerk/DevWerk/data/evals/review-context-memory-20260927/result.json)

以上两项位于默认被 Git 忽略的 `data/evals` 目录。后续按用户要求提交当前代码与文档时，仅将这两份合成数据诊断材料显式纳入版本控制；不纳入生产数据库、运行日志或其他本地数据。本文件保存具体条件、观测值和修复验收要求。初次审阅本身没有提交或推送。

## 3. 逐项目标判断

| 目标 | 当前代码判断 | 证据与限制 |
| --- | --- | --- |
| Project Conversation Agent 是主 Agent，无额外 System Main | 已有对应实现 | `assert_conversation` 校验 Project Conversation 身份及运行中的 Turn；规划工具不向 Column 委派。没有要求由更高层模型代理执行这些操作 |
| Column 内单 Agent，不嵌套多 Agent 调度 | 已有结构和能力边界 | `ColumnExecutor` 只有 `agent` 与确定性 `capability_sequence`；叶子 Agent 无主 Agent 规划/Worker 管理能力。单 Agent 可以多轮调用工具，不等于只能发一次模型请求 |
| Worker 有生命周期，能够跨 Assignment 复用 | 身份和调度已实现，知识接续未完整 | Worker key、单 active Assignment、generation/lease、idle/suspended/retired、replacement 均存在；但交接摘要加载存在 F2 |
| 每个 Agent 维持独立上下文 | 会话归属已实现，内容隔离仍有缺口 | Worker 私有 Session、Assignment 历史范围、叶子只读自己的 context 已有；Memory 装配存在 F4 |
| 分步骤限制上下文增长 | 正常工具循环已有机制，恢复路径未完成 | 工具投影、完整调用批次压缩、原始结果回读、输出预留与请求预算已实现；恢复 ledger 与 Memory 存在 F1/F5 |
| 压缩、重启后保留仍有效的工作约束 | 未完成 | 同一 checkpoint 在当前进程和重新加载时产生不同的指令集合，见 F3 |
| Mailbox 只负责通知 | 当前通知入口符合此方向 | 非 user Job 在调用 AgentCore 前进入 `_reduce_notification` 并返回；Worker 输入使用 `v1_worker_inputs`，缺陷返修使用持久化 Task feedback 与 Workflow 转移 |
| 多 Agent 协作并完成交付/返修 | 协议路径和离线验证入口已存在 | 有依赖、验收、反馈、重入与终态证据；这证明执行链条可以检查，不证明真实模型在任意需求上已可靠交付 |
| 比单 Agent loop / harness 更精准 | 尚未建立完整验证 | 需要固定需求、交付质量和模型条件的对照；现有软件评估脚本是交付/返修场景入口，不是精度对照工具 |

主要实现入口：

- [Executor 与 MemorySelector](D:/workspace/DevWerk/DevWerk/app/v1/domain.py:89)
- [Column 能力委派限制](D:/workspace/DevWerk/DevWerk/app/v1/capabilities.py:146)
- [Project Conversation 身份约束](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:174)
- [Worker Assignment](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:359)
- [通知提前分流](D:/workspace/DevWerk/DevWerk/app/v1/conversation.py:300)
- [通知归约器](D:/workspace/DevWerk/DevWerk/app/v1/conversation.py:581)

默认 Worker key 是 `task:{task_id}:{column.key}`；跨 Task 复用需要显式配置 `worker_key`。因此“可以持续使用 Worker”已经有机制，但不能描述成所有需求默认自动复用同一批 Worker。Column 的业务职责是否足够单一，也取决于实际规划质量，Executor 类型检查不能证明语义上已经拆分得恰当。

## 4. 已复现缺陷与最小修复方向

### F1 — P1：恢复执行把原始工具日志重新装入不可压缩的上下文

**触发条件：** 一个 Column 已产生较大的工具结果，随后发生 Provider 重试、Await 继续等需要重建 AgentRun 的情况。

代码链路：

1. [`_column_action_ledger`](D:/workspace/DevWerk/DevWerk/app/v1/runtime.py:752) 读取当前 ColumnRun 的历史 invocation，包含完整结果。
2. [`ledger_entry`](D:/workspace/DevWerk/DevWerk/app/v1/execution_ledger.py:35) 在 `facts.arguments` 和 `facts.result` 中保留完整参数、输出。
3. [`_execute_agent`](D:/workspace/DevWerk/DevWerk/app/v1/runtime.py:714) 把 ledger 放进 `spec.context`。
4. [`build_run_envelope`](D:/workspace/DevWerk/DevWerk/app/v1/agent_prompt.py:40) 把该 context 写进 system；[当前 Assignment 消息](D:/workspace/DevWerk/DevWerk/app/v1/agent_run_preparation.py:123) 又完整写一次。
5. [`ContextManager.prepare`](D:/workspace/DevWerk/DevWerk/app/v1/context_manager.py:191) 投影 `role=tool` 的输出、压缩 assistant/tool 批次，不会压缩上述 system/user 中的 ledger。

**本次复现：** 在临时 ColumnRun 中记录 4 份约 110 KB 的合成命令输出，再调用真实 ledger 重建和 AgentRunPreparer。ledger 序列化长度为 **443,352 字符**；同一日志在初始请求中共出现 **8 次**；可压缩工具批次为 **0**。输入估算 **513,223 tokens**，测试配置输入预算 **105,216 tokens**；强制压缩仍抛出 `ContextExhausted`。这些是本地估算，不是实际模型 usage。

**影响：** 工具消息本身限长不代表恢复请求有界。正常执行时曾压缩的内容，恢复时能够重新整体膨胀；直接重试不能消除这个输入。

**修复方向：**

- 保留 Runtime 内部完整 ledger，供副作用去重、失败关联和 completion 验证使用；不要为节约 Token 删除这些证据。
- 为模型单独生成有总量预算的 ledger 视图：操作 ID、工具名、结果状态、退出码、未解决失败、关键文件/回执引用与有限摘要。完整参数/日志留在证据存储，按引用回读。
- 初次与恢复装配共用同一投影入口；`spec.context` 不再同时完整嵌入 system 与当前 Assignment。
- 在请求预算中同时计入 instruction、schema、当前约束、Memory、ledger、历史，而非只预算工具消息。

**验收：** 带大日志的首次执行、Provider 重试、Await 恢复、进程重启都不把原文重复塞入请求；完整回执仍可检查；恢复不重复执行已完成的副作用。

### F2 — P1：Worker replacement / 显式压缩的交接摘要未进入实际 Assignment 上下文

**触发条件：** Main 用 `agent.worker.replace` 建立继任 Worker，或者对 idle Worker 执行 `agent.context.compact`，随后开始新 Assignment。

[`replace_worker`](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:229) 把交接摘要写入 `v1_agent_context_snapshots`；[`save_context_snapshot`](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:654) 也使用该表。但实际 [Assignment 历史装配](D:/workspace/DevWerk/DevWerk/app/v1/agent_run_preparation.py:207) 调用 `session_history(..., assignment_id=...)`，此分支只查询 `v1_context_checkpoints`，忽略 Session snapshot。

**本次复现：** 通过真实 replacement capability 传入独特的交接内容，继任 Worker 被正确指派。无 Assignment 过滤的 Session 查询能读到交接内容，**实际 PreparedAgentRun.messages 中没有**；`agent.context.read` 的消息页中也没有，因为其查询对象是消息表，交接摘要在 snapshot 表。

**影响：** “Worker 身份接续”与“交接知识接续”脱节。复用同一 Worker 时，自动提供的 prior Assignment 引用也仅有 ID、Task、ColumnRun、状态，不能替代明确保存的工作摘要。

现有 `test_retired_worker_successor_keeps_old_history_and_new_private_context` 源码检查的是不传 Assignment 的查询路径。这解释了为什么这一类测试即使通过，也没有覆盖实际执行入口。

**修复方向：**

- 分离 Session 级交接摘要和 Assignment 内 checkpoint：前者作为带来源的参考输入，后者覆盖当前 Assignment 的已归档历史；不能二选一。
- 新 Assignment 加载适用的 Session 摘要、来源 Worker/Assignment、覆盖边界，仍由当前 Task 合同提供执行权限和验收要求。
- 继任 Worker 的摘要提供可读取入口，不把旧 Worker 的全部 transcript 自动复制进新会话。

**验收：** 在真实 AgentRunPreparer 或注入模型入口断言新 Worker 看得到摘要；新 Assignment 不继承旧 Assignment 的完成状态/授权；跨 Requirement 不泄漏旧摘要。

### F3 — P1：checkpoint 的覆盖边界与实际保存内容不一致，重启后丢失已接收指令

**触发条件：** 历史中穿插多条非工具消息，例如 Main 向 Worker 发送的补充要求；随后工具批次被压缩，再重启或恢复同一 Assignment。

[`prepare`](D:/workspace/DevWerk/DevWerk/app/v1/context_manager.py:218) 仅从当前内存历史删除工具批次；较早的 user/assistant notes 仍保留。checkpoint 的 `through_message_id` 却取被删批次的最大消息 ID，并且最多只保存最后 4 条 notes 的截取内容。随后 [`session_history`](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:605) 把该边界之前的**全部**消息过滤掉。

**本次复现：** 在同一 Assignment 插入 6 条指令/说明消息，再插入一组完整工具调用并强制压缩。第 1 条有效约束仍出现在当前进程的模型输入；从数据库重新加载后消失。当前上下文有 9 条消息，重新加载只有 1 条 checkpoint。原始消息依然可从 archive 读取，**并非数据库删除**。

**影响：** 同一持久化状态在重启前后提供不同工作约束。Main 发出的 steering 不能仅依靠 Worker 恰好主动回读历史才能继续生效。增加总结模型调用也不能直接解决：本路径传给 `_summarize` 的是之前 checkpoint 与被删除的工具批次，并不包含全部保留 notes。

**修复方向：**

- checkpoint 必须准确表达它替代的消息集合。可以保存已归档区间/ID，并保留未覆盖 notes；也可以原子替换连续前缀，但必须把仍有效约束纳入结构化状态。
- 不把“最后 4 条”当作有效约束集合。Worker 输入应有持久化的接收/生效/撤销语义，必要约束不能随摘要预算淘汰。
- 当前内存上下文和重启加载应使用同一投影逻辑，避免一处保留全文、另一处仅恢复摘要。

**验收：** 对相同已持久化状态比较连续运行与重启恢复：有效要求、未解决问题、关键证据引用一致；明确撤销的要求不再生效；多轮压缩后仍成立。

### F4 — P1：Workflow Memory selector 没有绑定当前 Workflow 身份

[`MemorySelector`](D:/workspace/DevWerk/DevWerk/app/v1/domain.py:89) 支持 workflow scope，但没有 scope ID。[`MemoryManager.build_context`](D:/workspace/DevWerk/DevWerk/app/v1/memory.py:412) 仅接收 `task_id`；[`_scope_id`](D:/workspace/DevWerk/DevWerk/app/v1/memory.py:482) 对 workflow/conversation 返回 `None`。底层 `list/search` 在 scope ID 为 None 时不做身份过滤。

**本次复现：** 同 Project 写入 `workflow_A` 与 `workflow_B` 的 MemoryRecord；使用 workflow selector 装配任务输入时，两条都被选中，`omitted=[]`。这不是跨 Project 读取，而是 **Project 内的 Workflow 身份边界缺失**。当前接口甚至不能传入当前 Workflow 来表达这一约束。

**影响：** 不同 Workflow、修订或独立需求的历史结论可能混入当前输入。Project 级共享 Memory 可以是有意设计，但不能把 workflow scope 悄悄当成 Project 内所有 Workflow 的合集。

**修复方向：**

- 由 Runtime 传入当前真实 Workflow ID/修订与 Task ID；定义现有 workflow scope 究竟绑定逻辑 Workflow 还是冻结修订，并处理旧数据兼容。
- selector 默认限定当前实体；跨范围引用必须明确并记录来源，不凭关键词匹配自动扩大作用域。
- conversation scope 也明确绑定规则；这一修复不要求额外创造新的产品权限层级或新增一套 Agent。

**验收：** 两个 Workflow 同名关键词、旧修订与当前修订并存、不同 Requirement 并存时，默认上下文只包含所选范围；有意共享的 Project Memory 仍可正常读取。

### F5 — P1：Memory 输入缺少长度预算，可在首次模型调用前耗尽上下文

[`MemoryManager.build_context`](D:/workspace/DevWerk/DevWerk/app/v1/memory.py:423) 自动带入全部 core 文件全文；selector 的 `limit` 默认 None，且只限制记录数。没有单记录或总内容预算，`manifest.omitted` 始终为空。Column 的 Memory 又随 `spec.context` 重复装配，见 F1。

**本次复现：** 用正常 Memory append 给 `DECISIONS.md` 加入 230,000 字符合成内容。装配结果保留 **230,380 字符**、无任何 omitted 记录，当前请求在首次模型调用前被 ContextManager 拒绝。未执行外部模型请求。

**影响：** 当前硬预算确实能阻止超限请求发送出去，但不能保证任务继续推进。把溢出从 Provider 错误变成 Runtime 阻塞，不等于实现了完整的 Memory 生命周期管理。

**修复方向：**

- 在装配阶段给 Memory 设置总预算，并与 artifact、ledger、工具 schema 共用请求预算。
- 区分必须保留的当前约束、可选参考、历史记录；长记录先提供有限摘录和精确回读引用。
- manifest 记录实际选中/遗漏理由、范围、内容版本和大小；摘要失效后能重建。
- 如果不可再裁剪的有效约束本身超限，应明确报告是哪一类输入阻塞，不能静默丢约束，也不能盲目重试。

**验收：** core 文件持续增长、大量 selector 命中、中文长内容、与长工具历史同时出现时，输入预算可控且必要约束仍保留；完整来源可回读。

## 5. 本次新执行验证

复现命令（服务目录下执行）：

```powershell
.\venv\Scripts\python.exe -X utf8 data/evals/review-context-memory-20260927/probe.py
```

该脚本验证的不是修复已经通过，而是上述缺陷在当前代码上确实可达：

| 实验 | 观测结果 |
| --- | --- |
| Worker replacement | 继任身份正确；实际输入没有交接摘要 |
| Workflow Memory 范围 | A、B 两个 Workflow 的记录同时被选中 |
| 大 core Memory | 全文装配，无 omitted，首次请求被本地预算拒绝 |
| 大日志恢复 ledger | 4 条回执生成 8 份原文；强制压缩仍失败 |
| checkpoint 重载 | 连续运行保留的有效指令在重载后消失；原文仍归档 |

另外新执行已有测试源代码，结果为 **25 passed in 88.80s**。覆盖 Worker 生命周期/互斥与输入、叶子能力限制、mailbox 无模型通知、两轮跨 Column 返修、Memory 基础读写、工具投影、上下文超限重试不重复副作用，以及 36 轮大工具输出压缩。均使用隔离 fixture 和注入模型；未调用外部 Provider。

```powershell
.\venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_persistent_agents.py tests/test_agent_recovery_and_fencing.py::test_leaf_cannot_call_main_planning_even_if_capability_is_injected tests/test_conversation_contract.py::test_terminal_mailbox_reports_durable_state_without_model tests/test_repair_notification_contract.py tests/test_memory_workcell_contract.py tests/test_context_lifecycle.py::test_overflow_retry_shrinks_context_without_reexecuting_effect tests/test_context_lifecycle.py::test_projection_retains_error_and_cache_usage_semantics tests/test_context_lifecycle.py::test_large_command_loop_compacts_and_keeps_raw_evidence
```

**通过这些测试与发现上述缺陷并不矛盾。** 它们验证的是各自已覆盖路径：例如正常长工具循环不等于携带完整历史 ledger 的恢复请求，Session 查询不等于 Assignment 实际输入，Memory 可搜索不等于其 scope ID 已绑定。不能用这 25 项通过结果覆盖五项反例，也不据此声称完成真实模型端到端交付。

## 6. 如何判定目标“可验证”与“完成”

建议分两层验收，不新增 Web 调试显示，也不扩展用户产品需求。

### 第一层：框架不变量——现在就可以离线验证

| 检查项 | 明确通过条件 |
| --- | --- |
| 单 Column 单 Agent | Executor 结构合法；叶子实际调用主 Agent 规划/Worker 调度工具被拒绝 |
| Worker 生命周期 | 同一 Worker 只持有一个 active Assignment；过期 generation 不能写入；idle 可复用、retired 不自动复活 |
| 真正的知识接续 | 新 Assignment / successor 的实际模型输入包含适用交接知识，且不继承旧任务授权 |
| 通知隔离 | mailbox Job 不调用模型、不创建 Task、不消费 Worker 输入；业务反馈走声明的 Workflow |
| 请求有界 | 首次、长工具循环、Await、Provider 重试、重启、Memory 增长均覆盖，完整证据留在上下文之外 |
| 恢复一致性 | 重启前后未撤销约束、待解决问题及证据引用一致，不重新执行已完成副作用 |
| 范围隔离 | Memory / Session / artifact 来源范围可核对，不将无关 Workflow 记忆默默带入当前输入 |
| 交付闭环 | 完成必须匹配当前合同与有效证据；失败反馈能回到责任 Column，修复后重新验收 |

先修复 F1/F3（长任务恢复），再修复 F2（知识交接），随后 F4/F5（Memory 作用域与预算）。实现时应先完成对应详细设计，再小范围改动，最后执行每项针对性回归。

### 第二层：协作是否更精准——需要新的对照评估

[`eval_software_context.py`](D:/workspace/DevWerk/DevWerk/scripts/eval_software_context.py:1) 提供软件讨论、交付和用户返修入口；没有单 Agent 对照模式，也没有按任务相关性计算上下文污染或交接损失。不能据此宣称优于单 Agent loop / harness。

需要固定需求、模型、工具、起始文件、验收标准和资源边界，在以下两种执行方式下重复运行：

1. 单 Agent 长会话完成相同任务。
2. DevWerk 由 Conversation Agent 规划、单 Agent Columns 协作完成任务。

比较前先保证交付质量相同，再记录：

- 独立验收通过率、返修成功率、有效约束遗漏数。
- 每次请求实际输入 Token、最大上下文、重复工具内容占比。
- 错误范围的 Memory 命中数、无关上下文比例、交接信息遗漏数。
- 重复读取/重复命令次数、因上下文缺失产生的返工次数。
- 总模型 Token，包含规划、压缩、回读、审查和返修，不能只统计 Worker 正常执行。
- 重启、Provider 失败、长日志、需求扩展后是否仍能恢复并交付。

软件开发和连续写作可以作为不同类型的验证任务。评价重点是正确拆分、有效约束保留与可恢复交付；不能把“每个 Agent 的窗口较小”直接等同于“总体更精准”。本次没有执行外部模型对照，也不对其结果作推断。

## 7. 当前完成标准

当前可准确表述为：**多 Agent 工作流与上下文管理主干已经落地，具备进行机制验证的代码入口；长任务恢复、知识接续和 Memory 选择尚有已复现缺陷，暂不满足目标完成标准。**

修复五项缺陷并通过第一层检查，可以判定所需机制达到验收要求；第二层对照得到足够证据后，才适合对“比独立 Agent loop / harness 更精准”作效果结论。
