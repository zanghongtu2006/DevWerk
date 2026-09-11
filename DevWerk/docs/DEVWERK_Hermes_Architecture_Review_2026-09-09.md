# Hermes 源码借鉴与 DevWerk 架构差距 — 2026-09-09

状态：源码审阅结论，配套 [修订后的 P0 修改方案](DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09.md)。未修改 Hermes 或 DevWerk 生产代码。

## 1. 本次依据与限制

本地参考仓库：`D:/workspace/github/hermes-agent`，读取时 HEAD 为 `38b7d0f4cf8a11137d8da5e3d95d7b5b7e41fb46`，git status 无输出。没有从网络补充其他版本，也没有把 Hermes 的测试报告当作运行保证。

主要阅读：`agent/conversation_loop.py`、`agent/subagent_lifecycle.py`、`tools/delegate_tool.py`、`tools/async_delegation.py`、`agent/delegation_context.py`、`agent/context_engine.py`，以及 DevWerk 对应的 runtime、conversation、session、schema 和 capability 路径。

本地 `plugins/kanban` 只有 dashboard/systemd 等文件，没有找到预期的 worker.py/dispatcher.py 实现。因此不依据仓库指南的 Kanban 描述声称已经验证 Hermes 的完整 Kanban worker 架构。

## 2. 用户目标作为此次修订基准

- Conversation Agent 是持续协调需求的真实主 Agent：理解、规划、派发、读取结果、修订计划和交付。
- Kanban/workflow 控制工作单元、依赖、状态和验收，执行者是独立 sub-agent。
- Sub-agent 的逻辑身份和自己的 context 可贯穿一个需求中的多个调度回合，不要求每个 Column 都销毁重建。
- 一个 Column 只处理一个明确职责，由一个叶子 sub-agent 执行；单 Agent 多次模型/工具迭代允许，Column 内多 Agent 协商循环不允许。
- 重构目标是可交付与上下文隔离，不以增加安全审批、成本配额或复杂框架为目标。

## 3. 从源码确认的 Hermes 机制

| 机制 | 源码定位 | 确认的行为 | DevWerk 借鉴 |
|---|---|---|---|
| 主 Agent conversation loop | `agent/conversation_loop.py:1899,1980-2009` | 接收历史、当前输入和上下文，运行同一个 Agent 核心 | 主/子使用同一 AgentCore，差别在角色、工具和上下文 |
| 独立子 Agent | `tools/delegate_tool.py:1702,2071-2096` | 创建新的 AIAgent，独立 child prompt、session DB handle 与 parent_session_id | 子代理不是主代理 prompt 的一个临时角色 |
| 聚焦子任务输入 | `tools/delegate_tool.py:1230-1265` | child prompt 以 goal、显式 context、workspace 为中心 | 通过 Assignment 输入包选择上下文，不复制主代理全历史 |
| 递归深度控制 | `tools/delegate_tool.py:1738-1747` | 实际权限由 depth 和配置派生；legacy role 文本不是最终依据 | DevWerk v1 固定 main → leaf 一层，不开放孙代理 |
| 运行中 steering | `tools/delegate_tool.py:347-368` | 给正在运行的 child 排队消息，queued 不代表已消费；结束边界保留 missed_steer | 消息要有接收/消费事实，不能把 send 成功等同于完成 |
| 生命周期契约 | `agent/subagent_lifecycle.py:38-132,198-347` | typed handle、status、wait、cancel、result、reconnect | 用统一服务管理生命周期，不由业务操作私自管理线程 |
| 生命周期的边界 | `agent/subagent_lifecycle.py:150-192,340-347,392` | live registry 是进程内对象；reconnect 不会在重启后重建消失的执行；terminal 有保留期 | 只借鉴接口边界，不照搬其进程内持久性 |
| 子执行结束清理 | `tools/delegate_tool.py:3539-3558` | 移出 active child 并 close 资源 | Python 对象/资源寿命不等于逻辑 Agent/context 寿命 |
| 异步结果回到主 Agent | `tools/async_delegation.py:5-29` | 结果以独立后续 turn 输入，携带原目标及路由信息 | 主 Agent 能在 child 工作期间继续响应用户；结果事件再唤醒主 Agent |
| 持久化 dispatch/result | `tools/async_delegation.py:142-175,343-397` | SQLite 保存异步派发及结果；原进程消失的未结算执行标为 unknown | 重启后保留责任归属，不能把“线程没了”解释为安全重跑 |
| 结果补投与领取 | `tools/async_delegation.py:400,467-484,556-568` | 恢复未投递结果；claim/ack 条件更新 | DevWerk queued 和子代理结果都需要 durable 再发现 |
| 上下文选择与存储分离 | `agent/context_engine.py:215-246,281-303` | request selection 不覆盖持久化 transcript；turn observation 是独立接口且有异常边界限制 | 保留历史事实，单次 prompt 只选必要内容；不要把 best-effort hook 当恢复基础 |
| 父子身份隔离 | `agent/delegation_context.py:1-19,54-73` | ContextVar 隔离 child session，避免继承 dispatcher 身份 | 业务 owner 显式传递，不能靠全局环境或共享可变对象冒充父代理 |

说明：Hermes 部分注释仍描述 role 决定递归能力，但实际构造分支使用 depth/config；本分析以执行代码为准。它的生命周期服务和异步 delegation ledger 是不同层，不能合称“子代理全部支持跨进程续聊”。

