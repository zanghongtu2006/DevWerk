# 2026-09-12 讨论语义、执行边界与 TaskPlan 准入修复设计

状态：**详细设计，待用户 review；尚未修改运行时代码，尚未运行本次回归。**

本文件替代同名初稿。尤其撤回初稿“用中英文关键词解析讨论/开始指令”的主方案。本文依据当前代码、生产库的只读证据和本机 Hermes/Codex 源码；docs 内旧测试结果不作为本次正确性证据。原始记录见 [09-12 Bug](bugs/2026-09-12-software-discussion-premature-workflow-and-task-plan-contract-failure.md)。

## 1. 结论与本次边界

这不是仅补一句“不要提前开发”的提示词就能解决的问题。当前系统把用户意图、是否开始、需求确认和工具执行混在 start_task 与少数布尔字段里；模型一次语义误判可以立即变成持久的业务状态。其后 TaskPlan 保存与创建又执行不同校验，使系统接受了必然无法创建的计划。

本次推荐方案：

1. **每个 Project 仍只有一个持久 Conversation Agent，即该项目的主 Agent。** 它负责理解需求、讨论、规划、调度；不新增 System Main Agent、权限审批 Agent、语义分类 Agent。
2. 用户消息先进入尚未确定执行边界的回合。由同一个 Conversation Agent 形成结构化解释；宿主保存讨论约束和执行依据，并在每次副作用前执行确定性边界检查。
3. 分开保存需求讨论草稿、已获授权的需求快照、真正发布的 Workflow/TaskPlan 和实际执行回执。回答选型问题、方案完整、保存计划、任务启动是四件不同的事。
4. TaskPlan 使用同一套静态编译/校验生成不可变输入，保存与创建共用。动态冲突仍在创建事务里检查。
5. 用真实多轮模型行为评测验证语义；用可注入错误工具调用的确定性测试验证运行时边界。两类测试不可互相替代。

本次不重做已实现的 Worker 生命周期，不引入 Column 内的多 Agent loop，不处理之前延后的字数上限等问题，也不把它扩展成通用安全审批平台。本次边界的目的就是让 Agent 正确完成用户要的项目。

## 2. 事实、因果链与问题登记

### 2.1 现场证据

只读复核对象：

| 对象 | 标识/结果 |
| --- | --- |
| Project | prj_c24e9773680a4955962c7341a710b248 |
| 首轮 Job | cjob_7f1427b48367438fb1effc8c5f31f0b8，成功 |
| 第二轮 Job | cjob_abeaaa93d863492e9d0103ae5233b8d1，失败 |
| 失败 Run | arun_976d2da14c7c411f9a4a779f020954d6 |
| Workflow | wfrev_ac18faf10f9d4eed8b69e4f7d5364159 |
| TaskPlan | tplan_fc33bb3782934c76aa04eaf7aba152f9 |
| Loop | software.ddd_delivery 1.2.0 |
| 最终状态 | Workflow、TaskPlan 已提交；Task 数量为 0 |

用户消息 411：

> 我要做一个可本地运行的前后端登录示例：前端 Vue，登录成功后显示简单欢迎页；后端 Java Spring Boot，需要注册、登录和暴力防刷，并有自动化测试。先不要启动开发，请先列出真正影响实现的关键决策，并建议一个不过度设计的交付流程。

助手消息 412 提出 JWT、防刷、注册、UI 四个问题，并自行说“确认后我们立即开始交付，不再反复讨论”。

用户消息 413：

> 采用无状态 JWT，只做短期 access token，不做 refresh token；登录连续失败5次锁定15分钟，成功后清零；注册使用用户名和密码，不做邮箱验证码；前端 Vue 3 + Vite + Element Plus。还需要决定用户如何持久化，以及防刷应该按用户名、IP还是组合判断。请给出适合本地演示但可测试的最小方案。

这条消息确定了部分选型，同时明确提出新的讨论问题，没有解除“先不要启动开发”。助手自己的承诺不能替代用户指令。

两轮持久 Job 的 start_task 均为 1。第二轮依次执行：

1. loop.inspect 成功。
2. loop.apply 成功。
3. task.plan.save：整个 plan 被作为 JSON 字符串传入，失败。
4. task.plan.save：design 缺 requirements_confirmed，失败。
5. task.plan.save：保存八个阶段 Task，成功。
6. task.create(req_baseline)：输入出现多余 input 层，失败。
7. 再次提交相同失败操作，触发 ConversationProtocolStalled。

八个条目的 input 都是：

~~~json
{"requirements_confirmed": true, "requirements_path": "docs/requirements-baseline.md"}
~~~

第一个条目还包含：

~~~json
[
  {"pointer": "/input/requirements_path", "escaped_value": "docs/requirements-baseline.md"},
  {"pointer": "/input/requirements_confirmed", "escaped_value": "true"}
]
~~~

第一个 pointer 根位置错误；第二个还把 boolean 当作 exact string。保存时未应用这些覆盖，创建时 setter 自动建立 input 对象，才出现 additionalProperties 错误。其余七个条目没有 exact 声明。

### 2.2 问题登记

| 编号 | 判断 | 根因与影响 | 本次处理 |
| --- | --- | --- | --- |
| P0-0912-01 | 已确认 | Web 恒传 start_task=true，服务端没有独立持久讨论边界；选型答复被当作启动指令 | 回合契约、讨论状态、工具前置门禁 |
| P0-0912-02 | 已确认 | Columns 被再拆成 Tasks；同一 plan 内八个条目共享同一个交付身份 | flow_unit 上下文、正确示例、计划内身份唯一性 |
| P0-0912-03 | 已确认 | exact 覆盖、确定性能力绑定、readiness 等在保存与创建走不同路径 | 统一静态编译入口 |
| P0-0912-04 | 已确认 | exact setter 能造结构、覆盖非字符串；错误元数据污染 Task input | 严格存在性/类型/指针校验，保留合法文本还原 |
| R-0912-05 | 已确认的恢复缺口 | 协议停止掩盖最后一个业务错误与零 Task 事实 | 结构化原因及部分提交状态；随 P0 修复 |
| R-0912-06 | 代码可见的相关缺口，未在本次现场走到 | requirements_confirmed 是模型自报；Loop 的“需求文件存在且已确认”缺少对应版本证据 | 需求快照来源、执行依据、软件 Loop 入口证据对齐 |

不把两次正常的参数拒绝登记成新 bug；不能通过自动填 true、关闭 additionalProperties、随意剥掉 input 包装来“修复”。

### 2.3 当前代码定位

| 位置 | 已读到的行为 |
| --- | --- |
| app/web/static/dashboard.js:285 | 发送 message 与 start_task:true |
| app/v1/domain.py:340；app/v1/api.py:179 | 请求默认 start_task=true，API 透传 |
| app/v1/store.py:699 | 建 Job 时保存布尔值，没有语义快照 |
| app/v1/conversation.py:303 | 先调用 turn_requirement，再由 start_task/事件触发计算 graph_mutation_allowed |
| app/v1/repositories/agent_repository.py:115 | 用户 start_task=true 时可能创建/绑定 Requirement；因此不能只在 task.create 才补门禁 |
| app/v1/agent_tool_execution.py:135 | 有只讨论副作用门禁，但依赖错误的 start_task 来源 |
| app/v1/capabilities.py:131 | 从 Job 刷新 start_task；未与工具批处理层共用统一的讨论边界判断 |
| app/v1/repositories/planning_repository.py:71 | 保存只执行部分校验；hash 命中还会提前返回 |
| app/v1/store.py:1409 | 创建应用 exact、校验能力/readiness/身份等 |
| app/v1/capabilities.py:1591、1706 | exact 覆盖与能力校验混合；setter 自动建路径 |
| app/v1/domain.py:427、569 | exact 语法可通过；部分使用约束仅对 agent_execution=forbidden 检查 |
| app/v1/task_identity.py | 已有 logical_task_key，可复用而不是按 Task 名称猜阶段 |
| loops/ddd-software-delivery/loop.json:27 | flow_unit 是独立软件交付范围，identity_pointer 是 /requirements_path |

