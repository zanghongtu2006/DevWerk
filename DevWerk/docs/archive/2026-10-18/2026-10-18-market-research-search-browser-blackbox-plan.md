# 南美小型酒庄调研：搜索与浏览器能力黑盒验证（2026-10-18）

## 目标

从 `main` 建立独立分支，启动当前 DevWerk 服务，创建全新 Project。通过自然语言与 Project Conversation Agent 讨论南美各国小型或不知名葡萄酒庄/酒厂的调研方法，再授权其建立 Workflow 和 Task。观察 Task 是否能由看板推进，并实际取得可核验的外部资料。

## 用户需求与验收边界

- 从公开搜索结果或企业/行业名录寻找候选酒庄，逐一查证企业身份与所在国家。
- 调查可公开验证的市场份额或可替代的规模指标；资料不足时明确标记未知，不编造份额。
- 查找公开的企业联系渠道及负责人或市场推广人员的公开职务联系方式；不要求私人联系方式，也不发送消息。
- Conversation Agent 自行选择流程与工具，不由测试者替它编写 Workflow 参数或决定搜索方式。
- 本次重点检查搜索引擎与浏览器/MCP 能力是否在 DevWerk 的实际能力目录中可用、被正确调用、返回可追溯来源，并形成可交付结果。失败时记录实际 Project、Task、工具回执、日志与代码/配置原因。

## 测试步骤

1. 检查服务与实际能力目录；不终止已有服务。服务未运行时按现有启动入口启动。
2. 创建全新 Project，通过至少五轮自然语言讨论调研边界、资料来源与验证方法；明确开始后观察 Workflow/Task 创建。
3. 按五分钟间隔观察看板推进及 Column 工具回执，不通过外部脚本替 Agent 搜索、写结果或手工推进 Task。
4. 检查交付物的候选酒庄、来源 URL、规模证据和公开联系渠道；区分搜索/浏览器实际取证与模型无证据陈述。
5. 如任务在能力、规划或执行阶段失败，保留失败状态并记录最早阻塞点、可复现证据和影响范围，不把缺失资料写成成功。

## 基线观察（执行前）

源码包含 MCP 通用桥接模块；启动配置路径默认为 `config/mcp-servers.yaml`。当前工作区未发现该配置文件，核心能力注册中搜索仅见本地文件和项目记忆搜索，尚未看到内建互联网搜索或浏览器能力。此处仅为代码/配置预判，最终以新 Project 的实际能力目录和执行回执为准。

## 执行记录与初步结果

- 分支：`codex/market-research-search-browser-test`，从 `main` 的 `8fea734` 建立；未修改 DevWerk 源码。
- Project：`prj_58957bbf32d047398b8a12254a8e0ed9`，目录 `data/projects/south-america-winery-research-20261018`。原服务不在 8000 端口，使用现有 `startup.bat` 启动；此后没有终止服务。
- 实际能力目录有 `project.command.run`，没有 `mcp__*`、浏览器或互联网搜索能力。工作区没有 `config/mcp-servers.yaml`；Codex 自身的 Chrome 工具不会自动注册到 DevWerk。
- 前三轮自然语言讨论完成，但没有实地检索或创建 Workflow/Task。第四轮授权其自行验证网络并开始小样本工作。
- 第四轮 Agent Run `arun_11b95ac68ab04b46bc6b940b1c730076` 确实使用 `project.command.run` 中的 `curl` 访问阿根廷 Bodegas de Argentina 与智利 Wines of Chile 公共页面。因此本轮外部取证路径是**命令行 HTTP**，不是搜索引擎，也不是 MCP Chrome/浏览器。
- 它在项目目录写出阿根廷页面和 `argentina_wineries.json`（202 条抽取记录）。原始页面和 URL 可以核对，但抽取样例出现明显异常邮箱和残缺电话，不能将 202 条直接认定为有效企业/联系方式，更没有市场份额证据。
- 第四轮在单次 Conversation Agent Run 中累计 162 次工具调用，最终 `ExecutionBudgetExceeded: Agent iteration/tool-call ceiling reached`。一个 Workflow Plan 已保存（`wfplan_eb37460a73c74f8195653fbbf0138f4d`），但三次 `workflow.publish` 均失败：最初把 Task 输入字段当 Runtime 根字段；随后将 Plan 字段 `wip_decision` 错放进 Workflow Definition；再后一次协议参数结构错误，报缺 `workflow_plan_id`。看板仍为 `workflow: null, tasks: []`。
- 第五轮自然语言请求它利用已有资料、查阅工具协议、创建并启动最小可扩展 Workflow/Task，Job `cjob_f76d25f3562b49c689483ee3c5de0210` 的最终结果见下文。

