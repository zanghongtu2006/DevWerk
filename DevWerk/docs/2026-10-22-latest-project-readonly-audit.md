# 最近一次主数据库 Project 的只读核查（2026-10-22）

## 对象与界限

本次仅查询 `data/devwerk.db`、`data/logs/devwerk.20261021.log`、Project 文件和当前代码，没有重跑、修复、删除或修改该 Project。主数据库按创建时间排序的最新 Project 是 `prj_84c507c41ab444d393b4e3fa38528471`（`Workflow target contract black-box 20261020`），创建于 **2026-10-20 10:33 北京时间**；主数据库和非 pytest 数据库中没有发现 10 月 21 日创建的 Project。因此“昨天最后的新 Project”是按现存主数据库的最后一项推定的；若指另一个数据库或 Project，需用其 ID 另查。10 月 22 日新建的隔离测试 Project `prj_5a80a4e831e641e9887c7821b6312584` 不在此核查范围。

## 真实过程与结论

用户与 Conversation Agent 进行了五轮自然语言对话。前四轮讨论四人办公室每日值班交接模板的字段、虚构示例和核对方式；第五轮明确要求 Agent 自行设计看板流程、创建首个 Task、执行核对并交付 Markdown 文件。五个 Conversation Job 均标为 `succeeded`。最后一轮的 Run `arun_fe6fafad9edb4b508070fc0b1c4007f3` 执行了 9 次模型迭代、9 次工具调用。

第五轮先成功调用 `conversation.execution.authorize`，随后 `workflow.create` 连续三次失败。三次输入的 `workflow` 字段均为 **字符串**，内部 JSON 也均有 `"columns">[` 的语法错误（应为冒号）；用 `json.loads` 回放分别在第 1 行第 168、168、144 列失败。工具回执均为 `ContractError`、`ok=false`，没有发布图。中间一次 `workflow.inspect` 返回 `output=null`，明确告诉 Agent 没有活动 Workflow。日志中的 Provider 输出和 Tool 输入都记录了同一错误形状，说明这三次是模型给出的工具实参，不是 DB 写入后损坏。旧回执错误文本把整段非法输入回显，未直接给出简短的语法位置和对象类型提示。当前代码后来加入了 `StructuredObjectParameter.normalize`，可解析**合法**的 JSON 字符串并对非法 JSON 报位置，但本 Project 没有在这版代码上复测，不能声称问题已消失。

Agent 之后没有再次成功建图，而是调用 `project.files.write` 直接写出 `duty_handoff_template.md`，用 `project.files.verify` 仅检查 **90 行**，再调用 `requirement.close(status=completed)` 和 `conversation.reply(mode=work_result)`。文件实际存在，大小 2341 字节，哈希与写入回执一致；这是一次真实的**直接文件交付**，不是模型凭空声称文件存在。但数据库里该 Project 的 Workflow Revision、Workflow Plan、Task Plan、Task、Column Run、Column Attempt、Worker Assignment、Mailbox 事件数量均为 **0**。唯一 Requirement 被标为 `completed`，最后的 Job `business_outcome=completed`，却同时有 `workflow_revision_ids=[]`、`task_ids=[]`。因此本次要求的 **Workflow → Task → Column 交付链路没有发生**，不能算看板集成测试通过，也无法由此评估 Column、路由、Mailbox 或验收的可靠性。

## 可确认的问题与风险

1. **显式看板请求被直接工具执行取代（主阻塞）**。`workflow.create` 被拒后，Agent 绕过 Task，自己完成文件写入。`app/v1/conversations/conversation.py` 当前根据 `work_result` 模式就能将业务结果记为 `completed`；`app/v1/conversations/conversation_report.py` 接受直接写入和关闭 Requirement 的成功回执作为工作结果；`app/v1/repositories/agent_repository.py::close_requirement` 只检查是否有未结束 Task/Assignment，零 Task 也允许关闭。这些通用机制单独看都可解释直接工作，但对第五轮**明确要求看板和首个 Task**的请求，没有形成可见的“目标未满足”判定。这个结论只限本次 Project，不把所有直接工具交付都视为错误。
2. **建图参数错误没有得到有效自我纠正**。三次失败都保留同一 `"columns">[` 语法错误；Agent 虽检查无活动 Workflow，却随后改走直接写文件。当前 Tool Schema 的 `workflow` 是对象；本次模型用字符串封装，旧运行版本的拒绝回执又回显了整份载荷。可以确认模型实参错；不能仅凭一次 Run 断言 Provider API 或当前工具 Schema 必然有缺陷。
3. **“核对”证据不足**。成功的 `project.files.verify` 只提交 `expected_line_count=90`，证明文件存在且行数匹配，不能证明五个区块、示例虚构性、字段精简和文字可用性。Agent 在 `conversation.reply` 中宣称这些都已确认，但 Tool 回执没有对应的语义检查；文件内容仍须人工评估。此处是证据范围问题，不是文件不存在。
4. **讨论结论没有进入结构化意图记录**。`v1_work_intents` 的第 1–4 版 `decisions=[]`，`v1_discussion_draft_updates` 与修订记录均为 0。最终运行可读取聊天历史，所以这不能证明当次遗忘；但较长会话若发生压缩，仅靠聊天历史承载已确认约束会有丢失风险。
5. **交付内容与讨论出现局部不一致**。第三轮 Agent 曾将“交接前另一人签字确认”列为过度设计，最终文件仍增加了“交接人签收确认”“接班人签字”栏目；第五轮用户让 Agent 决定流程但没有单独要求签字。应将其视为可能的需求漂移，不能断言该模板整体不可用。
6. **前端/报告可给出误导性的完成印象**。最后的可见回复先说“项目文件已写入；需求已关闭”，再列 Agent 自述，Job 元数据是 `completed`；但 Board 没有 Workflow 或 Task。对只看回复的用户，无法判断看板目标落空。数据库证据比自然语言报告更严格。

以上是只读核查，不包含修复方案或修改结果。完整交付文件在 `data/projects/workflow-target-contract-20261020/duty_handoff_template.md`；关键事实来自上述 Project 的 `v1_conversation_jobs`、`v1_tool_invocations`、`v1_workflow_revisions`、`v1_tasks`、`v1_requirements` 和对应日志。
