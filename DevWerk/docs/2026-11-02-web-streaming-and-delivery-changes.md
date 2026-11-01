# Web 对话流式交付与可审查文件清单

本清单覆盖 11 月 1 日和 2 日两次对 Chrome 复测问题的修改。工作树还包含此前 JobBench 与上下文建设等未提交工作。表中“2 日”仅指此次涉及的方法与改动，不能将文件中全部 Git diff 归给本次。

| 作用 | 源文件 | 修改时间 |
| --- | --- | --- |
| Web 对话与实时状态 | [dashboard.js](D:/workspace/DevWerk/DevWerk/app/web/static/dashboard.js)、[projects.js](D:/workspace/DevWerk/DevWerk/app/web/static/pages/projects.js) | 11 月 1 日、2 日 |
| Thinking 视觉样式 | [pages.css](D:/workspace/DevWerk/DevWerk/app/web/static/styles/pages.css) | 2 日 |
| 模型公开文本、工具回复字段的流式输出 | [provider_stream_contracts.py](D:/workspace/DevWerk/DevWerk/app/services/provider_stream_contracts.py)、[visible_stream.py](D:/workspace/DevWerk/DevWerk/app/services/visible_stream.py)、[agent_provider.py](D:/workspace/DevWerk/DevWerk/app/v1/agents/agent_provider.py) | 2 日 |
| SSE 断线游标 | [routes.py](D:/workspace/DevWerk/DevWerk/app/v1/api/routes.py) | 2 日 |
| 执行中的公开进展消息、后续轮次的历史过滤 | [agent_runner.py](D:/workspace/DevWerk/DevWerk/app/v1/agents/agent_runner.py)、[store.py](D:/workspace/DevWerk/DevWerk/app/v1/store.py)、[conversation_notification_contracts.py](D:/workspace/DevWerk/DevWerk/app/v1/protocols/conversation/conversation_notification_contracts.py) | 2 日 |
| 工具开始执行事件 | [agent_tool_execution.py](D:/workspace/DevWerk/DevWerk/app/v1/agents/agent_tool_execution.py)、[runtime_json_contracts.py](D:/workspace/DevWerk/DevWerk/app/v1/protocols/workflow/runtime_json_contracts.py) | 1 日 |
| 延期、证据与最终验收 | [conversation_work_contracts.py](D:/workspace/DevWerk/DevWerk/app/v1/protocols/conversation/conversation_work_contracts.py)、[work_progress.py](D:/workspace/DevWerk/DevWerk/app/v1/conversations/work_progress.py)、[conversation_report.py](D:/workspace/DevWerk/DevWerk/app/v1/conversations/conversation_report.py) | 1 日 |
| 工具说明与工作记录用法 | [builtin_tool_catalog.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/builtin_tool_catalog.py)、[DEVWERK.md](D:/workspace/DevWerk/DevWerk/DEVWERK.md) | 1 日；平台说明 2 日续改 |
| MCP 连接说明、目录注册与展示 | [tool_catalog_contracts.py](D:/workspace/DevWerk/DevWerk/app/v1/protocols/tools/core/tool_catalog_contracts.py)、[mcp_tools.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/mcp_tools.py)、[capability_registry.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/capability_registry.py)、[tool_presentation.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/tool_presentation.py) | 1 日、2 日 |
| 本机 chrome_visible 与可分发的配置示例 | [mcp-servers.yaml](D:/workspace/DevWerk/DevWerk/config/mcp-servers.yaml)、[mcp-servers.example.yaml](D:/workspace/DevWerk/DevWerk/config/mcp-servers.example.yaml) | 2 日；前者被 Git 忽略 |
| 单次命令超时 | [policy.py](D:/workspace/DevWerk/DevWerk/app/v1/policy.py)、[capability_workspace_contracts.py](D:/workspace/DevWerk/DevWerk/app/v1/protocols/tools/workspace/capability_workspace_contracts.py)、[capability_handlers.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/capability_handlers.py) | 1 日 |
| 编码配置在执行前校验，注册表与执行器复用同一逻辑 | [command_resolution.py](D:/workspace/DevWerk/DevWerk/app/v1/workspace/command_resolution.py)、[process_runner.py](D:/workspace/DevWerk/DevWerk/app/v1/workspace/process_runner.py)、[capability_registry.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/capability_registry.py) | 2 日 |
| 测试 | [test_work_progress.py](D:/workspace/DevWerk/DevWerk/tests/unit/conversation/test_work_progress.py)、[test_conversation_contract.py](D:/workspace/DevWerk/DevWerk/tests/unit/conversation/test_conversation_contract.py)、[test_project_supervision.py](D:/workspace/DevWerk/DevWerk/tests/unit/conversation/test_project_supervision.py)、[test_capability_contract.py](D:/workspace/DevWerk/DevWerk/tests/unit/tooling/test_capability_contract.py)、[test_files_contract.py](D:/workspace/DevWerk/DevWerk/tests/unit/tooling/test_files_contract.py)、[test_provider_contract.py](D:/workspace/DevWerk/DevWerk/tests/unit/tooling/test_provider_contract.py) | 两次修改合计；Provider 流式用例为 2 日 |
| 历史诊断与首次回归 | [2026-11-01-chrome-mcp-conversation-investigation.md](D:/workspace/DevWerk/DevWerk/docs/2026-11-01-chrome-mcp-conversation-investigation.md) | 1 日 |

