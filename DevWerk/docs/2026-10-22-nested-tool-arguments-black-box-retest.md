# Workflow Tool 参数契约黑盒复测（2026-10-22）

## 范围

在 8000 端口运行当前代码，使用全新 Project 与 Conversation Agent 进行自然语言对话。沿用公众号首篇 Agentic 文章场景，观察 Agent 能否独立创建 Workflow 与 Task、Column 是否按图推进并交付 Markdown 文件。重点核对 `task.plan.validate/save` 和 `workflow.create` 嵌套 Tool 参数的新契约；Agent 的文字声称不替代 Tool 回执、数据库状态或交付文件。

测试者只发送自然语言，不使用管理 API 代替 Agent 创建 Workflow/Task，不在消息或请求参数中指定“仅讨论”或“现在启动”。保留 Project、数据库、日志和运行服务供复查。

## 环境与过程

- **环境核验纠正：**受限命令环境不能访问本机 socket，最初误把 8000 判为空闲。随后用正常网络权限核实当时监听 PID 38352，启动时间为 11:03，命令为系统 Python 的旧 `uvicorn app.main:app --host 127.0.0.1 --port 8000`。下面 A Project 的事实是旧服务诊断，**不能作为本轮代码修改的回归结果**。
- 用户已明确要求重启服务；确认 PID 后停止该旧 DevWerk 进程，以仓库 `startup.bat` 和项目 venv 启动当前代码。新 Uvicorn PID 26860，启动日志显示 21:27 完成 MCP 发现和应用启动；随后 `/v1/health=ok`，Supervisor 正常。
- A Project（旧服务诊断）：`prj_5b2adb9fdd8a48e68738437e8ed3e0d4`；目录 `data/projects/agentic-article-protocol-retest-20261022/`。

### 当前代码 B/C Project

- B Project（当前代码有效复测）：`prj_732dcce184494b9e9e735e8b32819660`；目录 `data/projects/agentic-article-protocol-retest-20261022-b/`。
- B 第 1 轮 Job：`cjob_7297074371864f51b780ff2d32d08719`。自然语言明确 1000–1500 汉字、可核查来源、Markdown 正文与独立来源清单，询问切入角度、结构和执行方式；请求只传 `message`。
- B 第 1 轮在模型请求前失败：`ContextExhausted: required_context_overflow`，Tool schema 281177 字节、估计 175873 tokens，输入预算 129025。根因是本轮强类型 `workflow.create` 把 32 个 MCP Tool 的输入 schema 重复内联到步骤与验收检查分支。这是本次协议修改引入的 P0 回归；B Project 没有 Workflow/Task。
- 最小修复：MCP ID 在 Agent 可见的 `workflow.create` 中合并为动态参数分支，直接 MCP Tool 定义继续提供输入 schema；Workflow 发布前仍由 Registry 的原始 schema 校验字段及类型。相同 102 Tool 组合的原始 schema 从 281177 降到 187527 字节。新增预算和运行时拒绝测试，相关 100 项单元测试通过，编译与 diff 检查通过。
- 为应用修复，停止已核实的本轮 DevWerk PID 26860 后重启；新监听 PID 35884，`/v1/health=ok`。
- C Project（预算修复后有效复测）：`prj_1dbf34fc672e4d1d81775caa425060e1`；目录 `data/projects/agentic-article-protocol-retest-20261022-c/`。C 第 1 轮 Job `cjob_6807550643f142f99c1c03cb157aa5d5`，使用与 B 相同的自然语言请求。
- C 第 1 轮不再发生上下文溢出；`workflow.create` 一次成功并发布 revision `wfrev_3c89aac46b594dc1b726679786412276`。随后 Agent 重复调用 `workflow.inspect` 与 `task.plan.contract`，虽能读取成功回执，却没有调用 `task.plan.validate/save` 或 `task.create`；最终触发 `ConversationProtocolStalled`。没有 Task。下一轮只以自然语言要求继续原交付，不直接使用管理 API 创建 Task。
- C 第 2 轮 Job `cjob_62c30af91ebd4f8da48cd4614967d3ff`：指出流程已建立但尚无 Task/产出，请 Agent 按原篇幅和来源清单要求继续，并自行说明、处理障碍。请求只传 `message`。
- C 第 2 轮再次触发 `ConversationProtocolStalled`。Tool Ledger 中先后调用 `workflow.inspect`、`task.plan.contract`、`requirement.list`、`task.list`、执行授权及 `agent.result.read`；没有尝试 `task.plan.validate/save` 或 `task.create`。检查结果投影后 `workflow.inspect` 约 1.5 KB、`task.plan.contract` 约 1.7 KB，包含当前 Workflow revision、任务输入 schema 和下一步提示；证据不足以归因为输出被截断。仍无 Task。
- C 第 3 轮 Job `cjob_e79f9d5be772458dbe86a82351b5e5f4`：以自然语言要求实际推进文章交付，若不能执行则明确说明阻塞；未提示内部 Tool 或状态。
- C 第 3 轮仍以 `ConversationProtocolStalled` 结束，重复失败 Tool 为 `workflow.inspect`。三个独立自然语言轮次都没有创建 Task，故停止继续提交相近请求，以免无效消耗。执行授权和 `task.plan.validate/save`、`task.create` Tool 在后续 Run 中均存在；阻断发生在 Agent 的下一动作选择，不是 API 不可用或 Workflow 未发布。

