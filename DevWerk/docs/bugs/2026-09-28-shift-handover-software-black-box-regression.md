# 值班交接软件开发黑盒回归：对话、流程与失败记录

测试日期：2026-09-28（Asia/Shanghai）
状态：开发任务尚未创建；四个执行或恢复轮次均失败，测试已停止。

## 目的和边界

使用当前 DevWerk 代码启动本地服务，建立全新项目，以普通产品使用者身份讨论一个可交付的软件需求。先完成超过 5 轮对话，再授权开发。提示词不提 Loop、Workflow、Column、Task、Agent 数量、内部工具或协议。测试者仅通过只读 API 审计实际实体和失败回执；未修改 DevWerk 代码或直接替 Conversation Agent 创建执行实体。

- 项目：`prj_00c8fd54148b44ba8b513238797a0770`
- 项目目录：`D:\workspace\codex-devwerk-project-files\software-conversation-test-20260928-100311`
- 服务：仓库 `startup.bat` 启动，`GET /v1/health` 返回 `ok`、Supervisor 和 Conversation Gateway 为 `running`
- 当前代码分支：`feature/context-provenance-handoff`；测试前工作树已有未跟踪 `devwerk.zip`，本测试未操作该文件

## 测试用例

目标产品是 12 人小团队的内网值班交接工具。每日一人主值班，次日上午交接。交接项分待办和普通备注；待办有接手人、截止时间、紧急程度和状态。接班人须确认接收，可附原因拒绝，交班人修改重提后仍须再次确认。接手人可完成、阻塞及转交待办；阻塞和解除都要记录原因。历史可按日期、状态、接手人筛选。每人独立账号，数据重启后保留。第一版不做排班后台和站外通知。

用户与 Conversation Agent 讨论后接受的实施方案：Flask、SQLite、原生 HTML/CSS/JS，Windows 本机或内网浏览器使用；交付完整程序、使用说明、自动化与真实使用验证，实际检查重启持久化和停止后无残留进程。

验收本次 DevWerk 测试的条件：讨论阶段不提前执行；授权后能以已确认需求创建合理 Workflow 和真实开发任务；后续阶段可以独立检验对应产品行为，并在失败时把具体反馈送回实现阶段；最终交付物满足人类需求。没有真实 Task 时不能宣称开发已开始。

## 过程记录

| 顺序 | Job / 消息 | 普通用户输入意图 | Conversation Agent 的实际表现 | Workflow / Task 提前创建 |
|---|---|---|---|---|
| 1 | `cjob_53c8153f1f2445b6914a51202e6f0b3b` / 555 | 提出值班交接想法，请一起理需求 | 追问团队、节奏、交接内容和痛点 | 无 / 无 |
| 2 | `cjob_676b1002c8db4361acdfbf347f57ca7e` / 557 | 确认 12 人、每日一人、内网和无人认领痛点 | 追问交接项结构与当前交接方式 | 无 / 无 |
| 3 | `cjob_0deef953de2a47b398a01c58471f2936` / 559 | 确认待办/备注及字段 | 追问提交、历史、提醒、确认接收 | 无 / 无 |
| 4 | `cjob_3b0d8342a06a41d580e4d60b22a6660f` / 561 | 确认 9 点提交、拒绝退回、首页和站内逾期标记 | 追问排班、状态流转与逾期标准 | 无 / 无 |
| 5 | `cjob_329f69d473904b2ba66cc6770d692871` / 563 | 确认预置成员、状态、截止时间 | 追问阻塞理由和历史保存 | 无 / 无 |
| 6 | `cjob_cd28d6cf76fa44e188194f45e6461a97` / 565 | 确认阻塞说明、全部历史与筛选，请做使用者完整性检查 | 追问账号、创建权限、转交、拒绝重提、编辑删除 | 无 / 无 |
| 7 | `cjob_373a7ce81bf841dda01978b4f126736f` / 567 | 拍板独立账号、权限、转交和删除限制 | 继续确认接手人是否能自行完成 | 无 / 无 |
| 8 | `cjob_2c5f337cbecd4334bbc3e14bf7c0dd49` / 569 | 确认完成权限，请推荐简单实现和验收方式 | 推荐 Flask + SQLite + 原生前端，列出功能、持久化和逾期验收 | 无 / 无 |
| 9 | `cjob_196d4ce3318340119b2dc5ffacfe0f5c` / 571 | 接受方案，明确要求开始交付 | Job 失败；回复明确为 0 个任务、0 个启动 | 创建 Workflow；Task 无 |
| 10 | `cjob_38c6223a29de46d398a461702dfcb275` / 573 | 要求沿用现有流程继续做，真正启动后再回复 | Job 失败；回复仍为 0 个任务 | Workflow 更新至 revision 2；Task 无 |
| 11 | 消息 575（用户另行发出的诊断提问） | 询问未完成原因 | Job 失败；未解释原因，仍为 0 个任务 | 无新 Task |
| 12 | `cjob_b6272bc372b843cc82f73960cdc2714e` | 再次以普通语言要求从现有成果继续 | 10 分钟后失败；仍无任务，见 F5 | 无新 Task |