## 3. Hermes 与 Codex 源码参考：借鉴边界而非照搬

本次实际阅读的本机版本：

- Hermes：D:/workspace/github/hermes-agent，HEAD 38b7d0f4cf8a11137d8da5e3d95d7b5b7e41fb46。
- Codex：D:/workspace/github/codex，HEAD 068c49f075cf287a1fe7d1ee36cf005efac922e7。

### 3.1 Hermes：同一个 Agent 可以讨论、计划和执行

[agent/plan_prompt.py:1](D:/workspace/github/hermes-agent/agent/plan_prompt.py:1) 明确说明 /plan 构建一个 prompt，交给当前 live agent，以普通回合运行，不建立另一套执行引擎。规则允许读操作和专门的方案文档，禁止当轮实现。调用入口见 [cli_commands_mixin.py:2214](D:/workspace/github/hermes-agent/hermes_cli/cli_commands_mixin.py:2214)。

借鉴：

- Conversation Agent 身份与会话连续，讨论模式不等于换一个能力更低的 Agent。
- 模式是当前任务的工作边界；规划产物与真正执行分开。
- 规划指令与相关证据进入当前会话，保留稳定工具面，不通过频繁换 Agent/工具清单维持状态。

不能据此声称 Hermes 的自然语言讨论边界已经得到完全确定性的保证：上述 /plan 主要通过 prompt 约束。DevWerk 已出现真实错误，必须加自己的持久状态和副作用门禁。

[agent/subagent_lifecycle.py:36](D:/workspace/github/hermes-agent/agent/subagent_lifecycle.py:36) 提供明确的生命周期与 launch/result/cancel 契约；[tools/delegate_tool.py:1230](D:/workspace/github/hermes-agent/tools/delegate_tool.py:1230) 为子 Agent 构造 goal、context、workspace 的聚焦上下文。DevWerk 保留现有持久 Worker 与 Assignment：每个 Column 激活一个 leaf Worker，跨 Column 的调度由 Workflow 和 Conversation Agent 负责；不能把计划清单直接转换成 Column 内并行子 Agent loop。

### 3.2 Codex：协作模式是宿主状态，计划本身不等于开始

[config_types.rs:673](D:/workspace/github/codex/codex-rs/protocol/src/config_types.rs:673) 定义 ModeKind；[collaboration_mode.rs:21](D:/workspace/github/codex/codex-rs/core/src/context/world_state/collaboration_mode.rs:21) 将模式及指令版本投影进模型上下文。该片段以 developer 角色进入上下文，并使用 snapshot/hash 避免重复注入。

[plan.md:5](D:/workspace/github/codex/codex-rs/collaboration-mode-templates/templates/plan.md:5) 明确区别协作 Plan 模式与 TODO/update_plan；[handlers/plan.rs:84](D:/workspace/github/codex/codex-rs/core/src/tools/handlers/plan.rs:84) 的 update_plan 只是进度事件，并非执行授权。

[plan_implementation.rs:29](D:/workspace/github/codex/codex-rs/tui/src/chatwidget/plan_implementation.rs:29) 在用户选“实施”后发送带模式的 SubmitUserMessageWithMode；[input_submission.rs:331](D:/workspace/github/codex/codex-rs/tui/src/chatwidget/input_submission.rs:331) 将协作模式单独传入 user turn。

借鉴：

- 请求边界是明确数据，不能每次靠历史聊天中一句助手总结恢复。
- “产生方案”“接受方案”“执行该方案”应当是可区分的状态与事件。
- UI 可提供不依赖措辞的明确开始动作，但 DevWerk 仍支持自然语言“按刚才方案开始”，不用复制 Codex 的具体交互限制。

本次没有在所读 apply_patch/unified_exec 路径中发现统一的 Plan 模式硬门禁，因此**不声称 Codex 已用模式字段确定性拦截所有写工具**。DevWerk 的统一门禁是基于本项目需求作出的设计。

### 3.3 澄清、错误和停止：不能误用参考实现

[request_user_input.rs:62](D:/workspace/github/codex/codex-rs/core/src/tools/handlers/request_user_input.rs:62) 由根线程询问用户；取消没有答复会返回错误，不生成虚假的回答。DevWerk 同样由项目 Conversation Agent 处理用户问题；Worker 通过 mailbox 汇报需要澄清的内容。

Hermes [tools/clarify_tool.py](D:/workspace/github/hermes-agent/tools/clarify_tool.py) 有超时后按最佳判断继续的文案。普通可选偏好与解除“不要执行”的约束不能采用同一个超时策略：等待多久都不能自动产生用户开始指令。

Codex [handlers/mod.rs:82](D:/workspace/github/codex/codex-rs/core/src/tools/handlers/mod.rs:82) 将参数解析错误作为 RespondToModel 返回。可修正的工具错误应回到 Agent，由它修正参数或回答阻塞，而非立即变成系统无法解释的失败。

Hermes [agent_runtime_helpers.py:4498](D:/workspace/github/hermes-agent/agent/agent_runtime_helpers.py:4498) 和 [conversation_loop.py:8315](D:/workspace/github/hermes-agent/agent/conversation_loop.py:8315) 确实存在“说要继续但没调用工具”的有界启发式续跑。**不能把检测助手将来时的正则用于判断用户授权。** 本次也不恢复 DevWerk 早期按中文词语推导必需工具及调用顺序的隐式业务引擎。

## 4. 职责与状态模型

### 4.1 五类事实分别保存

| 类型 | 负责理解/产生 | 持久事实 | 不能推出什么 |
| --- | --- | --- | --- |
| 用户意图和约束 | Conversation Agent 解释原始用户消息 | TurnResolution + 用户来源 | 助手认为明确，不等于已执行 |
| 讨论草稿 | 同一个 Conversation Agent | WorkIntent 的决定、提案、待定项 | 草稿完整，不等于获准开发 |
| 执行依据 | 用户动作/用户自然语言，经宿主保存 | ExecutionGrant + scope snapshot | grant 不等于 Workflow 已发布 |
| 工作定义 | Agent 选择 Loop、规划 Tasks；领域层校验 | Workflow/TaskPlan/Requirement revision | plan 保存，不等于 Task 已创建 |
| 实际结果 | Runtime/能力回执 | Task、Column、receipt、artifact | 调用过工具，不等于成功或交付 |

ExecutionGrant 只是“用户要求做哪件事”的可追溯记录，不是新权限角色，也不是需要额外的人审批的系统。用户已经明确要求开始时，主 Agent 可以在同一回合保存依据并执行，不再问一次确认。

### 4.2 总体路径

~~~mermaid
flowchart TD
  U[用户消息或明确 UI 动作] --> J[保存 Job 与消息因果边界]
  J --> C[同一个 Conversation Agent 理解意图]
  C --> R[结构化回合解释]
  R --> H[宿主保存回合契约与讨论状态]
  H --> D[讨论回复或需求提案]
  H --> G[已有或本轮有效执行依据]
  G --> P[主 Agent 自主选择工具和 Loop]
  P --> A[统一副作用与范围检查]
  A --> W[Workflow / TaskPlan / Task]
  W --> K[Column 激活单个持久 leaf Worker]
  K --> M[结构化结果与 mailbox]
  M --> C
~~~

### 4.3 推荐数据结构

新增的名称均是设计名称，尚未实现。复用现有 Requirement/Worker/Job，不建立第二套项目执行层级。

**WorkIntent：讨论中的工作范围。** 使用独立 draft_id 表示尚未创建 Requirement 的新需求；若讨论已有需求，则带 requirement_id/revision。V1 每个项目只维护一个当前讨论焦点，历史焦点仍可读取，不构造多焦点自动合并系统。

