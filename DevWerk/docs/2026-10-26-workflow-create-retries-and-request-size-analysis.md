# 建图重试与最新 Project 请求体分析

分析对象：`prj_60c65ad9f77e45f18febe8a565a6aa76`。证据来自 `data/devwerk.db` 的 Tool 调用、Column Run、Task 状态，以及 `data/logs/devwerk.log` 的实际 `llm.provider_request` 载荷。本文件只记录分析，不修改运行逻辑。

## 结论

本轮 `workflow.create` 调用 9 次，前 8 次参数或图引用不合法，第 9 次成功。不是九次都没有拿到 schema：`tool.catalog.select` 最初选中了 `workflow.create`，后续 4 个 Provider 请求确实携带它的完整 schema；但 Agent 在建图前又选择了 3 个 Task Plan Tool，当前实现将新选择**替换**旧选择。九次建图时，请求均已不再携带 `workflow.create` 的 Tool 定义。Runtime 仍允许调用 Agent Run 授权范围内、但本次未展示的 Tool，因此模型凭此前信息和错误回执反复猜测参数。这个链路是参数重试的直接、可复核诱因；无法仅凭单次样本量化模型自身的责任比例。

两个 Task 最终均为 `done`。本轮请求体最大约 162 KB，未发现新 Project 的 Provider 上下文溢出或超时；请求变大主要来自同一 Agent Run 反复附带的消息历史，其中包括 Tool 回执、此前的 Tool 参数和模型 thinking。这里的 KB 是 JSON 请求体字节数，不是 token 数，也不是网络层精确传输字节数。

## 建图调用时序

Conversation Agent Run：`arun_af5421c239f345d395d15619f710d490`。

| DB Tool 调用 | 操作 | 结果 |
| --- | --- | --- |
| 4629 | `tool.catalog.select` 选择 `workflow.create`、`task.create` | 成功；之后 4 个 Provider 请求展示 `workflow.create` schema |
| 4634 | `tool.catalog.select` 选择 `task.plan.contract`、`task.plan.save`、`task.plan.validate` | 成功；旧选择被替换 |
| 4638 | `workflow.create` | `transitions[0].to` 为未知字段 |
| 4642 | `workflow.create` | Column 根对象出现不允许的 `capabilities` 字段 |
| 4643–4644 | `workflow.create` | transition 传成字符串，协议要求对象 |
| 4645 | `workflow.create` | `target` 传成字符串，协议要求对象 |
| 4646 | `workflow.create` | `target` 缺少 `kind` |
| 4647 | `workflow.create` | `kind=column` 的目标使用了仅终态目标才有的 `status`，缺少 `key` |
| 4648 | `workflow.create` | 目标 `review` 结构合法，但 Workflow 只声明了 `draft` Column |
| 4649 | `workflow.create` | 声明 4 个 Column，创建成功 |

4638 到 4649 约 48 秒。所有九次实际建图请求的 `payload.tools` **均不含** `workflow.create`；调用仍被执行并被协议校验。错误定位在多数步骤有用，但错误回执不能代替完整 Tool schema。

实现链路：

1. `app/v1/tooling/tool_presentation.py::selected_ids` 只读取该 Run 最新一次成功的 `tool.catalog.select` 回执；`selected_schemas` 据此决定下一次展示哪些 schema。
2. `app/v1/agents/agent_provider.py::ProviderTurnRequester.request` 将当前选中的 Tool schema 附加到直展示 Tool，再送入 `ContextManager.prepare` 和 Provider。
3. `app/v1/agents/agent_tool_execution.py::AgentToolBatchExecutor.execute` 以 Run 的 `allowed` 列表和执行授权判断调用，没有核对“本次请求是否展示过此 Tool”。因此这不是授权绕过，但暴露出“可执行”与“可见参数契约”不一致。
4. `app/v1/tooling/builtin_tool_catalog.py` 对 `tool.catalog.select` 的描述只说“下一次请求展示”，未明确说明再次选择会**替换**上次选择，而且实现实际持续展示直到下一次选择。这增加了 Agent 误用的可能。

选择 `task.plan.*` 后，请求中这三个 Tool 定义合计约 13.9 KB；被移除的 `workflow.create` 定义约 12.3 KB。减少 schema 字节数本身没有问题；问题是同一 Agent 随后仍能调用缺少当前协议定义的 `workflow.create`。

## 请求体如何拼装

`AgentRunPreparer` 建立 Run 的系统/消息初始视图和授权 Tool 集；`ProviderTurnRequester` 按当前 `tool.catalog.select` 结果补充 Tool；`ContextManager.prepare` 对历史回执和上下文作投影/必要压缩；`AnthropicClient.complete` 将 system、messages、tools 转成 Anthropic 兼容请求，并按本机配置加上 `stream=true`。这 67 个已记录请求全部带 `stream=true`。

尺寸计算：从实际日志载荷重新编码为 `ensure_ascii=True`、紧凑分隔符的 UTF-8 JSON；所以是同一标准下可比较的**规范化请求体大小**，不声称等于 HTTP 客户端最终发送的精确字节数。67 次合计约 4.72 MB；重复发送的历史消息也计入每次请求。

| Agent / Column | 请求数 | 请求体最小 / 中位 / 最大 |
| --- | ---: | ---: |
| Conversation | 36 | 26.4 / 77.5 / 161.7 KB |
| `draft` | 11 | 25.2 / 42.1 / 108.9 KB |
| `review` | 7 | 26.5 / 41.5 / 96.6 KB |
| `revise` | 7 | 28.5 / 63.0 / 115.5 KB |
| `final` | 6 | 27.6 / 44.9 / 62.4 KB |

九次建图对应的请求从约 **69.8 KB** 增至 **97.1 KB**。其中 system 固定约 4.9 KB、Tool 定义固定约 25.2 KB；messages 从约 **39.6 KB** 增至 **66.9 KB**，消息条数由 21 增至 41。增长来自重试历史和错误回执，而非 Tool schema 越传越大。

最大的 Conversation 请求约 161.7 KB：system 4.9 KB，messages 156.0 KB，Tool 仅 0.6 KB（此时被限制为 `conversation.reply`）。messages 中约 65.1 KB 是 Tool 结果块、50.8 KB 是历史 Tool 参数块、24.7 KB 是历史 thinking 块，另有初始项目状态等文本。不能把这个峰值解释为“全量 Tool schema 重复发送”。

最大的 Column 请求约 115.5 KB，来自 `revise`：system 2.7 KB，Tool 定义 5.6 KB，messages 107.0 KB，只有 5 条消息。其中一条历史 assistant 消息约 61.0 KB，包含约 47.9 KB thinking 与 13.1 KB `project.files.write` 参数；两条 Tool 回执合计约 23.6 KB。另一个 `draft` 峰值约 108.9 KB，也以历史消息为主。Column 初始 Assignment 中同时包含 `current_goal` 和 `task` 视图，某些简短字段和 `input` 有重复，但本次较大的增量主要是历史 thinking、文件写入参数和 Tool 回执；不能只归咎于初始 Assignment。

## 后续修正边界

优先保持“按需展示 Tool”机制，同时让展示状态与后续调用保持一致：明确 `tool.catalog.select` 的替换语义，并在执行边界处理本次未展示的 Tool 调用，给 Agent 可操作的重新选择指引。也可在选择 Task Plan Tool 时显式保留即将使用的 `workflow.create`；但仅靠提示词仍不能保证协议稳定。修改前应确定所需语义，再用真实 Provider 请求验证首次合法建图调用率以及请求体增长。本分析未修改代码或运行中的 Project。
