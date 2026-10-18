# Chrome MCP 调研能力接入与黑盒复测计划（2026-10-18）

## 目标和边界

在当前分支验证 DevWerk 的通用 MCP 桥接能否连接真实浏览器服务，并由 Conversation Agent 与 Column Agent 通过 MCP 工具查找、读取公开网页，为南美小型酒庄调研产生可追溯证据。搜索引擎可以通过浏览器访问；本轮不接入需要密钥的搜索 API，不编写酒庄行业专用工具或流程判断。对外部页面采取只读操作，不发送邮件或提交表单。

本轮的“强制”是**测试验收约束**：只有 Agent Run 工具账本中出现成功的 `mcp__...` 工具调用，并且结果与交付物来源一致，才承认使用了 MCP。自然语言要求不能代替工具回执。若 Agent 改用 `project.command.run` 抓取，或仅在回复中声称搜索，测试记为未通过；不为了测试在通用代码中封死命令工具。

## 已知基线

- DevWerk 启动时从 `config/mcp-servers.yaml` 或 `DEVWERK_MCP_SERVERS_PATH` 读取服务器配置；`app/v1/mcp_tools.py` 把发现的 MCP tools 注册为 `mcp__server__tool`，可委托给 Workflow Column。
- 上次 Project `prj_58957bbf32d047398b8a12254a8e0ed9` 无 MCP 配置，因此能力目录没有 `mcp__*`。它用 `curl` 取得网页，但两轮规划都耗尽预算，未发布 Workflow/Task。
- 本机有 Node.js 22、Chrome，官方 Chrome DevTools MCP 当前 npm 版本为 `1.11.0`。使用固定版本、独立 headless 浏览器，不连接用户现有浏览器会话。

## 最小落地步骤

1. 在本地配置 Chrome DevTools MCP stdio server；仓库保存无密钥的示例配置与启用说明，本机实际配置作为运行环境文件。优先沿用现有 MCP bridge；只在真实连接暴露协议缺陷时修正桥接本身。
2. 用独立 MCP discovery/call 冒烟测试确认 `initialize`、`tools/list`、实际 `tools/call` 可成功；核对返回内容大小和来源 URL。
3. 因 MCP 仅在 DevWerk 启动时发现工具，配置生效时重启当前服务一次，确认 Project 能力目录含 `mcp__...`。重启只用于配置生效。
4. 建全新 Project，使用至少五轮自然语言讨论来源、覆盖和不确定性，授权 Conversation Agent 自行创建 Workflow/Task。明确本次必须经 MCP 浏览器访问搜索结果与来源页面；不代替 Agent 填写工具参数或看板定义。
5. 按五分钟间隔观察对话 Job、工具回执、看板和 Column Run。记录检索页面、候选条目的原始来源、市场份额或替代指标口径、公开商务联系人来源。无来源的字段标“未知”。

## 通过条件

1. MCP 服务被 DevWerk 发现，Project 能力目录出现浏览器 `mcp__...`，且实际工具调用成功。
2. Agent 用 MCP 浏览器访问搜索结果和至少一个原始公开来源；候选企业与页面内容可核对，不能只凭模型记忆或直接 `curl`。
3. Conversation Agent 发布 Workflow、创建 Task；Task 经至少一个 Column Agent 推进到终态，最终交付物引用实际来源。若无法完成，精确记录最早阻塞工具、参数、回执和看板状态，不把局部网页读取称为端到端成功。

## 回归范围

先运行 MCP bridge 现有针对性单元测试，再做真实浏览器冒烟测试和新 Project 黑盒测试。若无源码修改，不扩展到全套测试。测试过程和最终状态写入本文件或同日结果文档；服务保持运行。

## 接入与冒烟记录