~~~json
{
  "draft_id": "draft_x",
  "revision": 2,
  "focus": {"kind": "new_scope", "requirement_id": null},
  "goal": "本地运行的前后端登录示例",
  "decisions": [
    {"key": "token", "value": "短期 access token，不做 refresh token",
     "status": "user_decided", "source_message_ids": [413]}
  ],
  "open_questions": [
    {"key": "persistence", "blocking_for_start": true, "source_message_ids": [413]},
    {"key": "rate_limit_key", "blocking_for_start": true, "source_message_ids": [413]}
  ],
  "execution_hold": {"active": true, "source_message_id": 411},
  "proposal": null
}
~~~

key 和 value 是该需求的内容，不做 Vue、小说或 JWT 专用 kernel 字段。decisions.status 区分 user_decided、agent_proposed、delegated_choice；被用户允许自行决定的实现细节不强制变成待确认问题。proposal 带独立 revision/hash、目标、非目标、验收点和推荐方案。

**TurnResolution：当前用户消息的结构化解释。**

~~~json
{
  "schema_version": "devwerk.turn-resolution.v1",
  "source_message_id": 413,
  "based_on_intent_revision": 1,
  "act": "discuss",
  "focus_id": "draft_x",
  "execution_request": "none",
  "constraint_change": "retain",
  "user_evidence": [
    {"message_id": 413, "quote": "请给出适合本地演示但可测试的最小方案。"}
  ],
  "decision_updates": [],
  "open_question_updates": []
}
~~~

建议枚举：

- act：discuss / execute / status / control / clarify。
- execution_request：none / begin / continue / extend。只有 execute 类型可以使用后三者。
- constraint_change：retain / set_hold / release_hold；release 必须指向当前用户消息或真实 UI 开始事件。
- control 是用户要暂停/恢复/取消某个已存在对象的直接请求，必须有具体目标和动作。其许可只覆盖该控制动作，不扩展成任意 write/process/control。

**Job 保存的 TurnContract：宿主派生，不让模型直接填写 allowed=true。**

- source_message_id、用户消息序号上界、trigger_kind。
- requested_mode：auto / discuss；显式“开始该方案”另作为 user_action，绑定 proposal_id/revision/hash。
- resolution_id、intent_revision、phase：unresolved / discussion / execution / targeted_control。
- execution_grant_id 或 targeted_control_ref。
- contract_revision；和 Job/Run 当前 fencing 一起在副作用前核对。

**ExecutionGrant：获准执行的范围，不等于当前聊天模式。**

- id、project_id、来源 Job/用户消息或 UI action。
- draft/proposal snapshot hash；已有范围则绑定 requirement_id/revision。
- kind：begin / continue / extend；目标、非目标、验收点和用户允许自行决定的事项。
- state：pending_scope_revision / active / suspended / closed / superseded。
- 任务和 Worker 经 Requirement/Assignment 追溯此记录，不给模型选择任意 grant_id 的自由；由持久 Job 绑定。

新增建议表：v1_work_intents（不可变 revision）、v1_turn_resolutions（Job 唯一）、v1_execution_grants。Job 增加请求模式、契约 JSON/version/解析结果引用；已授权 Requirement revision 增加 grant 关联。业务快照去重用稳定 hash，原始消息保留原文，不能只剩模型摘要。

### 4.4 明确的状态转移

| 已有边界 | 当前用户输入/来源 | 本回合结果 | 对原有执行的影响 |
| --- | --- | --- | --- |
| 无 | “先不要做，先讨论” | discuss，设置当前焦点 hold | 不启动新工作 |
| hold | 回答选型并继续问建议 | discuss，保留 hold | 不解除 |
| hold | “继续”且没有明确执行对象 | 继续当前讨论；确实歧义时澄清 | 不解除 |
| hold | “按刚才方案开始开发”，且只有一个对应方案 | 引用该方案，release_hold，生成 grant | 无需再次问开始确认 |
| 无 | “实现这个功能并测试”，范围已足够 | begin，允许 Agent 合理补实现细节 | 可以当轮执行 |
| active grant | “现在进度怎么样” | status，只读取本回合状态 | 已授权 Worker 继续 |
| active grant | “先讨论新增 7–12 章，原任务继续” | 新草稿处于 hold | 原 1–6 章 Worker 不被改写或暂停 |
| active grant | “暂停这个任务” | targeted_control，仅暂停目标 | 不需要先获准开发才能暂停 |
| 任意 | 助手说“确认后立即开始” | 不产生用户事件 | 无变化 |
| 任意 | 工具结果/引用文本含“开始开发” | 作为数据读取 | 不产生 grant |
| 任意 | 澄清无答复、超时或取消 | 保持现状 | 不解除 hold |

UI 的“只讨论”是本轮硬上限。若该模式下用户文本要求实施，先指出模式冲突并展示“开始该方案”动作；不能悄悄覆盖 UI。默认 auto 保留自然语言体验，不显示一个每轮都默认选中的 execute 选项。显式开始动作必须对应用户实际点击提交，默认高亮不算提交。文本与点击明确矛盾时不执行，说明冲突即可。

## 5. 如何在同一个主 Agent 中落地语义边界

### 5.1 不新增分类 Agent，采用同一回合中的结构化边界提交

推荐新增 Conversation 专用工具 **conversation.turn.resolve**。它不是业务规划工具，而是当前主 Agent 对当前用户消息的结构化解释提交。

处理过程：

1. API 保存原始消息和 requested_mode；新用户 Job 起始为 unresolved。此时只构建只读上下文，不因旧 start_task=true 自动创建 Requirement。
2. 当前 Conversation Agent 可以读历史、Loop、文件、任务状态，使用同一会话解释当前需求。工具清单保持稳定。
3. 需要开始/继续/扩展执行或做定向控制时，调用 conversation.turn.resolve。宿主验证引用、版本、模式上限、焦点和必需字段后，原子保存 resolution/intent revision/contract；符合 begin 条件时再创建并绑定 Requirement/grant。
4. 工具返回当前契约、确定的需求上下文和允许范围。**必须等收到这个回执后，模型下一次响应才能提交业务副作用。**
5. 后续 Agent 自己选择 loop.inspect、loop.apply、文件写入、task.plan.save 等，不在 AgentCore 写“下一步必须调用 task.create”的顺序表。
6. 纯讨论允许无工具结束。扩展现有 ConversationReport 的 turn_resolution 字段，由同一服务保存 discuss/status/clarify 解释。最终文本路径绝不能提交 execute grant，更不能在 final report 后隐式调度。
7. 旧格式的纯讨论回复缺 resolution 时，兼容为保留边界的只读回合；不能清除已有 hold，也不能把“未提到未决问题”当作已解决。新模型提示要求提供决定与待定项；执行前缺少这些资料时读取原始上下文重新整理。

conversation.turn.resolve 可由当前项目 Conversation Agent 调用；Worker 和 mailbox 不能借此生成新的用户授权。未解析回合的 write/process/control 在能力层返回 TurnUnresolved。该工具本身按精确注册的 session_control 类型处理，仅能写会话控制数据和与本次合法开始关联的绑定，不能用名称前缀豁免其他控制工具。

同一 tool batch 同时包含 resolve 与业务副作用时，**整批在执行前拒绝**，要求先提交边界再调用业务工具。避免某个并行工具使用旧状态，也避免模型还没看回执就执行。该批次所有 operation 都写成 rejected_before_effect，不留待恢复的悬空操作。已有 generic tool-call/result 配对与重放机制继续负责协议完整性。

一次 Job 的 resolution 最多成功提交一次；重复相同内容返回同一回执；不同内容拒绝。变更用户约束属于新的用户消息/Job，不由 Agent 在被拒绝后反复“升级权限”。错误结构的未成功提交可以修正重试。

### 5.2 必须诚实说明语义保证的边界

**把自然语言变成结构化 JSON，不会使自然语言判断自动变成确定性事实。** 模型仍可能把“回答选型”错误分类为 begin；引用了真实原文也不能数学上证明那句话表示同意开发。

运行时能确定性保证的是：

- 未解析/只讨论/目标不符的契约不能发生业务副作用。
- 新用户授权来源不能是助手、工具消息、别的项目或未来消息。
- 已有 hold 不能由没有当前用户解除依据的字段缺失、助手承诺、普通结果回调消失。
- UI 的明确只讨论不会被模型字段覆盖。
- 模型不能在一个 Job 内重复尝试不同解释来扩大已落地边界。

