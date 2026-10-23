# v1 运行模块归包方案（2026-10-22）

## 范围

当前 `app/v1` 根目录尚有 46 个 Python 文件，API、Agent、Tool、Conversation、Workflow、Runtime 和文件执行代码平铺。此次仅按现有职责移动模块并改导入路径，不改协议类字段、Tool 注册、HTTP 路由、状态转换或运行逻辑。协议模型仍留在 `protocols/`，现有 `repositories/` 和 `services/` 不移动。

| 目标包 | 移入模块 |
| --- | --- |
| `api/` | `api.py` → `routes.py`；`__init__.py` 导出 `router`，保留 `from app.v1.api import router` |
| `agents/` | `agent_models`, `agent_prompt`, `agent_protocol`, `agent_provider`, `agent_run_preparation`, `agent_runner`, `agent_tool_execution`, `llm` |
| `tooling/` | `capability_handlers`, `capability_registration`, `capability_registry`, `capability_schema_adapter`, `completion_protocol`, `tool_protocol`, `mcp_tools` |
| `conversations/` | `conversation`, `conversation_failure`, `conversation_intent`, `conversation_report` |
| `context/` | `context_manager`, `context_projection`, `memory`, `session_replay` |
| `workflows/` | `loops`, `workflow_acceptance_editor`, `workflow_authoring`, `workflow_transport`, `task_identity` |
| `execution/` | `execution_control`, `execution_ledger`, `execution_recovery`, `runtime`, `states` |
| `workspace/` | `files`, `process_runner`, `windows_job`, `command_resolution` |

根目录保留 `agent.py`、`capabilities.py` 稳定入口，以及 `contracts.py`、`domain.py`、`policy.py`、`storage_support.py`、`store.py` 等跨职能核心模块。模块包只表示代码位置，不增加权限边界、依赖注入层或新的转发壳。内部调用与测试导入直接指向新位置；HTTP API 入口保留兼容导出，测试对 `trace_json` 的 patch 改为实际定义模块。

## 步骤与验证

1. 核对每个源文件与目标位置，先移动文件，再精确更新 `app/`、`tests/`、`scripts/` 中的导入和源码定位检查。
2. 检查无旧模块路径残留；逐个导入新模块，验证 API router 和 Tool Registry 构建；执行 Python 编译及少量聚焦的源码契约测试。
3. 检查 diff，记录结果。用户人工审阅前不做整体、黑盒或项目交付测试。

## 执行记录

已移动 38 个文件到 8 个包，更新 `app/`、`tests/`、`scripts/` 共 69 个 Python 文件中的 233 处模块引用；`app/v1` 根目录剩余 8 个 Python 文件。`app.v1.api` 继续导出 `router`，路由实现位于 `app.v1.api.routes`；API 输入/输出协议类继续集中在 `protocols/transport`，未复制 Schema。

静态核对：旧模块导入路径为零；`compileall` 对 `app/`、`tests/`、`scripts/` 通过；38 个迁移模块逐一导入通过；API 共有 39 个路由；内置 Registry 的 74 个 Tool 均有输出 Schema；`git diff --check` 通过。`tests/test_json_protocol_source_contract.py` 的 8 个用例通过。追加的单个 API 对话审计用例在测试客户端阶段超过 90 秒未结束，已中断本次测试进程，不能记为通过；这不影响前述静态检查结论。按人工审阅阶段约定，未运行整体或黑盒测试。
