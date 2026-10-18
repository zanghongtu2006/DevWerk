# 通用 MCP 在 Kanban Column Agent 中的使用

## 已实现的路径

DevWerk 在启动时读取 `config/mcp-servers.yaml`，`app/v1/mcp_tools.py::MCPToolManager.discover` 将任何符合 MCP 协议的 server tool 注册为 `mcp__<server>__<tool>`。这层不认识搜索、酒庄或具体业务。`CapabilityRegistry.register` 更新 Workflow 作者看到的可委托能力目录，使动态 MCP tool 可以写入 `workflow.publish` 的 Column executor allowlist。Conversation Agent 通过 `workflow.plan.save`、`workflow.publish`、`task.plan.save`、`task.create` 完成编排；Task 到达 Agent Column 时，`WorkflowRuntime._execute_agent` 按该 Column 的 allowlist 给单个 Worker 提供工具。MCP 的 tool result 是普通工具回执，不是第二条 Agent 对话通道，也不经 Mailbox 传递。

浏览器模式由 MCP server **部署配置**决定，不由 DevWerk 的业务代码决定。同一个 Chrome DevTools MCP 包可以注册为有界面或无头两个 server，Agent/Workflow 选择对应的能力名。当前本机实测配置只启用一个不带 `--headless`、不带 `--isolated` 的 `chrome_visible` server，使用本机安装的 Chrome 和 MCP 的持久 profile；窗口可见。仓库示例另演示可选的 headless server。两种模式各有自己的浏览器会话，状态不会自动迁移。

## 一个 Search Column 的最小约定

这是 Workflow 可以选择的模式，**不是 DevWerk 内置的搜索流程**：

1. Conversation Agent 定义 Task 的精确输入（例如查询词、输出路径），并在独立 Search Column 的 Agent executor allowlist 中放入所需的通用 MCP browser tool 与通用文件工具。一个 Column 只有一个 Worker。
2. Column instruction 将 Task 输入视为不可替换的目标。它可以使用浏览器工具打开页面、输入和读取；若结果不相关，只能如实记录或报告失败，不能私自换查询后把另一组结果当成当前 Task 交付。
3. 对结构化网页内容，Column 可以调用通用 `evaluate_script` 从当前 DOM 提取 `{page_url, results:[{title,url}]}`。它写文件前应确认结果数量和来源，并在完成输出中记录本次 MCP tool call 的 ID。无法取得约定证据时走 Workflow 的失败/返工边。
4. Workflow 的终态由它自己声明的有向边控制。`done` 表示 Column 按协议提交了成功结果；它不能单独证明任意自由文本与网页一致。需要强保证时，Workflow 应声明可执行的通用验收检查，或由独立审查 Column 核实；不能为某个站点在 DevWerk 核心硬编码解析器。

## 黑盒证据与边界

2026-10-18 的最终最小测试 Project `prj_f73c10ba0e0547d283a38f1c62c42dd6` 中，Conversation Agent 没有调用 MCP，只发布单一 Search Column Workflow 并创建 Task。Column Agent 的 run `arun_fef3a35006cb4145a78d6f886c995ee3` 使用可见 Chrome MCP 搜索 Task 中的精确查询，最后一次 `evaluate_script` 的页面 URL 保持同一查询，三条结果标题与 URL 均和[交付文件](../data/projects/search-column-exact-query-20261018/search-results.md)逐项一致。`column.complete` 引用了实际 tool call ID，Task 经唯一出边进入 `done`。这证明 MCP 可以进入 Column Agent 的工具 loop 并由 Kanban 驱动完成。

首次测试也说明约束的必要性：Column 已调用 MCP，却把不在页面回执中的链接写入文件；第二次测试补了结构化读取后，文件与回执一致，但 Worker 为追求相关性擅自改了 Task 查询词。第三次将“查询词不可替换”和“结果必须来自同一 run 的回执”写入 Workflow/Task 约定，黑盒逐项对照通过。完整运行回执见[测试记录](archive/2026-10-18/2026-10-18-chrome-mcp-research-integration-plan.md)。

目前 MCP manager 和浏览器会话由 DevWerk 服务共享。一个 Project 的 `list_pages` 可以看到另一 Project 留下的标签页；虽然最终测试没有借用它们，此状态共享不等同于 Project 级隔离。如果未来需要多个 Project 同时操作同一个有状态 MCP server，应在**通用 MCP 会话生命周期**上按 Project/Worker 分配与回收连接，并保持可见浏览器选项，而不是为 Bing 或某个行业加入专用代码。本轮没有实施这项较大的会话管理变更，也没有把测试中的 Workflow 固化为产品预设。
