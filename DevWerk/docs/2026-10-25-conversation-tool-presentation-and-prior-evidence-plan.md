# Conversation Agent 工具披露与历史证据帧修复方案

状态：已实施代码并完成相关边界测试；真实 Provider 黑盒尚待验证。依据为 `docs/2026-10-24-backend-code-map.md`、`docs/2026-10-24-frontend-code-map.md`、当前源码和 `data/logs/devwerk.log` 中 2026-10-25 09:29:19、09:29:25、09:29:35 的三次 `llm.provider_request`。不把历史文档的测试结论当作现状证据。

## 1. 目标和边界

解决两个独立问题：

1. Conversation Agent 每次向 Provider 发送全部可用 Tool Schema；一次普通文件修订也发送 69 个内置 Tool、32 个 Chrome MCP Tool。
2. 当前轮请求的 `unanswered_prior_user_message` 字段装入了内部 `prior_run_evidence_index`，把证据引用标成了用户消息。

保留 Conversation Agent 对全部已授权 Tool 的自主选择权，保留现有 Requirement/Workflow/Task 授权和派发校验。只改变 **Provider 看见哪些 Schema** 以及 **证据帧如何标记**。Column 继续直接获得 Workflow 分配的能力和 `column.complete`；不修改 Workflow 图、Task 状态机、Mailbox、Web、Provider 接口或数据库业务状态。

影响面核对：Tool 问题跨 **7 个运行环节**（注册、全量取 ID、全量生成 Schema、Run 内固定复用、请求前过滤、预算估算、Provider 发送），其中造成全量重复的关键是中间三个环节；证据问题跨 **6 个环节**（公开历史、先前 Run 索引、内部帧追加、当前帧合并、typed payload、Provider 转换），错误赋值集中在当前帧合并。实际直接修改 **9 个现有生产文件、新增 2 个职责文件**；Store 增加按 Run 读取最新成功选择回执的方法，ContextManager、MCP 和三个 Provider adapter 保持原代码并做兼容验证。

## 2. 实测现象与根因

三次请求属于同一文件修订回合：第一请求带两轮公开对话与当前修改要求；第二请求追加三次 `project.files.read` 回执；第三请求追加一次 `project.files.write` 回执。总请求约 168k、172k、176k 字符。每次 Tool 定义均约 149.3k 字符，三次重复发送约 448k 字符；其中 `workflow.create` 单项约 60.2k，全部 Workflow Tool 约 63.4k，Task Tool 约 23.7k，Chrome MCP Tool 约 29.2k。以上为 JSON 字符量，不是 Provider Token 计费值。

### 2.1 Tool 链路

```text
tooling/builtin_tool_catalog.py + capability_registration.py + mcp_tools.py
  -> CapabilityRegistry 中的全部 Tool
  -> conversations/conversation.py:ConversationGateway._process 取 registry.all_ids()
  -> agents/agent_run_preparation.py:prepare 为全部 allowed 生成 tool_schema()
  -> agents/agent_runner.py:run 在每轮复用 prepared.tools
  -> agents/agent_provider.py:ProviderTurnRequester.request 仅按授权阶段/强制回复过滤
  -> context/context_manager.py:prepare 把 Tool Schema 计入上下文预算
  -> app/services/*client.py 每次发送本轮完整 Tool 数组
```

`ConversationGateway` 只排除了少量旧控制 Tool；它没有区分“本 Run 可以调用”和“本次 Provider 请求需要看到”。`CapabilityEntry.toolset` 现在只有 `core/mcp`，主要用于 Workflow 参数模型是否内嵌外部 Tool 参数，不能直接拿来作为新披露/权限枚举。`workflow.create` 的大型 Schema 来自 `CapabilityRegistry._refresh_workflow_tool_models` 汇入所有可委派能力的 `workflow_tool_models`；只要它被披露，体积就会明显增加。`ContextManager` 按实际发送的 Tools 估算预算，因此固定的大 Tool 数组还会挤掉 Memory 正文或触发过早压缩。

Provider API 是无状态调用：**仍需要在每次请求重发当前可调用的 Schema**。可解决的是“不论任务内容都重发全部 101 项”，而不是声称可以在不改变 Provider 协议的情况下零重发。

### 2.2 错标证据链路