模型负责、必须用真实多轮评测验证的是：否定、引用、反问、含混“继续”、确认方案与确认开始的区别、跨回合约束继承。自然语言 release_hold 即使通过结构校验，语义解释仍是模型判断；本方案不虚构一个所谓“确定性同意检测器”。

选择 auto + 同一主 Agent 解释，是为了保留自然对话体验。若真实模型评测仍出现擅自解除 hold，**不得宣称 P0 已关闭**。届时可把“从 hold 开始执行”限定为 UI 明确动作作为临时上线降级，但这是交互行为收窄，需在评测结果和后续方案中明确给用户 review，不能偷偷退回关键词匹配或自动点击确认。

### 5.3 上下文与压缩

每轮从持久状态注入小型 current_turn_contract、当前 WorkIntent、相关用户原文证据、已绑定 Requirement snapshot。将其与历史聊天摘要区分；不能让总结后的助手文本覆盖这些来源。

只注入当前焦点及相关未决项，其他历史可按来源 ID 读取。更新使用 revision/hash，保留项目 Agent session 和稳定工具 schema。长期约束的寿命属于持久状态，不依赖它恰好还在最近 N 条聊天里。

给 Worker 的上下文只包含已获授权的 Requirement revision、当前 Task input、必要的交付证据、当前 Assignment/Column 目标和 Worker 自己的续接 context。讨论中的未来版本不自动进入正在执行的 Worker。

### 5.4 队列、重启和事件

- 用户 Job 按项目既有串行执行/租约处理；解释只读取该 Job 用户消息序号以前的事实。后面一句“开始”不能反向授权前面的讨论 Job。
- resolution 的保存与 Job 契约变更在同一事务，使用 expected revision + fencing；崩溃后从回执恢复，不重新猜一遍已提交语义。
- 每次执行能力均从持久 Job 读取当前契约，而非复用旧 AgentRunSpec.start_task。
- 新的暂停请求要走现有 cancellation/fencing；“讨论新增功能”本身不等于取消在途旧动作。不要声称普通消息入队瞬间能撤销已经发生的文件/进程副作用。
- mailbox 回合根据来源 Task 的 Requirement revision/grant 继续已授权的监督和返修；不把 trigger_kind != user 当成不受限制的执行许可。
- 关闭、暂停、过期版本与晚到结果继续受既有 scope/fencing 校验。无法唯一归属的事件只读整理，不能任选一个当前 Requirement。
- 讨论边界是“本回合及当前草稿能做什么”，不是项目全局停机开关；已有工作是否继续由其 grant/Task 状态决定。

## 6. 执行入口与能力层的一致性

新增一个可复用的 TurnAuthority 服务，由 AgentToolBatchExecutor 和 CapabilityRegistry.dispatch 共用同一个持久事实解析与检查函数。前者负责整批协议验证和清晰错误；后者是实际副作用入口，不能只靠前者。

判定以注册的 side_effect_kind、当前契约、scope、调用者绑定为输入，不读自然语言关键词。讨论允许 read，允许准确登记的会话草稿/回合解释；禁止发布 Workflow、保存可执行 TaskPlan、创建 Task、写项目实现文件或启动命令进程。

普通 shell 即使模型声称是只读，仍按 process 处理；本次讨论优先用已有专门读能力，不新增一套 shell 命令字符串安全分析器。Conversation 草稿存在 DB，不通过通用写文件能力绕过讨论边界。

现有直接 API/内部调用也要盘点：明确用户按钮操作可构建显式 action context；不能因为没有 agent_run_id 就默认允许一个来自 Agent 的业务调用。Worker 保留 Assignment 绑定检查，不能套用 Conversation 的回合语义。Store 内部服务不冒充用户授权入口。

授权与业务有效性分别检查：grant 允许实施某个范围，也仍然必须满足 Loop 入口、TaskPlan schema、readiness、工作区占用与现有幂等/副作用未知状态处理。不能用新的 grant 绕开这些约束。

### 6.1 与已有 scope extension 的衔接

begin、continue、extend 必须分开实现，避免重新引入“旧 Requirement 已关闭，所以用户无法开始新需求”的旧 bug：

- begin：当前新草稿有明确执行依据后，resolve 的事务建立 grant 与初始 Requirement 绑定；未讨论清楚且用户未委托决定的范围不进入执行。
- continue：复用该范围的既有 grant/Requirement revision，记录本轮延续依据；不每轮生成一个新需求，也不重新要求用户同意已有目标内的合理返修。
- extend：resolve 保存绑定旧需求及新草稿快照的 pending_scope_revision grant。该状态只允许读取与针对该目标调用 project.scope.revise；不能先创建新 Task。允许从 closed 旧需求进入这条明确的用户扩展路径。
- project.scope.revise 成功时，在其现有事务中绑定返回的新 Requirement revision/Workflow revision，并把 pending grant 转为 active；失败则保持 pending，不对旧 scope 做半次切换。新 TaskPlan 只能引用新 revision，旧 Task/Worker 仍引用旧快照。
- 新业务工具即使叫 control，也不自动属于 targeted_control；暂停/取消/恢复必须匹配用户指定的对象与动作。用户在“只讨论”模式里同时要求暂停时，进入显式模式冲突反馈，不悄悄把整个回合升级成执行。

回执恢复必须得到同一组 grant/Requirement/Workflow ID。新增 grant 状态不是新的 Agent 权限层；只有这一个项目 Conversation Agent 在行使用户已经给出的指令。

## 7. 从讨论到可执行需求：消除自报确认

### 7.1 需求确认的含义

本次用户第二轮明确决定 token、锁定规则、注册方式和前端栈，但持久化、防刷维度仍待建议。这些状态必须进入讨论草稿。Agent 的建议记作 agent_proposed；用户说“按这个方案开始”可以一次性接受唯一对应 proposal 并授权实施，无需再逐字段确认。

用户说“其他你决定，直接做”时，允许保存 delegated_choice，Agent 补足合理实现细节。不能把“确认所有字段”做成强制审批表；只有确实改变产品行为、存在矛盾或用户明确留待讨论的事项才阻塞。

对于“嗯”“可以”：按它回答的是哪个问题理解。“可以用 Element Plus”只决定 UI 库；如果上一问题明确是“是否按方案开始”且无冲突，可以视为开始答复。此项属于模型语义评测，不写中文特例代码。

### 7.2 软件 Loop 的入口证据

当前 Loop 要求“需求文件存在、用户已确认边界”，但 Task input 只有 path 和 true。建议保留这两个业务字段的兼容性，增加 **TaskPlan/Requirement 的通用来源元数据**，不让业务 input 变成授权令牌集合：

- 保存已接受 proposal/执行请求的 scope snapshot、来源消息和 hash。
- 开始后由 Conversation Agent 写需求基线，并获得文件/artifact 回执；before-start 讨论草稿不写入工作区。
- 软件 Loop 的入口声明引用该范围快照及基线文件证据。TaskPlan 保存/创建核对当前 Workflow 所属 Requirement revision、grant 的 snapshot、文件存在性与内容 digest。
- requirements_confirmed=true 仍校验，但不能单独满足该入口；没有来源证据必须返回 EntryEvidenceMissing。
- 来源元数据在 schema 中声明，不能临时在严格 input 下加未知字段；其他 Loop 按自己声明的 entry evidence 要求使用，不把软件专用字段硬编码进 AgentCore。

文件存在性/digest 是动态准入；用户授权引用和不可变快照绑定是静态条件。文档内容是否完整表达需求仍由主 Agent 整理和 requirements Column 复核，不能声称 hash 能检查语义正确性。

首个 requirements Column 负责核对和形成可追踪基线；不让它在无来源情况下自行推断用户已确认。确认后的基线路径应使用 Task input，不能在支持多独立交付 Task 时始终写死同一个 docs/requirements-baseline.md。实际补丁需要检查该 Loop 各 Column 对路径的引用，保证多个范围使用不同基线时不会互相覆盖。