## 最终结果

**未通过端到端验收。** 已完成五轮自然语言交互。第五轮 Job `cjob_f76d25f3562b49c689483ee3c5de0210`、Agent Run `arun_8b43820970ff4b0da7512450bc4bbadb` 最终再次以 `ExecutionBudgetExceeded: Agent iteration/tool-call ceiling reached` 失败，累计 173 次工具调用。最终 `/board` 为 `workflow: null`、`tasks: []`。因此没有 Column Agent、看板推进或最终交付物可验收。服务保持运行；没有手工创建 Workflow/Task，也没有修改 DevWerk 源码。

第五轮又保存了 Plan `wfplan_ed1080afe7414176b1e34d4d80658b2b`，但没有调用成功的 `workflow.publish`。失败的 `workflow.plan.save` 包括把 `plan` 作为 JSON 字符串而非对象、错误填写 `accepted_scope_snapshot` 和 `input_file_snapshot` 的字段。Agent 在已成功保存 Plan 后仍继续读取 `workflow.plan.list`、`task.plan.contract` 等工具和尝试再次保存 Plan，未收束到发布 Workflow。

### 能力与资料有效性

| 项目 | 观测 |
| --- | --- |
| 搜索引擎工具 | Project 能力目录没有互联网搜索能力；本次未发生搜索引擎调用。 |
| Chrome/MCP 浏览器 | Project 能力目录没有 `mcp__*` 或浏览器能力；当前无 MCP server 配置，本次未发生浏览器操作。 |
| 外部网页获取 | `project.command.run` 调用 `curl` 成功取回阿根廷与智利行业协会的公开 HTML；这是命令行 HTTP 路径。 |
| 候选资料 | 项目目录存在 `argentina_wineries.json`，包含 202 条自动抽取候选，来源字段指向协会名录。它只是中间生成物，未逐条核实。样本中的邮箱可指向酒店域名，电话字段有残片；不能认定 202 家均为目标小型酒庄。 |
| 市场份额/规模 | 未产生经来源核实的市场份额或规模指标；不能据此回答用户的市场份额问题。 |
| 联系人 | 未产生经来源核实的负责人或市场推广人员记录。候选 JSON 中的公开邮箱未经企业身份核实。 |
| 看板交付 | 没有 Workflow Revision、Task、Column Run 或交付报告。 |

### 阻塞原因与边界

1. **搜索/浏览器能力未接入。** `app/main.py` 在启动时加载 `config/mcp-servers.yaml` 或 `DEVWERK_MCP_SERVERS_PATH`；当前工作区没有该配置。`app/v1/mcp_tools.py` 负责通用 MCP 桥接，但没有运行中的搜索或 Chrome MCP server 注册到本 Project。Conversation Agent 能通过 `project.command.run` 运行本机 `curl`，该后备路径不能等同于搜索引擎或浏览器能力。
2. **工作流协议使用失败。** 第四轮先保存 Plan，随后三次 `workflow.publish` 分别因把 Task 输入当 Runtime 根字段、在 Workflow Definition 中加入 Plan 专属 `wip_decision`、顶层参数缺失 `workflow_plan_id` 而失败。第五轮虽保存第二个 Plan，却始终未发布。工具本身给出具体校验错误；实际 Agent 未在预算内稳定修正调用。
3. **单轮执行不收束。** 第四、第五轮分别用了 162、173 次工具调用，频繁重复抓取/读取和计划试错，最终触发迭代/工具调用上限。其结果是临时抓取文件保留在 Project 目录，而用户可见的对话回复、Workflow 和 Task 均没有完成。
4. **命令成功不代表来源有效。** 有的 `curl` 调用进程退出码为 0，但页面内容是 API 404；另有智利页面下载文件为 0 字节。解析程序还产生不可信字段。当前没有完成源页面响应、条目身份、规模口径和联系人来源的逐项验证。

本次只记录黑盒结果，不在测试分支为酒庄调研添加场景专用代码。下一步若要完成此类任务，应先为 DevWerk 显式接入通用搜索/浏览器能力，并解决 Conversation Agent 在保存 Plan 后无法按工具协议发布 Workflow、创建 Task 的问题；这些属于通用能力与工具使用路径，不应写成酒庄行业特例。