```text
store.py:conversation_session_messages -> 公开用户/助手历史
store.py:conversation_session_run_index -> 先前 Run 的有界证据指针
agents/agent_run_preparation.py:_append_session_history
  -> 把 PriorRunEvidenceFrame 追加为内部 user 角色帧
agents/agent_run_preparation.py:_append_current_request
  -> 无条件弹出末尾 user 帧
  -> 存入 CurrentTurnPayload.unanswered_prior_user_message
```

因此 Provider 当前请求中的该字段实际是内部证据索引。对本日日志可解析的 285 条 Provider 请求作统计，30 条含此字段的当前请求，30 条均为 `instruction,prior_run_evidence_index` 结构，未发现真正的未回答用户原文。`PriorRunEvidenceFrame` 自带“不是新用户请求/授权”的说明，但外层字段名与之冲突。`store.py` 的证据索引、公开历史隔离和 `agent.result.read` 引用机制本身不需要改。

## 3. 修复设计 A：能力快照与 Tool 披露分离

### 3.1 状态所有权

- **可执行能力**：仍由 `ConversationGateway` 建立的完整 `capability_ids`、Agent Run 的 `capabilities_json` 快照、`CapabilityRegistry.dispatch` 和现有用户授权边界决定。Tool 披露不得产生新权限。
- **当前披露集合**：仅为一个 Conversation Agent Run 的 Provider 呈现状态。初始为小而通用的直达 Tool；Agent 可在任何时间用目录 Tool 查询并选择额外 Tool。选择在本 Run 内有效，下一用户回合重新从直达集合开始，避免跨回合累积回全部 Tool。
- **持久恢复**：`tool.catalog.select` 的成功调用已通过现有 Tool invocation/operation receipt 持久记录。下一次模型请求从本 Run 最近一次成功选择回执重建披露集合；不新增数据库字段或第二套能力状态。选择是可替换集合，不无限累加。

### 3.2 Agent 可见协议

新增两个通用内置 Tool，ID/说明/副作用类别和 Column 委派策略在中央 catalog，固定请求和回执由 Pydantic 类生成 Schema：

- `tool.catalog.search`：按 ID、说明和来源（内置/MCP）查询本 Run 已授权且当前可用的 Tool，支持有界分页；返回 `id`、简要说明、来源、是否已经披露。空查询给出来源概览和有限目录页，未命中时仍可按来源浏览。结果不带全部 Tool 的完整 Schema，也不在 handler 中拆 Tool ID 临时推导业务类别。
- `tool.catalog.select`：提交确切 Tool ID 列表；ID 可来自搜索结果，也可来自 Agent 已知的稳定 Tool 协议，不强制先搜索。校验它们都属于本 Run 的授权快照、当前可用、符合统一的选择上限，返回**下一次**模型请求将披露的集合。它只改变披露，不改变业务权限，不要求 Requirement grant。成功回执后下一次模型请求才发送所选 Tool 的原有精确 Schema；Column 不可委派此 Tool。选择不追溯改变产生该调用的模型请求，同批其它调用仍按现有授权与 Schema 校验处理。

协议细节：`search` 使用 typed `query`（可空、至多 200 字符）、`source`（`builtin`/`mcp`/不限）、`offset`（非负）和 `limit`（默认 10、最多 20）；回执含条目、总命中数及 `next_offset`。`select` 使用去重的 Tool ID 列表，最多 16 个按需 Tool；直达 Tool 始终保留，不占此上限。回执含最终按需 ID 列表，并明确 `applies_on_next_request=true`。这些默认值与上限集中在 typed contract，不散落于调用链；未知 ID、非本 Run 能力、不可用 Tool、重复 ID 或超限分别通过现有标准 `invalid_arguments`/`not_found`/`permission_denied` 错误封套及明确字段说明返回，不产生部分成功。目录结果、选择回执和所有实际工具调用仍由 Registry 验证输入/输出。

