# Project Conversation Agent 重构：Hermes 级通用核心

状态：`feature/hermes-conversation-core` 的实施方案。基线提交 `f521053`。测试 Project `prj_77a6db3886434045b32fd9f0fb26b197` 在第二轮讨论后应用户要求主动暂停，不能作为新架构交付证明。遵循先方案、后代码、再回归；不直接复制 Hermes 的产品提示词或平台集成。

## 目标与边界

每个 Project 有一个持续存在的 Conversation Agent。它是用户直接对话的**通用主 Agent**：观察项目、操作文件和命令、使用内建及 MCP 工具、主动管理记忆、讨论与规划、创建/修订 Workflow、派生并监督 Column Worker、检查反馈、在授权范围内恢复失败、报告真实交付。没有更高的 System Main。现有 `v1_agent_instances.role='main'` 只是旧存储标签，不能形成另一权限层级。

Workflow 是主 Agent 组织长期工作的持久结构，不是主 Agent 的替身。一个 Column 一名职责明确的 Worker；Worker 有独立 Session、分配、暂停/恢复/替换生命周期。跨列协作经工作流产物和显式 Worker input，Mailbox 只通知事实和交付结果索引，不能当成模型间聊天、Worker 指令或执行授权。

“Hermes 级”指核心范式：持续工具调用循环、持久 Session/恢复、动态工具与 MCP、主动读写记忆、上下文压缩、独立子 Agent、委派摘要与生命周期、用户中途修正、有界错误恢复。不是要求复制 Hermes 的 Telegram/Discord 等全部平台适配器。DevWerk 必须用自己的 Kanban/Requirement/Column 管理长任务及隔离上下文。

## 代码依据与失败根因

Hermes `agent/conversation_loop.py::run_conversation` 是同一 Agent 的多轮工具循环；`agent/turn_context.py::build_turn_context` 恢复 Session、记忆和上下文；`model_tools.py` + `tools/registry.py` 统一工具；`tools/mcp_tool.py` 把 MCP server 工具注册为普通工具；`agent/memory_manager.py` 支持可替换的记忆提供者；`tools/delegate_tool.py` 为子 Agent 建独立会话并只向父 Agent返回摘要；`agent/subagent_lifecycle.py` 提供句柄、状态、等待、取消。Hermes 默认子 Agent 可短命，DevWerk 仍须保留跨 Assignment 可续用的 Worker 身份和上下文。

DevWerk 已有 `app/v1/agent_runner.py` 工具循环、`capabilities.py::CapabilityRegistry`、`memory.py`、`context_manager.py`、Agent Session/Assignment 和 Kanban Runtime，值得保留。但 Conversation 路径每轮强制 `conversation.turn.resolve`，要求模型同步 `decision_updates/open_question_updates`、revision、hold、proposal；未 resolve 时 Provider 只暴露一个工具，`open_questions[*].state=resolved` 还是 `execute` 的硬门槛。第四轮黑盒失败就是模型把已决议却未建立的 question key 标记 resolved，被 `NewQuestionMissing` 拒绝后原样重试。基线上的幂等补丁止住一次错误，未解决维护细粒度状态账本的结构性成本。

现有 `conversation.py::_reduce_notification` 主要确定性播报，主 Agent 没有长期读取反馈并主动监督已授权交付。CapabilityRegistry 主要是静态内建工具，尚无 MCP 发现和动态工具。Memory 和 Session 有基础，但要验证生命周期、检索、压缩和隔离。

## 新核心合同

### 自由会话与授权

每轮用同一 Project Agent Session 的历史、当前用户消息、Project/Task 快照及工具启动通用循环。模型可直接读状态、文件、记忆和工具说明并回复；**讨论不必先写 `resolved` 或分类账本**。工具回执是真实副作用来源，模型文本/工作笔记不代表 Task 创建成功。

Runtime 仅持久化必须验证的边界：当前用户是否明确请求开始/继续/扩展，授权 Requirement 与 revision，用户明确设置的暂停约束，及 Tool Run 来源。把 `conversation.turn.resolve` 拆成仅在请求执行/控制时用的窄授权工具（暂称 `conversation.execution.authorize`），只接受当轮用户消息 ID、动作、目标范围/引用；不接受 question、decision、proposal 的重复搬运。纯讨论、状态查询和工作笔记无需授权工具。Web `mode=discuss` 仍是硬上界；旧 Project 数据读取兼容。助理历史承诺或 mailbox 通知绝不产生新授权。

