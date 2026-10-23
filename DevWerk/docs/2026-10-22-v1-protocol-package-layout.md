# v1 协议模型按职能归包（2026-10-22）

## 目标与边界

`app/v1` 根目录近期新增大量只定义请求、回执、上下文帧和持久化事件的文件，审阅时无法按职能定位。此次仅移动这些协议模型并更新 Python 导入；类名、字段、默认值、Schema、工具注册与运行逻辑保持不变。`capabilities.py` 继续作为已有调用方的稳定入口，Registry、注册表和处理器仍留在根目录。已有的 `domain.py`、`contracts.py`、Agent、Conversation、Workflow、Store 等行为模块不搬迁。

目标布局为 `app/v1/protocols/` 下的职能包：

| 包 | 模型范围 |
| --- | --- |
| `tools/{agent,planning,workspace,core}` | 按 Agent 操作、规划/Task、文件命令、通用 Tool 协议细分内置 Tool 的输入、回执和 Schema |
| `agent` | Agent 提示/运行上下文、Run/Worker 仓储视图 |
| `conversation` | Conversation 意图、通知、反馈与会话投影 |
| `context` | 上下文压缩、工具结果投影、Memory 输出和会话历史帧 |
| `workflow` | Loop 绑定、Workflow/Task Runtime 帧、调度与状态目录 |
| `storage` | Store/Repository 的固定持久化与返回记录 |
| `transport` | HTTP 与 MCP 响应模型 |

仅改变 `app`、`tests` 中的导入路径；不改归档历史文档。不保留根目录的转发壳文件，否则平铺问题仍在。迁移前后逐个核对源文件清单，更新所有静态引用，检查没有旧导入路径。因人工审阅尚未结束，仅运行 `compileall`、全模块导入和 Schema/注册表构建等静态检查，不做整体或黑盒测试。

## 执行记录

已迁移 46 个文件并重写 `app`、`tests` 中 54 个文件的 153 处原始导入引用；Tool 类再归入 `tools/agent` 5、`tools/planning` 7、`tools/workspace` 3、`tools/core` 7。其余分类数量为 `agent` 5、`conversation` 4、`context` 5、`workflow` 6、`storage` 2、`transport` 2。`app/v1` 根目录的 Python 文件数由 92 降为 46。`tests/test_json_protocol_source_contract.py` 的处理器检查现指向实际实现文件 `capability_handlers.py`，避免检查空的 `capabilities.py` 导出入口。

迁移后检查：旧 `app.v1.<模型文件名>` 引用为零；`compileall` 对 `app/v1`、`app/core`、`app/services`、`tests` 通过；46 个新协议模块逐一导入通过；`app.v1.api` 导入通过；内置 Registry 仍注册 74 个 Tool，74 个均有输出 Schema；`git diff --check` 通过。聚焦的 `tests/test_json_protocol_source_contract.py` 为 8/8 通过。首次运行因沙箱默认临时目录不可创建而在 fixture 阶段报错，指定仓库内临时目录后通过，临时目录已清理。未运行整体回归或黑盒交付测试，符合当前人工审阅阶段的限制。
