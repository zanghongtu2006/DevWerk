# Project 联调列超时与进度查询失败：修复方案

## 现场事实

Project `prj_42ab1012ccf343689ca798a3ceec6f0e` 的新 Workflow Revision `wfrev_7347cae79dfd461f888f988af8ac3914` 与 Task `tsk_d8d5b2d260cc4d648d99ba344ee15cc7` 已成功创建。Task 进入 `integration` 后，Column Run `run_c66f1b6423c84c22a75e2a4d65ea3523` 运行 3600 秒、38 次模型迭代、46 次工具调用，最后以 `execution_budget_exceeded` 中断；Task 为 `recovering/paused`。

三次 `project.command.run` 各等待到 600 秒超时。其中最后一次集成脚本**已经打印全部测试结果与报告路径，直接命令退出码为 1**，但 DevWerk 仍将其等待到超时。报告显示 1 项通过、7 项失败，失败项的 HTTP 状态为 0；该脚本把 PowerShell 对非 2xx HTTP 响应的异常当成无响应，属于交付物脚本缺陷，应由恢复后的 integration Worker 修改。DevWerk 不能把该报告标成验收通过。

`app/v1/process_runner.py::run_command()` 的循环条件是“直接进程还活着 **或** 任一 stdout/stderr 读取线程还活着”。Windows 子服务继承输出管道后，即使脚本进程已退出，读取线程仍等待子进程关闭管道，于是整个命令被误判为 600 秒超时。Windows Job 最终会清理子进程，但清理发生得太晚。

用户消息 63 的只读进度查询另有问题：Conversation Agent 用本次 Run ID 搭配不存在的 tool-call ID 调用 `agent.result.read`，得到“结果不存在或超出范围”；它重复错误调用后触发 `ConversationProtocolStalled`。有效的任务状态已经由 `task.inspect` 返回。`agent.result.read` 应对本 Project 内存在的 Run 的无效 tool-call ID 给出可恢复的有界索引，而跨 Project 或跨 Worker 数据继续拒绝。

加载上述修复后的消息 67 已明确要求“恢复并完成现有 Task，不要新建 Task”，但 Conversation Agent 只读证据后仍把 `conversation.turn.resolve` 固定为 `act=discuss`，回复“需要我现在执行吗”，Task 没有被 `task.retry`。此前消息 63 的只读限制只约束那一轮；本轮 `requested_mode=auto` 且 `start_task=true`。这是对当前用户行动请求的错误降级，不是缺少授权。

## 最小修复

1. 在 `process_runner.run_command()` 中，直接进程退出后只给读取线程一个短暂排空窗口；随后关闭其 Windows Job/进程组以清理仍持有管道的子进程，再收集剩余输出并返回直接进程的真实退出码。真正仍在运行的直接命令继续受 `command_timeout_seconds` 限制；输出上限与取消检查不变。
2. 在 `capabilities.py::_agent_result_read()` 中，保留 Project 和 Worker 边界。若 Run 可访问但 tool-call ID 不存在，返回 `found=false` 与该 Run 最近的有界合法引用，避免只读查询因猜错 ID 直接中止；更新工具说明要求先用已有索引，状态查询优先 `task.inspect`。
3. 收窄 `conversation.py` 的当前轮行动说明：上轮只读检查不限制后续明确的修复/恢复命令；明确行动请求应使用当前用户证据授权并执行，不要再次索要同一权限。保留用户可以选择讨论的自由，不增加关键词白名单或新状态位。
4. 对已中断的 Task 不改数据库、不重置历史 Run。代码与单元测试通过后，仅为加载代码而重启 DevWerk，再通过公开 Conversation API 请求对同一个 Task 做恢复；由 Worker 修复脚本、运行测试并提交 Column 结果。每隔约 5 分钟观察一次 Job、Task、Column、工具错误与验收证据。若仍阻塞，只针对新证据继续修复，不把 `pending/running` 说成交付完成。

## 回归标准

- Windows 进程树回归：直接命令退出、子进程继承 stdout/stderr 且保持运行时，工具应快速返回真实退出码，子进程被清理；前台超时、输出上限与已有子进程清理回归保持通过。
- `agent.result.read` 对本 Project 正确引用可读、错误引用可得到有界合法索引，跨 Project/跨 Worker 仍不可读。
- 明确恢复请求的 Agent 轮次应真正授权并调用恢复工具；只读状态请求仍不产生执行授权。
- 现场 Project：恢复后 Task 离开 `recovering/paused`，集成测试脚本完成且真实 HTTP 断言通过，Workflow 沿定义的边推进至 `done`；有任何失败则记录并继续处理。

