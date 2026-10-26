# Conversation Agent 独立黑盒测试（2026-10-25）

## 测试目标

验证主 Agent 在未创建 Kanban Workflow/Task 的情况下，能读取 Project 文件、通过多轮自然语言识别证据冲突、直接写入并修订同一份交付文件。所有判断以 HTTP Job 状态、Agent Run Tool 回执、Project 文件及 Board 状态为准，不接受模型自述替代回执。

## 环境与输入

- 使用本机端口 8000 正在运行的真实 DevWerk 服务与已配置的真实模型；未使用模拟模型。**环境偏差**：第二轮后核对 Agent Run 的系统消息，确认端口实际由 2026-10-24 23:05 启动的旧版进程 PID 18344 占用，仍发送改动前的 `DEVWERK.md`、`TURN_INSTRUCTION` 和 `REPORT_INSTRUCTION`。本次前两轮是旧版基线观察，不能证明 2026-10-25 新代码有效。
- 新 Project：`prj_d2231f1ac5254f2a86268145017a51a2`。
- Project 目录：`data/blackbox-general-agent-2e62dca42c764ff5a1ccaf0ae4601c73`。
- 测试前仅放入两份资料：`client-note.md` 与 `meeting-note.md`。前者记录 30 家门店、11 月 15 日、桌面端优先、预算上限 8 万元；后者记录 20 家、12 月 1 日、移动端优先、相同预算，并注明三项变化需客户确认。两份资料均非最终批准版本。

## 轮次与观察

### 第一轮：只读核对

- 用户自然语言请求读取两份资料，列出具体冲突、确定事实与待确认项，暂不修改文件。
- Job `cjob_a08a0f59d0054a3d84a572c3cd2eafa6`：`succeeded`，业务结果为 `discussion`。
- Agent Run `arun_5693e640c30f4948a6932712992e6d09`：两次成功 `project.files.read` 分别读取原文件，随后成功 `conversation.reply`；无写入 Tool。
- 回复准确指出范围、日期、终端三处冲突，以及预算上限一致；说明两份记录均未获最终确认。回复末尾额外推测“未留痕的范围谈判”，此推断没有直接资料证据，计为答复质量瑕疵。
- Board：无 Workflow，Task 数量 0。

### 第二轮：直接写摘要

- 用户要求直接写入 `核对摘要.md`，保持三个冲突项未决。
- Job `cjob_30970e722c0447d7a48ff926329c41c2`：`succeeded`。真实 Tool 顺序为成功的 `conversation.execution.authorize`、两次成功的 `project.files.read`、成功的 `project.files.write`、成功的 `conversation.reply`。生成了不必要的 Requirement `req_973a4ed908904955a93e33dd6da186e0` 和 Grant `grant_06e893510aff43efa0f17938c23cd220`；Board 仍无 Workflow，Task 数量 0。
- 文件真实存在，列出共同预算、三处冲突、未决事项；未擅自选定冲突版本。内容把无直接证据的“未留痕范围谈判”推测写成较肯定的判断，又将变更来源增列为必须确认项，计为答复质量瑕疵。
- 多余授权的根因已通过 Run 系统消息定位：旧版 `TURN_INSTRUCTION` 明确写有“首次业务变更前调用 `conversation.execution.authorize`”。不是当前代码中的授权边界生效结果。

### 第三轮：用户确认后修订

暂停。8000 端口已有旧进程；依照开发纪律等待用户决定是否清理端口。不得在旧服务上继续把结果当作当前代码的黑盒验收。

## 旧服务阶段结论

前两轮证实旧服务能直接读取、写入项目文件，且未建 Workflow/Task；同时暴露旧提示词导致无关文件写入仍先创建 Requirement 的问题。由于该服务未加载本次修改，这两轮不能算当前代码验收。随后经用户允许重启并用新 Project 完成复测，结果见下节。

## 当前代码复测

用户明确允许停止旧进程并重启。已停止 8000 的旧监听进程 PID 18344；当前服务监听进程 PID 36492 于 09:14 启动，健康检查正常。数据库最新平台指令修订版为 4，内容以“persistent, general-purpose Agent”开头。复测使用与上面完全相同的两份输入文件和三轮自然语言请求。

