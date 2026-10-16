# P0：Conversation Agent 排队 Task 后未结束本轮，规划租约阻断 Worker

状态：方案先行。第六次黑盒 Project `prj_e19f8da8a7264d95aebae7d5eb5ff7c3`：授权 Job 第 61 次工具调用成功 `task.create` 后仍反复规划直至预算耗尽；恢复 Job `cjob_5d54e19cb0ec44ffa8dbb3ae7fe15c7a` 已成功 `task.retry`，新后端 Column Run 长时间保持 pending，而 Main Agent 继续反复 `task.inspect`、读源文件。调度器 `app/v1/services/scheduler.py::claim_task` 明确在 Project Conversation Agent 仍有 `planning` 租约时拒绝启动 Task。Agent 不结束本轮，Worker 就无法执行。这不是前后端代码质量问题。

## 原因和边界

`conversation.py::_process` 对用户轮次的主 Agent 权威说明只强调管理、工作流与防重复建 Task，没有解释“排队成功后必须让出本轮，Supervisor 才能执行”。用户要“监督直到交付”时，模型可能在同一长 Run 内反复观察 pending Task 或阅读项目文件，以为等待有助于推进，实际上自己持有的规划租约阻断了推进。当前 Project 的初始状态快照过期是另一个已修缺陷；仅靠刷新快照仍不能保证模型理解这个因果关系。

## 最小修复

只改 `app/v1/conversation.py::_process` 注入的 `turn_authority.instruction`：明确在 `task.create`、`task.retry`、`task.resume`、`task.reopen`、`task.rerun`、`task.successor` 成功排队后，Main Agent 应用 `conversation.reply` 说明真实队列状态并结束本轮；在本轮轮询 pending Task 不能使 Worker 启动。Supervisor 随后执行，Main Agent 在后续用户轮次或监督唤醒中复查、决定下一步。它仍可在回复前记录必要的监督计划或精确 Worker 输入，但不得为了等待完成反复读同一状态。保持一个 Project 一个真实主 Agent，不加权限层级、状态位或自动假交付，不修改调度锁、Worker、Mailbox 和 Web。

用已有 `tests/test_conversation_contract.py` 的 Conversation Run 断言权威说明包含交接和结束本轮的约束；不额外模拟固定状态机。相关测试通过后独立提交。黑盒新 Project 将验证：Task 创建/重试后 Conversation Job 及时结束，Worker 获得执行机会，Kanban 最终交付目标。旧 Run 使用旧进程代码，不能代表此修复。
