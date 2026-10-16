# DevWerk 1.0 通用核心五项硬编码修正方案

## 目标与边界

本轮只修复代码审查确认的五项通用机制问题。核心不认识软件、小说、市场调研等项目类型；项目领域的验证方式由 Workflow/Loop 声明。保留每列至多一个 Worker、Task 沿 Workflow 有向边推进、工具回执和 Project/Requirement 隔离。不新增 Column、Mailbox 职责、领域裁判、自动规划模板或成本优化。

实施顺序固定：每一项先作最小代码改动，运行对应单元/合同测试并记录结果，再进入下一项；最后运行交叉回归。当前工作树含既有未提交修改，本轮不清理或混入历史文件，也不操作运行中的 DevWerk 服务。

## F1：无检查反馈可能形成不可关闭的 Task

当前 `TaskFeedbackRepository.record` 只检查存在不同的 `task.feedback.resolve` 列；`resolve` 要求它在责任列成功修复后的更晚一次访问中执行。合法图 `review → work → done` 可在 review 提交责任列为 work 的反馈，却没有 work 到 review 的路径，终态永久被未解决反馈阻止。

修改 `app/v1/repositories/task_feedback_repository.py::record`：无检查反馈的复核候选必须是从责任列经 Workflow 有向边可到达的、不同于责任列且暴露 `task.feedback.resolve` 的 Column。仅检查图可达性，不自动选择路由、不代替 Agent 复核；若不存在，录入时返回明确错误，Agent 可改用冻结检查或先修改 Workflow。保留 `resolve` 的“修复后、不同列、显式说明”条件。测试覆盖可达/不可达图及已有定性反馈闭环。

实施：反馈录入前沿声明边搜索后续独立复核列；新增“复核列只在修复列之前”负例。`test_worker_selected_acceptance.py`：5 passed。

## F2：移除不生效的 Task 合同字段

`TaskContract.acceptance_invalidated_by` 没有核心读取者，但外部软件 Loop 仍声明它；真正生效的是 `WorkflowDefinition.acceptance_obligations[].invalidated_by`。修改 `app/v1/domain.py::TaskContract` 移除字段，删除 `loops/ddd-software-delivery/loop.json` 对该字段的声明。Workflow 义务本身保持不变，工具协议继续要求新增义务由 Workflow 显式声明。测试检查 Loop 加载、未知旧字段被严格拒绝和显式义务的失效判定。1.0 前的旧测试计划不做隐式迁移。

实施：字段与软件 Loop 声明均已移除；领域合同、Loop 和验收相关测试 60 passed。

## F3：验收工具与变更识别应基于通用工具元数据

`AcceptanceCheck.capability` 只接受 `project.command.run/project.files.read`；`TaskFeedbackRepository._latest_impact_run` 只识别 `project.files.write/project.command.run`。这会拒绝注册的 MCP/其他通用检查，并可能漏掉其他工具对既有证据的影响。

修改 `app/v1/domain.py::AcceptanceCheck`、`app/v1/capabilities.py::validate_workflow_capabilities`：capability 为已注册、可同步执行的 Column 工具；拒绝会变更 Workflow/Task 控制状态的工具，保留现有参数 Schema 校验与完成回执。修改 `_latest_impact_run`：依据 CapabilityRegistry 的 `side_effect_kind` 判定成功的 `write/process` 工具，而非工具名；未知旧工具按“有影响”保守处理。命令 `launch_validation` 仅对命令工具执行预检，其余工具由注册协议校验。测试覆盖注册的只读/MCP 风格检查、非命令效果失效、控制工具拒绝和原有固定检查。

实施：AcceptanceCheck 接受注册工具 ID，Workflow 发布时拒绝未注册、不可委派和写入/控制工具；失败访问中的证据影响按注册副作用类别识别。`test_capability_contract.py` 与 `test_context_acceptance_regression.py`：64 passed。

## F4：既有授权范围内的自主修复，不让 Mailbox 发号施令

当前监督事件只允许同一 Task 的 retry/reopen/Worker 输入；如果冻结 Workflow 的设计使 Task 无法交付，Conversation Agent 不能在原 Requirement 范围内修订 Workflow 并创建纠正后的 successor。

只对**持有仍有效原用户 execution grant、且已绑定一个 failed Task** 的监督回合，开放同一 Requirement 内的 WorkflowPlan/Workflow/TaskPlan 修订及该 Task 的 `task.successor`。`task.successor` 必须引用被监督的原 Task 和纠正后的 TaskPlan；不能新建 Requirement、扩展用户目标、创建无关联 Task 或跨 Project 操作。更改 `app/v1/conversation.py` 的监督能力上下文、`app/v1/repositories/conversation_intent_repository.py::_supervision_denial`、必要的 `app/v1/capabilities.py::_require_user_planning_turn` 与 `app/v1/conversation_report.py`，让成功 successor 的真实回执可结束监督回合。Mailbox 仍只标识触发事件，所有判断取 Task、Requirement、工具回执和 Workflow 状态。测试先证明无授权/非 failed/跨 Task 被拒绝，再证明 failed Task 的同范围修正链可创建 successor 并推进。若现有不可变计划接口不能在这个边界内安全复用，停止扩大该项并记录具体阻塞，不引入新的调度层。