## 当前执行

- `tests/test_p0_runtime_regressions.py`、`tests/test_context_acceptance_regression.py`、`tests/test_command_resolution.py`：90 passed，其中新增 Windows 子进程继承管道回归与缺失 tool-call ID 的可恢复索引断言。
- 已仅重启经命令行验证的端口 8000 DevWerk 实例，Project API 返回 200。
- 已经由公开 Conversation API 提交同 Task 恢复请求：Job `cjob_0db7650f92064b30aad0e7ad6dfed1a5`，消息 67。接下来以约 5 分钟间隔核对真实状态；提交请求本身不等于恢复成功。
- 第一次 5 分钟检查：Job 虽显示 `succeeded`，但只是讨论回复，`v1_turn_resolutions.act=discuss`，Task 仍 `recovering/paused`，未调用 `task.retry`。已将这条新发现纳入本次修复。
- 补充了与现场 `Start-Process -NoNewWindow` 行为一致的 Windows 回归，脚本打印并退出后约 2 秒即返回真实退出码 7；新增测试通过。Conversation 与上下文生命周期回归 39 passed。已重启 DevWerk 加载本轮提示修正，并通过公开 API 提交继续执行请求，Job `cjob_12349e456e584bc9971af4d9b26a0220`，消息 69。下一次检查仍按约 5 分钟间隔。
- 第二次 5 分钟检查：该 Job `succeeded`，本轮 `act=execute`，`task.retry` 成功；Task 已从 `recovering/paused` 变成 `running/active`，新的 integration Column Run `run_05178b4fc0ae436e94b4943eab074113` 正在执行。Agent 曾将 `agent.worker.send` 和 `task.retry` 放在同一工具批次，受到“队列动作须单独提交”协议拒绝；它改为单独重试成功，但诊断消息并未送达 Worker。下一检查须确认 Worker 是否自行从报告修复脚本，不能把恢复运行视为完成。
- 第三次 5 分钟检查（06:09 UTC）：Task 仍在 integration，但新 Run 已推进到至少 29 次工具调用，并重写 `scripts/run-tests.ps1`；此前 600 秒误超时未再出现。最近两次执行以 PowerShell 解析错误快速退出，原因是当前 `.ps1` 文件中的中文在 Windows PowerShell 5.1 下被错误解码；Worker 正继续修正。尚无真实 HTTP 验收通过证据。
- 第四次检查（06:14 UTC）：Worker 正尝试把后端启动和 HTTP 检查放在同一次 `project.command.run` 中。分开启动的服务会在调用结束时按工具契约清理，因而先启动、下一调用再验证的尝试无法成功。一次性联调脚本已产生，但首轮 0/8，Worker 仍在诊断；无工作流卡死迹象。
- 第五次检查（06:25 UTC）：新命令没有再误超时。Worker 修正了 PowerShell 对 HTTP 4xx 的读取方式，实际 HTTP 联调从 1/8 进至 7/8；余下第三次错误密码预期 401、实测 429，需核对预设防刷语义后修正代码或断言。Task 仍运行，尚未通过验收。
- 第六次检查（06:30 UTC）：原 Task `tsk_d8d5b2d260cc4d648d99ba344ee15cc7` 已于 06:27 UTC 进入 `done`，integration Column Run `run_05178b4fc0ae436e94b4943eab074113` 与 Worker Agent Run 均 `succeeded`。最后一次 `project.command.run`（工具序号 76）实际执行 `scripts/run-tests.ps1`，退出码 0、非超时，输出 `PASSED: 8 / FAILED: 0`；随后 Worker 读取 `docs/TEST-REPORT.md` 并调用 `column.complete`。报告载明正常登录 200、错误密码前两次 401、第三次及锁定期间 429、三种空参数 400，合计 8/8。最后的锁定语义与实际服务行为吻合。还需间隔复查状态是否稳定。
- 间隔复查（06:35 UTC）：Project 最新 Task 仍为原 Task，状态稳定在 `done` / `current_column=done`，仍绑定新 Workflow Revision `wfrev_7347cae79dfd461f888f988af8ac3914` 与 Requirement Revision 2；未被旧运行结果打回，也未出现重复 Task。此次阻塞已排除。
