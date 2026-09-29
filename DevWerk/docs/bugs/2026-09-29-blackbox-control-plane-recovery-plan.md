# 黑盒回归控制面阻断：预算恢复、Worker 输入与失败回执

测试 Project `prj_68f94e0f4ba64a1398e48bd61d88270d`，Task `tsk_c2872f27727049e6baeb3373412e7259`。本文件是本次代码修改前的方案；详细时间线见 `2026-09-28-vue-java-login-blackbox-test.md`，定时监控口径见 `2026-09-29-blackbox-monitoring-memo.md`。

## 冻结现场与总体判断

- Task 在 Workflow revision 4 的第 12 列 `frontend_development` 于 2026-09-29 04:32:25 UTC 因 `execution_budget_exceeded` 进入 `recovering/paused`。该 Assignment 的 100 次模型请求、111 次工具调用已经耗尽预算，故 3 小时后仍不能自行推进。第 9 列同样在 100 次请求后阻断，显式 `task.retry` 创建第 10 列后曾推进到 `integration_review`；第 11 列 Review 又返回前端列。问题不是单一 Provider 上下文上限，也不能靠无条件增大预算解决。
- 第 12 列后段至少有 11 次 `agent.result.read` 反复读取一份约 30 KB 的源文件回执。`project.files.read` 的直接分页投影已在源码与单测中实现，但尚未加载到运行服务；该优化可减少新 Run 的无效工具链，不能宣称已修复本次旧 Run。
- 两条给前端 Worker 的输入在 Assignment 阻断后被 `agent.worker.send` 接受，状态为 `pending`，均绑定旧 Assignment `assignment_5269245ac8cd4911816270604ca29012`。现有 `consume_messages()` 只消费新 Assignment ID 或无绑定输入。因此在预算恢复创建新 Assignment 时，这两条已接受输入将永久留在旧 ID 下，修复事实会丢失。mailbox 的 72 个事件均已 `acknowledged`；它只负责通知，不负责转送这两条 Worker 指令。
- Conversation Job `cjob_2201ffc295b4408a90fd27bab23948fe` 已实际调用 `agent.worker.send` 并持久化其中一条输入，但随后多次用不存在的 tool_call_id 提交 `conversation.reply`，最终 `ConversationProtocolStalled`。失败通知的 `durable_progress=false`，因为当前分类漏掉 `agent.worker.send`；`failure_summary()` 也没有说明已排队的 Worker 输入。这造成交互层对部分成功事实的错误呈现。保留严格的引用校验，修正失败投影，不让 mailbox 代替通信。

## 最小修改边界

1. **预算恢复时保留同一逻辑工作的 Worker 输入**。在 `app/v1/repositories/agent_repository.py::assign` 新建 Assignment 的事务中，仅当直接前一个同 Task、同 Column 的 Run 为 `interrupted`，其 Assignment 为 `blocked` 且 `result_json.code=execution_budget_exceeded`、Worker ID 与新 Assignment 相同，才把该旧 Assignment 上仍 `pending` 的 `v1_worker_inputs.assignment_id` 原子改绑到新 ID。其他 Task、Column、Worker、其他阻断原因均不转移；已确认消费/失败的输入不变。这样沿现有 `consume_messages()` 路径一次性投递，保持精确收件人和原内容，不引入 mailbox 职责或新 API。`task.retry` 创建新 Run 的精确条件保持不变。测试覆盖预算恢复、隔离边界、只消费一次。
2. **失败时准确显示已持久化投递**。在 `app/v1/conversation.py::_has_durable_governance_progress` 将成功且 completed 的 `agent.worker.send` 计入持久进展；在 `app/v1/conversation_failure.py::failure_summary` 汇总成功投递条数，明确“已排队，消费状态需另查”，同时保留最终答复失败原因。可在 `conversation_report.py::render_report` 给成功 `agent.worker.send` 一条同义的事实标签，防止成功回合只显示笼统的“操作已完成”。不放宽 `conversation.reply` 的真实 tool_call_id 校验，不把排队说成已消费。
3. **实服务加载与恢复**。只有确认全局无运行 Job/Run 后重启 8000，使既有 `project.files.read` 分页投影和上面两处修复加载。先看 Task 的失败详情与输入队列，再经当前 Project 的 Conversation Agent 显式 `task.retry` 同一 Task/Column；检查新 Run/Assignment、两条输入是否改绑且被消费、模型预算从零开始。约每 3 分钟检查一次控制面状态。若再次在相同 Review↔Frontend 边循环且无新鲜检查证据，停止重复重试，分析冻结验收预设与反馈新鲜度。

## 回归与判定

单测使用现有 Repository/Conversation 合同测试，不增大通用预算、不改 Workflow Schema、Web UI 或交付物。核心断言：预算恢复前两条输入 pending 且绑定旧 ID；新 Assignment 一经创建即改绑；消费后均指向新 AgentRun；无关消息不迁移。Conversation 失败回执需同时表明 `agent.worker.send` 已排队及 `conversation.reply` 引用错误，`durable_progress=true`，不得声称投递已消费。测试通过后才进行实服务恢复与黑盒观察；真正完成交付后再检验前后端质量和冻结验收。

## 实施记录

已在 `agent_repository.py::assign` 按上述精确前驱条件原子改绑待消费输入，并在旧阻断 Assignment 被新 Assignment 接管后拒绝过期投递。已在 `conversation.py`、`conversation_failure.py`、`conversation_report.py` 将持久化的 `agent.worker.send` 投递呈现为“已排队”，不宣称已消费。针对预算恢复、单次消费、过期投递及部分成功失败回执的单测通过；相关 Worker 生命周期与 Conversation 合同共 **44 passed**，`git diff --check` 通过。

全局无运行 Job/Run/Task 后停止原 8000 服务。受限执行环境无法以写入模式打开已有 `data/devwerk.db`，导致第一次隐藏启动在 SQLite schema 初始化处退出；未修改数据库权限或文件。改在已获授权的服务执行环境启动，健康接口 `ok`、supervisor `running`、8000 监听 PID 4972。2026-09-29 08:11:27 UTC 经正式 Conversation API 提交 Job `cjob_6b74fc75588e4dc8aaaffb332ede9d6e`，要求先 compact 该 Worker 的事实摘要、再 `task.retry` 同一 Task/Column；尚未把 API 受理误作工具成功或 Task 恢复。

08:13:52 UTC，该 Job 为 `succeeded`，工具回执依次显示 `conversation.turn.resolve`、`agent.context.compact`、`task.retry`、`task.inspect`、`conversation.reply` 全部成功。新 Column Run `run_fc06b51a08da4cf7b10d12c1ced4f18d`（sequence 13）和新 Assignment `assignment_1d0110d881ae4c569bb2ccf4d148e461` 已运行，首次检查模型计数 10。原两条输入 `winput_0bce...`、`winput_7489...` 均已改绑新 Assignment，并由新 Agent Run `arun_e0355c439f264161960e63210954b6bf` 确认消费；mailbox 73 个事件全部 acknowledged。此处只证明控制面恢复与消息交付，不证明产品交付完成。
