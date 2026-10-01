# Loop 可选模板与 Kanban 创建闭环：修复方案

状态：方案已确认，实施与回归中。依据：`prj_7d43d4e7c0c54a4bb602604ace8ac769` 的数据库、工具调用与[复盘记录](2026-10-01-hermes-core-blackbox-retest11.md)，以及当前代码。目标是让用户只通过 Web 与 Conversation Agent 讨论、授权、创建和维护 Workflow；Kanban 自动派生各 Column 的 Worker，推进 Task 至验收。Loop 是优先可选模板，不是唯一入口。

## Conversation Agent 究竟怎样创建

Web 对话触发 Conversation Agent 的正常工具调用；Agent 不调用脚本或数据库。这里的 API 指 DevWerk 注册给 Agent 的原生 capability，不要求用户手动填写参数。用户明确授权交付后，确定性执行路径是：

1. Agent 可先调用 `loop.list/inspect`。合适就调用 `loop.apply` 得到可编辑的 Workflow 草稿；不合适就直接构造 `WorkflowPlan`。此步骤可跳过 Loop。
2. Agent 调用 `workflow.plan.save` 保存 Column 清单、职责与先后关系，再调用 `workflow.publish`，提交与该 plan 对齐的 `WorkflowDefinition`。修改 `publish_workflow()` 后，这一调用在项目没有 Workflow 时创建初始 revision；有 Workflow 时创建新 revision。`loop.apply` 只是提供模板数据，不再是创建权限的前置条件。
3. Agent 调用 `task.plan.contract` 获取该 Workflow 的 Task 输入要求，构造 TaskPlan 后调用 `task.plan.validate`。只有返回 `valid=true` 才调用 `task.plan.save`，再调用 `task.create`。`task.create` 的持久化和调度回执才表示 Task 已创建并交给 Kanban；Agent 文本回复不算成功。
4. Runtime 依冻结的 Workflow revision 自动调度各 Column Worker；Agent 用 `task.inspect` 监督。若发布/验证失败，能力返回精确诊断供同一授权轮修改重试；若仍未拿到 `task.create` 回执，交互层必须明确显示未启动及原因，不能宣称已交付。

这是“具备可靠创建通道”的保证，不是承诺任意模型输出都合法。确定性保障来自工具注册、schema 验证、持久化回执，以及未创建 Task 时禁止成功宣称；不是靠提示词随机遵守。第一步实现时应加一条集成回归：无 Loop 的 Project 使用上述工具链创建 Workflow 和 Task，确认 `task.inspect` 出现首个 Column Run。

### API 协议如何被 Agent 知道并校验

当前机制是 `app/v1/capabilities.py::CapabilityEntry` 注册工具名、说明、`input_schema`、`output_schema` 和 handler；`app/v1/agent_run_preparation.py::prepare` 把当前允许的工具转成 function-tool schema，随每次模型请求发送。Agent 调用后，`CapabilityRegistry.dispatch` 以同一 `input_schema` 校验参数，再执行 handler，返回 `ToolResult(ok, output/error)`；结果写入工具消息。这里不依赖 Agent 猜 API、访问 DB 或用户运行脚本。

现状缺口：`workflow.plan.save`、`workflow.publish`、`task.plan.validate/save`、`task.create` 注册时未指定专属 `output_schema`，默认 `{}`，所以 Runtime 对这些成功回执的 **output 形状** 没有严格验证；`task.plan.validate` 的公开输入 schema 也仅写 `plan: object`，实际完整约束藏在后续 Pydantic/准入逻辑里。Agent 看到的是工具输入 schema 和实际返回，不是完整、稳定的 Kanban 协议。这也是本次猜参数和重复保存的诱因。

修复必须补齐：

1. 优先使用现有 `CapabilityEntry`/Pydantic 合同：把 Kanban 作者工具组织为清晰的工具集，并给 Conversation Agent 简短、稳定的调用顺序指导。模型仍从实际 function-tool schema 获得参数，而非从另一份手写文档猜测。若嵌套 schema 太大，先扩展现有只读 `task.plan.contract`/Workflow inspect 投影来按需呈现字段和合法下一步；仅在回归证明现有工具无法清晰表达时再增加独立只读查询工具。无需为 DevWerk 内部 Kanban 创建 MCP 服务或依赖 Skill 执行状态变更。
2. 为上述关键工具注册严格的 `output_schema`：保存返回 ID/revision/digest；验证返回 `valid`、结构化 `diagnostics`；创建返回 Task ID、冻结 Workflow revision 与调度状态。保持统一 `ToolResult` 包装。输入由 JSON Schema + Pydantic +业务准入校验；输出在写入回执前由 Runtime 校验。
3. Agent 只能根据 `ok=true` 的创建回执和随后 `workflow.inspect` / `task.inspect` 的持久状态宣称成功。`valid=false` 不允许进入 `task.plan.save`；诊断给出字段路径和期望值，让 Agent 在同一授权轮修正。测试覆盖错误参数、错误返回结构、发布失败不留半成品、以及模型收到的 tool schema 与 Runtime 实际 schema 一致。

