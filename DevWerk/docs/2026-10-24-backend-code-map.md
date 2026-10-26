# DevWerk 后端代码地图

> 依据：2026-10-24 直接核对 `app/`、`tests/unit/` 源码。描述当前运行路径与所有权，不采用 `docs/` 中的历史测试结论。

## 组合入口与主链路

`app/main.py:create_app/lifespan` 组合 FastAPI、配置/日志、Tool Registry、MCP 发现、`V1Store`、`ConversationGateway` 和 `RuntimeSupervisor`，并挂载 `/v1` API 与 Web 静态资源。业务对象不由 Web 页面直接修改。

```text
用户对话 → api/routes.py → ConversationGateway → AgentCore
    → AgentRunPreparer → ProviderTurnRequester/AgentExecutionRunner
    → AgentToolBatchExecutor → CapabilityRegistry → typed Tool handler → V1Store
       workflow.publish / task.create 等持久化动作
Task 可运行 → RuntimeSupervisor → WorkflowRuntime.step
    → Column executor（单 Agent 或 capability_sequence）
    → 声明的 outcome/transition → V1Store.finish_run → 下一 Column 或 terminal
```

## 模块与状态所有权

| 位置 | 当前职责 / 权威状态 |
| --- | --- |
| `app/core/` | 环境、全局设置、日志与 trace、托管重启；`app/services/` 适配 LLM provider 与用量。 |
| `app/v1/api/routes.py` | FastAPI 传输层：Project、Conversation、Workflow/Task、Loop、projection、事件 SSE、settings 等；请求/响应模型在边界验证。 |
| `app/v1/conversations/` | Project Conversation job 队列、持续 Session、用户意图/授权、事实化答复、通知触发的监督回合；持久状态通过 Store/Repository 访问。 |
| `app/v1/agents/` 与 `app/v1/agent.py` | `AgentCore` 外观、Run 准备、模型轮次、工具批执行、Provider 适配、Prompt envelope；Agent run/session 消息与工具回执落库。 |
| `app/v1/tooling/` | 内建 Tool catalog（稳定 ID、说明、effect、Column 可委派性）、registration（schema/handler 绑定）、registry（能力与输入/输出验证、dispatch）、`tool_presentation.py`（Conversation Run 的按需 Schema 披露）、handlers（具体行为）、completion protocol、MCP bridge。`app/v1/capabilities.py` 是稳定入口。 |
| `app/v1/domain.py`、`app/v1/workflows/` | Workflow/Column/transition/terminal、Plan、Task contract 的 Pydantic 领域模型；Workflow authoring/canonicalization、Loop 文件目录、图传输与验收配置编辑。 |
| `app/v1/execution/` | Runtime 执行、状态机、执行控制/ledger/recovery。`WorkflowRuntime` 从冻结的 Workflow revision 读取 Column 和出边，执行后按声明的 outcome 前进。 |
| `app/v1/context/` | Run 上下文投影、预算/压缩、Memory 读写和 Session replay。Context checkpoint/summary 是派生数据，不是 Workflow 状态来源。 |
| `app/v1/services/` | Scheduler/lease/依赖、恢复、mailbox、Task admission、completion admission 和验收检查等跨 Repository 协调。Mailbox 是通知/交付信息通道；Task 业务路由仍按 Workflow transition。 |
| `app/v1/store.py`、`app/v1/repositories/` | SQLite 事务、Workflow revision/Task/run/await/projection 等持久化入口；Repository 分担 Project、Plan、Scope、Agent、Artifact、Event、Feedback、schema。 |
| `app/v1/workspace/` | Project 文件和命令执行边界。 |
| `app/v1/protocols/` | typed wire contracts，分 `tools/agent/conversation/context/workflow/storage/transport`；动态 MCP 数据由其声明 schema 约束。 |

当前 `loops/` 只保存可选的 Markdown 方法指导；`loop.list/inspect` 是只读发现与阅读入口。Conversation Agent 必须通过独立的 Workflow/Task Tool 发布实际图和任务。历史数据库中的 Loop 绑定只供已冻结 Workflow revision 继续读取，不代表新 Loop 可以直接发布执行图。

当前 `V1Store` 同时保留不少持久化方法并组合 Repository/Service；不要误认为所有状态已迁入 Repository。`V1Store.tx()` 处理 SQLite 事务和嵌套 savepoint，`SchemaRepository` 建库及校验持久化状态；`execution/states.py` 定义 Task、Column run、Attempt、Agent run、Tool invocation、Feedback、Mailbox 的状态机。Workflow 的业务路径由每个 Column 的 `transitions` 和 Workflow terminal 定义（`domain.py:WorkflowDefinition.validate_graph/terminal_kind`），Task 当前列和状态由 Store 事务写入；Conversation 的口头陈述不代表该写入已经发生。

## 关键调用链与边界

