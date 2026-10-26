# Agent 规划 Tool 契约与换版前 Task 静止路径

## 证据与边界

- 新 Project `prj_c5d70fef7189427bad9329f96d0db9a9` 的 `workflow.create` Provider Schema 约 57 KB。前两次调用把 `item`/`transitions` 等放在 `workflow` 顶层，第三次才成功。Schema 已送达，但一次建图不稳定。
- 旧 Project `prj_9a5eb5084a824be08070a0023cf6e56b` 的 `project.scope.revise` Provider Schema 明确声明 `expected_revision` 为整数。前两次正确传 `1`，却因旧 Task `tsk_d40d0df405ff4ded85516066401e4120` 停在 `pending@reviewing` 而被 `WorkflowRevisionBusy` 拒绝。`pending_scope_revision` 又拒绝 Task 控制；后续重试才把 `1` 写成字符串并停机。
- 目标仅限通用的 Requirement / Workflow / Task 操作协议和换版静止边界。不加入小说或其他场景规则，不改 Kanban 路由、Column 执行或交付物判定。

## 统一的 Agent 可见规则

1. 每个管理 Tool 接收一个由 Pydantic 类定义的**平铺命令对象**。顶层只出现该操作自己的字段；嵌套只用于真实领域对象，例如 `columns[*].executor`、`columns[*].transitions[*].target`、`task.plan.save.plan`。`workflow.create` 直接接收 `WorkflowAuthoringRequest` 字段，不再要求外层 `workflow` 包裹。`project.scope.revise` 继续使用五个平铺字段，`expected_revision` 保持整数。
2. `workflow.create` 对 Provider 和 Runtime 使用同一份静态作者模型 Schema。不要将所有已注册 Column Tool 的输入 Schema 复制进整张 Workflow 图；可选的 `capability_sequence.steps[*].arguments` 与 `acceptance_checks[*].arguments` 保持明确标记的动态对象，发布前继续用所选 Tool 的原始 Schema 严格校验。Agent 需要具体 Tool 参数时通过现有 `tool.catalog.select` 获取该 Tool 的直接定义。
3. Scope 修订的返回和说明必须明确：它会在已有图时复制并发布旧图的新修订、更新 Requirement，但不改变列，也不创建 Task；如需改列，随后调用 `workflow.create`。Scope Tool 的版本参数只用于并发核对，不是 Agent 自行编造的编号。
4. 保持所有固定入参/出参的类型来源于模型；错误回执包含字段路径、期望类型与实际类型。对同一操作的重试不得把已正确的字段重新解释为其他类型。后一项通过较小的工具说明和焦点测试验证，不依赖把字符串暗中强转为整数。

## 合法静止路径

1. `project.scope.inspect` 在当前快照中返回换版准备状态和阻塞 Task（ID、状态、所在列）。这个只读信息来自与 Workflow 发布相同的静止判定，避免 Agent 猜测。提交时仍在事务内重新校验，快照不提供提前授权。
2. `conversation.execution.authorize(request=extend)` 的 `pending_scope_revision` 状态允许**同一 Requirement 的 `task.pause` 和 `task.cancel`**，仅用于旧 Task 静止；仍禁止创建新 Task、重试/恢复旧 Task、改图或其他范围外变更。运行中 Task 先请求暂停并等到真正停止；已推进但未终结的 pending Task 可由 Agent 根据用户目标决定等待或取消。未收到用户对废弃的授权时，Agent可解释并等待/询问，Runtime 不自动取消。
3. `project.scope.revise` 仍执行原子版本核对和发布；旧 Task 没静止则返回明确阻塞列表，不能绕过。成功后 grant 进入 active，再按返回的 Workflow revision 创建 Task Plan / Task；若需要改图，先 `workflow.create` 再用最终 revision 创建 Task。

## 代码落点与测试