## 根因与边界

- 测试项目里的 `scripts/*.ps1` 是 Conversation Agent 在授权轮通过 `project.files.write` 创建的，不是 DevWerk 启动或使用所需的历史脚本。Agent 提前写脚本，是因为 `software.ddd_delivery` 要求五列在 Task 准入前冻结可执行行为命令、报告路径和场景覆盖；`DEVWERK.md`、`loop.inspect` 投影及 `workflow.acceptance.runners` 继续强调这一步。结果 76 次工具调用、21 次写文件，仍未创建 Task。
- `DEVWERK.md` 禁止无 Loop 时创建初始 Workflow；`app/v1/store.py::publish_workflow` 和 `_publish_workflow_revision` 实际拒绝无 Loop 的初始 Workflow。Agent 后来判断 8 列模板过重，却无法在当前授权需求内换成自建 4 列流程；`project.scope.revise` 只处理新的用户范围扩展，不是同范围规划修订。
- `task.plan.validate` 已给出 `valid=false`，Agent 仍反复尝试 `task.plan.save`。工具链碎片化、错误反馈与真实规则不一致，放大模型失误；不能以新增脚本目录白名单继续修补。
- 本次没有 Task、Column Run 或 Worker Assignment，因此没有证据认定 Mailbox 或 Worker 本身故障。保留“单 Column 单 Worker、Mailbox 只通知”的现有架构；不以交付物质量推断尚未运行的流程。

## 目标行为

1. Conversation Agent 在讨论中可 `loop.list/inspect` 寻找模板。适合时 `loop.apply` 导入，再以 `workflow.plan.save`、`workflow.publish` 修改为本需求 Workflow；没有合适模板时，直接用这两个固定原生能力创建初始 Workflow。无需新建 Loop、写数据库或调用用户侧脚本。Loop 来源保留为可空的 provenance。
2. 授权后先冻结需求、Column 职责、输入输出、转移与验收**目标**，然后验证 TaskPlan、创建 Task。Workflow 创建前不要求 CA 编写或指定各列的测试脚本。具体命令、测试代码与报告由执行该列的 Worker 按交付物选择；它们是内部实现，不是 Web 使用步骤。
3. 每个 Column 独立执行并提交可追溯证据；验收 Column 按需求中的场景/准则核验结果。失败反馈通过 Task 的责任列和重验准则回流，Mailbox 仅通知状态和交付摘要。若用户或模板显式提供固定命令检查，现有 `acceptance_checks` 仍可选用并由 Runtime 执行。

## 代码修改顺序与位置

### A. 打开 Workflow 作者入口

- `app/v1/store.py::publish_workflow/_publish_workflow_revision`：允许在项目没有 Workflow 时，用已保存的 `WorkflowPlan` 发布初始定义；仍执行现有 capability、graph、Column schema 与 plan/definition 对齐检查。初始无 Loop 时 `source_loop_*` 为 `NULL`，已由表结构支持；有 Loop 时保留来源。发布与修订均生成不可变 revision，已有 Task 始终引用旧 revision。
- `app/v1/capabilities.py::_workflow_publish`：调用上述路径，并将当前 Requirement/revision 绑定到新 Workflow。`app/v1/repositories/scope_repository.py::for_workflow/bind_workflow` 已允许 `binding_id=NULL`；检查所有读取 Loop binding 的调用方，不能把“无 binding”误判为“无 Workflow”。同一授权需求内可反复保存 plan、发布修订；新范围才走 `project.scope.revise`。
- `app/v1/capabilities.py` 的 `workflow.plan.save` / `workflow.publish` 描述、`DEVWERK.md`、`app/v1/context_manager.py::planning_projection`：明确三条合法路径：应用模板、应用后修改、从零创建。提供简短固定 schema/下一步说明，取消“无 Loop 不得创建”表述。保持模型只能用公开能力，不写 DB。

