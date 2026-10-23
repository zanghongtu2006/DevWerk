# 内置 Tool 注册目录化（2026-10-22）

## 现状与字段来源

`app/v1/tooling/capability_registration.py` 当前有 74 次 `register_builtin` 调用。每次前两项是写在调用处的稳定 Tool ID 和 Agent 可见说明；其余项绑定 Schema、处理函数和执行策略。字段含义如下：

| 字段 | 来源 | 消费位置 |
| --- | --- | --- |
| `id` | DevWerk 内置 Tool 的稳定协议标识；现为注册调用处的字符串 | Registry 索引、Provider function name、Workflow Tool 引用、调用分发 |
| `description` | DevWerk 给 Agent 的 Tool 使用说明；现为注册调用处的字符串 | `CapabilityEntry.tool_schema()` 交给 Provider |
| `input_schema` | 请求协议类的 `model_json_schema()`；部分加当前 Runtime policy 的限额 | Provider Tool 参数声明和 Registry 入参校验 |
| `handler` | `capability_handlers.py` 函数或当前 Store/Registry 上的闭包 | Registry 实际分发 |
| `output_schema` | 回执协议类的 `model_json_schema()` | Registry 输出校验、Workflow 可引用结果定义 |
| `side_effect_kind` | 对调用副作用的分类，值域已有 `SideEffectKind` Enum | 执行控制、事务/回放、授权检查 |
| `delegable_to_column` | 该 Tool 能否暴露给 Column Agent；当前为布尔值 | Column Tool 集合和 Workflow 能力校验 |
| `argument_preflight` 等 | 个别 Tool 的附加参数预检、引用字段与对象解包定义 | Registry 参数规范化与 Workflow 检查 |

例如 `conversation.reply` 的输入来自 `ConversationReply`，输出来自 `ConversationReplyReceipt`，执行者是 `_conversation_reply`；`session_control` 表示控制当前会话，`False` 表示 Column Agent 不可委派。`conversation.draft.update` 的执行者是 `ctx.store.intents.update_draft`；`conversation.turn.inspect` 的输入是 `EmptyInput`，输出是 `ConversationTurnInspectReceipt`，执行者是 `_inspect_conversation_turn`。这些都是 DevWerk 内置协议，不是用户项目内容。

## 修改边界

新增 `app/v1/tooling/builtin_tool_catalog.py`：每个 Tool 使用唯一 Enum 成员，成员值为不可变 `BuiltinToolDefinition`，集中放置 `id`、`description`、`side_effect_kind`、`delegable_to_column`。`side_effect_kind` 使用既有 `SideEffectKind` 枚举，杜绝注册调用处的自由字符串。`capability_registration.py` 的调用只引用目录成员，并传入请求/回执协议类生成的 Schema、处理函数和个别 Tool 的附加预检。`register_builtin` 从目录成员组装 `CapabilityEntry`，对目录完整性进行一次构建校验。

此次不改变 74 个 Tool 的 ID、说明文字、Schema、默认值、执行策略、可见性或处理函数；不把使用说明变成业务规则；不改模型提示词、Workflow 设计或 API。所有注册项迁移后按 ID 逐一对比旧快照。只做静态/聚焦检查，暂不运行整体或黑盒测试。

## 执行记录

已建立 `builtin_tool_catalog.py`，其中 74 个 `BuiltinTool` 成员各自持有不可变的 ID、Agent 可见说明、`SideEffectKind` 和 Column 可用性。`capability_registration.py` 的 74 次调用现全部引用目录成员，不再在注册处填写这些字符串/布尔策略；请求与回执 Schema 仍由原协议类生成，处理函数和少数附加预检保持原位。输出 Schema 参数改为必填，避免漏填时静默退化为空对象；构建 Registry 时核对目录与实际注册 ID 集合完全一致。

改动前后按注册顺序对 74 项的 ID、说明、输入 Schema、输出 Schema、副作用类别、Column 可用性生成快照，SHA-256 均为 `576d450bf69aa2835e3e00ba8a4d35588efb4349b9f2444cd59cccf7f0034404`。编译及 `git diff --check` 通过；聚焦的源码契约、Registry 声明、Column 可用性和 Workflow Tool 模型测试共 11/11 通过。未执行整体或黑盒测试。