`open_questions/resolved/decision_updates` 可作为可选工作笔记，或迁移到 `project.memory.write`、Requirements 基线，不再是执行准入。Agent 自己提出的问题不能凭 `blocking_for_start` 锁死 Project；用户明确授权后由 Agent 提问、采用委托选择或在交付中标注假设。真正 Task 输入和验收约束仍由现有 Workflow/TaskPlan admission 校验。

### 工具与 MCP

保留 CapabilityRegistry 为统一 schema、dispatch 和审计入口，支持动态注册/撤销。内建文件/命令、Workflow/Task、Memory、Worker input 和 MCP 都产生同形 ToolResult 与 Run ledger。MCP 按 Project/全局配置 server，至少支持 stdio 和 Streamable HTTP，具备发现、schema、调用、超时、断线恢复、命名空间和输出限长。未知副作用的 MCP 工具按保守类别处理；讨论态不得被未知工具偷偷写业务状态。MCP 内容是数据，不得替代用户授权。工具集只在回合边界刷新，持久化版本便于重放。

### Session、Memory、Context

Project Agent Session 跨用户回合持续；Agent Run 只是一次执行尝试。新回合恢复已提交消息/工具结果，正确处理未完成工具调用，接受中途修正。上下文用近期轮次、压缩摘要、当前项目状态与按需检索的长记忆组成；工具输出先限长/结构化，再在预算阈值压缩。压缩须保留最新用户请求、授权、未完成调用和确定性失败反馈。Memory 可主动 search/read/write，带 Project/Conversation/Workflow/Task 范围和来源。子 Agent Session/Memory 按 Worker/Assignment 隔离。

### 委派与监督

主 Agent 发布 Workflow 和 TaskPlan；Runtime 给每 Column 分配一名 Worker。Worker 有独立 Session、工具集、预算和完成合同；父 Agent 只见控制面摘要/产物引用，不吞下全部子代理日志。Worker Run 结束可保留身份以续用、交互或替换。用户修正经主 Agent Session，授权范围内的变更经 Workflow revision 或显式 Worker input，不能借 mailbox 对话。

Runtime 凭冻结 Workflow 自动推进。Mailbox 仅触发“有新事实可检查”；主 Agent 可在已有授权范围内被唤醒，先读取 Task/Run/反馈，再审计式恢复、重试、修订或向用户报告。超出原范围须等用户新授权。通知不能自行生成需求，也不能重复唤醒造成无限规划。

## 阶段与文件

1. **协议拆卸**：`conversation_intent.py`、`repositories/conversation_intent_repository.py`、`agent_provider.py`、`agent_run_preparation.py`、`agent_prompt.py`、`agent_runner.py`、`conversation_report.py`、`conversation.py`。先建立最小授权合同/旧数据兼容，再移除每轮 resolve、问题状态准入和讨论必须结构化报告。回归：纯讨论不建 Task；明确授权即开始；历史助理承诺不授权；失败如实报告。
2. **动态工具与 MCP**：`capabilities.py` 版本化注册；新增 MCP client/配置适配层；`agent_run_preparation.py` 在回合边界快照工具，`agent_tool_execution.py` 正常记录/dispatch。用本地测试 MCP server 验证发现、调用、失败/重连、重放。Web 只展示产品功能，不展示 debug。
3. **Session/Memory/Context**：沿用 `agent_repository.py`、`memory.py`、`context_manager.py`、`session_replay.py`，修复历史恢复和压缩边界。覆盖跨回合、重启、MCP 回执和 Worker 续用，不在每轮注入冗长全量状态。
4. **监督/Worker 生命周期**：`conversation.py`、`agent_repository.py`、`task_feedback_repository.py`、Workflow/Task Runtime。通知与有授权的监督回合分离；Worker input 用显式 API，Mailbox 只作事件索引。
5. **真实黑盒**：全新 Project，复用同样五轮 Vue+Java 登录防刷讨论及授权。讨论阶段零 Workflow/Task；随后验证跨列推进、失败恢复、通知、真实可运行交付。完成标准是 Project 交付且无 P0 控制面阻塞，不是某个 Job succeeded。