1. **对话与工具**：`api/routes.py:converse` 提交 Conversation job；`conversations/conversation.py:ConversationGateway` 排队、租约、运行与记录回合；`agents/agent_run_preparation.py:prepare` 组装可见 Tool schema、Session/history/context；`agents/agent_runner.py` 调用 Provider 并执行 Tool batch；`tooling/capability_registry.py:dispatch` 验证与派发；`tooling/capability_handlers.py` 调用 Store/服务。`conversation_report.py:render_report` 基于落库事实生成回执。
2. **Workflow 与 Task 创建**：`tooling/builtin_tool_catalog.py` 声明 Agent 可见协议；`capability_registration.py` 绑定 typed schema 和 handler；`capability_handlers.py:_workflow_create/_workflow_publish/_task_create` 调用 `workflows/workflow_authoring.py`、`domain.py` 与 `V1Store.publish_workflow/create_task`。Plan 保存在 `repositories/planning_repository.py`，Task 入场检查在 `services/task_plan_compiler.py`、`task_graph_admission.py`、`task_entry_admission.py`。
3. **推进**：`execution/runtime.py:RuntimeSupervisor._tick` 取可运行 Task 与 await handle；`WorkflowRuntime.step` claim Task、读取冻结 revision、装配 Column input、执行 Agent 或声明的 capability sequence，验证输出、找 outcome 对应 transition，并调用 `V1Store.finish_run` 原子记录 run 与下一位置。`services/scheduler.py` 管可运行性、依赖和租约；`services/recovery_manager.py` 管异常恢复/暂停/重试。`services/completion_admission.py` 只判定 Agent 完成提交能否推进；`tooling/completion_protocol.py` 约束 `column.complete` 参数。
4. **投影与通知**：`V1Store._event/_refresh_projection` 写事件/看板投影；API `projection`/`stream` 给 Web 读取。`services/mailbox.py` 记录、交付和确认通知；`ConversationGateway` 可因通知做监督回合，但通知内容本身不是 Column 转移命令。
5. **上下文与外部工具**：`context/context_manager.py` 估算预算、压缩和恢复当前权威信息；`context/memory.py` 管作用域记忆；`tooling/mcp_tools.py` 将配置的 MCP server 工具发现/注册到同一 Registry；`workspace/files.py` 和 `workspace/process_runner.py` 执行 Project 文件/命令效果。

## 协议与测试定位

- Workflow 图和 Task/Plan 模型：`domain.py`；Workflow 输入迁移/规范化：`workflows/workflow_transport.py`、`workflow_authoring.py`；Tool schema 与 receipt：`protocols/tools/*`。
- Conversation/Agent/context：`protocols/conversation/*`、`protocols/agent/*`、`protocols/context/*`；API response：`protocols/transport/*`；持久事件/receipt：`protocols/storage/*`；Runtime checkpoint：`protocols/workflow/*`。
- 测试按责任归在 `tests/unit/{api,agent,conversation,tooling,workflow,storage,config}/`；重点入口：`api/test_api_web_contract.py`，`tooling/test_capability_contract.py` 与 `test_provider_contract.py`，`workflow/test_domain_contract.py`、`test_runtime_contract.py`、`test_completion_protocol.py`，`agent/test_context_lifecycle.py`，`storage/test_store_contract.py`，`config/test_architecture_conformance.py`。共享 fixtures 在 `tests/conftest.py`、`tests/helpers.py`、`tests/support/`。

## 改动入口与高风险边界

| 要改什么 | 先看哪里 | 同时核对 |
| --- | --- | --- |
| HTTP 入/出参 | `api/routes.py`、`protocols/transport/*` 或对应领域/工具合同 | `tests/unit/api/` 与 Web `core/api.js`。 |
| 新内建 Tool | `tooling/builtin_tool_catalog.py`、`capability_registration.py`、`capability_handlers.py` | typed `protocols/tools/*`、registry 边界验证、Column 委派策略、tooling 测试。 |
| MCP 接入 | `tooling/mcp_tools.py`、`config/mcp-servers.example.yaml` | Registry 动态 schema、结果限长、Provider 工具格式。 |
| Workflow 图/发布 | `domain.py:WorkflowDefinition`、`workflows/workflow_authoring.py`、`store.py:publish_workflow` | Plan 绑定、运行 Task 与 revision、Graph/Runtime 测试。 |
| Task 推进/恢复 | `execution/runtime.py`、`services/scheduler.py`、`services/recovery_manager.py`、`store.py:finish_run` | `execution/states.py`、ledger、await、租约、Mailbox 通知。 |
| Agent 对话/模型问题 | `conversations/conversation.py`、`agents/*`、`context/*`、`app/services/*client.py` | Session 消息/回执、压缩边界、Provider 兼容、事实化报告。 |
| Project 文件/命令 | `workspace/*`、相关 Tool handler | Project 根边界、执行回执与 Artifact 登记。 |

高风险：Workflow revision 冻结后 Task 持有其 revision ID；修改“当前 Workflow”不能默默改写运行 Task 的图。Column outcome、`column.complete` 验证和 `finish_run` 是不同边界，勿建立第二套业务路由。Agent/Provider 返回内容与已提交 Tool receipt 要分清。上下文摘要可丢弃原文细节，但当前授权、任务计划、失败事实须从持久数据重建。状态修改要与事件、projection、mailbox 的提交关系一起核对。