初始直达集合按 Tool ID 在中央 catalog 明确标记，**本次拟定的完整清单**为：`tool.catalog.search/select`；`conversation.reply`、`conversation.execution.authorize`、`conversation.task.control.authorize`、`conversation.history.read`、`conversation.turn.inspect`；`project.files.list/read/search/write`、`project.command.run`、`project.memory.read/search/write`；`agent.result.read`、`workflow.inspect`、`task.inspect`、`loop.list/inspect`。合计 20 个 Tool，其中新增目录 Tool 两个。这里披露 `loop.list/inspect` 的 Schema，不预读 Loop 内容；`workflow.create`、Task 写入 Tool 与外部 MCP Tool 均须按需选择。此清单保证普通对话、直接文件工作、记忆操作、基本监督与管理授权无需额外发现步骤；其余能力可自行发现，不按用户文本写死编程、小说、调研等意图规则。目录选择的数量上限只在 typed contract/统一 policy 中定义，不在执行逻辑散布数字；再次选择可替换集合。初始集合应在此次实测 Provider 格式下低于 25k JSON 字符（原为 149.3k）；这是验收基线，不是运行时硬编码的裁剪阈值。

`conversation.reply` 在需要事实化结束时仍能按现有规则成为本次唯一 Tool；提交授权后仍隐藏一次性授权 Tool。Column 的精确能力清单、完成 Tool 和 `agent.worker.send` 行为保持原状。保留原 Tool 的输入/输出 Schema、Registry 校验、Tool receipt 和执行账本；不引入可以绕过声明 Schema 的 `tool.call(arguments:any)` 入口。

### 3.3 文件与方法

| 文件 | 预期修改 |
| --- | --- |
| `app/v1/tooling/builtin_tool_catalog.py` | 为内置 Tool 声明直达/按需披露元数据；新增目录 Tool 的稳定 ID 与说明。 |
| `app/v1/tooling/capability_registry.py` | `CapabilityEntry` 承接独立的披露元数据；Registry 原执行校验不变，提供按现有授权快照解析目录条目和精确 Schema 的方法。不要重用 `CapabilityToolset.CORE/MCP` 充当权限。 |
| `app/v1/tooling/capability_registration.py` | 把两个目录 Tool 的 typed Schema 绑定到具名 handler；保留其余 Tool 注册方式。 |
| **新增** `app/v1/tooling/tool_presentation.py` | 集中实现目录搜索、选择校验、按 Run 最近成功回执恢复披露集合，避免把逻辑堆进 `capability_registration.py`。 |
| **新增** `app/v1/protocols/tools/core/tool_catalog_contracts.py` | 查询、选择、目录摘要及回执的类型、默认值、范围和错误语义。 |
| `app/v1/agents/agent_run_preparation.py:prepare` | Conversation 只生成初始直达 Schema；仍把完整授权能力及 effect kinds 保存在 Run 快照中。Column 维持原逻辑。 |
| `app/v1/agents/agent_runner.py:run` | 每次调用 Provider 前取本 Run 当前披露集合，而非恒传固定 `prepared.tools`。 |
| `app/v1/agents/agent_provider.py:ProviderTurnRequester.request/_visible_tools` | 在动态披露后继续执行现有一次性授权隐藏、强制 `conversation.reply` 过滤，再以**实际发送的** Tools 做预算估算与 trace。 |
| `app/v1/store.py:latest_successful_tool_invocation` | 按 Project/Run/Tool ID 读取最近成功选择回执，避免从有分页上限的 Tool 调用列表推断披露状态。 |

`conversations/conversation.py:ConversationGateway._process` 的完整能力快照、`agent_tool_execution.py` 的执行与回执、`context_manager.py` 的预算算法、`mcp_tools.py` 的真实 MCP 注册、各 Provider adapter、Web 与 DB schema **只核对不预设修改**。尤其不能因为 Schema 未披露就把已授权 Tool 从 Run 快照移除。`workflow.create` 的 60k Schema 本轮先改为按需发送；不削弱其已经建立的强类型 Workflow 参数校验。若实际 Workflow 阶段仍发生预算不足，再单独评审它的 Schema 体积，不混入本次改动。

披露是 Provider 表示层，不是执行授权。若模型在未披露该 Schema 的请求中仍构造出一个已授权 Tool Call，保留现有 Run 快照、Registry 参数验证和业务授权的处理方式；不能用“未先搜索/选择”拒绝本来合法的项目操作。目录选择只决定后续请求带哪些精确 Schema。不能把 `tool.catalog.search/select` 加进 Workflow Column 的能力目录。

## 4. 修复设计 B：把内部证据按其真实类型装入当前轮