- 新 Project：`prj_3949a1d8ffe54790a51373a4f6e0e439`。
- Project 目录：`data/blackbox-general-agent-v2-cc633565aa364bc7a2b783aa242c04ae`。
- 第一轮 Job `cjob_224ed956dadd4eaca5a064069557e371`：`succeeded`，Agent Run `arun_3fb99d64c50b40fab35c4556af7417d5`。系统消息确认平台指令修订版 4 与新的通用 Agent `TURN_INSTRUCTION`；两次成功 `project.files.read` 和一次成功 `conversation.reply`，无写入、授权或管理 Tool。准确识别三处冲突与共同预算；Board 无 Workflow，Task 0。答复仍将未最终确认的共同预算称为“硬约束”，并推断需交付可用系统，后者没有材料根据，属内容准确性问题。
- 第二轮 Job `cjob_36a982129d554ef0b3f95854636665af`：`succeeded`，Agent Run `arun_aabc94b7af15474095d190e6d38155ef`。真实 Tool 为两次成功 `project.files.read`、一次成功 `project.files.write` 写入 `核对摘要.md`、一次成功 `conversation.reply`。**未调用 `conversation.execution.authorize`，未创建 Requirement、Workflow 或 Task。** 文件存在，三项冲突保持未决。文件同时把“需交付可用系统”写成两份资料一致的事实，原文件没有此表述；也将尚未批准的共同预算称为“硬约束”。这是内容证据越界，不影响直接执行路径通过。
- 第三轮 Job `cjob_1d863a8f0c4b4221bbce08794a987956`：`succeeded`，Agent Run `arun_9113a0844a004ba188331dfe1c0119c9`。真实 Tool 顺序为成功读取原摘要、成功读取两份原始记录、成功重写同一个 `核对摘要.md`、成功 `conversation.reply`；无授权或管理 Tool。
- 修订文件将范围 30 家、12 月 1 日、移动端优先标为“用户本轮决定（message_id 280）”，原始记录的冲突版本仍并列保留。预算数值一致但批准状态未定；“需交付可用系统”已删除。仍列出预算批准、桌面端边界、客户书面确认等未决事项。两份输入文件的哈希与复制时的原件一致。
- Board 无 Workflow、Task 0；数据库只读核对本 Project 的 Requirement 0、Workflow revision 0、Task 0。

### 复测结论

**独立通用 Agent 路径通过本用例。** 当前代码下三轮对话依次完成资料读取、直接文件写入和基于用户新决定的同文件修订；没有被迫先建 Requirement，也没有误建 Kanban。操作由真实 Tool 回执与文件内容证实，服务保持在 8000 运行，可在 Web 审阅此 Project。

**内容准确性仍有瑕疵。** 第一轮把未批准的共同预算称为硬约束，并推断原材料未写明的“需交付可用系统”；第二轮将该推断写入摘要。第三轮在用户指出后正确撤回。此问题属于证据边界与模型答复质量，不能由本用例证明已普遍解决；它没有阻塞工具调用或多轮修订。测试只覆盖这个资料核对场景，不代表 Hermes 的全部工具广度、长会话或主动 steering 功能已达到同等水平。

## 内容失真根因追踪

1. **首次出现于模型输出。** 两份原文件都写“预算上限 8 万元”，其中客户记录整体尚未最终确认；两份都没有“需交付可用系统”。第一轮 Agent Run `arun_3fb99d64c50b40fab35c4556af7417d5` 的原始 assistant Tool Call `conversation.reply` 参数已包含“硬约束”和“可用系统”，在 DevWerk 渲染回复前就存在。因此不是文件读取 Tool 或报告渲染器编造。
2. **会话历史使错误延续。** 第一轮公开回复（对话消息 277）含“可用系统”。`V1Store.conversation_session_messages` 读取同 Project 的历史公开答复，`AgentRunPreparer._append_session_history` 在第二轮重放。第二轮用户又要求整理“刚才的核对结果”。模型重新读取两个原文件后，仍把旧答复中的推断写入 `project.files.write` 的 `content` 参数；文件写入 Tool 只是按参数持久化内容。这里的“重放”是有意的会话能力，问题在于历史模型推断未带证据等级，容易被模型提升为已核事实。
3. **Harness 校验范围有限。** `ConversationReply.message` 是自由文本；`render_report` 在 `discussion` 模式直接呈现该文本，在 `work_result` 模式核对被引用的 Tool 回执和 Task 状态，只把 Agent 的文字说明标为“未作为完成依据”。它不逐条验证自然语言断言与读过的文件是否相符。Tool 目录中 `conversation.reply` 的“fact-checked reply”描述因此容易被误读为内容已获事实校验，实际只校验执行事实。
4. **另有上下文偏置风险，尚不能定为直接因果。** `ConversationGateway._process` 无条件把 `store.list_loops(limit=20)` 放入主 Agent 当前上下文。本轮虽未选择 Loop，却实际看到了 4 个 Loop 简介，其中 2 个为软件交付方法。`AgentRunPreparer._append_current_request` 又在每轮加入以“implement/deliver”“讨论与执行边界”为核心的通用指令；本轮原始模型请求确实包含两者。这可能增加从“门店试点”联想到“软件系统交付”的概率，但单次样本不能证明该句具体由哪段上下文诱发。它说明普通资料核对轮次仍携带不相关的领域模板元数据和偏实施的边界文本。
5. **归因。** 首要触发是模型把“数值一致”推成“获批硬约束”，把“门店试点”推成“需交付可用系统”；这是证据推断错误。DevWerk 没有错误读取、改写原文件或自动插入该句，也没有 Workflow/Task 状态变更。作为通用 Agent Harness，仍有证据归属缺口：模型推断与文件事实在会话重放和自由文本交付中没有结构上的区分；Tool 文案又把执行回执核验称为“fact-checked reply”。第三轮用户明确纠正后模型撤回，说明当前交互能修正个案，但不保证下次自动识别。