### 旧服务 A Project（不计入当前代码回归）

- 第 1 轮 Job：`cjob_d72a4a6dd865486f9d872ee42147ee88`。消息提出读者、主题、可核查来源和 Markdown 交付，询问切入角度与结构。请求只提供 `message`，使用 Web API 默认参数。
- 第 1 轮完成：Agent 自行发布五列 Workflow（调研、结构、写作、审校、定稿）并创建 `tsk_44cef58c9bc24824840a6d2a651abc9d`；随后实际 Task 状态为 `running/research`。Agent 将“短文章”解释成 2000–3000 字，未等待更多讨论。
- 第 2 轮 Job：`cjob_9a8a894a46304953aa1907a1776cbb6b`。用户自然语言纠正为 1000–1500 汉字，要求来源单独成表、正文简短标注，并请 Agent 调整已开始的交付。

第 1 轮 Tool Ledger 显示：`workflow.create` 连续两次返回 `invalid_arguments`，第三次成功；两次错误均在 Workflow 的 Task `input_schema`：`required` 数组被生成成 `{"item": [...]}`，第二次嵌套为 `{"item": {"item": [...]}}`。`task.plan.save` 和 `task.create` 均一次成功，本轮没有调用 `task.plan.validate`。这是动态 JSON Schema 文档自身的数组形状错误，与本次新增的验收检查 `arguments` 类型不同；不能把第三次成功当作首次调用稳定。

第 2 轮提交约五分钟后仍为 `running`；原 Task 仍为 `running/research`，尚无修订结果，继续保持观察间隔。

第 2 轮约十二分钟时，Conversation Run `arun_e18c7aaaf5714f51a90b1edbe3b9a233` 和 `research` Column Run `arun_ab2d63e49632457da366710a1578b45e` 均为 `running`、`iterations=0`、`tool_calls=0`。`research` 心跳仍更新，日志末尾出现新的 MiniMax HTTPS 连接；目前没有实际模型回复或 Tool 调用，不能将等待直接归因为 Workflow 协议缺陷。

随后 `research` Column Run 结束为 `ExecutionBudgetExceeded`，Task 落到 `failed`。最终 Assignment 记录 100 次模型调用、160 次 Tool 调用；Tool Ledger 中 148 次为 `mcp__chrome_visible__evaluate_script`，7 次新建浏览器页，只有一次 `project.files.list`，**没有 `project.files.write`**。该 Column 的指令明确要求写 `research/sources.md` 与 `research/source-accessibility.md`，工具授权也包含 `project.files.write`，但 Agent 一直细读网页直至迭代上限，未落地任何中间交付。这是通用 Agent 工作收敛/预算内保存进度的问题，非 Workflow 状态路由错误；本轮不加文章特化代码。

## 结论

本轮对最新代码的回归 **未通过**。MCP schema 引入的首轮上下文溢出已做通用最小修复，102 Tool 的 schema 体积下降，C Project 成功调用 `workflow.create` 并发布 Workflow；但 Conversation Agent 三轮均未从已发布 Workflow 走到 Task Plan 保存和 Task 创建，后两轮取得执行授权后仍重复只读 Tool，最终触发停滞保护。没有 Task，自然也没有 Column 推进或文章交付；不能宣称端到端通过。下一步需要单独设计并修复“发布 Workflow 后继续创建 Task”的通用决策/工具交互边界，不能为公众号场景写特例。当前 Project、数据库、日志和 8000 服务保留。