`AgentRunPreparer._append_session_history` 生成先前 Run 的 `PriorRunEvidenceFrame` 后，在有 `current_request` 的 Conversation Run 中将它作为类型化返回值交给 `_append_current_request`，不先作为末尾 user 帧追加再被通用 `pop()` 错认。`CurrentTurnPayload` 增加 `prior_run_evidence` 类型字段，使用已有 `SessionRunIndex`/`SessionToolReceipt` 定义；无证据时不序列化此字段。**删除通用的末尾 user 帧 `pop()` 和 `unanswered_prior_user_message` 字段**：公开历史中真正尚未回答的用户消息按原对话顺序保留为独立历史帧，其他内部帧也不再被伪装成用户原文。Anthropic 对相邻 user 角色消息的合并由现有 Provider adapter 处理，OpenAI/Ollama 按各自合同核对。没有当前请求的恢复/独立 Agent Run 继续使用原有独立 `PriorRunEvidenceFrame`，避免丢失证据。先前失败、Tool ID 与回执指针必须保持原值，可继续用 `agent.result.read` 精确读取。

直接改动仅涉及 `app/v1/agents/agent_run_preparation.py:_append_session_history/_append_current_request`、`app/v1/protocols/agent/agent_prompt_contracts.py:CurrentTurnPayload`、`app/v1/protocols/agent/agent_context_frames.py:PriorRunEvidenceFrame`。`store.py:conversation_session_run_index`、`context/session_replay.py`、`context_manager.py:_refresh_conversation_authority` 与 Provider 消息转换只做兼容回归。此字段是内部 Provider 帧，不是 Web/API 或数据库协议；既有已完成 Run 原始日志无需重写。

## 5. 小步实施与验收

按两个缺陷分别提交，先修证据字段，再实施 Tool 披露；每步只碰其所有权范围，先单测后扩展测试。Tool 披露实施时先加入 typed 目录和目录检索单测，再切换 Conversation 的 Provider 呈现；中途不能留下既无完整 Tool 又无目录入口的状态。

重点回归：

1. 实际 Provider 请求：普通文件修订首轮不再带 `workflow.create`、`task.*` 和 32 个 Chrome MCP Schema；Tool 定义字符量显著低于此次 149.3k 基线，当前请求与公开历史仍完整。
2. Agent 通过目录自行找到并选择 Workflow 创建、Task 计划/创建工具；在自然语言多轮对话中实际创建 Workflow/Task，并由 Column 推进到 terminal。模型看到的是原工具的强类型 Schema，实际回执可追踪。
3. 外部 MCP Tool 可被查询、选择、调用；未选择不预装 32 个 Schema。Column 仍只看到其 Workflow 明确分配的 Tool 和 `column.complete`。
4. 授权工具在 grant 前可见、grant 后按原规则隐藏；Task 入队后的强制 `conversation.reply` 仍为唯一 Tool。目录 Tool 不授予新的业务权限。
5. Provider 溢出与上下文压缩按**当前披露集合**计算；重启/操作回放后仍能从成功回执重建所选 Tool，不会错误重复外部副作用。
6. 先前 Run 失败索引进入 `prior_run_evidence`，不存在 `unanswered_prior_user_message` 字段；真实未回答用户消息与无当前请求的恢复路径均不丢失。Anthropic、OpenAI、Ollama 转换后的消息和 Tool Call 顺序有效。

前端代码地图确认 Web 只提交 Conversation 消息并读取结果，此次不需要 UI 改动。服务仍遵守仅使用 8000 端口及保留现场的开发纪律；实施和测试前先核对现有服务状态。

## 6. 风险、取舍与回退

- **额外目录回合**：少见 Tool 可能先搜索、再选择，增加一至两次模型往返；常用的 20 个直达 Tool 避免普通对话和文件工作付出这笔开销。黑盒回归必须确认 Agent 能自行找到 Workflow/Task/MCP Tool；若无法发现，优先改通用目录说明和检索，不按案例加入项目类型提示。
- **复杂 Workflow Schema**：`workflow.create` 仍约 60k 字符，但只在 Agent 选择它时发送。不能为了缩小当前数字而删除参数类型或绕开原有业务校验。
- **相邻 user 帧**：移除错误 `pop()` 后，Anthropic adapter 会合并相邻 user 消息；必须检查合并后的顺序、当前请求定位和历史用户原文。公开会话历史只回放人类可见对话，旧 Run 的内部帧不会跨用户回合进入新请求；既有日志无需迁移。
- **回退边界**：两个缺陷分别提交。Tool 披露如未通过通用 Agent、Workflow/Task 或 MCP 验收，可单独回退其提交；证据字段修复不依赖目录 Tool。实施后更新 `docs/feature-pending.md` 的 F-001，避免把已落地部分仍写成待办。