- 添加无密钥示例 `config/mcp-servers.example.yaml`，固定官方 `chrome-devtools-mcp@1.11.0`，通过 Windows `npx.cmd` 启动隔离 headless Chrome，使用三个精简工具。实际 `config/mcp-servers.yaml` 为本机运行配置，已加入 Git 忽略；README 记载启用方式。未修改 DevWerk 的 MCP 桥接或行业逻辑。
- 本机 `MCPToolManager.discover()` 返回 `mcp__chrome__evaluate`、`mcp__chrome__navigate`、`mcp__chrome__screenshot`。经同一桥接实际调用 `navigate` 打开 Bing 搜索 URL，再用 `evaluate` 读取页面标题、地址和正文；两个工具均未返回 `isError`。这验证了真实 MCP 服务器链路，不代表搜索结果内容或业务调研已验收。
- `tests/test_capability_contract.py -k mcp`：5 通过。第一次执行因沙箱系统临时目录不可写，改用仓库 `data/pytest-mcp-tmp` 后通过；不是产品测试失败。
- 配置生效后通过仓库 `shutdown.bat` / `startup.bat` 重启一次，`/v1/health` 返回 `ok`；Project 能力目录有三个 `mcp__chrome__*`。新 Project `prj_9fae3fb7cdcd4a21a6d0ac4103e5ac30` 已创建，开始自然语言黑盒测试。

## 黑盒过程（进行中）

第一轮 Job `cjob_fece58513f234050bb41821f9fbafb67` 成功，Agent Run `arun_dfb551e5e37945278507c0a76a62f84c` 共 133 次工具调用，其中 129 次为 `mcp__chrome__*`，没有使用 `project.command.run`。浏览器确实打开 Google、Bing、DuckDuckGo 的搜索 URL、WOFA 酒庄名录、4 Gatos Locos 详情、Wines of Chile、OIV 等页面，并用 `evaluate` 读取 DOM。Google 导向反自动化页面；部分 Bing 结果不相关。Agent 的答复列出了来源，且有具体酒庄页面及 20 公顷文本的工具回执，但第一轮只完成来源讨论，没有 Workflow/Task。Agent 无端引入了“客户 Cordón”，第二轮已要求其删除该假设。

这证明通用 MCP 工具确实可以进入 Conversation Agent 的真实模型-工具 loop；同时也暴露单次简单来源讨论用了过多浏览器调用、搜索结果相关性需复核。后续轮次与看板测试尚未完成，不视为端到端通过。

第二轮 Job `cjob_1588fe97e5c04f54b882be04a25b6fe0` 以纯讨论完成，没有工具调用。Agent 承认“Cordón”是错误假设，并建议阿根廷、智利各一 Task；其回复错误地把“无可靠企业市场份额时填未知”概括成市场份额一律未知，且在没有用户阈值时提出人为阈值问题。第三轮已要求按来源核实规模，严禁把国家统计写成企业份额，由 Agent 自行说明阶段划分。

第三轮 Job `cjob_60f68060aaa04e4cbcba217475a5889b` 讨论成功，提出名录定位、候选筛选、详情核实、规模判断、联系渠道五个逻辑步骤，并说明国家统计不可写入企业市场份额。它曾试图在讨论轮写 Memory，被正常拦截。第四轮已告知“页面名称”不能替代可打开的来源 URL，也不要求恰好五个 Column；要求其给出简短最终轮廓，不再调用工具。

第四轮 Job `cjob_cf9c1015fecb438299b44a2187c0a2c6` 成功，以发现、逐家核实、整理交付三个阶段描述一个 Workflow 和两个国家 Task。第五轮 Job `cjob_391100f8b9e942e3935824ddceff79f0` 已明确授权创建、发布、启动，并要求所有外部来源操作通过 Chrome MCP；待检查工具账本和看板。

### 第五轮阻塞与最小通用修复

第五轮未创建 Workflow/Task。第一轮浏览器取证时，`conversation.execution.authorize(request=begin)` 创建了 Requirement `req_8bd5b0885b7949c48365b4275caaf81a`，其目标仅为来源讨论；此时 Project 尚无 Workflow。第五轮扩展到正式交付时，Agent 使用 `request=extend` 获得 `pending_scope_revision` grant。当前 `conversation_intent_repository.py::denial` 只允许 `project.scope.revise` 激活该 grant；但 `ScopeRepository.revise` 无条件调用 `get_workflow`，空看板抛 `no active workflow`。工具 schema 又要求 `expected_workflow_revision_id` 必须为字符串，不允许空看板的 `null`。因此形成真实循环依赖，不是 Agent 重试可以解除的旧状态残留。

