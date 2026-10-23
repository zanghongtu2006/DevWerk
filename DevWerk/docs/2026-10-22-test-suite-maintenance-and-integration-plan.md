# 单元测试收敛与 Workflow 主链路集成验证（2026-10-22）

## 目标和边界

先整理确定性测试并保持全部通过，再以新 Project 完成多轮自然语言对话 → Conversation Agent 创建 Workflow/Task → Runtime 推进 Task 到 `done` 的真实集成测试。仅修复阻塞这条主链路的 P0 缺陷。集成成功必须看数据库/Tool 回执/文件等事实，不能以 Agent 自述替代。历史 Project 不作为此次测试对象，不杀正在运行的服务；需要启动服务时只管理本次启动的进程。

## 单元测试维护方案

现有 `tests/` 根目录有 26 个 `test_*.py` 文件，包含约 500 个确定性用例，多个文件超过 800 行；`tests/README.md` 的 25 文件/538 用例统计已过时。按被测职能移动到 `tests/unit/{api,tooling,conversation,workflow,agent,storage,config,evaluation}/`，保留现有测试函数和断言；`tests/conftest.py` 与通用 `tests/helpers.py` 保持单一入口。更新跨测试模块的导入路径和显式仓库根路径；不借重排测试改变生产逻辑。先记录迁移前 collected 数，再核对迁移后数量完全一致；随后跑全量确定性测试并修复因迁移产生的导入/路径问题。跨测试文件调用的构造器逐步提取至 `tests/support/`，避免以其他测试文件作为辅助 API。

生产代码中已有一个与上轮模块归包有关的路径风险：`app/v1/workflows/loops.py` 用 `Path(__file__).resolve().parents[2] / "loops"` 计算默认模板目录；文件从 `app/v1` 移入 `app/v1/workflows` 后，该表达式指向 `app/loops`。应增加默认目录单元测试并修复路径，不改变 Loop 选择逻辑。

## 集成测试方案

使用新的独立 Project 和最小、可验证的交付目标，至少五轮自然语言澄清与授权；不替 Agent 直接调用 Workflow/Task Tool，也不代替 Agent 决定 Column。记录每轮用户输入、Agent 回答与实际 Tool 调用；核对 `workflow.create`/等效发布、`task.plan.save`、`task.create` 的回执和持久化状态。等待 Runtime 自行调度，按合理间隔查看进展；结束时核对 Task `done`、终态路径、产物内容及可用性。P0 故障须记录请求、回执、日志和 DB 证据，先归因后做最小修复，补聚焦回归，再启动新的 Project 复测。

## 执行记录

已将 26 个测试模块归入 8 个 `tests/unit/` 职能包，保留 `tests/conftest.py` 和 `tests/helpers.py`，新增 `tests/support/paths.py` 作为跨目录仓库根路径。迁移前后均收集 572 个既有用例；新增默认 Loop 路径回归后为 573 个。`tests/README.md` 已按实际目录重写。单元夹具使用临时空 MCP 配置，避免确定性 API 测试启动真实 Chrome MCP。

测试中发现并修复：归包后的默认 Loop 目录层级错误；Workflow/Loop 投影协议将对象错误声明为字符串；上下文维护失败回退因 `exclude_unset` 丢失 `method`；Tool Registry 归包后漏导入运行时引用检查；暂时性 Tool 错误被默认错误码遮住而未进入重试；API 响应把原始 dict 赋给已验证的 Pydantic 字段，产生序列化警告；usage SQLite 连接与评估脚本/夹具连接未显式关闭。测试夹具改用合法 `ToolError`，有关 Tool 回执的断言改为解析 JSON 字段，不依赖空格序列化。`DEVWERK.md` 的建图指令改为当前 `workflow.create` 路径并保持长度约束。

Windows 当前受限执行环境的 `socket.socketpair()` 会阻塞，FastAPI TestClient 和 asyncio 用例需在获准的本机回环 socket 环境运行；这属于测试环境限制，不记为 DevWerk Runtime 缺陷。573 项测试已全部按职能包通过：Agent 98、Workflow 184、Conversation 131、API/Tooling/Storage/Config/Evaluation 160。`compileall` 与 `git diff --check` 通过。Conversation 旧测试的两处断言把“Agent 原话以未作为完成依据的形式保留”误判为事实状态错误，已改为检查真实 Task 状态先于免责声明；另有一处预算测试使用失败的重复 `task.inspect`，被当前重复失败拦截，应改用成功的 `project.inspect` 验证预算。

随后将全部 573 项放在一次 pytest 进程中复跑，结果 `573 passed in 807.66s`；测试包之间未发现顺序干扰。