## 7. 本次实施与验证记录

- 已实现类型化 `prior_run_evidence`，删除当前轮的 `unanswered_prior_user_message` 及其通用末尾 `user` 帧 `pop()`。先前失败索引仍按原 Run 回执生成；独立恢复 Run 仍保留原证据帧。
- 已实现 Run 内 Tool 目录查询、选择、成功回执恢复及 Provider 动态 Schema 呈现。Conversation 首轮直达 20 个 Tool，测试中的 JSON Schema 总量低于 25k 字符；完整 Run 能力快照、Registry 执行校验和 Column Tool 列表没有因此缩减。
- 2026-10-25 在可创建 Windows `asyncio` 本地 socketpair 的测试环境中运行目录、Conversation、通用 Agent 和 Provider 四个相关单元套件：**72 passed**。另有 Context、Store、API 等相关套件在实施过程中通过；本次最终重跑未覆盖完整 607 项单测，也未进行真实 MiniMax/Chrome 黑盒验证。
- 当前共享工作树原本包含其他未提交修改，相关文件有交叠；本次保留改动供审阅，未把既有改动混入新提交。第 5 节的独立提交是计划中的追踪要求，尚未执行。

## 8. 真实服务黑盒复测（2026-10-25）

用户要求重启服务并重放上次的通用 Agent 用例。确认 8000 原服务无活动会话后，使用 `startup.bat` 的重启标记机制重启；新监听进程 PID 13856，`/v1/health` 为 `ok`。新 Project `prj_33035bfc22bc4405b3a6851357af9ffb`，目录 `data/blackbox-general-agent-catalog-20261025-1455/`，复制上次同样的 `client-note.md` 和 `meeting-note.md`，依次重放原先三条自然语言用户消息。未使用模拟模型。

| 轮次 | Conversation Job / Run | 实际结果 |
| --- | --- | --- |
| 读取核对 | `cjob_7bac756f1d744612bea3288ce046812c` / `arun_aff23b3a3dd14a0983039f3e0ebb2fef` | `succeeded`；两次 `project.files.read`、一次 `conversation.reply`；未写文件。 |
| 写核对摘要 | `cjob_40da3eb449c1414596cee2b3ffb0e64d` / `arun_51b996385cb4465eaf601b543e0d9c4b` | `succeeded`；两次读取、一次 `project.files.write`、一次 `conversation.reply`；无授权或管理 Tool。 |
| 按用户决定修订 | `cjob_e86f46ccfe54473486e330c5482bf8a0` / `arun_17728070d51843a2be4f3567eeeaab9e` | `succeeded`；读取/检索后重写同一文件。首次 `conversation.reply` 因引用本轮不存在的 Tool Call 被拒绝，随后用有效引用成功完成。 |

从 `data/logs/devwerk.log` 的 10 次真实 `llm.provider_request` 逐项解析：每次恰有 **20 个 Tool**，实际发送的 Tool JSON 为 **13,111 字符**；均无 `workflow.create` 或 Chrome MCP Schema。对应 `agent.model_input` 里的当前请求，在第二、三轮含 `prior_run_evidence`，均不含 `unanswered_prior_user_message`。Provider 表示层实际生效，不只是单元测试中的 Registry 列表变化。

交付文件 `data/blackbox-general-agent-catalog-20261025-1455/核对摘要.md` 存在，标明三项用户决定的来源，并把预算列为未批准、待确认；原始两个文件 SHA-256 与复制来源相同。数据库只读核对该 Project 的 Requirement 0、Workflow 0、Task 0。服务继续在 8000 运行，Project、文件、DB 和日志均保留供审阅。

本用例的**通用 Agent 直接读写与多轮修订路径通过**，但再次出现“预算数字一致即可作为确定约束”的模型证据判断错误；用户第三轮纠正后已修复交付文件。本用例只使用首轮直达 Tool，因此**没有验证 Agent 在真实模型下自行调用 `tool.catalog.search/select` 后使用按需 Tool**，也不证明 Workflow/Task 或 MCP 发现路径已通过黑盒验收。
