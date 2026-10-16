# 系统测试列服务生命周期失配与执行预算耗尽

## 当前黑盒证据

新 Project `prj_9887385cae194d82a4a09632559d0108` 经五轮自然语言讨论，第六轮由 Conversation Agent 实际调用 `loop.inspect`、`loop.apply`、`task.plan.validate/save`、`task.create`，Task `tsk_faffe78d85c04be0a689b9cadbeddbfa` 自动通过需求、设计、后端、前端、集成审查，进入 `system_test`。八列均为单 Agent executor，路由遵循 Loop 有向边。

`project.command.run` 在 Windows Job 中运行命令，调用结束即清理子进程；其工具描述已写明前台服务会超时，HTTP 检查必须在同一次有界调用里启动、检查和停止。然而三个列反复用前台 `java -jar`、`Start-Process`、`start /b` 后再开另一次调用请求 HTTP。前台命令多次占满单命令 600 秒上限；后台启动命令即使退出码为 0，下一次调用也没有可访问的服务。

`system_test` 写出的 `scripts/acceptance/run_tests.py` 确曾运行：首次因 Windows 控制台输出符号编码错误退出；修订后退出码 1，报告 **0/9**，实际回执为后端不可连接，并未证明 A1–A9。该脚本最初用未排空的 `stdout=PIPE/stderr=PIPE` 启动 Spring，也可能阻塞子进程。Worker 后续又回到跨调用启服务/探测的路径。第六列 Agent Run `arun_d663499ca5e04859aa6bd30a85a5aed6` 运行 3600 秒、80 轮、92 次工具调用，最终 `ExecutionBudgetExceeded: Agent wall-clock budget exhausted`；Task 为 `recovering/paused/blocked_runtime`，没有错误地前进到 `delivery`。

一次监督测试请求错误地以 `start_task=false` 提交，协议将其限定为讨论，`agent.worker.send` 被拒绝；这是测试请求参数错误，不归因于 DevWerk。已以 `start_task=true` 重发对现有 Task 的监督授权，待观察其工具回执。此前 Conversation Agent 曾据 `sql.init.mode: never` 推断 admin 不存在，但源码 `DataInitializer` 另会创建 admin；这一诊断不能替代 HTTP 证据。

后续确认：获执行授权的监督回合成功调用 `conversation.execution.authorize`、`agent.worker.send`、`task.retry`，原 Task 在 `system_test` 新 Run 中真实执行 `py scripts/acceptance/run_tests.py`，工具退出码 0，stdout 为 `9/9 scenarios passed`；脚本 A7 包含停服、重启及锁定持久化断言。随后 Task 自动进入 `delivery`。因此原 Task 可经主 Agent 监督恢复；仍需减少下一个独立项目中重复前台服务命令造成的预算耗尽。

补充：旧 Workflow 的 `accept` 因非阻断建议误入 Task Feedback 沿图返工（另见同日反馈方案）。第二次 `delivery` Worker 用 `cmd /c Test-Path ...` 查询文件，而 `Test-Path` 是 PowerShell 命令，这四个调用都退出 1。后续 `cmd /c if exist ...` 和 `powershell -Command Test-Path ...` 已成功证明文件存在，但完成协议要求失败调用被同操作成功重试或由后续可识别的行为检查覆盖；Worker 五次重提交 `delivery_ready` 后 `CompletionProtocolStalled`，Task 再次暂停。此处根因仍是列未优先使用已有 `project.files.list/read` 文件工具，且未按失败回执修正完成证据；不放宽完成门槛。

## 最小修复方案（待当前监督回合结果核对）

1. 只调整 `loops/ddd-software-delivery/loop.json` 中与当前阻塞相关的列指令，不改状态机、权限、列数或转移图。`backend_development` 以真实编译/单测和可运行产物为主；需要在线行为检查时，用同一次 `project.command.run` 执行会自行启动、等待就绪、断言、停止服务的脚本。`integration_review` 以契约、实现和已有可执行证据审查为主，不把临时启动服务当作必经关卡，HTTP 全量验收留给 `system_test`。`system_test` 明确先维护一个可重复运行的测试入口；该入口在同一进程生命周期内启动服务、等待就绪、执行冻结场景、在 `finally` 停止；子进程输出写文件或被持续消费，不能让未排空的 PIPE 阻塞；运行失败时修复本列脚本或提交 `tests_failed` 返工，不以跨工具调用的后台服务作为测试前提。`delivery` 验证启动/健康/停止时复用同型有界入口，不能前台运行服务等待退出；文件存在性由 `project.files.list/read` 检查，不把 PowerShell 内置命令交给 `cmd /c`；无需重复完整 A1–A9 系统测试。
2. 仅在 `app/v1/capabilities.py` 的 `project.command.run` 描述中强化已存在的调用生命周期约束，明确成功退出的独立 `Start-Process`/`start /b` 调用不表示后台服务可供下一次工具调用使用。保留现有执行上限与输出语义，避免启发式识别任意项目命令或引入新服务管理工具。
3. 给 Loop 合同测试增加针对这四列指令的断言，验证单列 Agent、有向图、`system_test` 的真实测试与单次调用服务生命周期说明仍在；给 `project.command.run` 合同测试增加描述检查即可。不写复刻文本内容的大段脆弱快照。

## 回归门槛

先运行 Loop/工具合同相关测试，再重启 DevWerk 使新 Loop 生效。仅用新 Project 完成至少五轮自然语言确认，由 Conversation Agent 自己按工具协议创建 Workflow/Task；约五分钟间隔核查，看板自动推进至 `done`，独立核对真实 A1–A9 运行回执、后端编译与前端构建。若当前 Project 能通过现有 `task.retry` 恢复，可作为附加证据，但新 Loop 的验证仍以新 Project 为准。

## 实施记录

- 已只修改软件 Loop 四个执行列的指令及 `project.command.run` 描述，并将 Loop 版本从 `1.4.1` 升到 `1.4.2`。没有修改列数、边、状态机、权限或任何业务 API。
- `test_ddd_software_delivery_loop_has_generic_engineering_lifecycle` 与 `test_command_tool_advertises_bounded_process_lifecycle` 均通过。首次 pytest 因系统沙箱临时目录无法创建而未运行到测试；改用仓库内独立 `--basetemp` 后 `2 passed`。
- 原 Project 已自动进入 `delivery`，仍使用发布时冻结的旧 Workflow；新 Loop 效果必须由新 Project 验证。
- 旧 Workflow 在一次验收返工后的第二次 `delivery` 中，把 PowerShell `Test-Path` 当作 `cmd /c` 命令执行，后续虽然以正确命令证明文件存在，仍因多次未结失败命令而暂停。方案已补充文件检查优先使用 `project.files.list/read`；软件 Loop 最终版本 `1.4.4`。这没有放宽完成协议。
- 仅重启核实为 `uvicorn app.main:app --port 8000` 的 PID 34960；新进程健康检查 `ok`、Supervisor `running`。下一步从新 Project 黑盒回归，不复用旧 Task 的上下文或 Workflow。
