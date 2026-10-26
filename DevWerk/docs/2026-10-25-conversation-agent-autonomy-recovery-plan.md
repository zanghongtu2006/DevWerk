# Conversation Agent 独立能力恢复方案

## 目标与边界

Conversation Agent 是每个 Project 的持续通用 Agent，具备普通对话、文件、命令、MCP、记忆和项目管理工具。Kanban 是它在需要拆分交付时选择使用的专业工具；Workflow/Task 的状态仍由既有状态机和事务维护。Column Worker、Mailbox 和 Workflow 图的职责不变。本次不加入场景规则，也不重构 Workflow 引擎。

## 现状根因

1. `ConversationIntentRepository.denial` 把全部 `write/process/control` 效果归入 Requirement Grant。文件、命令、记忆和 MCP 直接执行也必须先建立 Requirement，这把通用 Agent 误变成项目规划入口。
2. `CapabilityRegistry._dispatch` 在任意 Task 运行时一律拒绝 Conversation Agent 的直接 Workspace 效果。这使它在 Kanban 工作时失去一般 Agent 能力。
3. `DEVWERK.md`、`TURN_INSTRUCTION`、`ConversationGateway` 每轮注入和 `REPORT_INSTRUCTION` 重复给出详尽的 Workflow 操作路径，压过 Agent 自主判断。
4. `repeated_no_progress_result` 未把首次成功执行授权视为阶段推进；同一轮授权前的三次读取仍计入授权后的重复读取，造成当前 Project 的误判停机。
5. Project Conversation Job 串行执行。长轮次期间新消息仅排队，不能像 Hermes 的主 Agent 一样在安全边界接收用户转向。

## 修改设计

### A. 将 Tool 权限归还给通用 Agent

- `app/v1/tooling/builtin_tool_catalog.py`：集中列出真正受 Workflow/Task Grant 约束的内建状态变更 Tool。文件、命令、记忆、MCP 等通用 Tool 默认不属于该集合；不通过字符串前缀或场景名称猜测。
- `app/v1/repositories/conversation_intent_repository.py:denial`：当前用户的 `auto` 执行轮次可直接调用通用 Tool；显式 `discuss` 仍只读；非用户通知轮次仍不能擅自执行。只有 Workflow/Task/Requirement 状态变更继续检查已有 Grant、Requirement revision 和 Task control 约束。
- `app/v1/tooling/capability_registry.py:_dispatch`：移除“只要有 Task 运行就禁止通用 Agent 使用 Workspace”的全局互斥。各 Tool 的执行回执、文件路径与实际错误照旧记录；不改变 Column 的写入边界。

### B. 减少强制流程提示，保留精确 Tool 协议

- `DEVWERK.md`、`app/v1/conversations/conversation_intent.py:TURN_INSTRUCTION`、`app/v1/conversations/conversation.py:ConversationGateway._process`：以简短通用身份和工具选择原则替换每轮详细的“先授权、再修订、再建计划/任务”指导。用户请求 Kanban 管理时，使用对应 Tool 声明的参数与回执；普通工作可直接执行。Mailbox 仍为通知。
- `app/v1/conversations/conversation_report.py:REPORT_INSTRUCTION`、`app/v1/tooling/builtin_tool_catalog.py`：说明执行 Grant 只保护 Workflow/Task 状态；完成报告引用实际 Tool/Task 回执。普通讨论可自然语言结束。
- 保留图校验、状态机、TaskPlan 准入、已运行 Task 的 Workflow revision 冻结，以及真实回执核验。

### C. 修复交互生命周期

- `app/v1/execution/execution_ledger.py:repeated_no_progress_result`：一次成功的授权形成新的读取阶段；之前的相同读取不能触发新阶段的拒绝。仍保留真正连续无进展调用的终止保护。
- `app/v1/conversations/conversation.py` 与 Agent Run 控制边界：新用户消息到来时，让正在运行的 Conversation Agent 在模型/Tool 安全边界接收该消息或中断当前轮次；保留已经提交的 Tool 回执，避免旧请求继续执行覆盖新指令。不得把用户新消息当成 Mailbox 通知。

## 验证

1. 单元测试：无 Grant 的用户轮次可直接写文件、使用记忆及普通工具；显式讨论和通知轮次仍不能写；Workflow/Task 变更仍需合法 Grant；运行中 Task 不封锁通用 Agent 的无关直接动作。
2. 单元测试：授权前重复读取不污染授权后阶段；真实连续重复仍被限制。
3. 集成测试：同一 Project 可自然对话、直接使用普通 Tool、选择创建 Workflow/Task；Task 由原状态机推进；任务运行时仍可处理新的用户指令。验证基于真实 Tool 回执，而非模型声明。

## 风险

允许 Conversation Agent 与 Column Worker 同时使用 Project Workspace 后，双方可能修改同一文件。本次不引入新的锁层或任务场景策略；Tool 回执与文件版本仍可追踪。应在测试中明确覆盖无关文件并行，后续如需同文件冲突处理，应由 Workspace 层以文件版本处理，不恢复 Project 级一刀切封锁。

## 实施与验证记录

- 已将受 Requirement Grant 约束的管理 Tool 在内建 Tool 目录中显式标记；普通文件、命令、记忆和 MCP Tool 可由用户轮次直接调用。显式讨论轮次和通知轮次依旧不能执行副作用。Task 运行或恢复中时，主 Agent 的独立 Workspace 操作不再被全局封锁。
- 已缩短主 Agent 的系统和每轮说明，保留管理 Tool 的参数契约与真实回执约束。成功授权会重置重复读取保护的阶段。
- 新用户消息会在模型请求或 Tool 调用的安全边界结束旧轮次，保留已提交的 Tool 回执，并由下一轮处理新消息；不会把新消息转为 Mailbox 指令。
- 新增单元测试覆盖无 Grant 的文件与记忆操作、运行中 Task 与主 Agent 并行、显式管理边界、打断后未执行的效果不落地以及已提交效果保留。Conversation/Tool 聚焦测试 168 项通过；Workflow 与相关 Conversation 测试 275 项通过；新增用例最终单独运行 6 项通过。完整单测运行曾在旧提示词和旧全局锁断言处停止，这些断言已按新边界更新，余下测试由 Workflow/Conversation 分组覆盖。`compileall` 和 `git diff --check` 通过。
- 服务只在 8000 启动；本次启动后 `/v1/health` 返回 `status=ok`、Supervisor 与 Conversation Gateway 均为运行中。

当前并未宣称与 Hermes 功能完全等量：DevWerk 仍一次向模型提供较多 Tool schema，也没有 Hermes 的独立 steer 模式。本次恢复的是主 Agent 的直接执行权与可打断的持续交互；不扩张为 Tool 系统或 Workflow 引擎重构。
