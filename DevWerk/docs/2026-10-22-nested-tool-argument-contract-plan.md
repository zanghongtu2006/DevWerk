# Workflow 嵌套 Tool 参数契约修复计划（2026-10-22）

## 问题与边界

本轮黑盒调用中，`task.plan.validate` 的宽泛 `plan` schema 允许 Agent 把 `tasks` 连续提交成对象或字符串，而 `task.plan.save` 的 `TaskPlan` schema 明确要求数组。`workflow.create` 的 `acceptance_checks[].arguments` 以及确定性步骤的 `steps[].arguments` 也是宽泛对象；Agent 两次将文件验证的整数字段写成字符串，第三次通过删除整个检查完成建图。运行时已经能够按 Registry 校验部分嵌套参数，但 Provider 在生成 `workflow.create` 调用前看不到“选定 capability → 对应 arguments”的关联。

当前 Registry 有 74 个内置 Tool；本次运行发现 32 个 Chrome MCP Tool，最近的 Conversation Run 实际暴露 102 个。直接 Tool 的 schema 已提供给 Agent，因此本次不添加查询 Tool，而是让 Workflow 作者工具复用 Registry 中同一份输入契约。Agent 可见视图按所选 capability 给出嵌套类型；运行时继续使用原始严格契约检验最终值。`workflow.create` 的 Provider 视图新增约 49 KB JSON，`workflow.publish` 不重复这份视图，以限制每轮上下文开销。

本次只处理 Tool 参数契约。Domain 仍拥有 Workflow 和 TaskPlan 的持久化结构；Tooling Registry 拥有 Tool ID、输入 schema 和执行校验；`protocols/tools/planning` 拥有 Agent 可见的动态调用模型。MCP 的输入 schema 是外部声明的动态契约，内置 Tool 的 schema 继续从类型化输入模型产生。Workflow 的业务路由、Column 职责、Task 调度均不变。

## 修改步骤

1. `app/v1/protocols/tools/planning/capability_contracts.py`：使 `TaskPlanValidateInput.plan` 与 `TaskPlanSaveInput.plan` 使用同一个 `TaskPlan` 类型。形状错误在边界返回含路径的 `invalid_arguments`，形状正确后由现有诊断器返回语义校验结果。保留对整个 `plan` JSON 字符串的传输层解码；不另加参数类型转换。现有 Anthropic 兼容层会按已声明 schema 规范化无歧义的 Provider 原生表示，最终 Registry 校验仍以强类型 JSON 为准。
2. `app/v1/protocols/tools/planning/capability_workflow_models.py`：从 Registry 的实时 Column Tool 契约创建按 `capability` 判别的 Pydantic 变体，并把对应的输入 schema 绑定到 `arguments` 属性。覆盖 `workflow.create` 的 `WorkflowAuthoringColumn.acceptance_checks` 和 `CapabilitySequenceExecutor.steps`；步骤参数的 Provider 视图须容纳现有精确 `$ref` 模板。`workflow.publish` 保留原有较小的 Tool schema，避免在同一个 Agent 请求中重复发送全部嵌套 Tool 契约；两条发布路径继续使用相同的 Registry 执行校验。动态模型不写入 Domain，也不为某种项目类型建立特例。
3. `app/v1/tooling/capability_registry.py` 与注册处：由同一 Registry 生成 `workflow.create` 的 Provider 强类型 schema，并沿原有通用调用入口及 Workflow Handler 校验输入。Agent 可见的强类型视图和执行时的原始契约来自同一组已注册 Tool；嵌套错误带 Workflow 列、检查或步骤及字段完整路径，并指出预期/实际类型。不能把失败检查从 Workflow 中自动删掉；不能在这里引入项目类型逻辑。
4. `app/v1/domain.py` 与 Tool catalog：以简短、通用的字段说明提示 Agent：嵌入的 `arguments` 遵循其所选 Tool 的输入类型；`task.plan.validate` 通过后继续以同一计划调用 `task.plan.save`。

## 验证

- 聚焦测试检查两个 Task Plan Tool 的 schema 具有相同 `TaskPlan` 形状，以及合法计划仍能校验、保存。
- 检查 `workflow.create` 的 Agent 可见 schema：选择 `project.files.verify` 时 `minimum_non_whitespace_characters` 为整数，选择其它 Tool 时遵循各自输入类型；注册新 Tool 后 schema 自动更新。记录新视图相对原协议的大小。
- 分别对验收检查、确定性步骤的错误字段调用建图，断言完整路径、失败前无 Workflow 持久化；合法嵌套参数和合法 `$ref` 模板继续可用。
- 先跑上述 focused 测试，再跑相关 Tooling、Workflow、Conversation 单元测试；不启动或干扰 8000 服务，不做黑盒项目测试。

## 本轮执行结果

- `task.plan.validate` 与 `task.plan.save` 已共用 `TaskPlan` 入参模型；错误形状在 Tool 边界拒绝，语义有效性仍由原计划诊断器判断。
- `workflow.create` 的 Agent 可见 schema 现按实时注册的 Column Tool 生成嵌套参数类型，覆盖验收检查和确定性步骤；`workflow.publish` 保持较小的原有视图。两者运行时仍使用 Registry 的原始输入契约校验。
- 嵌套校验错误现在包含 Column、检查或步骤以及具体参数的路径，并报告预期和实际 JSON 类型；拒绝发生在 Workflow 持久化之前。
- Tooling 与 Task Plan 相关测试 99 项通过；Conversation 的初次建图和修订发布路径测试 2 项通过；相关 Python 模块编译和 `git diff --check` 通过。尝试扩大到完整单元测试时部分长时间运行的用例未结束，故不宣称全套通过。
- 未启动新黑盒 Project，也未重启 DevWerk。结束时探测 127.0.0.1:8000 未发现监听服务；本轮未操作服务进程。

## 黑盒发现的 P0 回归与最小修正

在确认当前代码由项目 venv 重启后，全新 Project 第一轮尚未调用模型就触发 `required_context_overflow`：102 个可见 Tool 的原始 schema 为 281177 字节，预估 175873 tokens，输入预算 129025。`workflow.create` 一项达到约 181 KB；32 个 Chrome MCP Tool 同时作为步骤和验收检查的判别分支重复内联输入 schema。此结果证明之前仅用内置 Tool 测得的 schema 大小不足以覆盖真实 MCP 环境。

修正边界：内置 Tool 继续在 `workflow.create` 中按 capability 内联强类型参数；外部 MCP Tool 的所有 ID 合并为一个动态参数分支。其输入 schema 已作为各 MCP Tool 自身的直接 Tool 定义提供给 Agent；嵌入 Workflow 时仍由 Registry 原始 schema 严格校验，并返回完整字段路径。这样不增加 Tool、不引入场景规则，也不削弱运行时契约。增加带大量 MCP Tool 的 schema 预算测试，再重启并以全新 Project 黑盒复测。
