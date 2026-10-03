# 中断命令的重放阻塞：诊断与最小修复方案

## 本次事实（只取本次 Project 的 DB、事件、代码）

- Project `prj_81c5a3a514434143ad995260c528e02f`、Task `tsk_211fbe620312410aa9c2e26292e9d1ff` 在 `backend_development`。前两列已完成。
- 2026-10-03 23:52:23（北京时间）旧 Agent Run `arun_2b9418b40fca41cc9c34820e8266372c` 提交 `project.command.run`，参数为 `cmd /c backend\start_and_test.bat`。Operation `op:arun_2b9418b40fca41cc9c34820e8266372c:104:0` 和对应执行收据已经持久化；收据仍为 `started`，没有 `result_json` 或 `finished_at`。
- 该脚本期间确有文件效果：`backend/acceptance-report.json` 于 23:52:45 写出，含通过和失败场景。因此不能把旧命令推定为“没有执行”，也不能凭现有文件推定命令整体成功。
- 我错误理解“暂停测试和开发”，在旧命令运行期间强制停止了 DevWerk 服务。服务重启后按既有 `auto_resume_previous_tasks=false` 将 Task 置为 `startup_hold`。用户后续恢复时，原 Operation 被重新交付；`CapabilityRegistry._dispatch()` 看见同一收据仍为 `started` 且未被本轮认领，正确抛出 `ExecutionReplayUncertain`，以防重复执行不确定副作用。之后 `task.retry` 只改变 Task 的运行状态，没有处理未结收据，所以多次重试立即撞上相同错误。最终 Task 为 `recovering/paused/effect_outcome_unknown`。此次服务中断由我造成，不能归咎于 DevWerk 自行停止。

## 控制流缺口

`app/v1/store.py::start_execution_receipt()` 保留 `started` 是正确的。`app/v1/capabilities.py::CapabilityRegistry._dispatch()` 拒绝盲重放也是正确的。缺口在 `app/v1/services/recovery_manager.py::retry_task()`：用户或 Conversation Agent 已明确要求重试一个被中断的 Task，但这里没有把**已结束 Agent Run 中未获退出结果的命令**转换为可见的失败事实，导致新 Agent 只能重复遇到同一不可执行 Operation。普通重试和旧的未结副作用被混为一谈。

## 最小代码修改

1. 仅在 `retry_task()` 处理 `recovering/paused/effect_outcome_unknown`、且目标仍是当前 Column 时，在同一事务中查找属于该 Task 当前 Column Run 的未结 Operation/执行收据。要求收据状态为 `started`、Operation 尚无结果、源 Agent Run 已结束、Column Run 已中断。
2. 对 `project.command.run` 的此类未结收据，只将收据记为 `failed`，错误明确写明“命令被中断，退出结果未知，工作区可能有部分效果，继续前需检查”；**不重新执行命令、不宣称没有副作用、不宣称测试通过**。把被处理的 execution key 记入现有 `task.retry_requested` 事件数据，保持可追踪。原 Operation 在重放时会收到既有 `PriorExecutionFailed` 结果，随后由 Worker 检查现有文件/报告并选择新命令或声明的失败/返工 outcome。
3. 对未结的文件写入、控制动作、验收命令等不做自动推断；保留阻断，并返回说明具体 execution key 的错误。禁止通过选择另一 Column 绕过当前未结效果。不新增数据库状态、路由器、权限层或新的调度机制。
4. 同步 `app/v1/capabilities.py` 中 `task.retry` 的工具描述，让 Conversation Agent 知道重试只会把中断命令标成“结果未知的失败”，需让 Worker核对已有效果。

实施时还增加了一个必要的拒绝条件：如果当前中断 Column Run 没有匹配的未结命令收据，或者同时有未结的 Runtime 验收收据，则 `task.retry` 保持阻断；收据更新逐条核对影响行数。这样不会把非命令的不确定效果误认作已处理。

## 回归与新测试

- 单元回归覆盖：中断命令只被标为失败且不执行第二次；原 Operation 重放得到失败结果；其他 `started` 写入/控制效果仍阻断；重复重试不会重复修改收据；普通 Task 重试路径不受影响。保留已有“未明确重试时拒绝盲重放”测试。
- 完成定向测试和必要的全量回归后，因服务需加载新代码才重启 DevWerk。只停止已核实的 DevWerk 监听进程用于必要重启，不触碰其它进程。旧 Project 保留诊断证据但不继续执行；用全新 Project 开展同一登录防刷自然语言测试，至少五轮对话，观察 Workflow 是否先固定 API 契约、再驱动前后端、联调和验收直到交付。运行中按约五分钟间隔检查状态，不实时轮询。