修复限于通用“首次 Workflow 发布前扩展 Requirement”路径：

1. `app/v1/capabilities.py` 的 `project.scope.revise` 参数允许 `expected_workflow_revision_id: null`，并在描述中说明空看板时只修订 Requirement、不会发布 Workflow。
2. `app/v1/repositories/scope_repository.py::revise` 在当前 Workflow 不存在且预期 revision 为 `null` 时，只原子更新 Requirement revision/objective、记录快照和事件，激活现有 execution grant；拒绝此路径的 Loop binding patch/digest。已有 Workflow 的发布逻辑保持原样。
3. `tests/test_scope_extension.py` 增加真实的 begin→尚无 Workflow→extend→scope.revise→workflow.plan.save/publish 回归测试，并确认 grant 从 pending 转为 active。之后重启服务，用**同一个** Project 的后续自然语言轮次复测；无需清理数据库。

实现已按上述最小范围完成。针对性 scope 测试 3/3 通过，MCP bridge 测试 5/5 通过；真实 Chrome MCP `navigate/evaluate` 冒烟通过。较宽的 scope 测试在与本次修复无关的 gateway 测试处长时间无进展，已停止该测试进程，不影响这组针对性结果。下一步只做必要服务重启与同一 Project 的自然语言恢复验证。

### 动态能力目录与双浏览器模式（修复前方案）

同一 Project 的恢复轮次成功完成 `project.scope.revise` 与 `workflow.plan.save`，但 `workflow.publish` 连续拒绝 Column 使用 `mcp__chrome__*`。`app/v1/capabilities.py::build_core_registry` 在核心能力注册完成时把 `workflow.publish` 的 `AgentExecutor.capabilities` 与 `CapabilityStep.capability` 枚举绑定为当时的能力目录；`app/main.py` 随后才调用 `MCPToolManager.discover` 注册外部工具。因此 Conversation Agent 能调用 MCP，Workflow schema 却不承认它。这是任何动态注册能力都会遇到的通用缺陷，与调研场景无关。

最小修复：在 `CapabilityRegistry.register` 接纳新的可委托能力时同步刷新既有 `workflow.publish` schema 中的两处能力枚举；沿用现有 `_bind_workflow_capability_catalog`，不新建另一份能力目录。用现有 MCP fixture 增加回归断言，确认发现工具后两处枚举都包含外部能力，并能通过 `workflow.publish` 参数校验。保留既有 Workflow 运行逻辑及 Column 边界。

浏览器模式是部署配置而非 DevWerk 业务功能。示例配置提供两个标准 Chrome DevTools MCP server：`chrome_headless` 使用 `--headless --isolated --slim`，`chrome_headed` 使用 `--isolated --slim`（官方默认有界面）。两者通过同一通用 MCP bridge 暴露独立工具名；Agent 可以在对话或 Column 中按需要选择，浏览器会话彼此独立，不自动迁移 Cookie/页面状态。README 说明选择方式。本机配置同步为两个 server，分别执行真实 `navigate/evaluate` 冒烟测试，再重启 DevWerk 复测 Workflow 发布及 Task 推进。此变更不增加任何调研专属能力、规则或路径。

实现与验证：`CapabilityRegistry.register` 在新可委托能力注册后更新同一 Workflow schema 的能力目录；MCP fixture 针对两种 Column executor 引用的回归断言通过。`tests/test_capability_contract.py -k mcp` 5/5、首次 Workflow 之前的 scope 与现有 Workflow scope 两项回归 2/2 通过。真实 `chrome_headless` 和 `chrome_headed` 都发现 `evaluate/navigate/screenshot`，各自 `navigate` 到 `https://example.com/`、`evaluate` 读出 `Example Domain` 和实际 URL 成功。第一次冒烟脚本误传 `function` 而非工具要求的 `script`，两个服务均正确拒绝；脚本改正后成功，未对 DevWerk 加兼容分支。沙箱内的 npx 初始化超时，正常本机进程权限下双实例在约 24 秒完成，因此实测使用后者。重启后 Project 能力目录出现六个工具，服务健康；现有 Project 恢复 Job `cjob_0c10da61944044f58a0f48301dacfaf4` 已提交，等待五分钟间隔检查实际发布及执行。