这涉及 Loop 契约与指令改变，应发布新 Loop 版本（建议 1.2.1）并固定新 digest；不修改旧 Workflow 的冻结快照。历史 1.2.0 不伪造确认来源，按第 11 节兼容处理。

### 7.3 入口证据的最小声明形式

建议在 WorkflowPlan.task_contract 增加可选 entry_requirements，在 TaskPlan 增加 entry_evidence 元数据；不复用现有用于文字说明的 required_context/entry_meaning 当作可执行规则。最小版本仅实现两种规则：accepted_scope_snapshot 与 input_file_snapshot，不做任意脚本规则引擎。

~~~json
{
  "entry_requirements": [
    {"key": "confirmed_scope", "kind": "accepted_scope_snapshot"},
    {"key": "requirements_file", "kind": "input_file_snapshot",
     "input_pointer": "/requirements_path", "scope_evidence_key": "confirmed_scope"}
  ]
}
~~~

TaskPlan 对应 evidence 只引用服务器已有的 scope snapshot ID、文件写入/artifact 回执 ID。实际路径、hash、project、Requirement revision 从回执和持久 Job 查得，不采用模型自填的“检查通过”字段。编译校验声明/引用形状，动态准入验证来源、绑定与当前文件；证据缺失时明确报错。

accepted_scope_snapshot 内容不可变。requirements Column 后续生成追踪矩阵或补充验证结果，属于工作产物，不重写用户当初接受的 scope 快照。若写回同一路径导致内容变化，不自动更新原始证据 hash；后续新 Task/rerun 的入口检查必须区分原始接受快照与当前工作产物，选择有效来源或报告变化，不能悄悄把最新文件当作新一版用户确认。

本规则核验“哪个文件、基于哪个已接受范围”，不能确定性核验自然语言文件内容完全忠实；后者仍由 Conversation Agent 和 requirements Column 负责。首个软件交付 Task 可先用固定基线路径；多范围 Task 必须完整对齐各 Column 输入/输出契约，而不只是换掉 Task input 的路径。

## 8. Task 是交付单元，Column 是生命周期阶段

软件示例应当产生一个完整交付 Task，经过 requirements、domain_design、backend_development、frontend_development、integration_review、system_test、delivery、accept。

这不意味着所有软件项目永远只准有一个 Task。多个真正可独立验收的交付范围可以有多个 Tasks，各自独立输入/身份，依赖用于表达交付间顺序；前端/后端/review 这些词本身不是拒绝依据。

实施点：

1. loop.inspect 的模型可见结果优先展示 flow_unit、entry_meaning、Task Contract、identity_pointer，以及“Task 与 Column 的关系”。列出 Columns 时清楚标明它们是单个 Task 的生命周期。
2. 工具/规划提示增加一个正确的软件单 Task 示例和一个合法多范围示例，去掉“把流程步骤逐个建 Task”的暗示。小说章节等真正多交付单元仍保留。
3. 统一编译时先按规范化 input 计算 logical_task_key；同一计划内重复非空身份必拒绝，并返回冲突 refs、identity_pointer、值及合并方向。
4. 不因为 Task refs 不同就视为不同交付；不按名称包含 backend/design 来硬拒绝。
5. 计划之间已存在的身份冲突，在保存时给出可创建性检查，创建事务内再次检查；需要返修/继承时使用已有 reopen/rerun/successor 语义。
6. identity_pointer 缺失的旧/通用 Loop 不臆造身份；保持已有图验证，并提示 Agent 根据 flow_unit 规划。无法从 schema 证明交付独立性时仍需语义评测，不能靠给八个阶段换八个文件名算修好。

## 9. TaskPlan 保存与创建共用静态编译

### 9.1 新的职责拆分

建议新增 app/v1/services/task_plan_compiler.py，提供纯准备入口：

~~~python
compile_task_plan(plan, frozen_workflow, task_contract, capability_registry)
    -> CompiledTaskPlan

check_task_plan_admission(compiled, scope_snapshot, existing_tasks, entry_evidence)
    -> AdmissionResult
~~~

CompiledTaskPlan 包含规范化 plan、各条目 effective_input、readiness、logical_task_key、静态依赖图、冻结 workflow digest、compiler_version 和 input hash。它不创建 Task、不执行命令，也不需要先有数据库 task_plan_id。

静态步骤固定为：

1. 验证 TaskPlan 结构、ref 唯一性、依赖存在与无环。
2. 按 Task input schema 规范化已有输入；不补确认、scope、路径等缺失的业务事实。
3. 规范化 exact 指针，校验它们指向已有字符串叶子，解码 escaped_value 一次并应用。
4. 验证最终 Task Contract，包括 additionalProperties、required、const。
5. 验证 agent_execution、exact 使用约束、可在此时解析的 deterministic capability 参数。
6. 构造/验证 readiness，检查 deterministic deliverable coverage。
7. 计算身份并拒绝计划内重复身份。
8. 生成不可变编译结果及 hash。

只依赖 Task input 的引用必须此时解析。依赖未来 Column 输出的引用标为 deferred，不伪造值，也不为了让 save 成功跳过已可解析的静态错误。

保存：先编译，再检查当下动态准入，最后事务保存规范化 plan 和编译证据。hash 命中也不能跳过静态校验；重复保存已经合法且已 materialize 的同一 plan 返回原记录并说明当前状态，不因它自己的 Tasks 造成“重复交付”假错误。

创建：加载冻结 plan，复用同一编译逻辑；比较版本/hash 后，在事务内重查 scope、身份、依赖与入口证据，原子 materialize 整个 graph。现有 task.create 请求一个 ref 会 materialize 全图的语义需要在工具描述中明确，不能让模型误以为每次只创建一个条目。

保证范围是：**相同冻结定义与输入，在静态合同上 save/create 一致。** 不承诺保存后永远能创建，更不承诺最终实现成功。文件变化、scope superseded、另一事务抢先创建同身份等必须能使创建返回明确动态冲突。

### 9.2 exact 的精确定义

- pointer 相对于 Task.input；/requirements_path 合法，/input/requirements_path 只有在实际 input 内真有 input 对象时才合法。
- 保留已支持的完整运行时前缀 /input/task/input/... 的规范化。
- 不能通过移除任意 /input 前缀猜测用户意思。
- 目标必须存在且为字符串；不能创建中间对象、新增叶子、把 boolean/number/null/object/list 改成 string。
- 列表仅允许现有非负索引，不允许 - 或负索引追加/倒序访问；保留 JSON Pointer 的 ~0/~1 语义。
- 规范化后重复指针拒绝；父子路径冲突拒绝。
- 合法 exact 可覆盖原来的字符串内容，用于恢复 Provider 丢失的 LF/空格等；不是要求原值已经完全一致。
- escaped_value 仅在解释 exact 声明时解码一次；持久保存原声明和最终文本，读取最终文本不再反转义。编译重跑必须幂等。
- 对 agent 类型任务，声明了 exact 也必须验证存在性/类型；没有 deterministic string 引用时不要求模型额外生成 exact 元数据。

原始错误应在 save 返回 ExactInputTargetMissing，并标出 /tasks/0/exact_input_strings/0/pointer；布尔目标返回 ExactInputTargetType。给出“相对 Task.input 的已有字符串字段”提示，不自动修改用户计划。

### 9.3 参数错误的处理选择

本次**维持 object 类型的严格要求**：plan 传为 JSON 字符串时返回 ArgumentShapeError，提示“发送对象，不要再 JSON 序列化一次”，并提供短结构示例。

不加入通用多层 JSON 解包，不以字符串解析成功为由扩大所有契约的隐式类型转换。现有 Provider wrapper 的纯语法兼容继续保留并回归，但不能补 requirements_confirmed=true。这比初稿的自动字符串解包更易保持可预测性，也与 Codex 的明确参数解析错误反馈方式一致。

### 9.4 历史计划和事务

旧计划没有 compiler_version 时用相同编译器重新验证；失败原样保留，不自动去掉 exact 或把八项合并后执行。新的正确计划有新 ID 和来源关系。