实施：沿用现有计划/发布/successor 工具，监督授权仍从原用户 Grant 与失败 Task 读取；修复 TaskPlan 必须只含原任务引用并显式写 `repair_of_task_id`。successor 创建后结束该回合，通知写明新 Task 的实际状态。`test_project_supervision.py`：14 passed；新增完整 Conversation Agent 回合验证：1 passed。未修改 `_require_user_planning_turn`，现有授权检查已覆盖这一场景。

## F5：移除交互和上下文的项目类型/表达预设

`conversation_report.py` 强加“本轮仅讨论，未新建任务”前缀，并在 work_result 忽略 Agent 的 message；`DEVWERK.md` 固定普通回复字数。`files.py` 与 `runtime.py` 把软件构建目录名当成所有 Project 的默认排除目录。

修改 `conversation_report.py`：普通讨论原样显示 Agent 文本；工作结果保留由回执生成的事实状态，同时允许 Agent 的非空 message 作为说明呈现，不能以 message 代替工具/Task 回执或改变状态。删去 `DEVWERK.md` 的固定字数上限，保留“不要输出工具轨迹”的通用沟通要求。文件搜索与 Column 参考资料仅默认排除 DevWerk 自身内部目录；其他目录是否忽略由 Project/Workflow 显式选择，维持现有总文件数/字符数预算。测试覆盖讨论原文、成功回执与说明共存、`build/target` 中资料可被选中以及上限仍生效。

实施：普通讨论不再加固定前缀；work_result 先显示持久回执对应的 Task 状态，另以“Agent 说明（未作为完成依据）”展示文本，文本不能改变事实状态。默认搜索只排除 `.devwerk`，引用资料不再按目录名排除，字符/文件上限继续有效。F5 定向非 asyncio 回归：8 passed。Windows 当前运行环境的 `asyncio.run` 在 Proactor 事件循环构造时停于系统 `socketpair`，尚未进入 DevWerk 会话代码；该 Web 会话用例不能据此判为通过，需在可创建事件循环的环境补跑。

交叉回归时发现一个独立的测试夹具边界：纯 `AgentRunSpec` 未绑定真实 Task 时，Column 完成判定仍访问 `context['column']`。在 `agent_tool_execution.py` 对“有真实 Task 与 Column”再查询反馈终态阻塞；两个无 Task 的完成协议用例通过。这不改变真实 Workflow 的终态验证。

## 完成标准

五项均有对应测试；不再有无效合同字段、工具名白名单或项目类型目录排除左右通用流程。每项测试通过后记录实际命令和结果；最终跑相关 Workflow/Conversation/Feedback/Context/Loop 测试。静态与单元回归不等于真实交付黑盒通过，本轮不启动新 Project 或重启服务。

## 最终验证与范围核查

- F1：`tests/test_worker_selected_acceptance.py`，5 passed。
- F2：`tests/test_domain_contract.py tests/test_loop_contract.py tests/test_context_acceptance_regression.py`，60 passed。
- F3：`tests/test_capability_contract.py tests/test_context_acceptance_regression.py`，64 passed。
- F4：`tests/test_project_supervision.py`，14 passed；单独新增 Agent 完整修复回合，1 passed。新增授权边界断言后再跑定向 4 项，4 passed。
- F5：搜索、讨论展示、验收说明、引用上下文定向非 asyncio 测试，8 passed。
- 最终交叉测试：`tests/test_domain_contract.py tests/test_loop_contract.py tests/test_worker_selected_acceptance.py tests/test_capability_contract.py tests/test_context_acceptance_regression.py tests/test_project_supervision.py tests/test_project_search_bounds.py tests/test_runtime_contract.py`，144 passed。其后增加授权拒绝断言，并要求监督报告明确引用 successor 的 Task 或回执；对应定向测试分别 4 passed、3 passed。`python -m compileall -q app/v1` 通过。
- 静态检索核心 `app/v1` 与 `DEVWERK.md` 未发现小说、软件、前后端、Maven、Node 构建目录名的领域指导或默认过滤。Loop 仍是外部模板，由使用者选择。

限制：这不是黑盒交付证明；未重启 DevWerk 服务或创建新 Project。一个 Web 会话用例在本机 Windows Python `asyncio.run` 创建 Proactor 事件循环时卡在系统 socketpair，测试体未进入会话处理；该用例需在可正常创建事件循环的环境补跑。已有直接 `ConversationGateway._process` 回归覆盖了本轮监督后续任务创建。
