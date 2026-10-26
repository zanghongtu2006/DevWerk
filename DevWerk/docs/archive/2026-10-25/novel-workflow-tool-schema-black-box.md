# 小说 Workflow Tool Schema 黑盒复测（2026-10-25）

## 测试边界

新 Project `prj_9a5eb5084a824be08070a0023cf6e56b`，目录 `data/projects/novel-explicit-workflow-20261025-1530/`；真实 MiniMax-M2.7、8000 服务。前两轮仅用自然语言讨论《雨站来信》的四章设定、人物、篇幅和结尾，不写正文。第三轮在拍板五项创作决定后，明确要求 Conversation Agent **启动工作流**，先交付独立的第一、二章样章；没有提供 Tool ID、字段名或 Workflow 图。首次失败后，第四轮只以普通用户语言催办同一工作，没有提供参数修正。

## 首次执行：目录可用，建图失败

Job `cjob_53cad4bc46be4ab0bd4e12d5fbe6a022`、Run `arun_f0ec7e51ea3a40bd8155779eec5b0734`。执行授权、`tool.catalog.search/select`、`task.plan.contract` 与 `workflow.inspect` 均成功。Agent 选择了 `workflow.create`、`task.plan.contract/save/validate`、`task.create/list`。`workflow.create` 的五次已提交调用分别被拒：

1. Column 根对象多出 `worker_key/worker_role`；这两项应在 `executor` 中。
2. Workflow 根对象多出 `type/output_contract`。
3. Column 缺少必填 `transitions`。
4. 章节字数验收检查的 `project.files.verify` 参数缺少 `path`。
5. `task_input_schema.required` 被写成 `{ "item": "output_path" }`，并非 JSON Schema 规定的字符串数组。

第六次模型调用的参数与第五次相同，执行前被 `ConversationProtocolStalled` 拦截，Job `failed`；该时点没有 Workflow 或 Task。前五次参数和错误各不相同，说明模型接收错误后曾尝试修正，不能描述为五次盲目重复。

直接解析真实 `llm.provider_request`：从该 Run 第 6 次模型请求开始，MiniMax 请求带有 `workflow.create` 的 ID、完整说明与参数 Schema。一次代表性请求包含 24 个 Tool、Tool JSON 约 83,290 字符、消息约 45,331 字符；`workflow.create` 单项约 56,700 字符（其中 `input_schema` 约 55,995 字符）。Provider Schema 明示 Workflow 必填项、Column 的 `executor/transitions` 及终态边；`worker_key/worker_role` 位于 `executor`。因此**目录披露和 Provider 发送没有缺失**。

真实参数合同仍有可用性缺口：`WorkflowAuthoringRequest.task_input_schema` 在 Provider Schema 中只是 `type=object, additionalProperties=true`，说明为“可省略”，并未把嵌套 JSON Schema 的 `required` 数组约束呈现给模型；`_workflow_create` 到执行时才调用 `check_schema`。另外，判别联合体经 `provider_view` 展平成带文字说明的单对象，跨字段条件比原始 typed contract 弱。超长 Schema 可能增加模型定位相关字段的难度；此次样本不能证明长度是唯一直接原因。

## 普通用户催办后的恢复

Job `cjob_49d54a3fee5a4c45be27ec088f9f94c4`、Run `arun_6f65fcac410149f09db73dc3275e3b2b` 最终 `succeeded`。Agent 再次自行搜索/选择 Tool；`workflow.create` 一次成功，成功参数**省略**了可选 `task_input_schema`。随后保存 Task Plan 并入队三个 Task：

- `tsk_efb0de3bac974ddeac22eea2059a149c`：设定、人物与四章线索链，输出 `setting/00-bible.md`。
- `tsk_9ddb2abb8ed444c483aef3af96d43aa9`：第一章样章，输出 `chapters/01.md`，依赖设定。
- `tsk_d40d0df405ff4ded85516066401e4120`：第二章样章，输出 `chapters/02.md`，依赖设定和第一章。

发布版本 `wfrev_2a3c0c9e9da9450a80eb4745fcd5633d` 有两个职责列：`writing` 与 `reviewing`，成功边为 `writing → reviewing → done`，失败/阻塞边均到 `failed`。这是可复用的同一张图，不是按章命名 Column。最后一次读数时设定 Task 已交付并释放第一章，第一章运行中，第二章等待上游；该记录**不把尚未完成的章节算作交付通过**。

## 结论

Conversation Agent **能够**通过目录获取管理 Tool、创建 Workflow 和 Task，并驱动到开始执行；但首次执行因复杂参数不稳定而失败，靠普通用户催办才恢复。Tool Schema 确实传到模型，问题重点是 Provider 参数视图的复杂度和部分嵌套合同呈现不足。后续若修复，应针对通用 Workflow 参数合同处理，不在代码加入小说专用规则。本轮仅做黑盒测试与只读分析，未改代码或人工代建 Workflow/Task，服务及 Project 现场保留。