### Task 输入指针协议阻断（修复前方案）

五分钟检查确认该 Job 已成功发布 Workflow `wfrev_6fbcd6bd205c4efd8d1198c0be59da39`，证明动态 MCP 能力现在可被 Column 引用。但 `task.plan.save` 拒绝：`Task input cannot resolve identity pointer '/input/task/input/country'`；Conversation Agent 未修改参数而重试，被已有重复失败保护停止，故无 Task。该完整指针来自之前 `workflow.plan.save` 成功保存的 `task_contract.identity_pointer`，同一计划的 `dependency_contract.order_pointer` 也使用完整运行时形式。`task_identity.resolve_input_pointer` 实际只接受相对于 Task input 的 `/country`。模型使用了其它工具常见的完整运行时引用写法，Workflow Plan schema 却允许它保存，直到 Task Plan 阶段才报错；根因是通用指针表示不一致，不是酒庄数据或 MCP 特例。

最小修复：`app/v1/domain.py` 对 Task-input 指针字段在模型验证时把 `/input/task/input/...` 规范化为相对的 `/...`，与现有 `ExactTaskInputString.pointer` 行为一致；包括 identity、依赖排序和 entry evidence 三个同样交给 Task-input resolver 的字段。相对指针原样保留。`tests/test_task_entry_admission.py` 增加完整形式 identity 指针的真实 Task Plan 保存与 materialize 回归，并验证依赖排序字段同样规范化。随后重启服务，使已保存的旧计划经 `WorkflowPlan.model_validate` 读取时获得规范化结果，再以自然语言要求 Conversation Agent 继续创建两个 Task。不会改写既有数据库记录或手工创建 Task。

### 用户要求核验可见 Chrome（暂停后续开发）

先前双模式冒烟的 MCP 回执只能证明浏览器协议操作成功，不能单独证明有头窗口在用户桌面实际存在。另起一次独立的 `chrome_headed` MCP 调用并保持浏览器打开 45 秒：`navigate` 打开 `https://example.com/`、`evaluate` 返回 `Example Domain`；同时 Windows 进程表确认新 Chrome 主进程 PID 37332，执行文件为本机 `C:\Program Files\Google\Chrome\Application\chrome.exe`，独立临时 profile，启动命令含 `--remote-debugging-pipe` 且不含 `--headless`；`MainWindowHandle=459306`，窗口标题 `Example Domain - Google Chrome`。因此真实本机 Chrome 的可见窗口确实被 MCP 启动过；该独立验证结束后窗口已关闭。它可能未抢占用户前台，所以用户未见明显反应。

本轮 Project 尚无 Task，因此还没有 Column Agent 实际调用浏览器。先前的 129 次 MCP 调用属于 Conversation Agent 且配置为无头模式；最新恢复轮次发布 Workflow 后，因 Task 指针问题停在 `task.plan.save`。不能把本机独立 MCP 冒烟或 Conversation Agent 的调用写成 Column 已经使用有头 Chrome。按用户要求暂停后续开发与黑盒推进，等待讨论。

### 可观察 Bing 搜索最小编排（执行前方案）

用户现要求先单独验证 Conversation Agent 能启动可观察的本机 Chrome 并操作 Bing。暂不推进旧 Project 的 Task/Column，也不借此证明 Column 浏览器能力。只把本机忽略的 `config/mcp-servers.yaml` 改为**一个** `chrome_visible` 标准 Chrome DevTools MCP server：固定同一 npm 版本，省略 `--headless`、`--isolated` 和 `--slim`；因此使用官方默认的有界面、持久浏览器配置和完整浏览器工具。绝不传入用户现有 Chrome profile 路径，以避免正在运行的个人浏览器配置冲突；这仍是电脑本地安装的 Chrome，不是无头/临时隔离实例。仓库的通用 MCP 代码与示例配置不因 Bing 增加特例。

