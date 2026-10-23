# JSON 协议硬拼接全仓审查与修复方案（2026-10-21）

## 边界与完成标准

本轮只处理 DevWerk 的 JSON 协议构造和校验，不改变 Workflow 路由、Agent 权限或项目交付策略。固定结构的请求体、回执、持久化记录、Agent 上下文帧和事件必须由独立模型的属性定义；序列化只发生在 Provider、MCP、HTTP、SSE、文件或数据库的边界。动态 MCP Tool 参数、用户 Loop JSON Schema、Task 输入等保持通用 JSON 容器，但在入口验证 JSON grammar 和外部声明的 Schema，不能把业务字段写死进引擎。

同一对象的输入校验、输出生成与 `model_json_schema()` 应共用一个类。禁止先 `model_dump()` 再用字符串 key 改写固定协议；禁止 `stable_json({...})`、`packed({...})`、`json.dumps({...})` 等构造未建模的固定载荷。AST 检查是防退化辅助，完成标准还包括 Registry/HTTP 的运行时出入参校验和邻接回归。

## 全仓审查清单（独立审查已完成）

| 优先级 | 路径与入口 | 已确认问题 | 修复边界 |
| --- | --- | --- | --- |
| P0 | `app/v1/repositories/conversation_intent_repository.py` 的 `context`、`contract`、意图合并和 `resolve` | `WorkIntentState.model_dump()` 后按字符串 key 改写状态；proposal、turn contract、result 手工构造并入库 | 保持 `WorkIntentState` 模型实例到仓储边界；为 proposal、turn contract、resolve receipt 建独立类，兼容旧 DB wire 字段 |
| P0 | `app/v1/agent_run_preparation.py`、`agent_runner.py` | 手工字典进入 `stable_json` 形成 Agent 系统/用户上下文 | 按当前 Assignment、历史投影、handoff 等帧建类，序列化类；不得改变提示词语义 |
| P0 | `app/v1/context_manager.py` | 截断 Tool 输出、checkpoint 用户消息与 metrics JSON 手工拼装 | 使用已有上下文结果类并补齐 checkpoint/metrics 类；保留预算和来源引用 |
| P1 | `app/v1/services/task_plan_compiler.py` | 校验诊断、延期命令检查和结果返回仍拼字典 | 复用 `ValidationDiagnostic`、`ValidationReceipt`、`DeferredCommandCheck`，不改变检查顺序和错误文字 |
| P1 | `app/v1/api.py` | health、board、Task/Agent Run 详情、SSE 事件仍手工构造或就地增补；约 38 路由未声明 response model | 按路由分批建立独立响应类，先覆盖实际拼装处，再覆盖其余固定响应；动态事件数据保持明确容器 |
| P1 | `app/services/{openai,anthropic,ollama}_client.py` | 请求已类化，响应 `tool_calls`/usage 仍手工字典 | 统一 Provider response adapter 的类和校验，维持现有规范化返回 wire |
| P1 | `app/v1/capabilities.py` Registry | 74 个内部 Tool 中仍有 34 个未配置成功输出 Schema | 对应 Store/Repo 真实返回建立回执类，再注册并运行验证；不得以宽泛 `dict[str, Any]` 伪装完成 |
| P1 | `app/v1/capabilities.py` 的 `column.complete` 结果包装与内部 `workflow.publish` 调用 | `ToolResult.model_dump()` 后追加 `evidence`；内部调用手工组参数 | 扩展真实回执模型、直接实例化既有 `WorkflowPublishInput` |
| P1 | `app/v1/store.py` 的 Loop、等待、执行、Task 记录和投影 | 固定回执与持久化数据在多个出口仍由字典加字符串 key 生成 | 沿 `Tool → Store → DB` 调用链分组建模，不改通用动态数据 |
| P1 | `app/v1/services/scheduler.py` 的调度决定与通知；`repositories/{agent,task_feedback,scope,event,artifact}_repository.py` | 调度决定、Worker/Context/反馈/事件/交付回执仍有固定字典，部分外发 Tool 无输出 Schema | 各固定回执独立建模并接入 Registry 校验 |
| P2 | `app/services/usage.py` 的统计输出 | 固定统计响应手工字典 | 给 HTTP/调用边界使用统计模型 |

## 修改顺序与回归

