# Loop 描述性边界修正案（2026-10-24）

## 目标与现状

Loop 是供 Conversation Agent 阅读、选择和借鉴的可选方法说明，类似 Skill。它不保存可直接发布的 Workflow、Task 契约、Column 执行配置、JSON Schema、固定产物路径或命令。Agent 依据用户需求与 Loop 说明，调用既有 `workflow.plan.save`、`workflow.publish`、`task.plan.save` 等内部 Tool 建立实际工作流与任务。Workflow revision 才是执行器读取的唯一图与 Column 契约来源。

当前 `loop.json` 是可执行包；`loop.apply` 在 `V1Store._apply_loop` 中直接把包内 Plan 和 Workflow 发布，产生未经 Agent 针对 Project 调整的冻结图。小说 Loop 允许 Task 自由选择文件名，却在 Column 上写死平铺目录 glob 和正则，第二章因此在 Agent 启动前阻塞。这是 Loop 责任越界。

## 修改顺序与所有权

1. **描述文件**：将四个 `loops/*/loop.meta` 调整为完整 Markdown 方法卡（保留可检索的名称、key、版本、分类、用途等元数据），删除四个 `loop.json`。卡片仅描述推荐阶段、各 Column 的角色/输入/输出/返工思路、项目级问题以及成功证据原则；不包含可执行 Workflow JSON、Tool 入参、脚本、固定产物路径或 JSON Pointer。`assets/` 保留纯写作/交付方法说明，但不再由 Runtime 作为项目绑定资产自动预装；检查其中的路径和命令指令并清理。`loops/README.md` 改为 Markdown 卡片编写与使用指南。
2. **读取协议**：`app/v1/workflows/loops.py::LoopCatalog.get/_records` 只读取 Markdown 卡片及可选方法资料，计算内容 digest；`app/v1/protocols/tools/planning/capability_loop_receipts.py::LoopInspectReceipt` 只返回摘要、完整说明和方法资料，删除 `parameter_schema`/`bundle`；`app/v1/context/context_manager.py::planning_projection` 与 `app/v1/protocols/context/context_result_contracts.py::LoopInspectPlanningProjection` 对新只读结果做有界投影，保留对旧 Agent Run 历史记录的读取兼容。
3. **切断自动发布**：从 `app/v1/tooling/builtin_tool_catalog.py`、`capability_registration.py`、`capability_handlers.py`、`app/v1/api/routes.py` 移除 `loop.apply` 的可调用入口，并更新 Agent 可见说明：先 `loop.inspect` 取得方法建议，再由 Agent 明确调用 Workflow Plan/Publish 工具。`app/v1/conversations/conversation.py`、`conversation_report.py`、`conversation_failure.py` 只删除对新调用链的依赖；历史回执读取不因 Tool 消失而崩溃。
4. **持久状态与旧 Project**：`app/v1/store.py::get_loop` 不再校验/解析可执行包；移除新 Loop 的直接发布方法。已发布 revision 与 Task 继续依靠数据库中的冻结 definition、plan 和 `package_json`，不得因磁盘 Loop 删除而改写。`app/v1/repositories/scope_repository.py::revise` 对历史绑定只沿用当前已发布图和绑定，不再解析旧包或从新描述卡重算图；真正的图变更由现有 Workflow Tool 明确发布。`get_project_loop_assets` 仍可读取旧 `package_json` 快照，保证已运行 Task 的上下文兼容。
5. **测试迁移**：将 `tests/unit/workflow/test_loop_contract.py` 中直接套用模板的单测拆分为：描述卡发现/读取/无可执行字段测试，以及独立 Workflow Runtime/Task 图单测。其余依赖 `store.apply_loop` 或 `loop.apply` 的旧测试改用显式 Workflow fixture；保留一个旧冻结 Project 兼容测试。更新 Conversation Tool 可见性、API、上下文投影测试。先跑聚焦测试，再完整单元测试。服务不重启、不改现有 Project 数据。

## 不变量

- Loop 只提供建议；`loop.inspect` 不改变 Project。没有 `loop.apply` 这种读卡即发布路径。
- Agent 可自由采用、裁剪或不使用 Loop；任何实际图和任务都必须经过独立、类型化的 Workflow/Task Tool 调用与校验。
- Column 执行、路径选择、权限和出边只来自实际发布的 Workflow/Task，不由 Loop 目录文件隐式注入。
- 已冻结 Workflow revision 不随本次模板目录变动；当前第二章的旧 revision 不会因本次修改自动解除阻塞。

## 测试迁移说明