先独立确认此配置发现并调用成功，再为配置生效必要地重启 DevWerk。建一个全新最小 Project，仅通过自然语言让 Conversation Agent 打开 Bing、输入一个明确搜索词并读取搜索结果标题/URL。证据必须同时满足：该 Job 的 MCP 工具账本有真实成功回执；Windows 进程显示新启动的本地 Chrome 主进程不带 `--headless`、窗口句柄非零且窗口标题属于 Bing；Agent 的读取结果含实际 `bing.com` 页面 URL。若 Bing 验证/反自动化阻断搜索结果，应如实记录页面状态，不能声称完成检索。测试时保留可见窗口供用户观察，不因测试结束主动关闭 DevWerk 或浏览器。

实测结果：本机配置只保留 `chrome_visible`，参数仅为固定包版本，未加 `--headless`、`--isolated`、`--slim`；标准 MCP discovery 得到 32 个通用浏览器工具。新 Project `prj_140280872d9d4d41883ffc8e73d9de66` 的 Conversation Job `cjob_b8eb932db3a64438b7490cf056753f4a` 成功。工具账本共 29 次调用，其中 `new_page` 真实打开 `https://www.bing.com/`，`take_snapshot` 读取页面，`fill_form` 因页面控件不可交互失败后，Agent 使用通用 `evaluate_script` 找到 Bing 的 `input[name=q]`、设置查询词并提交表单；MCP 回执报告页面实际导航到 `https://www.bing.com/search?q=Chrome+DevTools+MCP+official+documentation&form=QBLH`。后续 `evaluate_script` 从结果 DOM 读到前三条标题和 Bing 跳转链接，Agent 还点击第一条打开来源后重新选回搜索结果页。失败的首次 `new_page`、`click`、`fill_form` 及等待调用未被记为成功。

启动前 Windows 只有两个既有 Chrome 主进程。Job 结束后新增 PID 11308，执行文件是本机安装的 `C:\Program Files\Google\Chrome\Application\chrome.exe`，命令行没有 `--headless`，`MainWindowHandle=4657452`、窗口标题为 `Chrome DevTools MCP official documentation - 搜索 - Google Chrome`。默认持久 profile 位于 `C:\Users\hongt\.cache\chrome-devtools-mcp\chrome-profile`，不是 `--isolated` 临时 profile，也不是用户正在使用的个人 Chrome profile。已对该已核实窗口调用 Windows `ShowWindow`/`SetForegroundWindow`，两项可见与前台请求均返回 true。窗口和 DevWerk 服务保持运行，未主动关闭。本次证明 Conversation Agent 经通用 MCP 操作可见 Chrome 完成 Bing 搜索；按用户本轮范围，未创建 Workflow 或 Task，故不宣称 Column 浏览器能力已通过。

### Search Column 使用通用 MCP 的下一轮最小验证（执行前方案）

用户现要求将浏览器操作放入 Kanban 的独立 Search Column。使用当前同一个 `chrome_visible` 通用 MCP 服务，新建干净 Project，由 Conversation Agent 通过 `workflow.plan.save`、`workflow.publish`、`task.plan.save`、`task.create` 自主规划一个极小 Workflow：Search Column 由单个 Column Agent 执行，能力 allowlist 包含所需 Chrome MCP 工具和写交付物的通用文件工具；Task 指定一个 Bing 查询词和交付路径。Column 自己打开 Bing、提交搜索、读取实际结果并写带 URL 的结果文件，然后按 Workflow 有向边结束。测试只规定可观察的任务目标，不手工替 Agent 填 Workflow/Task 参数，也不为搜索添加 DevWerk 专属工具、状态或验收代码。