1. 先补齐 AST 审计：覆盖 `stable_json`、`packed`、Provider `json=`、直接 `return {...}` 的关键外发函数；新增针对审查中确认的真实违规样例的测试。记录合法动态 JSON 和传输拼装例外，不将字符串格式化的 SSE 帧误当 JSON Schema。
2. 优先修 Conversation 持久化和 Agent 上下文帧。每个对象固定字段从类属性生成，DB 旧记录可读；现有对话授权、恢复、上下文生命周期测试须通过。
3. 修 Task Plan 诊断及 Provider 响应适配，每类分别回归其 Tool/Provider 合同。
4. 按 Store/Repository → Tool 输出 → HTTP 路由的调用链逐组修复，不一次性机械替换。保留原线字段、默认值和响应形状，明确错误码与动态字段边界。
5. 最后全仓复扫并记录剩余项为零或说明合法边界。运行源合同、Capability、Conversation、Context、Store、Provider、MCP、Loop 与 API 邻接测试；长时间中断的测试不得算通过。

独立审查另确认以下**合法动态 JSON**：外部 MCP Schema 经 `check_schema` 检查后的透传；Loop 参数替换、JSON Pointer 的递归变更；任意 Tool 参数、事件 meta 和 checkpoint 的纯传输编码；SSE 帧字符串。它们不应被机械替换为场景固定字段。当前源代码 AST 守卫漏掉 `stable_json`、`packed`、`stable` 以及 `model_dump()` 后的增补，最后阶段须补齐。

本文件先于后续代码修改落地；每组结果与回归在此续记。

## 本轮进度（持续更新）

- 已清理跨轮 WorkIntent 的 `model_dump → 字符串 key 修改`，新增 proposal/turn/Requirement/resolve receipt 类，原 DB wire 字段保留。Conversation 意图用例 55/55 通过；随后增加 Tool 输出 Schema 的聚焦用例 19/19 通过。
- 已清理 Agent 上下文帧、Tool 回执 evidence、checkpoint coverage/metrics/截断投影、Task Plan 诊断和三种 Provider 的规范化响应及 Anthropic 消息块。Provider 用例 24/24、Context 用例 52/52、Capability 用例 53/53 通过。
- Store、Scheduler、Scope/Event/Artifact 的固定回执已逐组类化。调度回归发现先前 `WaitRequestPayload` 将 `SequenceCheckpoint` 作为基类序列化时丢失 `awaiting_save_as`，已用 `SerializeAsAny` 保留子类字段；该失败用例单独复测通过。
- `test_orchestration_policy_contract.py` 继续跑到一项非本轮 JSON 改动的文档长度断言：`DEVWERK.md` 实际 3110 字符，测试要求小于 3000。此项与 JSON 协议改动分离，未改变该文档或断言。其余回归仍在进行。

仍需沿审查清单完成 Repo/Tool 输出与 HTTP 的余项，并在全部修改结束后复扫。上述通过数仅对应各次运行的代码时间点，新增修改须再跑邻接回归。

## 2026-10-22：先整理 `capabilities.py`，暂停整体测试

用户审阅指出该文件仍将 Registry 执行、74 个 Tool 的注册协议、Workflow 参数校验及 Tool 处理器混在一处，并在注册函数中临时导入类型、定义 `reply` 闭包。先仅做代码结构整理：Registry 和执行边界进入独立模块；内置 Tool 的声明集中在注册模块；处理器与通用 Workflow 参数检查进入处理器模块；`capabilities.py` 保留稳定导出入口。`conversation.reply` 将经 `ConversationReply` 模型序列化后交给既有报告渲染器。Tool ID、说明、入参/出参 Schema、可委派属性、执行副作用类别和调用顺序保持原值。整理后只做静态检查和必要的聚焦合同检查，不运行整体或交付测试；等待人工代码审阅后再决定整体回归。

已完成此文件的职责分离：`capabilities.py` 仅导出调用方使用的稳定符号；`capability_registry.py` 负责注册、可见性、分发和合同验证；`capability_registration.py` 集中声明 74 个内置 Tool；`capability_handlers.py` 保留通用执行与 Workflow 参数校验。原先的 `reply` 闭包改为命名处理器，参数经 `ConversationReply` 模型验证和序列化（排除未传默认字段）。仅 Task Plan 编译器的两处导入保持延迟，以避免其反向依赖 Capability 校验造成循环。静态编译、模块导入及 Registry 建立检查通过：74 个 Tool 均有输出 Schema，24 个可委派给 Column；`git diff --check` 通过。未运行整体测试，也未进行黑盒交付测试。

本轮结构整理不等于全仓 JSON 审查完成。其余事件/通知等固定载荷仍按上面的清单待处理；在人工审阅此结构之前不继续扩大代码修改。

在用户要求暂停整体测试之前，邻接回归暴露两个协议兼容问题：`ProviderErrorDetails` 从 dataclass 改为模型后仍有位置参数调用，已恢复位置参数入口并交给模型校验；执行授权的幂等重放回执比首次授权多三个字段，已将重放回执设为首次回执的显式子类并用于该 Tool 的输出 Schema。这两处修改尚未进行用户审阅后的整体回归，不记为最终通过。