- `app/v1/protocols/tools/planning/capability_contracts.py::WorkflowCreateInput`、`app/v1/domain.py::WorkflowAuthoringRequest`：一个平铺作者请求；既有字段校验保留。
- `app/v1/protocols/tools/planning/capability_workflow_models.py::workflow_tool_models`、`app/v1/tooling/capability_registry.py::_refresh_workflow_tool_models`、`app/v1/tooling/capability_registration.py`：移除 `workflow.create` 的动态 Schema 膨胀，保持 `workflow.publish` 旧兼容路径；不得让 Provider 与 Runtime 看见不同的 Create 根参数。
- `app/v1/tooling/capability_handlers.py::_workflow_create`、`app/v1/tooling/builtin_tool_catalog.py`：使用平铺请求，并准确描述 Scope/Workflow/Task 的效果和顺序。
- `app/v1/store.py::_publish_workflow_revision`、`app/v1/repositories/scope_repository.py::inspect/revise`、相应 `protocols/` 回执：共用只读静止诊断与事务内最终校验。
- `app/v1/repositories/conversation_intent_repository.py::denial`、`app/v1/tooling/capability_handlers.py::_task_pause/_task_fail`：严格限定 pending extend grant 可作用的 Task 和操作。
- `tests/unit/tooling/test_capability_contract.py`、`tests/unit/conversation/test_scope_extension.py`：平铺 Schema、动态 Tool 参数仍在发布前严格校验、Provider Schema 体积、pending grant 静止操作、跨 Requirement 拒绝、阻塞/成功原子性。
- 先跑相关单元测试，再跑合适的全套测试。重启 8000 后用两个全新 Project 做真实 Provider 黑盒：一项首次建图并创建 Task，一项旧 Task 未静止时扩展范围。记录首次调用结果、每次错误和最终 DB/Tool 回执；旧 Project 不作为回归目标。

## 首轮真实 Provider 反馈与补充修正

- 新 Project `prj_81f1cc77d5074a9084225b2f9db4a00c` 的第一次 `workflow.create` 已成功；Agent 随后第二次建图也成功，三项 Task 已创建。首次 `task.plan.validate` 把 Task 字段误放在 Plan 根对象，第二次纠正。前者首次成功率为 1/1，后者为 0/1；样本数不足以推断长期概率。
- 同一 Project 的扩展轮先调用 `conversation.task.control.authorize`，成功暂停旧 Task，再尝试 `conversation.execution.authorize(request=extend)`，被已提交的定向授权边界拒绝。`project.scope.revise` 因授权范围不符失败。此失败发生在模型使用了两个相互竞争的公开授权入口，而不是 `expected_revision` 类型或 Task 静止判断失败。
- 补充最小修正：旧定向授权接口保留为兼容入口，但不再在 Conversation Agent 的 Provider Tool 列表、搜索目录或选择结果中公开。公开操作统一使用 `conversation.execution.authorize`：Task 控制用 `continue`，同轮还要修订范围用 `extend`。`task.pause/cancel/resume` 在 Agent Run 内均严格核对同一 Requirement；不放宽旧定向授权的已提交边界，也不自动废弃 Task。相应说明位于中心 Tool 目录与 Conversation 指令，测试覆盖 Agent 不可见、直接旧接口仍可用以及同轮 extend 路径。

## 复测结果（真实 Provider）

- 新 Project：`prj_5aa818bbdb8d42528210a078fc2f530e`。第一轮自然语言建图 Job `cjob_739c3251ae454100bb028bc5bbb888cd` 成功，发布 Workflow 修订 1，并创建两项 Task。首个 `workflow.create` 调用失败于根部多出的 `context` 字段；第二次失败于可选 `task_input_schema` 中非法的 JSON Schema 属性类型；第三次成功。因此这一轮建图首次调用为 **0/1**，成功调用为 1/3。`task.plan.validate` 和 `task.plan.save` 均首次成功。随后在中心 Tool 说明中补充了 Column context 的正确位置及 JSON Schema 属性示例；这两句说明尚未单独做 Provider 重测。
- 同一 Project 的自然语言扩展 Job `cjob_ec1e7cc81d754500ae5498e079f891e3` 成功。`conversation.execution.authorize(request=extend)`、`task.pause`、两次 `task.cancel`、`project.scope.revise`、`workflow.create`、`task.plan.validate`、`task.plan.save` 均首次成功。旧两项 Task 终结于原 Requirement 修订 1，新四项 Task 绑定 Requirement 修订 2 与 Workflow 修订 3，其中首项已启动。此轮没有调用旧定向授权入口，也没有 `TurnAlreadyResolved` 或 `TurnControlScopeMismatch`。
- 两轮 `workflow.create` 的首次调用成功率为 **1/2**；这是两个真实调用的观察值，不是总体可靠性估计。第一轮还发生授权与目录搜索并发提交被拒、两次 `conversation.reply` 引用错误 Tool Call ID；第二轮出现一次排队后再读 Task 被规划租约拒绝、一次回复引用错误。这些都被 Agent 恢复，未阻断建图/换版，但说明 Tool 使用仍非零误差。
- 焦点单元测试：Tool 可见性与 Conversation 授权 54 passed；Tool 契约与 Provider 契约 86 passed；Scope 静止路径 2 passed。全套测试未执行；当前仓库还有本任务之外的大量未提交改动，测试结论不覆盖它们。