通过条件：工具账本显示 Workflow/Task 确由 Conversation Agent 创建；Column Run 的 agent_run_id 下出现真实成功的 `mcp__chrome_visible__*` 调用，搜索页 URL 与结果回执一致；结果文件可读且 Task 进入 Workflow 终态。窗口必须仍是本地可见 Chrome。若 Column 缺少工具、MCP 会话冲突、结果虽写但无法推进或终态判断错误，则按回执、DB 与代码定位通用缺陷，先补本节具体方案，再做最小代码修复及回归。当前这一个 Search Column 是测试 Workflow 的选择，不写入产品默认流程；MCP 仍是可选、通用能力。

首次 Search Column 测试 Project `prj_3291a4ac0b4d4983af2a56fc7d578b3c`：Conversation Agent 成功发布仅有 `search` 的 Workflow `wfrev_da133a490f734bf486b3906bf6d62331`，`task.create` 创建 Task `tsk_6f85985ee93c46da814ee7bc464ccd3c`，Column Run 的 Agent `arun_7e22290c687c4cfda0e40d1b81404cc1` 有 8 次成功 MCP 调用、一次成功 `project.files.write` 和一次成功 `column.complete`，Task 进入 `done`。本机 Chrome PID 11308 仍有非零可见窗口句柄，未运行无头模式。故**MCP 到 Column 的路由与状态机推进已经实测通过**。

但**内容验收失败，不能把这轮算作完整交付**。Column 新开 Bing 搜索页后先遇到 Cloudflare 真人验证，随后 `wait_for` 回执确实包含 Bing 搜索结果；其前三条可见标题是 ChromeDevTools 官方 GitHub 仓库、Chrome for Developers 入门页、该仓库 README。写入 `search-results.md` 的前三条却是另一个 MCP 网站、`modelcontextprotocol/servers` 和 OSCHINA 页面；三条 URL 均未出现在该 Column 的 MCP 工具回执中。Column 的工具 allowlist 没有 `evaluate_script`，只靠页面 snapshot；Column instruction 仅写“提取前三条”，没有明确逐条复制回执中的标题/URL。Workflow 的 output_contract、acceptance_checks 也均为空。运行时按声明的 `success -> done` 合法推进，但不能证明内容真实。此处是该 Workflow/Column 的证据契约不足与 Agent 事实错误，不是 MCP 传输失败；不能加入搜索行业专属校验代码。

下一步先用**工作流配置**修正并重新测试：请 Conversation Agent 针对该证据缺口，自行更新独立 Search Column 的能力 allowlist，使其可用通用浏览器 `evaluate_script` 读取结构化结果；在 Column instruction/输出约定中明确每条标题、URL 必须逐字来自本次 Column MCP 回执，记录搜索页 URL 与引用的 tool call，取不到结果就提交失败/返工而非猜测。新 Task 仍由看板推进。先检验这种通用证据约定是否足够；只有观察到同类协议缺陷且工作流无法表达时才考虑最小通用代码修改。

新 Project `prj_9840ed6848f64013aea4c69fc324420d` 的首轮 Job `cjob_24f07829e83944b68f9bb222cb18f65d` 停在 Workflow 发布前；尚无 Task/Column 调用。Conversation Agent 成功保存了 Workflow Plan，`workflow.publish` 的三次不同尝试依次因多余 `steps` 属性、无效的 `timer` wait_policy、以及 Column 顶层 `input_contract` 声明了实际上只位于 `task.input` 下的 `search_query`/`output_file` 而被拒。最后它重复第三次失败参数，被现有重复失败保护停止。三次错误均给出了精确字段；本轮先把回执作为自然语言反馈送回同一 Project，让 Agent 修正声明并继续，不为单个模型参数错误加容错代码。若它仍无法按协议发布，再判断通用 schema/错误引导是否需要改进。

同一 Project 的纠错轮 Job `cjob_794c85ef77d1466297a1fc9742b80842` 成功发布 Workflow `wfrev_405d8909081e4627a81dce667c1d6cc2`、创建 Task `tsk_d02453d465e846ebacfd7b5746922b7e`，由独立 Search Column Agent `arun_4fcf3c82112c4dff9e9124a259b64893` 执行到 `done`。该 Column 确实调用 `type_text`、两次 `evaluate_script`、写并复读 `search-results.md`。第二次 `evaluate_script` 返回三条 Bing 页面结果，逐条 title/URL 均与文件一致，证明添加通用结构化页面读取与证据约定能够修复上一轮的“文件链接不在工具回执中”。