同一个计划的所有 Task 继续全图预校验、同事务插入。任意条目失败，Task 表和 task_created 事件均为零；失败 receipt 可以保留。并发身份检查与插入必须在同一事务，而不是只在 plan.save 前 SELECT 一次。

本次不引入跨模型多轮调用的大事务，也不新增强制 delivery.commit 编排工具。loop.apply 成功后其他工具仍可能失败，采用明确的部分提交语义；不能用数据库 rollback 假装撤回文件或进程结果。

## 10. 错误恢复与用户可见结果

### 10.1 错误合同

每个失败输出提供：

- code、category（argument/static_contract/dynamic_admission/turn_boundary/external_unknown）。
- capability、task_ref、json_pointer、expected/actual。
- failure_disposition（rejected_before_effect / committed / outcome_unknown）。
- 可操作 repair_hint；是否能通过修改参数重试。
- 原始异常在审计中保留，用户界面不展示整份计划或巨大 schema。

收到正确拒绝的 Agent 可以读相关定义、改变参数再试，或自然回答阻塞。没有副作用的正常讨论不因缺少 loop.apply/task.create 回执而被强制续跑。

相同失败副作用重复提交的保护继续保留；不能通过增加固定重试次数掩盖问题。终止时从最后一个非重复的业务错误提取原因，协议重复只是停止理由。不要任意选更早已经被修复的 schema 错误。

### 10.2 事实反馈

扩展现有 ConversationReport/失败 renderer，统一读取当轮 action ledger + 数据库：

- created/reused workflow、plan、task 分别统计。
- Task 创建数量、实际开始运行数量分别统计；queued 不渲染成“已启动”。
- 只统计本轮归属对象；项目过去已有 Tasks 不应算本轮新建。
- 不确定的外部结果明确为待核实，不算失败且无副作用。

针对原现场，预期简短回复：

> 本轮没有启动开发，创建任务数为 0。流程和计划已保存，但计划中的 exact 输入指针多了一层 input，任务创建被拒绝。需要修正计划后才能创建任务。

这只是历史失败恢复示例；修复后原两轮讨论不应留下那些流程/计划。讨论门禁拒绝错误调用后，最终回复仍应回答用户的最小方案问题，并准确说明没有启动开发。

## 11. 兼容与历史数据策略

1. 新 Web 默认 mode=auto，不再恒传 start_task=true；提供显式“只讨论”和绑定具体 proposal 的“开始该方案”动作，刷新/重启能看到当前状态。
2. 旧 API start_task=false 映射为本回合 discuss 硬上限；true/未传仅代表 legacy auto，不代表用户已授权。若新模式与旧 false 冲突，保留 false 上限并返回清晰结果，不能悄悄放行。
3. 新队列 Job 使用新契约；旧未执行 Job 不能凭 start_task=true 直接产生副作用，要按其当时消息边界解析。模型服务暂不可用时保持 unresolved，不用正则兜底。
4. 已完成历史 Job 不重新执行，也不改写其原始 start_task/消息/回执。恢复旧讨论状态时保留来源、标注 recovered；缺乏可判定证据的旧新工作请求保持未解析。
5. 已存在的 Task/Assignment 继续按其冻结 Requirement 与旧执行绑定运行。迁移可登记 legacy_existing_work 关联，只覆盖已有对象的生命周期；不凭此获得新范围规划/扩展授权。
6. 已有旧计划重读仍可展示；新增 materialization 执行新校验。返修/续跑按既有身份和范围处理，不强行给历史成果补一份虚假的用户确认。
7. 原问题项目的 Workflow/Plan 暂保留审计，不自动删除、不自动重启。后续修复完成后如要恢复该项目，先展示残留状态，主 Agent 根据用户新的明确意图创建正确计划。
8. Schema 使用增量迁移。部署前先在临时 DB 副本验证；回滚到旧二进制会重新使用错误的 start_task 语义，因此不能把代码降级称作语义安全回滚。需要停新 Conversation 派发或保留新门禁再回滚其他补丁。

## 12. 文件级实施计划

以下顺序用于后续实施。本轮只交付本设计。

| 批次 | 文件/模块 | 具体改动及完成标准 |
| --- | --- | --- |
| A：数据契约 | app/v1/domain.py；新增 app/v1/conversation_intent.py；新增 app/v1/repositories/conversation_intent_repository.py；schema_repository.py | 定义请求模式、草稿/解释/grant；不可变 revision；Job 快照与幂等迁移；不新增 Agent 角色 |
| B：入口与门禁 | app/v1/conversation.py；agent_repository.py；capabilities.py；agent_tool_execution.py；tool_protocol.py | unresolved 只读启动；注册 resolve；统一 authority；禁止 resolve 与业务副作用同批；移除 raw start_task/event 绕过 |
| C：上下文与回复 | app/v1/agent_prompt.py；agent_models.py；conversation_report.py；agent_runner.py 的通用协议接入点 | 注入事实快照；纯讨论 report 保存解释；运行时不在 Core 中判断业务词语；失败原因与部分结果渲染 |
| D：交互 | app/v1/api.py；app/web/static/dashboard.js；app/web/static/pages/projects.js | mode/action 请求；只讨论与开始方案状态；不默认提交开始；旧接口兼容 |
| E：统一准入 | 新增 app/v1/services/task_plan_compiler.py；planning_repository.py；store.py；capabilities.py；task_identity.py；task_graph_admission.py | 共用准备/编译；严格 exact；计划内身份；静态与动态分离；全图创建事务 |
| F：需求入口 | scope_repository.py；Loop 契约相关 domain/校验；loops/ddd-software-delivery 版本文件 | grant/snapshot/基线证据；所有基线路径引用审查；新 Loop 版本，旧快照不变 |
| G：验证和交付记录 | tests 中下列专项；新增真实多轮评测脚本/fixture；本文件实施附录 | 先写可复现失败的用例，再改对应逻辑；记录新执行证据及语义评测限制 |

A–G 都完成并通过相应验证后才能关闭本问题。E 可以独立实现，但单修 exact/identity 不能宣称修复“讨论被执行”。不通过仅修改当前 DB 的 start_task 或提示词完成验收。

重点复用现有 tests/test_conversation_contract.py、test_api_web_contract.py、test_capability_contract.py、test_domain_contract.py、test_failure_transparency_contract.py、test_scope_extension.py、test_persistent_agents.py、test_agent_recovery_and_fencing.py、test_p0_runtime_regressions.py。新增 test_conversation_intent_contract.py 与 test_task_plan_compiler.py；测试组织以这些实际模块为起点，不按旧测试数量下结论。

## 13. 回归设计与验收门槛

### 13.1 确定性测试：验证宿主真的挡住副作用

| 组 | 输入/注入 | 断言 |
| --- | --- | --- |
| 边界 | unresolved 或 discussion 时注入 loop.apply、plan.save、task.create、文件写、process | 业务表/文件/进程无变化；错误为 rejected_before_effect |
| 绕行 | 直接 registry.dispatch；陈旧 spec.start_task=true；不同 scope；Worker 调 resolve | 同一门禁结果，无额外角色授权路径 |
| 批次 | resolve + loop.apply 同一批；恢复未完成 batch | 整批拒绝，零业务副作用，无待重放悬空 operation |
| 来源 | 助手承诺、工具文本、跨项目/未来消息作为 release 依据 | 非用户或越界来源被结构校验拒绝；用户消息内引用句的意思另由真实语义评测验证 |
| 持久性 | hold 后回答选型、重启、压缩历史、相邻 Job 排队 | 已保存 hold 不因缺字段、重启或后来的开始消失 |
| 并发 | 同 Job 两个 resolution、旧租约副作用、重复请求 | 唯一结果/幂等/fencing，无状态覆盖 |
| 旧工作 | 新讨论 + 旧 Worker 完成事件；暂停/关闭后迟到消息 | 不污染 Worker context；不生成新范围；按既有状态监督 |
| exact | 原始错误路径；bool；缺失叶子；列表越界；嵌套合法 input；~0/~1；LF/CR/空格/Unicode/反斜杠 | 错误在 save 发现；合法文本保持；编译重跑不二次转义 |
| 静态一致 | 缺字段、错误 capability 参数、readiness、deliverable coverage | save/create 使用同一错误码/指针规则 |
| 身份 | 八个同路径阶段；合法多范围；同 plan 重放；并发两 plan 相同身份 | 阶段重复拒绝；合法图接受；事务无半张图 |
| 动态状态 | 保存后 scope 变化、文件 digest 变化、同身份被占用 | create 明确动态拒绝；不把 save 视为永久担保 |
| 入口证据 | 仅 input.confirmed=true；绑定错误 proposal；真实接受与文件回执 | 无来源拒绝；正确来源可用；不隐式填 true |
| 失败回复 | loop.apply 成功后 plan 失败；create 全图失败；queued/running 混合 | 工作流/计划/创建/启动数量准确，显示最后真实原因 |