## 4. 不直接照搬的部分

1. 不复制临时 child 的“执行结束即 close”作为 DevWerk 逻辑身份结束条件。
2. 不采用 process-local registry 作为持久化 Agent 真相。
3. 不开放 Hermes 可配置的 orchestrator → child → grandchild 树；DevWerk 的分工应体现在 workflow。
4. 不把历史消息都直接追加到下一次 prompt；长期 context 仍需分层和摘要。
5. 不照搬运行中修改最后一条 tool result 的具体 steering 实现。DevWerk 用持久化收件箱在合法消息边界注入，原调用事实保持不变。
6. 不复制 Hermes 的整个插件、网关、预算或模型路由体系；复用 DevWerk 现有 AgentCore/SQLite/事件。
7. 不假定 Hermes 可保证任意 shell exactly-once；它对失联结果也显式保留 unknown。

## 5. DevWerk 当前已存在的基础

不能把当前 Conversation 描述为纯聊天壳：

- `app/v1/conversation.py:334-348` 已以持久 logical_id 调用同一 AgentCore，并加载 conversation session。
- `app/v1/agent_prompt.py:41-69` 已有相对稳定的 Conversation system envelope。
- `app/v1/runtime.py:672-705` 已支持可选 agent_session_key。
- `app/v1/store.py:2218-2246` 已有逻辑 session 创建/恢复，`2341-2352` 有 suspended 状态。
- 已有 Task owner、Await、mailbox、receipt、workflow revision、artifact，可以增量改造。

不足在于这些组件目前尚未形成用户要求的“主代理持续规划 + 可持续子代理 + 独立工作分配”契约。

## 6. 新目标下的架构缺口

### R7 — 主代理由事件驱动时无法自主继续规划

`conversation.py:300-324` 仅用户回合允许 task graph mutation；`capabilities.py:1883-1931` 的真实 guard 拒绝非用户回合的 loop.apply、plan.save、workflow.publish、task.create。

因此“已有需求的 child 回报需要新修复 Task，主代理收到结果后继续拆分”被架构直接禁止。不是主代理不存在，而是能力被 user-turn 条件截断。

这是按此次明确目标识别的 P0 架构冲突；不冒称已对全部真实项目重放。应改成主代理可在当前有效需求范围内规划，事件来源不改变它的角色，leaf 不获得此权限。

### R8 — Task 内摘要续接不足以作为持续子代理 context

`store.py:2218-2223` 以 project + task + session_key 查 session；相同 session_key 跨 Task 得到不同身份。`store.py:2307-2338` 只选最新成功/最新结束 run 的 final_text/error，并排除 waiting；`agent_run_preparation.py:175-185` 把它们包成一个 prior history 用户消息。

所以“开发子代理完成 T1，之后在 T3 根据评审继续使用自己的决策、工作记录和待处理消息”目前没有一等支持。摘要可用于压缩，但没有覆盖范围、来源游标和 context revision 的最终文本摘录，不能替代可恢复的会话。

### R9 — 缺少与 Task/AgentRun 分离的分配与通信生命周期

当前 AgentExecutor 只有 kind/capabilities（`domain.py:107-110`）；Runtime 直接执行 Column 对应 AgentRun。session 没有独立的 Assignment 绑定、实例级单写者、收件/消费状态、retire/reopen 语义。

因此无法统一表达：这个 sub-agent 当前空闲但仍存活；同一 sub-agent 收到后续工作；发出去的消息尚未被消费；主代理 turn 结束不意味着 child 工作取消。

这是相对于新目标的缺失契约，不是另一次独立崩溃复现。实施需先增加实例/会话/分配/消息边界，再把原 R1–R6 套在正确范围上。

## 7. 原六项的重新定位

| Bug | 结论 | 修订 |
|---|---|---|
| R1 | 原复现仍成立 | 身份归属于具体 Assignment 的持久操作，不能属于 Agent 生命周期内的参数 hash |
| R2 | 原无关命令豁免仍错误 | 限制完成豁免，不禁用 Agent 的正常尝试与改命令；通过当前工作验收事实决定是否完成 |
| R3 | 原队列丢项仍成立 | queued 用户消息、主代理事件、子代理消息、结果通知都要可补投 |
| R4 | 原过期控制仍成立 | 同时校验 Agent turn generation、Assignment generation 和目标版本 |
| R5 | 原 Await 事实脱节仍成立 | 子代理可等待/让出/继续；外部结果与持久 context checkpoint 分别提交清楚 |
| R6 | 原无限 re-await 仍成立 | 限制工作分配和无进展循环，不限制一个逻辑子代理能存在多久 |

第 R7–R9 为新目标下源码可确认的架构缺口；R1–R6 保留原独立复现级证据，两类不能混淆。

## 8. 方案修订结论

推荐：持久主代理 + 需求范围内可复用的叶子子代理；workflow 为调度与验收状态机；Column 对应一次单职责 Assignment。运行线程可以释放，Agent/context 保留。多 Agent 协作在 workflow 和主代理层表达，不能藏进 Column 内部。

具体数据结构、消息状态、工作分配、P0 修复、迁移与验收见当前修改方案。上一版保存在 [Hermes 阅读前方案](DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09_pre_Hermes.md)，不再作为实施主文档。

