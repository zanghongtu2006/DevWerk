# Anthropic 兼容 Provider 流式传输 P0 修复方案

## 现场证据

- 小说回归 Project `prj_10e8f109f1a649efa7525978d47f9475` 的第 4 章 Task `tsk_219249ee787948c8b7f4e32e7feb6bf5` 在 `revise` 列连续发生三次 `LLM_TIMEOUT`（每次非流式请求读等待 600 秒），另有一次 MiniMax `722 (1000)`。Task 已进入 `recovering`，WIP=1 阻止后续两章启动。
- `app/services/anthropic_client.py::complete` 当前调用 `requests.Session.post(..., timeout=600)`，整段生成完成前没有接收正文。错误发生于 Provider 请求等待，未进入 Column Tool 或 Kanban 状态机。
- 既有独立重放 `docs/2026-10-23-minimax-stream-timeout-demo.md` 对三份历史超时请求只增加 `stream:true`，三次均收到完整 `message_stop`；这支持流式传输可减少该类整包等待超时，但不保证解决 Provider `722` 或全部请求故障。

## 责任与边界

- Provider transport 负责 SSE 收发和完整消息组装；Agent、Workflow、Column、Task 状态机保持不变。
- 以通用 Anthropic 兼容协议提供可选流式模式，由模型配置显式启用；不按小说、Column 名称或 MiniMax 特例分支。现有非流式模式继续可用。
- 每个 SSE 事件由独立 typed contract 校验；组装完整内容块、usage 和 stop_reason 后才交给原有的响应解析。只有收到 `message_stop` 才提交结果。中途断流、错误事件、非法 JSON 或总时限到达一律失败，不执行不完整 Tool Call。
- 不修改既有 Project 的 Task 状态，不用重启补救历史 Run；修复后用新 Project 复测。现有服务保留，重启加载代码前按端口规则取得用户决定。

## 代码落点

1. `app/core/config.py::LLMModelConfig/AgentModelConfig/ApiProfile` 与 `app/core/llm_config_contracts.py::ProviderClientConfig`：增加 `stream_response` 布尔配置，默认 `false` 保持旧模式；写入 `config/llm.example.json` 的 MiniMax 示例与本机 `config/llm.json`，只通过配置启用。
2. `app/services/provider_request_contracts.py::AnthropicCompletionRequest`：typed `stream` 字段，按模式序列化；不在非流式请求中额外发送该字段。
3. 新建 `app/services/provider_stream_contracts.py`：SSE 事件类型、内容 delta、消息结束与 usage 的 typed contract 及聚合器。聚合器接收完整事件序列并生成现有响应解析所需的内容结构；不可从不完整事件生成 Tool Call。
4. `app/services/anthropic_client.py::complete`：复用原参数与响应解析；流式模式使用 `stream=True` HTTP、逐行读取、总时限 `request_timeout_seconds`、完整终止检查、`response.close()`。非流式分支保持行为。记录结构化状态和错误，不额外打印 Web 调试信息。
5. `tests/unit/tooling/test_provider_contract.py`：覆盖 text/thinking/tool_use 的完整 SSE、跨多个 `partial_json` delta、usage、错误事件、未收到 `message_stop`、传输超时、非法 JSON；并确保失败时无部分 Tool Call 输出。运行 Provider 契约与相关 Runtime 恢复测试。

## 回归判定

- 单测证明字节流被正确组装，非流式旧契约保持通过。
- 真实 Provider 在新 Project 中能完成同类小说工作流，不再因 600 秒整包读取重复耗尽恢复次数；若仍失败，记录 SSE 事件时序、错误码、请求大小并重新定位，不把 Provider `722` 误认为已修复。

## 已完成的独立验证

- `tests/unit/tooling/test_provider_contract.py`：31 passed；含完整 thinking/text/tool_use SSE、多片 `partial_json`、usage、错误事件、断流与超时。
- 同配置测试 `tests/unit/config/test_global_settings_contract.py` + Provider 契约：45 passed。
- 取本轮第 4 章 `revise` 连续超时中的原始请求 `llm_f75fd861719744ba97ab84a6e5f3023d`，只增加 `stream:true` 后通过新 `AnthropicClient._stream_complete` 重放。真实 MiniMax 返回 HTTP 200、完整 `message_stop`、thinking/text/tool_use 三块、`stop_reason=tool_use`，usage 为 32,011 input / 25,167 output tokens。返回的 Tool Call 未执行。此结果验证新解析器能完成这一份曾经非流式超时的请求，但不是对所有请求的成功率保证。
- 加载代码后新建 Project `prj_60c65ad9f77e45f18febe8a565a6aa76`，自然语言请求用看板交付前两章。Conversation Job `cjob_4aaf6d2676164c849ef0ef3d5da4bc0f` 成功，创建两项 Task，首项运行。此 Project 截至首个五分钟检查有 46 次真实 Provider 请求，日志中的请求载荷全部为 `stream:true`；这是端到端启用证据，终态仍需继续观察。

## 配置发布时序造成的附加 P0

- 在旧进程尚未重启时先写入 `config/llm.json` 的 `models.main.stream_response=true`，旧进程每次模型请求重新读取并用旧 `LLMCatalog` 校验该文件，因此 Project `prj_10e8f109f1a649efa7525978d47f9475` 的监督 Job `cjob_045367cc191e4db497127340044200c4` 被 `extra_forbidden` 终止。这是本次修改的发布顺序错误，也是运行中配置被重复解析的生命周期缺口；当时第 4 章 Task 已因多次 Provider 超时进入 `recovering`，随后暂停的监督未能恢复它。重启后的新进程已接受该字段。
- 最小通用修复：`app/core/config.py::Settings.llm_config` 在同一 Settings 实例上缓存已校验的 `LLMCatalog`，运行进程保持启动时的模型配置快照；显式 `reload_settings` 或服务重启才读取新文件。返回深拷贝，避免调用方改变快照。对运行中的模型配置变更不做热更新；`global-settings.yaml` 的既有设置机制不受影响。
- 新增焦点测试：同一个 Settings 实例读配置后修改文件，继续得到旧值；调用 `reload_settings` 后得到新值。这样以后代码和配置分步部署时，旧进程不会在一次请求中遇到尚不认识的新字段。

## 配置错误处理与后续回归

- `app/core/config.py::Settings.llm_config` 已改为进程内已校验快照；`reload_settings` 创建新 Settings 才重新读取。包含深拷贝保护的焦点测试通过，Provider 契约总计 32 passed。服务当前已经加载支持 `stream_response` 的代码和有效配置；快照改动本身尚待下次必要重启加载。
- 对受影响 Project 的用户轮 `cjob_f68155c185a843b9ba47f3731b989d51` 已完成：Agent 检查旧 Task 与证据，先误试 `task.resume` 被明确拒绝，再成功调用 `task.retry`，第 4 章从 `recovering` 返回 `running@revise`。因此先前失败的自动监督未被假装成成功，而是通过新的授权用户轮真实恢复；最终终态仍需观察。
- 新流式 Project `prj_60c65ad9f77e45f18febe8a565a6aa76` 的两项样章 Task 均已到 `done`。首轮 `workflow.create` 经八次不合法参数后第九次成功，说明建图参数稳定性仍低；这是可恢复的调用错误，不是本次 Provider 超时 P0。需在后续独立工作中继续测量，不把流式修复当成建图稳定性修复。