这些测试预设合法的讨论契约后注入错误工具调用，证明的是门禁。**它们不证明模型一定能把原始文本解释为讨论。**

### 13.2 真实模型多轮评测：验证语义没有再次走错

从生产消息抽取最小脱敏 fixture，使用临时项目/临时 DB 与隔离工作目录；使用当前实际配置的 Conversation 模型与 Provider 适配路径。记录模型标识、配置、prompt/schema/Loop digest、完整消息、resolution、工具轨迹、DB diff、文件 diff；不能仅检查最终中文回复。

核心序列：

1. 原消息 411：询问关键决策/流程；零 Requirement 新执行绑定、零 Workflow/TaskPlan/Task、零实现文件。
2. 原消息 413：回答持久化与防刷最小建议；记录已决定与尚待接受项；继续 hold，仍零业务副作用。
3. 用户明确“按你刚才建议的最小方案开始开发”：引用唯一方案，解除 hold，生成执行依据；准备基线后应用新软件 Loop，保存并创建 **一个** 完整软件交付 Task。
4. 后续“现在进展如何”：只读汇报，同时原 Task 继续；核对没有重复创建。
5. 在临时环境跑实际 Workflow，验证至少成功进入 requirements 并跨到下一 Column，最终软件交付验证则记录真实构建、测试与验收结果。只到 Task 创建不能写成“项目已交付”。

最低语义用例集覆盖：

- “先别实现”“这里只讨论架构”“先给我方案，我 review 后再改”及中英混合表达。
- “这些选型都可以，数据库你有什么建议？”“可以用这个库”与“可以，按方案开始”的区别。
- “继续”“接着说”“继续开发已有任务”的上下文区别。
- “如果我说开始，你会做什么？”、引用一段含“开始开发”的日志/代码。
- “不用再问，剩下实现细节你决定，直接交付”必须能执行，不陷入反复确认。
- “先讨论第二期，第一期照常做”、旧需求关闭后的新范围、小说 7–12 章续作。
- 助手主动许诺、用户尚未答复、问答超时、被取消的明确开始 UI 动作。
- 同一需求给相同路径拆多个阶段、给八个阶段伪造不同路径，以及两个真正独立范围。
- 第一轮“只讨论”，随后明确开始，再切回讨论：既不提前执行，也不永久无法执行。

最低执行要求：核心三轮序列独立重复 5 次；其余至少 20 个多轮变体各运行 3 次。所有必守边界不得出现擅自开始；明确开始的正常序列不得出现稳定无法启动。失败样例必须进入固定回归集，不能删掉偶发失败再报告通过。有限样本零失败不是数学保证，记录样本量和局限。

如果实际 Provider/模型不可用，只能报告确定性回归结果和语义评测未完成，不能用 mock 的正确工具选择替代真实验收。

### 13.3 后续执行命令与报告要求

后续在 D:/workspace/DevWerk/DevWerk 使用项目 venv，先运行新增专项与受影响专项，再执行完整现有测试集。例如：

~~~powershell
.\venv\Scripts\python.exe -m pytest tests/test_conversation_intent_contract.py tests/test_task_plan_compiler.py tests/test_conversation_contract.py tests/test_capability_contract.py tests/test_scope_extension.py tests/test_failure_transparency_contract.py -q
.\venv\Scripts\python.exe -m pytest tests -q
~~~

这两条是实施后的计划命令，当前新增测试文件尚未创建，也没有执行结果。真实模型评测脚本在 G 批次落地，报告必须分开列出静态/集成测试、真实语义评测、完整交付验证，不把其中一种通过当成另外两种通过。

## 14. 本次 review 的关键取舍

| 选择 | 理由 |
| --- | --- |
| 单一 Conversation Agent + 宿主持久边界 | 保留 Hermes 式主 Agent 能力，不再产生上级 Main Agent |
| auto 自然对话 + 显式 UI 动作可选 | 不强迫每次审批；为明确只讨论提供确定性上限 |
| 结构化同回合 resolve，而非额外分类 Agent | 不引入第二份需求理解和独立 context；错误有来源与回执可查 |
| 不用语义关键词引擎 | 避免不断补“开始/继续/确认”的例外，保留通用 tool loop |
| 不把 schema/quote 当作语义证明 | 真实模型评测决定这类 P0 是否可关闭 |
| 统一静态编译 + 创建时动态检查 | 尽早拒绝必失败计划，同时正确处理并发和外部状态变化 |
| 跨工具保留部分提交 | 不引入大事务/隐藏编排；用户能知道真实留下了什么 |
| 新 Loop 版本 + 旧工作冻结 | 对齐软件入口证据，同时保持现有任务和 Worker context 的归属 |

本轮交付物仅为本设计及 Bug 记录中的设计链接。生产数据库、受测项目工作区、服务与运行时代码均未修改；下一阶段按此方案实施时，仍须先建立失败回归，再提交代码修改和新的验证结果。

## 15. 2026-09-13 实施补充（进行中，尚未验收）

第 13–14 节中的“尚未实施”描述保留为设计阶段记录。当前已实现持久讨论边界、同 Agent resolve、统一 TaskPlan 编译、软件入口证据和 UI 模式，正在回归。生产 DB 和服务未修改。

真实 Provider 回归暴露了结构化协议的可用性问题：模型把 execution_request 的无执行值输出为 JSON null，并在错误后重复提交；另有决策枚举、版本号和最终回复格式错误。失败样例保留在临时隔离评测目录，不能将确定性测试通过视为语义验收通过。

本次调整：仅对 execution_request 的 null 定义显式“无执行”含义，归一为 none；execute 仍必须携带 begin/continue/extend，null 永远不能授权。保留严格字段与来源/版本校验，不添加自然语言关键词推断，不自动修复业务 input。补充失败回复的实际创建/复用/启动统计，以及 continue 复用相同 Requirement revision 的有效执行依据，避免同一基线因继续请求而无故失效。回归结果另写实施报告。

进一步真实回归 `devwerk-intent-eval-1g24aqcw` 中，当前配置 MiniMax-M2.7 将第二轮技术选型与“请给最小方案”仍解释为 begin，反复修改协议参数后建立了 Workflow，证明只在全量工具集前添加 resolve 门禁不足。继续按单 Agent 架构调整：每次 Provider 调用按持久 phase 提供工具，unresolved/discussion 只展示读取工具和必要会话工具，业务计划 schema 在执行确认后才进入上下文；当前请求单独携带宿主生成的边界提示，区分用户要求建议与要求实施。修改 focus 不允许清除已有 hold。协议拒绝提示应要求重新理解用户诉求，不应暗示机械补 release_hold 就能执行。这仍由同一个 Conversation Agent 在同一上下文完成，未新增分类 Agent；最终仍须以真实回归验收。