### B. 将验收目标与脚本解耦

- `loops/ddd-software-delivery/loop.json` 与相应 Loop 编译/领域模型：`required_behavior_columns` 不再意指“每列必须提前有一个脚本”；保留阶段职责、场景 ID、最终验收目标及修改后需重验的关系。模板可带**可选**固定检查，但不要求规划 Agent 为未来 Worker 预写入口。
- `app/v1/services/task_plan_compiler.py::acceptance_diagnostics/diagnose_task_plan`：Task 准入验证 Workflow 连通性、责任列、需求场景与验收目标覆盖；仅当 Workflow 显式声明 `acceptance_checks` 时验证其命令和报告契约。不能把“缺少命令”视为通用准入失败。`task.plan.validate` 应一次返回结构化、完整、可操作的诊断；`task.plan.save` 使用同一诊断，避免无效保存循环。
- `app/v1/domain.py`、`app/v1/services/completion_admission.py::evaluate`、`app/v1/runtime.py::_column_completion_contract`：保留现有显式固定检查的执行和失败保护；无固定检查时，要求 Worker 给出与本列目标对应的真实执行证据，并让验收列依据场景做独立核验。`done` 必须有验收列的核验 receipt，不能仅凭 Agent 文字声称完成。具体 evidence schema 与记录位置在实现时选择最小增量，不能允许空证据直接交付。
- `app/v1/repositories/task_feedback_repository.py::record/inherit/verified/settle`：当前反馈强制引用冻结的 `check_key`；增加指向冻结验收目标/场景的验证引用，使无脚本流程仍可回流、重验并确定性结清。显式固定检查的旧反馈继续按原 `check_key` 语义运行；新旧 receipt 不混淆，历史 Task 不迁移语义。
- `app/v1/capabilities.py::workflow.acceptance.runners`：降为可选的高级固定检查配置；修正“已有 Project 脚本即可用”与实际责任目录规则不一致的描述。不要为了本案例扩大可写目录或新增 Web 上的调试信息。

### C. 收窄 Agent 规划路径

- `DEVWERK.md` 与 `app/v1/context_manager.py`：授权后建议顺序为：选模板或自建 → 保存/发布 Workflow → `task.plan.contract/validate` → 仅当 `valid=true` 才 `task.plan.save` → `task.create` → 监督 `task.inspect`。不引导 CA 提前写 `scripts/*.ps1`；Worker 根据本列实际技术栈选择测试工具。对于当前需求的规划修正，使用新 Workflow revision，不调用 `project.scope.revise`。
- 如当前 `workflow.plan.save`/`workflow.publish` 的参数描述仍导致多次 schema 猜测，可补一个**只读** `workflow.authoring.contract` 能力，输出简短准确的字段、合法 Column key/转移和下一步诊断；不新增另一套持久化对象、权限层或 Main Agent。先试现有能力的说明与回归，确有必要再加此只读入口。

## 回归与完成标准

1. 更新 `tests/test_capability_contract.py`、`tests/test_api_web_contract.py`、`tests/test_orchestration_policy_contract.py`：从零发布、Loop 导入后修改、同需求多次修订、Task 冻结旧 revision、无 Loop binding 的 inspect/scope；保留跨 Requirement 授权边界。
2. 更新 `tests/test_context_acceptance_regression.py`、`tests/test_task_entry_admission.py`、`tests/test_loop_contract.py`、`tests/test_repair_notification_contract.py`：无预设脚本的需求可创建 Task；空/伪造证据不能 `done`；验收失败回到责任列并在重验后结清；显式固定检查与历史 Task 仍按旧规则运行；Mailbox 不承载反馈对话。
3. 新 Project 黑盒：Web 连续至少五轮讨论 → 授权交付 Vue/Java 登录与防刷 Demo → CA 选择/修改 Loop 或自建合适 Workflow → 产生 Task、Column Runs、单列 Worker → 失败可恢复并推进至最终验收。测试中不通过脚本直接控制 DevWerk；测试方可用内部脚本做启动与观测，但用户路径始终是 Web 对话。记录调用次数、无效写文件数、最终状态和交付物核验结果。

按 A、B、C 的依赖顺序做小步提交，每步跑对应单测，最后才运行完整黑盒。若 B 的 evidence/feedback 设计发现旧契约无法兼容，先在文档中定最小 receipt 结构并审查，不能靠放宽 `done` 准入赶进度。