每阶段独立提交，保留 `f521053` 基线。旧测试若只锁定被替代的 `resolved` 设计，应改为用户可见行为测试，不能删除权限/追溯/交付验证。未跟踪的 `devwerk.zip` 不参与修改。

## 阶段 1 实施记录

Project Agent 不再在每轮首个 Provider 请求中被强制只看到 `conversation.turn.resolve`；整个 Session 的工具目录在相邻回合保持稳定，讨论/只读检查可直接进行。Tool-free 普通答复会由 Runtime 自动记入 discussion hold；需要业务副作用的回合仍必须先绑定当前用户请求，未授权调用由 dispatch 拦截。`open_questions[*].blocking_for_start` 不再覆盖用户后来的明确执行授权。模型指令不再要求逐项更新决定/问题状态，保留旧字段读取以便历史 Project 兼容。

回归：`test_conversation_intent_contract.py`、`test_conversation_contract.py`、`test_conversation_reply_tool.py` 中旧的“工具目录必须缩到一个工具”的断言调整为稳定工具目录及 dispatch 边界。新增“Agent 自己提出的阻塞问题不挡用户后续明确授权”用例。用仓库 venv 的 Python 3.12 运行四组 Conversation/API 合同测试，**77 passed**。系统 Python 3.14 对未关闭 SQLite 连接发出 unraisable ResourceWarning，导致同一通过用例在进程退出时非零；仓库 venv 无此问题。后续测试均用仓库 venv。阶段 2 开始前仍需审视执行回合无效果时的结果准入。

## 阶段 2 实施记录：MCP 工具接入

新增 `app/v1/mcp_tools.py`，从 `config/mcp-servers.yaml`（或 `DEVWERK_MCP_SERVERS_PATH`）读取配置。格式：

```yaml
mcp_servers:
  local_tools:
    transport: stdio
    command: python
    args: [path/to/server.py]
    timeout_seconds: 30
  remote_tools:
    transport: http
    url: http://127.0.0.1:9000/mcp
```

当前实现使用持久 stdio 子进程或 Streamable HTTP Session，做 initialize、tools/list 分页、tools/call；工具注册成 `mcp__server__tool`，经现有 CapabilityRegistry、ToolResult、执行回执和权限边界。MCP `readOnlyHint=true` 为只读，未标注的工具保守地作为 process 操作；Column 只有在 Workflow 明确给它该 capability 时才能调用。返回内容在进入模型上下文前有大小上限。三项本地 stdio/HTTP SSE/配置校验测试通过。仓库 venv 中安装的 Python MCP SDK 引用了 Python 3.10 的 pywin32 DLL，无法在当前 3.12 Runtime 导入，因此此阶段用边界清晰的本地 JSON-RPC 适配器，不把一个不可加载的 SDK 当成完成的功能。

HTTP Session 失效后，`tools/list` 会重新初始化并重试；`tools/call` 不会盲目重试，因为其外部效果可能已经发生，交给执行回执标为结果不确定。stdio 请求超时或断开会关闭旧进程，下一请求建立新 Session，避免旧响应串到新请求。工具目录每 60 秒后台刷新；新工具只在下一 Agent Run 建立时进入目录，运行中的 Run 保留开始时的工具列表。发现中重复分页游标与工具别名碰撞会被拒绝。服务器重启、HTTP 过期和输出限长的基础测试已覆盖。

回归：`test_mcp_tool_bridge.py`、四组 Conversation/API 合同测试，仓库 venv Python 3.12 下 **82 passed**。未跟踪的 `devwerk.zip` 未参与变更。

当前边界：此版本只接入 MCP tools；resources、prompts、sampling 尚未接入。运行期新增工具会出现，但同名工具的 schema 更新/撤销仍需重启才能刷新；这是为了不改变在途 Run 的 schema/dispatch 合同。HTTP `tools/call` Session 过期时回执保持不确定，需人工或后续对账恢复，不能自动重放未知副作用。上述能力未达到全部 Hermes 平台集成的范围，后续阶段不得据此宣称完整等同。