## 此次复测的证据

11 月 2 日 07:34:00 至 07:36:45（北京时间），新项目 prj_06bb47c1328e41479f63b0c422f13d16 的 arun_0410c38eb74d4c4bb5dd16483e9cef37 使用了更新后的工具描述，20 轮、26 个工具调用。第 7 次 list_pages 已列出空白页。9223 已被另一项目使用。第 14 次用普通命令启动 9224，第 15 次检查连接失败，第 16 次又用 WMI 启动。07:35:58 已写入说明。第 22、23、25 次回复分别因缺 revision、未来项未延期、回执 ID 错而被拒，第 26 次成功。数据库中有最终答复，无法据此证明当时网页成功显示。三段面向用户的说明此前只存在内部 assistant 记录。

随后用户追问为何打开两个窗口。此轮失败于 PowerShell 调用使用 python_utf8。旧顺序先申请执行回执再校验解释器，从而把尚未执行的命令误判为结果未知。现在在申请回执前验证，Agent 收到参数错误后有机会纠正。

## 实现及边界

- 当前配置启用了 Anthropic 兼容流。一个用户 Job 的主模型请求在自身调用范围内订阅公开文本增量，以及 conversation.reply 顶层 message 的已解析前缀。流未结束时，服务端把最近的可见文本合并为有界预览，通过原有项目 SSE 发送。网页区分正在生成、工具状态和已通过验收的回复。Thinking 用浅灰底展示活动，聊天内容沿用原消息布局。中途与工具调用一同返回的公开说明会持久化为进展消息，独立于最终交付凭证和下轮历史。原始 thinking 块、签名和其他工具参数不进入该预览。
- 工具配置支持公开 target_access。已在本机 chrome_visible 和示例的默认可见会话声明 pipe 模式、端口不适用、使用 list_pages 验证后复用。其他工具可以描述自身访问方式。该信息指导模型，实际执行决策仍需真机验证。如果修改 MCP 启动参数，应同步更新操作说明。
- 非流式 Provider 仍按运行事件展示阶段，模型整段返回后显示公开说明。临时预览显示最近 2400 字符，正式回复取完整验收结果。实际可见延迟包含 SSE 轮询间隔和浏览器渲染时间。

## 验证

第二轮合并回归运行了 Provider 协议、命令工具、Web API、Conversation Gateway 及 stdio MCP 发现用例，92 项通过。测试在隔离数据库中检查模型尚未返回完整响应时已产生可见事件、执行中公开进展已进入对话、验收后只有一条正式回复，以及非法编码配置在创建执行回执前被拒绝。分片测试覆盖回复 message 尚未闭合时的预览、其他工具和非公开块的隔离。Node 渲染检查覆盖预览增量、HTML 转义、浅灰 Thinking 面板类和最终消息替换。未运行新的真实模型 Chrome 任务，未重启现有服务。