变体评测进一步发现，大量正常讨论在最终回复时因为冗余 resolution/draft JSON 格式失败。调整最终协议的职责：已经持久确认为 discussion、且本轮没有成功业务副作用的用户回合，允许自然语言结束，并由宿主附上“本轮仅讨论，未新建任务”的事实说明；此路径不能创建/释放执行依据，不能改变已保存决策。未解析回合与执行结果仍使用原有严格结构化报告。新增带完整 schema 的 conversation.draft.update 工具保存讨论决策/方案，供需要更新草稿的同一个 Agent 使用；保留合法结构化 final draft_update 的兼容入口。这样不让对话排版协议成为业务权限机制，也不允许自然语言冒充执行结果。新增测试必须证明 execution/unresolved/成功副作用回合不能使用此自然语言路径。

`devwerk-intent-eval-twdqass9` 的开工轮暴露另一项接口问题：模型更新已有问题状态时只提交 key/state，却被要求重抄 question；随后又把用户原文中的“由你决定”抄成“由我决定”。将 open_question_updates 明确定义为局部更新：已有 key 可只更新 state，未提交的正文、来源和 blocking 标记保持；新 key 必须提供问题正文。来源依据优先引用不可变用户 message_id，quote 改为可选的精确摘录；提供 quote 时仍严格校验，不接受改写。执行仍必须显式引用当前真实用户消息，其他角色、未来/跨项目消息仍拒绝。宿主不从这些字段推断语义，也不自动授权。此调整减少重复输入已有事实的负担。

## 16. 2026-09-14：消除历史约束与当前请求的冲突表述

全量确定性回归已得到 347 passed，但 `ip2lv5ub/variant-2-0` 中，“继续”接在只讨论与助手自行承诺之后仍创建了一个 Task，故 P0 保持打开。

进一步检查发现当前请求封装器告诉模型“当前请求优先于历史对话指令”，没有限定为明确的约束变更。这会把简短延续误当成旧约束失效。修复方案：当前请求与未解除约束共同构成权威输入；含混的延续继承已获准的交互方式，助手自己提出的下一步不能提升该方式。将 hold 的真实用户消息来源直接置于当前边界上下文，持续讨论时保留原 hold 来源；明确解除后清空。不新增分类 Agent、不用关键词匹配，不改变当前模型配置。加入讨论中延续与明确要求开始的成对示例，使用同一 Agent 重新评测。

## 17. 同一 Agent 的解析上下文与结束协议

扩大样本后 `evhpchgm/variant-2-0` 再次误启动；`gfs52_gw` 则已创建一个合法 Task，却因最终回复 JSON 不合法而将用户 Job 标为 failed。说明最终自然语言要求和持久边界握手仍互相干扰，完整旧工具轨迹也会影响新的回合判断。只添加更多示例不足以关闭问题。

继续按单 Agent 架构修复两处接口：

1. **解析阶段的上下文投影。** unresolved 用户 Job 的 Provider 请求保留同一 Agent 身份、当前权威请求、WorkIntent/hold 来源、项目当前状态，以及最近可见对话作为带角色的参考资料；暂不重放此前 Job 的原始工具调用轨迹。该阶段只展示并要求 `conversation.turn.resolve`，模型先理解本轮用户需要讨论、状态、控制还是执行。解析完成后恢复同一会话的正常上下文与该 phase 可用工具。历史仍完整保存在同一 Session，没有新建/委派分类 Agent，没有更高权限的 Main Agent，也不修改模型配置。需要读取文件后答复的请求可先解析为讨论/状态，再正常读取；已明确要求实施的请求解析为执行后再细化实现。
2. **工具化结束协议。** 新增 `conversation.reply`，用简单的 mode/message/task_ids/tool_call_ids 工具 schema 提交最终报告。必须单独调用，宿主仍用现有事实校验/渲染器，校验成功后结束原 Agent Run，不创建新 Run/新 Agent。执行或状态报告格式失败时，下一次 Provider 请求明确选择此工具修正，而不是要求模型重写一串未受工具 schema 约束的 JSON。已经解析的纯讨论仍可自然语言结束；原有合法 JSON final 保持兼容。

测试必须覆盖：解析时没有旧工具轨迹、讨论后仍可读取、执行后恢复工具、混合 reply+mutation 零副作用、伪造 Task/回执拒绝、reply 成功结束同一 Run、自然语言不能冒充执行结果。所有真实评测失败仍保留，旧批次不作为最终通过依据。

## 18. Task 创建后的异步交接契约

`4hr4uhag/original-0` 的前两轮均保持讨论，第三轮成功保存计划并创建一个软件 Task，随后却把 Conversation Agent 的 ID 当作 Worker 接收者发送启动消息。被正确拒绝后，模型直接编写实现文件，未结束创建请求。测试因该偏离主动中止，不记录为通过。

Task 创建工具应明确返回异步调度语义：创建整个计划的 Task graph，pending 表示等待 Runtime 调度；Runtime 负责依据 Column 创建 Assignment 与 Worker，Conversation Agent 无需手动启动，也不应因尚无 Worker 而接管 Column 实现。在工具描述、创建回执及主 Agent 工作指令中说明这一契约；创建完成后可继续提交独立 Task，随后用实际 Task ID 回复，后续 Runtime 事件驱动监督。保留 Conversation Agent 的文件和系统能力，用于用户明确要求的直接操作与计划入口材料，不新增上级权限角色或基于路径的语义猜测。回归检查 Task 创建后不会由 Conversation Agent 在当前回合写实现代码，并实际执行一次 Runtime step 验证首个 Column 交接。

## 19. 回复中断恢复与空响应

结束工具增加后，恢复分支必须区分 Column completion 和 Conversation reply：已持久的 reply operation 不能送入 Column 完成校验器，恢复时重新根据实际 Task 状态渲染会话报告，并保留同一 operation 身份。以真实 SQLite operation 回放验证，不再执行业务效果。

`s2jk1glg/variant-14-0` 的按钮文案讨论未产生副作用，但 Provider 返回空文本且无工具，回合被直接标记失败。会话空响应改为最多两次纠正机会，未解析时仍只请求 resolve，已解析时请求 reply；沿用原回合及执行依据，绝不默认执行或伪造成功。第三次空响应明确失败。Column 完成契约不放宽。补充一次空响应可恢复、持续空响应有界失败测试。

## 20. 讨论草稿的多次更新与实施中版本兼容

草稿从单次 final 元数据扩展为工具后，不能仍限制每个 Job 只更新一次。改为以 `(job_id, based_on_revision)` 标识一次不可变更新：相同内容重放返回原回执，同版本不同内容拒绝，新版本允许继续补充；每次成功更新仍增加 WorkIntent revision，不改变执行边界。新建版本记录表并从旧单次记录表补入，保留旧记录。早期实施数据库若带 `work_intents.job_id UNIQUE`，在初始化事务中重建该表、保留全部快照、去除已不适用的单 Job 唯一限制；复合 `(project_id, revision)` 主键保持。测试覆盖两次更新、幂等重放、版本冲突，以及旧记录迁移后可继续更新。

## 21. 空响应恢复的 Provider 序列化兼容

检查实际 Anthropic 适配器发现，它会将无文本且无工具的 assistant 记录序列化成空 content 数组。第 19 节增加重试后，这类审计记录不应作为下一次请求的对话内容重发。适配器仅跳过完全空的 assistant 消息；真实文本、tool_use 和 tool_result 保持原顺序与内容，审计数据库仍保留原始空响应。补充适配器回归验证空记录被跳过且工具轨迹没有丢失。此修改不改变已冻结的回合意图模型、提示词或 TaskPlan 逻辑；最终报告单独记录适配器验证结果。

## 22. 2026-09-15 实施验收

本方案及第 15–21 节实施补充已落地。最终全量测试 360 passed；真实核心序列 5/5，讨论变体 60/60（120 个用户回合），真实 Provider 空响应恢复 1/1。核心序列均完成合法 Task 创建、无副作用状态查询，以及首个 requirements Column 向 domain_design 的交接。详见 [实施及回归记录](DEVWERK_Discussion_TaskPlan_Implementation_2026-09-13.md)，其中保留全部先前失败批次与最终证据位置。本结论限定为本次讨论边界和 TaskPlan 准入问题，不代表整个软件项目已经构建、测试并交付；生产服务与原用户项目未修改。
