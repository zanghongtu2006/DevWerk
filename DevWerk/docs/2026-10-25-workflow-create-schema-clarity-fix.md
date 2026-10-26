# `workflow.create` 参数说明修正（2026-10-25）

## 证据与目标

新 Project `prj_9a5eb5084a824be08070a0023cf6e56b` 首次建图时，真实 MiniMax 请求已含 `workflow.create` 的说明与约 56k 字符参数 Schema；Agent 五次提交不同但不合法的图，第六次重复最后一份被停止。最后一次把 `task_input_schema.required` 写为对象。当前字段在 Provider 视图中仅是允许任意键的对象；`required` 的数组类型直到执行时才由 `check_schema` 检出。随后一次普通用户催办，Agent 省略该可选字段并一次成功建图。

本次只修正通用 Workflow 创建 Tool 的**参数可读性及该字段的类型约束**，不改状态机、Column 执行、Requirement 授权、Task Plan、Provider 全局转换，也不加入小说规则。

## 所有权与依赖方向

- `app/v1/protocols/workflow/task_input_schema.py` 新增简洁的 Task 输入 JSON Schema 文档模型。固定关键字由类属性定义：`type`、`properties`、`required`、`additionalProperties`；高级 JSON Schema 关键字作为显式动态扩展保留，原有 `check_schema` 继续验证完整文档。
- `app/v1/domain.py:WorkflowAuthoringRequest` 使用该类型代替 `dict[str, Any]`；字段说明明确“可省略，默认接受对象；若提供，`required` 是字段名数组，Task 具体值属于 Task Plan”。Column 的 `executor` 字段说明强调 Worker 参数嵌套位置；`transitions` 维持已有必填合同。
- `app/v1/workflows/workflow_authoring.py:materialize_workflow` 把模型序列化为现有 `TaskContract.input_schema` 字典；`app/v1/tooling/capability_handlers.py:_workflow_create` 对同一序列化文档运行原有 `check_schema`。不新建第二套运行时 Schema。
- `app/v1/tooling/builtin_tool_catalog.py:WORKFLOW_CREATE` 中央说明给出最小有效结构、可选字段省略原则与创建 Task 的后续步骤；行为仍由 handler 拥有。
- `app/v1/protocols/tools/core/provider_schema_contracts.py:_flatten_discriminated` 保留原字段的说明文字。聚焦测试发现展平执行器联合类型时会丢掉 `WorkflowAuthoringColumn.executor` 的位置说明；此处只保留说明，不改变联合类型投影及运行时验证。
- `app/v1/protocols/tools/planning/capability_workflow_models.py:workflow_tool_models` 动态生成的 Workflow 模型不再用泛泛的说明覆盖原 `executor` 字段说明，而是复用基础 typed model 的说明。这是第二个使位置说明未到达 Provider 的原因。

## 验证

聚焦测试同时检查严格 Tool Schema 和 `provider_contract_schema`：`task_input_schema.required` 均显示数组；对象形式在 Tool 边界被拒；省略该字段仍使用现有对象默认值；Column 的 Worker 参数与边目标位置在 Provider 说明中可见。运行相关 Workflow、Tool、Provider 单测。真实模型重测单独讨论，不把 Schema 修正等同于首次建图稳定性保证。

## 实施记录

- 已按上述文件与方法修改。动态 Workflow Tool 模型原本还会用泛泛的 `executor` 描述覆盖基础模型的字段说明，Provider 展平联合类型时又会丢掉字段说明；两处均已修正。没有改变 Workflow 图、任务状态、授权及执行策略。
- 新的 `WorkflowTaskInputSchema` 让 `required` 在严格 Tool Schema 与 Provider 视图中都是字符串数组；其他高级 JSON Schema 关键字仍为显式动态扩展，由现有完整 `check_schema` 检验。省略字段保持原 `{"type":"object"}` 默认值。
- 相关 Tool、Provider、Workflow 单元套件 **109 passed**；补充的完整输入边界用例 **1 passed**；`compileall` 和 `git diff --check` 通过。以当前代码本地生成的 Provider 视图核对：Tool 总说明含最小必填结构，`task_input_schema.required` 为 array，`executor` 嵌套位置说明可见。
- 未重启 8000 服务，未启动新 Project 黑盒测试；正在运行的服务仍用重启前代码。用户要求先完成 Schema 修改，再讨论测试。