不过这轮仍**不算目标完全通过**：Task input 的 `search_query` 是 `Chrome DevTools MCP configuration official`，Column 先执行该词，看到前三条结果偏向一般 Chrome 产品后，自行导航到 `Chrome DevTools MCP official documentation` 并按后者的结果写文件、提交 `done`。Agent 消息明确说明它是为了“更相关”而主动改词，不是 MCP 故障或跨 Project 页面误读；Workflow instruction 只要求搜索关键词、保留来源，却没有强调 Task 输入的查询词不可自行替换。当前 MCP 服务在全局浏览器会话中会列出其它 Project 留下的标签页，这是可观察到的跨 Project 浏览器状态暴露，但本次改词的直接原因有 Agent 自己的明确说明，不能把两者混作同一根因。

下一步只改**测试 Workflow 的通用任务边界**再复测：一个独立 Search Column；允许 `evaluate_script`；把 Task 输入的查询词定义为不可替换的精确输入，若结果不相关也要如实记录，不得优化、改写或借用其它标签页；结果文件的页面 URL 必须对应这个确切查询，三条 title/URL 必须和同一 Column 的结构化 MCP 回执一致。取不到三条时走失败路径，不允许以另一搜索词补救后标记完成。先通过新 Project 自然语言让 Conversation Agent 建立上述契约并看板执行；不在 DevWerk 添加搜索业务规则。全局 MCP 浏览器会话的跨 Project 标签可见性另外记录为通用会话隔离问题，当前单 Column 测试不实施大范围生命周期重构。

第三轮 Project `prj_f73c10ba0e0547d283a38f1c62c42dd6` 达成这次最小目标。Conversation Agent Run `arun_fdc5bf34763b4dceb2b047050a2dcede` 没有 MCP 调用，只通过通用工具保存 Workflow Plan、发布唯一 Search Column Workflow `wfrev_e3b3c28d501f4d0792e73b8cb5fcef46`、保存 Task Plan、创建 Task `tsk_b990d1833dad4b0190a576d315c58038`。同轮有三次 `workflow.publish` 参数被拒，但 Agent 改正后成功，未卡死。Task 的原始输入查询为 `Chrome DevTools MCP configuration official`。

Search Column Agent `arun_fef3a35006cb4145a78d6f886c995ee3` 有 9 次成功 `mcp__chrome_visible__*` 调用：列出页面、新开实际 Bing 查询页、等待加载、六次通用 `evaluate_script` 逐步提取结果；没有使用命令行 HTTP。最后一次结构化读取的 `queryUrl` 中 `q` 参数与 Task 输入完全一致，返回三条结果。`project.files.write` 生成的 `search-results.md` 包含同一个查询页 URL；三条标题和 URL 均与该次 MCP 回执逐项相同；`column.complete` 输出引用了实际 `evaluate_script` tool_call_id `call_function_drqlz7e9ouqv_1`。Task 依 Workflow 的 `done` 出边到达终态。可见本机 Chrome 主进程 PID 11308 保持窗口句柄非零、标题为本次 Bing 搜索页且无 `--headless`。Bing 对该查询返回的是三个一般 Chrome 页面；Column 如实记录，没有再为相关性私自改词。交付文件：[search-results.md](../../../data/projects/search-column-exact-query-20261018/search-results.md)。

因此本轮证实了 **Conversation Agent 编排 → 单 Agent Search Column 领取通用 MCP 工具 → 可见 Chrome 搜索 → 精确来源交付 → Workflow 终态** 的完整最小链路。边界也很明确：当前运行时不会机械比较任意交付文本与 MCP 回执，本次逐项一致性由黑盒验收脚本核对；通用浏览器 MCP 进程仍由服务共享，多个 Project 的标签页可以相互可见。本轮未把搜索结果校验或浏览器会话隔离写成产品内的场景专用代码。