定向回归：`tests/test_agent_recovery_and_fencing.py` 9 passed；`tests/test_p0_runtime_regressions.py tests/test_p0_assignment_regressions.py tests/test_workflow_transition_authority.py` 60 passed。测试均使用隔离临时数据库。

补充环境核验：系统 Python 3.14 跑全量测试时为 547 passed、1 failed、1 error，两个异常均为 Python 3.14 对未关闭 SQLite 连接发出的 `PytestUnraisableExceptionWarning`；用服务实际采用的项目 venv Python 3.12 单独重跑这两项，2 passed。此处没有修改测试或产品代码来掩盖环境告警。

最终使用项目 venv Python 3.12 完整运行 `venv/Scripts/python.exe -m pytest tests -q`：**549 passed**。

新黑盒测试 Project：`prj_42ab1012ccf343689ca798a3ceec6f0e`，工作区 `data/projects/login-blackbox-20261004-replay-fix`。2026-10-04 新服务加载修复后健康检查为 `ok`；第一轮自然语言请求已被接收，Job `cjob_8d4de50046cb402c92f76555beb48526`。后续按五分钟间隔观察，记录会话和 Workflow 证据。

前四轮自然语言：① Vue/Java 登录防刷演示需求；② 补充简单范围、无缓存，Agent 自行建议 Spring Boot/Vue 3/H2/JWT 与失败次数限制；③ 询问接口先对齐和联调，Agent 提出先写协议；④ 选择先看协议，并要求工作流承担开发及联调验证。前三轮 Job 均 succeeded、尚无 Task；第四轮 Job `cjob_0fa24812309241309d3327e11a0fb302` 等待约定间隔后检查。

第四轮 Agent 写出 `docs/API-PROTOCOL.md`，但 Web 回复只有“项目文件已写入”；协议额外包含 `/me`、logout、备用账号等，已在第五轮明确收窄需求并授权开始。第五轮 Job `cjob_da25946d674a4146817c2def4c3088c9` succeeded；Conversation Agent 通过内部流程创建 Workflow Revision `wfrev_0ac32e3aad5c4ef8a7883aae312c524e` 和 Task `tsk_bad2634c66cd46ef91271b83985f114c`，Task 进入 `domain_design`。Workflow 取自 `software.ddd_delivery` 模板，包含需求、设计、后端、前端、集成、系统测试、交付和最终验收列。此时五轮对话与工具创建条件已满足；完整交付和编译条件仍待运行验证。

后续五分钟观察：`requirements`、`domain_design`、`backend_development` 均 succeeded，Task 自动进入 `frontend_development`。后端 Agent 曾用 `mvn spring-boot:run` 和 `start /b java -jar` 启动服务而触发命令超时，但没有造成 Task 运行时阻断；它随后继续执行并提交后端列。后端测试和构建的命令收据中有成功结果。整个过程中未手工改动 Project 状态或执行其测试脚本。

最终黑盒结果：`frontend_development`、`integration_review`、`system_test`、`delivery`、`accept` 均经 Workflow 推进到 succeeded，Task `tsk_bad2634c66cd46ef91271b83985f114c` 为 `done`。期间 `system_test` 因先在 Project 根目录执行 `npm.cmd run build` 失败，虽然随后正确指定 `frontend` 并构建成功，严格执行证据仍拒绝其 `tests_passed`；Agent 重复相同完成请求触发 `column_completion_protocol_stalled`。Conversation Agent 随后自动调用 `task.retry`，第二次尝试通过，并推进到终点。没有再出现 `ExecutionReplayUncertain`。独立复核后端 `mvn test -q -f backend/pom.xml` 退出 0，前端 `npm.cmd run build --prefix frontend` 退出 0。

**验收证据缺口（另案，不在本次收据修复范围）**：冻结 Task Plan 的一条标准是 `scripts/verify-api.sh` 实际执行并得到 6/6 PASS；生成的 `docs/test-report.md` 明确承认 HTTP 脚本未能执行，改用 MockMvc 覆盖；`FINAL_ACCEPTANCE.md` 却把“脚本存在且静态检查”写成该标准已通过。故本次可以确认 Workflow 到达终态、前后端可编译、MockMvc 核心场景通过，但**不能确认完整满足 Workflow 自己冻结的脚本执行标准**。这暴露了验收列对自然语言验收标准的证据核对不足，不应把 `done` 当作该脚本已通过的证明。本次没有通过手工修改终态或伪造脚本运行来掩盖该差异。