讨论阶段的 8 轮均为成功的纯讨论，未抢先创建工作流或任务。需求澄清质量较好，Agent 能发现实际使用中的权限与拒绝重提边界。

## 实体与 Workflow 审计

首次启动轮次成功固化了需求并取得执行授权：需求 `req_225b14d463b34b4fad4c2d17f907e611`；Workflow Plan `wfplan_93ee23d66ffb4e1393da81bf97a5423c`；Workflow revision 1 `wfrev_031a824f2f1b4013873db859eae71739`，来源为预置 `software.ddd_delivery` 1.4.0。第二次恢复轮次创建了 revision 2 `wfrev_a50b149c0c7d493782dfa3aeff57b35b`。

工作流阶段为：需求基线 → 领域与接口设计 → 后端实现 → 前端实现 → 独立集成审查 → 系统测试 → 交付验证 → 最终验收。返修边允许前端问题返回后端、审查问题返回相应实现阶段、系统测试或交付问题返回集成审查。整体阶段划分和单个产品贯穿全流程的方向合理。

但 revision 2 的可执行验收配置不合理：`backend_development`、`frontend_development`、`integration_review`、`system_test`、`delivery`、`accept` 六处均使用同一命令 `python -m pytest backend/test_scenarios.py -v --tb=short`。它却把同一结果分别标作前端场景 S07/S08 和交付场景 S11（停止后无残留进程）的覆盖证据。仅运行后端 pytest 无法证明页面显示、浏览器完整流程、重启持久化或进程清理。当前没有 Task，故没有实际执行，更不能推断产品通过验收。

## 失败记录

### F1：首次启动轮次停在 Workflow Plan 保存

Agent Run：`arun_1d45da8c9b814d68a63dd5823486b113`，12 次迭代、12 次工具调用，最终错误为 `ConversationProtocolStalled`。

1. `conversation.turn.resolve` 初次错误地携带旧提案 ID 与新提案内容，被拒绝；随后缺少 `scope_summary`，也被拒绝；第三次成功。说明初期工具错误尚能修正。
2. 成功写入需求文件、检查并应用预置 Loop。首次尝试自行 `workflow.publish` 时产生不可达列；第二次提交把 `project.files.write` 参数漏掉 `content`。预置 Loop 路径最终仍成功生成正式 Workflow revision 1。
3. 随后尝试保存 Workflow Plan 时，`self_check.tasks_are_independently_reviewable=false`，契约明确要求 `true`。Agent 原样重复失败的 `workflow.plan.save`，被重复失败保护终止。该轮未生成 Task。

影响：用户已授权且实体已部分落地，但未进入开发。错误回复正确承认 0 个任务，未虚称成功。

### F2：第二轮恢复反复在任务输入与验收契约上撞错

Agent Run：`arun_3521ddfcdabd477fb022d7d2e4d099d5`，29 次迭代、33 次工具调用，最终 `ConversationProtocolStalled`。

- 它先再次 `loop.apply`，收到“Project 已有 Workflow，不能再次应用”的明确错误。稍后读到了现有 Workflow。
- `task.plan.save` 先引用不存在的输入路径 `/task/input/contract/content`，又缺少必填 `requirements_path`。
- 补齐输入后，Runtime 指出当前 Workflow 缺少在后端、前端、系统测试、交付与最终验收阶段的可执行验收义务，且 S01–S11 尚未被充分覆盖。Agent 经过两次失败的 `workflow.acceptance.configure` 后，第三次成功建立 revision 2。
- 再次保存 Task Plan 时，先把 `entry_evidence.requirements_confirmed` 写成布尔值，后漏 `title`，再漏 `input.requirements_confirmed`，最后仍把该值填成非 `true`。这些错误来自不同层次的输入契约混淆。
- 已解析的对话边界又被它重复调用 `conversation.turn.resolve`，得到 `TurnAlreadyResolved`；最终它将该调用与读取类工具放在同一批次，又触发“只能单独提交”的协议错误，并重复此失败。

影响：revision 2 存在，但 Task Plan / Task 仍为零。一次普通“接着做”未完成恢复，反而产生大量无效工具调用和错误修订。

### F3：失败原因的普通对话无法答复

用户消息 575 只询问“未完成的原因是什么？失败是怎么导致的？”。Agent Run `arun_d6c17e1935984701a83b7dcc67d085e1` 用了 21 次迭代和 24 次工具调用。它先正确检查 Task Plan、Workflow、历史结果、项目文件与记忆，却又尝试保存 Task Plan，被 `ConversationMutationDisabled: continue discussing; no work was authorized` 拒绝。之后 `conversation.turn.resolve` 携带未声明的 `proposal_id`，再把 `source_message_id` 575 写成字符串，后者重复失败直至 `ConversationProtocolStalled`。最终回复只有 0 任务和技术报错，没有向人解释原因。

