# Conversation 规划控制面阻塞：授权工具生命周期与证据分页

状态：方案先行；以 `prj_f3ec6ffb98ce4b87ad9109da63b78e0d` 的正式黑盒 Job `cjob_5a6e6a6fbdd047be8c56c44c992f5cb3` 为证据。保持 Project Conversation Agent 为唯一主 Agent、现有授权边界、Workflow/Column Worker 架构不变。以下缺陷依次独立修复、独立提交，并在新 Project 复测。

## 事实与原因

六轮讨论均正常且零 Workflow/Task。第七轮用户明确授权后，`conversation.execution.authorize` 一次成功并持久化 Grant/Requirement，`loop.apply` 创建 Workflow。Task 未创建；Job 在 53 次调用后因重复失败的 `conversation.execution.authorize` 被 `ConversationProtocolStalled` 终止。工具分布：`agent.result.read` 32、`conversation.execution.authorize` 3、`workflow.acceptance.configure` 3、`task.plan.save` 2。曾有一次 `task.plan.save` 缺 `requirements_path`，第二次缺完整行为验收；这些是合法准入拒绝，Agent 本该读取诊断继续修复。

`app/v1/agent_run_preparation.py::prepare` 一次性生成整个 Run 的工具清单。`app/v1/agent_provider.py::ProviderTurnRequester.request` 每次原样发送，导致授权提交后仍向模型展示“授权”工具，且详细的已授权状态与旧工具说明并存。`app/v1/capabilities.py::_authorize_execution` 按最新 work_intent 重建授权参数；同 Job 再调用且改写 scope_summary/requirement_id 会触发 `ConversationIntentRepository.resolve` 的正确不可升级保护。工具继续可见诱导错误恢复方向，最后重复失败被协议防循环机制终止。不可放松 `TurnAlreadyResolved`，也不可允许后续参数扩权。

`app/v1/capabilities.py::_agent_result_read` 把每页内容硬限 2,000 UTF-8 字节，即使调用者请求 8,000 字符且 Context Policy 给工具结果约 4,000 token。一次约 19 KB 的 `workflow.inspect` 源回执因此多页消耗模型调用；`app/v1/context_manager.py::project_result` 对 `agent.result.read` 没有保留精确 cursor 的专门投影，直接放大内容会产生截断后跳过未见字符的风险。这是独立的上下文成本缺陷。

规划合同本身还需单独观察：`workflow.acceptance.configure` 有两次有效验证反馈后成功，`task.plan.save` 缺路径和行为验收被拒绝。这些反馈必须继续保留；若下一轮仍无法完成 TaskPlan，再根据具体诊断设计独立修复，不以绕过验收或替 Agent 写计划掩盖。

## 修复 A：一次性工具只在相应阶段对模型可见

在 `app/v1/agent_provider.py::ProviderTurnRequester.request` 基于当前 Job 的持久 `turn_contract` 生成本次 Provider 调用的*视图*，不修改 Run 的权限快照或 Registry。`phase=execution` 时移除 `conversation.execution.authorize` 和 `conversation.task.control.authorize`；`phase=targeted_control` 时同样移除两者；`phase=discussion` 时移除两者及仅能执行的业务工具是否可见另行评估，本次不扩范围。未授权用户轮保留授权工具；事件轮原有权限不变。模型上下文预算与 Provider 实际工具视图使用同一清单。Dispatcher 的现有 grant/Requirement/revision 校验保留，对手工或错误工具调用仍拒绝。重复授权同参数的幂等回执由现有 `resolve` 保留，不新增第二 Grant。只收敛一次性工具的模型可见生命周期，不引入新的状态位。

测试：`tests/test_conversation_contract.py` 注入模型观察首轮工具包含授权、授权成功后下一模型请求不再包含授权；继续能用 `workflow.inspect`、`task.plan.save` 等工具；讨论轮无副作用；`tests/test_conversation_intent_contract.py` 保留“修改已提交授权必拒绝”回归。单独提交。

## 修复 B：归档结果精确、适量分页

在 `app/v1/capabilities.py::_agent_result_read` 按已有 `ContextPolicy.tool_result_tokens` 的 Provider 投影预算计算页大小，设有限上限，使用 UTF-8 前缀截取，返回的 `next_offset` 必须与*实际可见*字符严格一致。`app/v1/context_manager.py::project_result` 为 `agent.result.read` 加专门投影：若原回执超当前预算，缩短 `content`、重算 cursor 和完整性，绝不使用泛用的头尾摘要冒充完整页；若预算不足以容纳元数据，返回明确的延期/原 offset，不跳字。旧 `field=result/stdout` 与 Project/Worker 权限隔离不变。

测试：`tests/test_context_acceptance_regression.py` 覆盖多字节字符、结果体大于 19 KB、连续分页准确拼回原文、较小 Provider 预算投影后也不跳字，跨 Project 读取继续拒绝。单独提交。

## 新 Project 验收

服务在所有在途 Job/Task 退出后重启。新建 Project，原样复用本轮七条用户消息；前六轮必须仅讨论且零 Workflow/Task，第七轮只凭当轮明确授权产生 Grant。检查 Workflow/TaskPlan/Task 的真实回执，Column 各有独立 Worker/Session，Mailbox 只通知。执行期约三分钟观察一次；恢复失败先查实际失败证据并传给对应 Worker，避免同事件重复 retry。若未交付，记录新失败链、先文档后修复，再开新 Project；交付后再审查 Vue/Java 功能和测试质量。