原有部分单测直接依赖早期可执行 Loop。为验证旧冻结 Project 的读取、Task 入场和 Runtime 边界，其旧包快照移到 `tests/support/legacy_loop_fixtures/`，由测试专用 helper 建立历史数据库状态；生产 Loop Catalog 不读取该目录，用户和 Agent 无法通过 Loop 将这些快照发布为新 Workflow。新测试以 `loop.inspect` 只读、显式 Workflow 发布、无 `loop.apply` 入口为判定。

## 落地与验证记录

- 四份生产 Loop 已改为 Markdown 方法卡，删除各自的 `loop.json`；`loops/README.md`、项目 `README.md` 与 Agent 指令同步说明只读边界。
- 新 Project 不再有 `loop.apply` Tool 或 HTTP 入口。已冻结的旧 Workflow 和绑定快照仍可供对应旧 Task 读取；新描述卡不会覆盖它们。
- 聚焦的 Tool、Conversation、Task 入场测试 113 项通过；Loop、Scope、API 测试 43 项通过。完整单测首轮为 595 通过、2 失败；失败分别是历史绑定的裸 JSON 序列化和旧指令措辞断言。改为类型化历史快照、更新断言后，相关 Scope/协议/策略测试 43 项通过，最终两项相关测试所在集合 9 项通过。`compileall` 与 `git diff --check` 通过。未重启现有服务，也未改动 Project 数据。

## 新 Project 黑盒复测（进行中）

2026-10-24 在确认 8000 端口空闲后，以现有 `startup.bat` 启动服务；健康检查确认 Supervisor 与 Conversation Gateway 运行。仅删除本轮 pytest 临时目录，保留原数据库、日志和旧 Project。新建 Project `prj_90d3ca61b97a4f1da13a889f9f45cc54`，用两章现实悬疑小说检验 Conversation Agent 是否读描述性 Loop、自己规划 Workflow、创建 Task 并执行到终态。第一轮 Job `cjob_f57b1db04ebf4a67981455648ce08bfa` 成功；Agent Run 中 `loop.inspect` 和 `conversation.reply` 均有成功回执，未发布 Workflow。第二轮 Job `cjob_cd5eb1a2c5714421ab4b750e73e34156` 成功，仍只讨论人物与冲突，未发布 Workflow。第三轮 Job `cjob_96cb0dca465144b1909726c30111d63c` 已提交，用户明确了故事方向、两章篇幅和交付目标，待观察。该段记录只列真实回执，不把 Agent 的口头计划算作创建或交付。

第三轮实际结果：`workflow.create` 初次参数错误后第二次成功，发布 revision `wfrev_a0191185368a457f80c3f945a0b7cd5f`；其 `source_loop_key` 为 `null`，说明没有由 Loop 直接绑定发布。`task.plan.validate` 三次均返回 `valid=true`，但每次后续 `task.plan.save` 的计划对象都与刚才预览的对象不同，例如 `delivery_files.item` 被模型改为 `delivery_files.item.item`，部分字段又被重复嵌入。全局开关 `require_task_plan_preview=true` 因而返回 `TASK_PLAN_PREVIEW_REQUIRED`；相同的失败调用重复后，Job 以 `ConversationProtocolStalled` 结束，未创建 Task。原始 Assistant Tool Call 已包含这些不同的参数，非 Runtime 转换。此处是 Task Plan 预览和提交之间缺少稳定引用的通用协议阻塞，尚未修复。

## 通用工作流复用单位的黑盒发现

预览引用修复后的新 Project `prj_81e052766ad44cdaa070c6852f1ea202` 已建立 Workflow 并把 Task 推进到 `done`，但图不符合可复用性目标：Workflow Plan 的 `flow_unit` 是“两章交付包”，Column 为 `baseline → chapter1 → chapter2 → review`，仅一个 Task。`chapter1`、`chapter2` 是两份重复交付物的序号，不是可重用的职责；独立审查又发现跨章问题，图却只给 `review → chapter2` 一条返工边。根因是小说 Loop 的描述只列“分章写作”阶段，没有明确章节属于 Task 的交付身份；通用 Agent 指令虽说一个 Task 穿过完整生命周期，也未排除把多个同类交付物打包成一个 flow_unit。

修正范围：只改 `loops/novel-production/loop.meta` 的自然语言指导，明确在连载/多章场景中同类章节各为可独立验收的 Task，写作、审查、返工等是反复使用的 Column 职责；跨章设定可作为先行工作或共享输入，由 Agent 按项目决定。再把 `DEVWERK.md` 中关于 Task/Column 的一句通用说明改为“重复交付物用多个 Task，不用编号 Column”。不在 DevWerk 代码中加入小说路径、章数、状态或专用验证器。随后用全新 Project 检查 Agent 自行建的图是否按此边界规划；旧 Project 的冻结图不改。