影响：正常的状态解释请求无法提供可读答案，而且在未授权工作的轮次尝试了变更工具。Runtime 成功拦截了变更。

### F4：流程验收证据与宣称覆盖不一致

这是对已发布 Workflow revision 2 的静态审计发现，尚未进入 Task 执行。相同后端 pytest 命令被赋予 S01–S11 不同场景覆盖，包含前端画面、交付停止与清理。该配置若被后续执行和验收直接接受，可能出现“命令通过、产品需求未验证”的假阳性。是否最终会被 Runtime 的其他检查阻止，本次尚无执行证据。

### F5：第三次普通恢复仍未创建 Task，且重现协议与输入混淆

Job `cjob_b6272bc372b843cc82f73960cdc2714e` 于 13:40:23 失败。Agent Run `arun_275eb1dc20af4910b512e9ea19b4a7fe` 历时约 10 分钟，55 次迭代、72 次工具调用，其中 18 次工具调用失败。终因重复已失败的 `conversation.turn.resolve` 触发 `ConversationProtocolStalled`。

关键过程：

1. 开始时成功解析本轮执行意图，也读到了当前 Workflow 与历史记录。
2. 它首先把一个产品交付拆成 `domain-design` 与 `backend-development` 等多个 Task；Runtime 用 `DuplicateTaskIdentity` 拒绝，并明确指出同一交付应是一个 Task 穿过所有 Column。随后它尝试单 Task，方向纠正。
3. Task Plan 仍反复违反输入契约：把 `plan` 作为 JSON 字符串而非对象；遗漏 `requirements_confirmed` 或 `acceptance_scenarios`；输入包含不被允许的 `confirmed_scope`、`product_name`、`tech_stack`；把 `requirements_confirmed` 填成非 `true`；`exact_input_strings` 指向不存在的 Task.input 字符串。
4. 它多次再执行 `conversation.turn.resolve`，被 `TurnAlreadyResolved` 拒绝；并再次尝试 `loop.apply`，被“项目已有 Workflow”拒绝。
5. 它更换了场景 ID 为 `sc-001-...` 至 `sc-009-...`，但 Workflow revision 2 的接受检查仍采用 S01–S11，Runtime 报终验场景缺口。随后尝试新的验收配置时又缺少符合契约的场景证据。

最终 API 核查：Workflow 仍是 revision 2 `wfrev_a50b149c0c7d493782dfa3aeff57b35b`；Task Plan 数量 0，Task 数量 0。第三次恢复没有解除阻断，进一步表明不能把它归因于单轮偶发措辞。

### F6：新需求基线写入了与用户确认相反的权限

本轮新写入的 `requirements/baseline.md` 第 64 行写“标记完成：接手人或创建人”。消息 569 已明确约定“接手人可以自己完成，创建人也能补充说明但不能替接手人完成”；先前 `docs/requirements-traceability.md` 第 57–58 行也保留了这一区分。新基线是未被接纳为 Task 的工作文件，但它若在恢复后被当作已确认需求，会直接污染实现和验收。这是 Conversation Agent 将已确认产品事实改写错误，不能仅看 Task 是否创建来忽略。

### 失败调用规模

四个失败 Agent Run（首次启动、第二次恢复、用户追问原因、第三次恢复）合计 **117 次模型迭代、141 次工具调用、41 次工具失败**。这些数字来自各 Agent Run 的持久化计数；本报告未把它们推算成 Token 数量或模型费用。四轮都没有创建 Task。

## 当前判断

- 测试目标 1：多轮自然对话与需求确认，**通过**。
- 测试目标 2：确认后真实创建并启动开发任务，**未通过**；多次普通恢复仍无 Task。
- 测试目标 3：Workflow 阶段与返修结构，**基本合理**；其可执行验收配置 **未通过**。
- 测试目标 4：Column 生命周期、返修交互、交付物、真实测试和 Token 开销，**尚不能评价**，因为执行尚未启动。
- 测试目标 5：失败后通过普通对话恢复，**未通过**；两次恢复和一次原因追问均失败。

所有错误等级仅指 DevWerk 框架和流程，不把尚不存在的交付物称为产品缺陷。此测试存在阻断从已确认需求进入 Task 执行的框架问题，应作为 P0 候选调查。工具契约的拒绝和重复失败保护起了作用，但 Conversation Agent 未能消费错误并收敛到合法调用。

## 测试停止点

停止继续发送同义“请开始”消息；证据已足以复现阻断，更多重复请求只会消耗模型调用。保留项目、Workflow、需求文件和 Agent Run 历史供代码调查。本次没有观察到任何 Column Worker 启动，因此不能出具开发交付、返修生命周期或生成物质量结论。
