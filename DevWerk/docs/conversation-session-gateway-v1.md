# DevWerk Conversation Session Gateway — Version 1 设计

## 1. 目标

每个 Project 拥有一个长期 Conversation Agent Session。用户消息、Task 终态、运行异常和定时监督都进入同一 Session，由它持续理解项目、调整 Workflow、重新调度 Task、执行恢复操作并向用户汇报。

长期 Session 是可持久化、可恢复的逻辑实例。每次模型调用仍是短生命周期 Agent Run；服务重启、Provider 失败或单轮执行失败不得改变 Session 身份，也不得降低后续对话执行能力。

## 2. 核心模型

```text
Web / Runtime Mailbox / Scheduled Review
                    |
                    v
         Conversation Session Gateway
                    |
          project_id 串行化与唤醒
                    |
                    v
       Project Conversation Session
       - 稳定 logical_id
       - 完整用户对话 transcript
       - Conversation Agent Run / Tool evidence
       - Project / Workflow / Task runtime facts
                    |
                    v
        一次短生命周期 Agent Run
                    |
                    v
       回复、治理动作与证据写回 Session
```

`v1_conversation_agents.logical_id` 是 Project Conversation Session 的稳定身份。Session 不依赖某个线程、进程或永久 LLM 连接存活。

## 3. Session 连续性

Conversation Agent 每次被唤醒时恢复三类信息：

1. **Conversation transcript**：用户与 Conversation Agent 的完整、按序、可审计对话。
2. **Operational memory**：此前 Conversation Job、Agent Run、工具调用、治理决定及其实体引用。
3. **Current project facts**：当前 Project、Loop binding、Workflow revision、Task、依赖、运行状态、Mailbox 和待复核事项。

完整历史永久保存在 SQLite。模型输入是该持久 Session 在本轮的可恢复投影；当前请求和当前项目事实始终优先于历史内容。工具原始负载继续作为审计证据保存，不在 Web 对话中冒充用户可读消息。

Version 1 的 `memory` 指上述持久 transcript、运行证据和结构化项目事实，不引入向量数据库、自动事实晋升或新的语义 Memory Provider。

## 4. Session Gateway

Gateway 是 Web/IM 接入和 Conversation Agent Run 之间的调度边界：

- 新消息先持久化为 Conversation Job，再唤醒对应 Project Session。
- 同一 Project 同时只运行一个 Conversation Turn。
- 不同 Project 可以独立并行。
- 每个 Turn 使用短生命周期后台 task 执行同步 AgentCore，不占用 Web 请求。
- Turn 成功或失败后都释放 Session 执行权并继续处理后续持久 Job。
- 单轮异常记录到 Job、Agent Run、Event 和日志；异常不得退出 Gateway 或永久减少执行容量。
- 服务启动时恢复排队 Job，并明确结算上次进程中断的运行中 Job。

Gateway 的执行 task 只是载体，不保存 Project 真相。SQLite Session、Job、Run 和 Event 是恢复依据。

## 5. Agent Run 与 Transcript

每次 Conversation Agent Run 必须绑定：

- `project_id`
- 稳定的 Conversation Session `logical_id`
- `conversation_job_id`
- 本轮冻结的 instruction 与 Platform Policy revision
- 本轮项目事实快照

Agent Run 内的 assistant/tool 消息继续完整写入 `v1_agent_messages`。用户可见回复继续写入 `v1_conversations`。下一轮从相同 Session 恢复对话和已完成治理动作，并使用实时 inspect 事实校正历史快照。

Column Agent 的逻辑 Session 仍以 Task 和 Column 声明的 `session_key` 隔离，不继承 Project Conversation Session。

## 6. Hermes 参考边界

本设计采用 `D:/workspace/hermes-agent` 的以下工程边界：

- `gateway/platforms/base.py`：每个 Session 的后台消息 task、Session 活跃保护、异常后的 finally 清理和后续消息继续处理。
- `gateway/session.py`：稳定 `session_key -> session_id` 映射与持久 transcript。
- `gateway/run.py`：把持久 transcript 重放给 Agent，并在实时内存记录比持久副本更新时保持连续性。

DevWerk 保留自己的 Project 单写者治理、Kanban Workflow、Task Runtime 和 Mailbox；不复制 Hermes 的渠道、业务 prompt 或命令体系。

## 7. 状态与失败语义

```text
queued -> running -> succeeded
                  -> failed
```

Conversation Job 失败是一次 Turn 的终态，不是 Conversation Session 的终态。Session 在 Job 结算后恢复为 `idle`、`planning` 或 `attention`，并能够接受下一条用户消息或监督事件。

Mailbox delivery 同时形成明确终态：成功为 `acknowledged`，失败为 `failed` 或 `attention`。Gateway 不把失败消息自动恢复为 pending；只有显式、可审计的 redelivery 可以再次投递，具体状态机以 [`mailbox-lifecycle-p0-design.md`](./mailbox-lifecycle-p0-design.md) 为准。

Provider 超时、Token Plan 不可用、工具协议失败和模型输出错误必须原样进入日志与失败记录。Gateway 只隔离失败边界，不伪造成功、不隐藏错误、不执行业务兜底。

## 8. Web 与可观察性

- POST Conversation 在消息持久化后立即返回 `202 accepted`。
- Conversation progress、Job 状态、Agent Run 和工具审计继续通过现有 API/Event 查询。
- Health 必须报告 Conversation Gateway 是否运行、活跃 Project Session 数和待处理 Job 数。
- 用户对话只展示 user/assistant message；运行状态和工具原始结果进入独立状态与审计视图。

## 9. Version 1 验收不变量

1. 每个 Project 只有一个稳定 Conversation Session 身份。
2. 同一 Project 的 Conversation Turn 严格按持久化顺序运行。
3. 下一轮能够读取之前的用户要求、Conversation Agent 回复和治理动作证据。
4. Conversation Agent 可以依据当前事实调整 Workflow、恢复或重调度 Task。
5. 一个 Project 的 Turn 失败不会阻断该 Project 的下一轮，也不会影响其他 Project。
6. 服务重启后排队消息仍会被处理，Session 身份和历史不变。
7. Column Agent Session 与 Conversation Session 保持隔离。

