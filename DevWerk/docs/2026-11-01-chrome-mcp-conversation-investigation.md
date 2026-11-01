# Chrome 已打开后仍持续操作，聊天页长时间无反馈

Project `prj_f9058b9368004006b59d7b1669351797`，Job `cjob_4427773a32864835ab1527380ee3831a`，Run `arun_7a4220bff41f48b6a7de507bff86d726`。以下时间均为北京时间。使用只读 SQLite 查询，对照当前 MCP 配置、命令执行、工作记录和聊天页源码。没有重启服务或操作浏览器。

## 发生了什么

| 时间 | 已提交证据 | 判断 |
| --- | --- | --- |
| 10:45:04 | 用户要求现在打开 Chrome MCP 浏览器，记录端口和操作方式，把使用流程留到后续规划。 | 本轮交付是浏览器和连接说明。 |
| 10:45:26 | 工具 #9 通过 DevWerk 的 chrome_visible.list_pages 成功列出选中的 about:blank。 | 此时 MCP 已经能操作一个浏览器。只凭这一条不能判断 TCP 端口或屏幕上是哪一扇窗口。 |
| 10:45:26.588–10:52:23.050 | 同一模型批次还请求了 #10。PowerShell 在 AppData、LocalAppData 和用户主目录递归搜索配置文件，耗时 416.46 秒，以退出码 1 结束。期间无新的 conversation.progress。 | 单条范围过大的发现命令占整个 Job 约 57%。两个调用来自同一模型响应，因此看到 #9 的成功结果前，#10 已经被选入批次。 |
| 10:52:29–10:53:08 | #11–18 查进程、监听端口和调试参数，9210 属于其他应用；同时看到带 remote-debugging-pipe 的 Chrome 和指定 9223 的 MCP 客户端进程，探测 9223 当时未通。 | 多个进程代表不同连接方式。当前 DevWerk 的 chrome_visible 配置为 stdio，启动参数没有 browser-url。不能把别的 MCP 进程指定的端口归给 DevWerk。 |
| 10:53:33–10:54:11 | #19 用 Start-Process 启动 Chrome，当次能读 9223/version。#20 下一条命令返回连接失败文本，尽管工具外层 ok=true。#21 经 WMI 启动，#22–23 在后续命令里从 9223 读回页面。 | 第一次窗口出现和持续可连接是两个时点。DevWerk 的 Windows Job 在普通命令结束时回收子进程，与第一次端口消失一致。第二次是一次实际恢复。 |
| 10:54:28–10:54:38 | #24–25 写入启动脚本和说明；#26 再运行脚本，回报 9223 已监听、复用 PID 2668；#27 写项目记忆。 | 到这里已验证独立 CDP 端口跨命令存活，说明也已写入。后面再次调用脚本返回复用，不是每次都新起 Chrome。 |
| 10:54:43–10:57:19 | #28–48 共 21 次工具调用。#30、31、37、44 四次提交回复失败；#48 才成功。 | 最后约 2 分 41 秒主要耗在工作记录和回复验收。整个 Job 37 轮、48 个调用，最终 succeeded。 |

两条连接需要在最终说明中分开。DevWerk 的 MCP 调用验证了空白页，9223 的直接 CDP 调用验证了 LinkedIn 页。本次没有用 DevWerk 的 list_pages 再读到后者并匹配相同目标。原回复把内置 MCP 列为操作 9223 实例的首选，证据不足。登录状态也没有单独验证。原始产物保存在[操作说明](D:/workspace/linkedin-find-job/docs/chrome-mcp-browser.md)和[启动脚本](D:/workspace/linkedin-find-job/scripts/start-chrome-mcp.ps1)。

## 为什么迟迟不能结束

第一次工作记录更新提交了 Runtime 所有的 observation，遭到拒绝。随后 revision 1 虽将启动项标为 ready，但没有可供校验的检查回执。回复先遇到 WorkRevisionStale，引用版本后又遇到 WorkIncomplete。Agent 尝试删除既有项、填写不存在的回执 ID，又产生多次失败。

更关键的是，revision 2 把用户明确留待以后的 plan_browser_flow 放入本轮必做清单。revision 3 已经解决浏览器项，#44 回复仍因这一个未来项被拒绝。最后 Agent 在 revision 4 中把该项设为 ready，使用成功的 conversation.turn.inspect 回执说明“已提出待确认问题”，#48 随即通过。该回执证明读取了本轮状态，不能证明已与用户确认需求并排出流程。这是一个实际的假完成信号。当前 report 对整个快照调用 current_readiness()，而 update() 禁止静默删除旧项，两项规则共同放大了一次范围误判。[工作状态更新及核验](D:/workspace/DevWerk/DevWerk/app/v1/conversations/work_progress.py:21) · [最终报告校验](D:/workspace/DevWerk/DevWerk/app/v1/conversations/conversation_report.py:72)

## 为什么用户觉得没有反馈

数据库中仅有两条公开对话消息，即用户请求和 10:57:19 的最终回复。同时，事件表中有 122 条 conversation.progress，包含模型等待、工具请求和返回。聊天页的 changesConversation() 明确排除该事件，conversationStatusStrip() 在 running 时给固定说明。递归搜索执行期间本身又没有中间输出事件。因而后台有活动，聊天页仍可连续数分钟没有可理解的进展。最终回复已经落库，无法从服务端记录判定当时客户端是否收到最后一次刷新。[事件刷新判断](D:/workspace/DevWerk/DevWerk/app/web/static/dashboard.js:403) · [运行状态显示](D:/workspace/DevWerk/DevWerk/app/web/static/pages/projects.js:45)

## 建议拆成三个可独立验收的修改

| 优先项 | 文件与入口 | 修改及验收 |
| --- | --- | --- |
| 用户可见进度 | dashboard.js 的 flushProjectEvents()、projects.js 的 conversationStatusStrip()，以及 store.py 的 record_conversation_progress() | 按当前 Job 投影最新阶段和持续时间，使用有界、可展示的状态字段。长命令显示正在执行及已用时间。不要直接展示原始参数、进程列表或 stdout。验收用本次事件顺序，MCP 成功、后续检查运行中、完成三个状态均可见，提交按钮与最终消息各自保持正确。 |
| 明确连接对象并缩短探查 | [mcp_tools.py](D:/workspace/DevWerk/DevWerk/app/v1/tooling/mcp_tools.py) 的 MCPConfig 加载与 MCPToolManager.discover()、[命令输入协议](D:/workspace/DevWerk/DevWerk/app/v1/protocols/tools/workspace/capability_workspace_contracts.py:51)、[run_command()](D:/workspace/DevWerk/DevWerk/app/v1/workspace/process_runner.py:32) | 从受管配置提供脱敏的连接事实，区分 MCP 服务传输、浏览器目标及显式 CDP URL，未知项标为未知。Agent 成功列页后先记录已知连接方式，仅在确需 TCP 时定向验证。为探查提供受全局上限约束的短超时。若产品支持持久浏览器，应由有明确所有者和关闭机制的生命周期接口启动，保留普通命令的子进程回收。验收时分别测默认连接和显式端口连接，只有同一目标已验证才报告同一浏览器。 |
| 让当前工作与后续意向各归其位 | [工作记录协议](D:/workspace/DevWerk/DevWerk/app/v1/protocols/conversation/conversation_work_contracts.py)、ConversationWorkProgress.update()/current_readiness()、render_report() | 将“以后规划”保存在下一步备注；若已误建为本轮必做，允许有依据、留修订历史地纠正范围。更新协议只暴露 Agent 可写字段，校验失败返回具体 item/check 及待补证据。验收时真正缺少的本轮交付仍阻止完成，未来事项保持待办，不能靠任意成功的 inspect 回执把它算作已完成。 |

本次为历史运行诊断。未复现客户端实时显示，未检查 Chrome 当前是否仍在监听，未更改产品代码、项目文件或业务数据。

## 后续实现与复测（11 月 1 日）

本节记录上述诊断完成后的改动。

- 执行器在通过执行准入、即将派发单个工具时记录当前轮次和能力名称。聊天页只读取本项目当前 Job 的活动信息，独立更新状态栏与计时。长操作期间可持续看到已用秒数。最终消息到达时，即使输入框有焦点也能更新消息区。[工具执行](D:/workspace/DevWerk/DevWerk/app/v1/agents/agent_tool_execution.py) · [聊天页](D:/workspace/DevWerk/DevWerk/app/web/static/pages/projects.js) · [事件处理](D:/workspace/DevWerk/DevWerk/app/web/static/dashboard.js)
- 普通未来意向可写入 next_action。若误列为本轮工作，可保留原 item_id 并设为 deferred，附当前用户请求中表达延期的原文。运行时检查引文存在、仍有本轮工作、当期工作没有依赖延期项。报告将延期项列为“留待以后”。当用户在新 Job 中显式续接该项时，恢复为待做。引文语义由 Agent 判断，Runtime 核对来源和结构。正常更新的输入 schema 与带文件观测值的回执已分开。[工作协议](D:/workspace/DevWerk/DevWerk/app/v1/protocols/conversation/conversation_work_contracts.py) · [工作记录](D:/workspace/DevWerk/DevWerk/app/v1/conversations/work_progress.py) · [最终报告](D:/workspace/DevWerk/DevWerk/app/v1/conversations/conversation_report.py)
- MCP 工具的 describe 回执增加已配置的服务名与传输方式，只描述 DevWerk 到 MCP 服务的连接。项目及系统命令可用 timeout_seconds 缩短单次探查，受全局时限约束。平台指引要求定向检查已有工具、验证控制对象，完成当前交付后回复。进程托管能力没有纳入这次修改。[工具描述](D:/workspace/DevWerk/DevWerk/app/v1/tooling/tool_presentation.py) · [命令输入](D:/workspace/DevWerk/DevWerk/app/v1/protocols/tools/workspace/capability_workspace_contracts.py) · [平台指引](D:/workspace/DevWerk/DevWerk/DEVWERK.md)

隔离测试覆盖“现在写便签，以后规划发布”的完整 Gateway 调用、延期来源与依赖检查、产物变化后的验收、命令短超时、MCP 元信息。用 Node 调用聊天页渲染函数验证当前 Job 过滤、耗时、结束状态和原始工具内容隔离。真实页面、实际模型决策及原 Chrome 任务的耗时留给手动复测。

**实际回归结果。** 完整单测启动时收集 648 项，646 项通过，2 项失败。失败项分别是旧测试要求命令完全禁止超时参数，以及新增用例没有将测试 Job 置为 running。前者已更新为检查命令级时限与 Agent 运行预算的边界，后者已修正测试准备步骤。修正后的两项与同组检查合计 4 项重跑通过。另一次针对 API 页面、MCP 注册、命令和新 Gateway 场景的回归为 17 项通过。首次全量收集后新增的 Gateway 和真实命令短超时测试均已单独通过。没有把分批结果记为一次全量绿灯。

**手动复测。** 启动修改后的 DevWerk 并刷新项目页。新发一条“先完成 A，B 以后再规划”的消息；旧 Job 是历史记录。观察执行期间的阶段、当前工具与秒数，核对最终回复能否在 A 有真实回执后结束。对浏览器原任务，再核对回复列出的连接目标是否与实际控制页面一致。
