# Vue/Java 登录与防刷 Demo 黑盒回归

日期：2026-09-28。测试目标：重启当前 8000 端口的 DevWerk，在**新 Project** 中以普通用户自然对话至少 5 轮，确认需求后授权开发，观察 Conversation Agent 是否创建一个贯穿各 Column 的 Task，并由 Workflow Workers 交付前后端登录与防刷 Demo。执行过程、证据 ID、缺陷、最小修复和再测结果逐步记录在本文。

## 用户目标与验收边界

- 前端 Vue，后端 Java；仅登录与防刷，不做注册、密码找回、管理员后台或其他业务。
- 可使用持久化数据库；**不使用缓存服务或内存缓存作为依赖**。简单本机 Demo 即可。
- 至少 5 轮讨论只确认需求，不提前建立开发 Task；明确开始后才创建 Workflow/Task 并执行。
- 验收检查前后端真实启动、成功/失败登录、连续失败触发限流、限流后恢复、页面反馈、测试报告和交付说明。防刷边界由讨论确认，不替用户增加产品功能。
- 经 Web 同源 `/v1/projects`、`/v1/projects/{id}/conversation` 等 API 驱动；不调用内部计划或 Task API 替 Agent 完成工作。每轮等 Job 结束后再发送下一轮，审计实体、日志、DB、产物和模型调用。

## 执行规则

先建立可观察的黑盒记录。遇到 P0 阻断，先把数据库回执、日志、根因和具体最小修复方案写入本文，再改代码及必要测试；每处代码改动记录文件和验证结果。保持 Conversation Agent、单 Column 单 Worker、Mailbox 通知的既有架构，不重构。不得将错误工具回执当作交付；没有 Task 时不评价 Worker 实现。保留测试 Project 和历史记录，便于复查。

## 启动记录

- 原服务：`http://127.0.0.1:8000/v1/health` 返回 DevWerk `ok`、Supervisor 与 Gateway `running`；监听进程 PID 20848 已停止。
- 仓库 `startup.bat` 在沙箱身份下尝试初始化既有 `data/devwerk.db` 时收到 SQLite `attempt to write a readonly database`，未启动成功；独立写锁检查正常。随后使用获准的非沙箱后台启动，仍使用既有 DB。新服务健康，监听 PID 8020；stdout/stderr 为 `data/logs/blackbox-login-20260928-172932.*.log`。
- 仓库内既有未提交代码与文档是上次修复，`devwerk.zip` 和前次用户黑盒记录保持原状。本轮开始前没有清理或覆盖它们。

## 对话与实体记录

Project `prj_68f94e0f4ba64a1398e48bd61d88270d`，目录 `data/projects/blackbox-login-20260928-173106`。

| 轮次 | User 消息 | Job | 结果与关键确认 |
|---|---:|---|---|
| 1 | 579 | `cjob_88fb39c765cf4d2094fdea6bf79c8a63` | 成功；明确先讨论，Agent 询问登录/防刷/技术/验收边界。 |
| 2 | 581 | `cjob_15f19c34eb5842b8baf2dc38b64b6f5b` | 成功；Vue 3、Java 17、H2 文件库、预置账号、无注册/找回/缓存服务；建议 HttpSession。 |
| 3 | 583 | `cjob_0a48bb88b9d8403ca293574e8e5e1606` | 成功；确认 IP 与用户名分别计数、滚动 5 分钟内 3 次失败、锁 15 分钟、数据库存计数/锁。 |
| 4 | 585 | `cjob_a2641cdaee3945a0a1a573a81fe6a6f4` | 成功；确认跨用户名 IP 计数、跨 IP 用户名计数、成功清零、Spring Boot + Vue/Vite 双进程。 |
| 5 | 587 | `cjob_4d50777f7ac5418b9331fb4b645a6789` | 成功；确认 200/401/429、Vite proxy、不信任伪造转发头、BCrypt、欢迎页/登出、前后端测试。Agent 总结错误地把跨 IP 用户名计数写成互不影响，还把无 Router 前端写成独立欢迎页 URL。 |
| 6 | 589 | `cjob_4f8322e2a0fb4e278d3024b240cc0ec8` | 成功；用户纠正 T-08 与 JUnit/浏览器边界。Agent 修正 T-08，但 B-04 仍写“手动访问原欢迎页 URL”，说明自然语言总结尚有残余误抄；授权消息将再次明确无独立欢迎页 URL。 |

前 5 轮结束时只读查询 DB：5 个 Job 成功，Workflow、Workflow revision、Workflow Plan、Task Plan、Task 均为 **0**，讨论未提前进入执行。第 6 轮仍是讨论。

第 7 轮消息 591 明确授权开发，Job `cjob_a8ffa97ba9b6457ea2846d15d5bf8617`、Agent Run `arun_d0f3064d6c0c4c4a85e720c45decfa85`。Agent 成功解析授权、应用 software Loop、写需求文件；`workflow.acceptance.configure` 前四次因缺少场景/报告、非法能力和义务缺口被拒，第五次创建 revision 2。之后已调用新的 `task.plan.contract` 与 `task.plan.validate`，但截至约 09:46 UTC（授权后约 8 分钟）仍无 Task Plan 或 Task；Agent Run 尚在执行。

revision 2 静态检查发现高风险验收命令：`system_test` 用 `echo Integration smoke test deferred...`，`delivery` 用 `findstr ... README.md`，`accept` 用 `echo Browser acceptance ... performed by user manually`，却都声明了行为场景与 JSON 报告路径。这些命令本身不会运行行为断言，也不会创建新鲜的 `devwerk.acceptance-report.v1` 报告；Runtime 到 Column 验收时应拒绝，但目前发布与计划预检允许把它们冻结进 Workflow。待本轮结束后将按实际回执设计最小准入修复。

## 缺陷、修复与回归

### P0 候选：行为验收接受纯占位命令

**证据**：授权轮次创建的 revision 2 在 `system_test` 使用纯 `echo`，在 `delivery` 用 `findstr` 搜索 README，在 `accept` 再次使用纯 `echo`，却都宣称需要 `scenario_ids` 和结构化 JSON `report_path`。`task.plan.validate` 的主要诊断是终验场景覆盖缺口，没有提前识别这些命令不可能在该调用中运行行为断言及生成新鲜报告。`acceptance_execution.run_acceptance_check` 在真实 Column 执行时会因报告未新鲜写入而拒绝；因此即使 Task 创建，也会在后续 Column 阻断。这是准入放过显然不可能成功的验收配置，并非 Java/Vue 交付物本身的问题。当前 Run 仍在运行，最终影响待继续观察。

**最小修复方案，待当前 Job 结束后实施**：仅扩展 `app/v1/services/task_plan_compiler.py::known_nonbehavior_command` 的确定性排除表，将纯 `echo` 与纯 `findstr`（包括 `cmd /c` 包装和末尾 `2>&1` 重定向）认定为 `NonBehaviorCheck`；有 `&&`、管道等实际复合命令时不基于关键词臆测脚本语义。继续由既有 `workflow.acceptance.configure`、`task.plan.validate`/`task.plan.save` 准入路径调用该函数，保持单 Column Worker 与报告运行机制不变。在 `tests/test_context_acceptance_regression.py` 增加直接函数/配置回归：拒绝本次三个占位命令，允许实际测试 runner。该规则不声称能证明任意命令做了真实浏览器验收；该部分仍必须由 Runtime 的新鲜报告和黑盒人工检查确认。

**授权轮次最终结果补充**：Run `arun_d0f3064d6c0c4c4a85e720c45decfa85` 在第 71 次工具调用成功回复。形成 Workflow revision 3、Task Plan `tplan_bf72422d1e484d5d8da460ede74334c5`、唯一 Task `tsk_421af58780704981a483260fe648c141`，Task 已进入 `requirements` Column。最终冻结的 revision 3 比 revision 2 更危险：`backend_development`、`frontend_development`、`system_test`、`accept` 都改为纯 `echo ... passed`，`delivery` 改为 `type README.md`，却全部声称有行为场景和新鲜 JSON 报告。这些命令显然不能实现声明的验收。故最小排除表还须覆盖纯 `type`，且测试覆盖五列配置。该 Task 已引用不可变 revision 3；代码修复后不得原地改写，需新 Workflow revision 与 Task successor 才能得到修正验收。

授权 Job 持续 13 分 17 秒；只读按 Job 时间窗汇总 `llm_usage`：Conversation Agent 69 次模型请求，输入 230,581、输出 40,064，合计 270,645 token（各请求累计，不是单次上下文）；最高单次输入 29,576。大量调用用于多次配置验收、反复读取结果及补正 Task Plan。该轮最终创建 Task，因此把效率问题记录为后续优化项，不能等同于上下文溢出或本次 P0 阻断。

**实施记录**：修改 `app/v1/services/task_plan_compiler.py::known_nonbehavior_command`，仅把可确定无法执行行为断言的单独 `echo`、`type`、`findstr`（含 `cmd /c` 包装；允许真实复合 runner）判为 `NonBehaviorCheck`。修改 `app/v1/capabilities.py::_workflow_publish`，让直接发布也阻止此类命令；`workflow.acceptance.configure` 和 `task.plan.validate` 仍用既有 `acceptance_diagnostics`。扩展 `tests/test_context_acceptance_regression.py`，覆盖本次命令和两条发布路径。聚焦测试 **12 passed**，完整 `pytest tests -q` **449 passed**（668.60 秒）。新诊断只读套用该 Project 的 revision 3，后端、前端、系统测试、交付、最终验收共 **5 列**均报预期 `NonBehaviorCheck`。修改不触及已运行服务内存与已冻结的 Task revision 3；目前 Task 已正常完成 `requirements`、`domain_design`，正在 `backend_development`。

后端首次 Maven 测试 30 个用例均在 Spring Context 启动时报错；从真实 603 KB 测试输出定位为 H2 不接受初始化 SQL 的 `ON DUPLICATE KEY UPDATE`。这是 Demo 交付代码错误，Worker 随后持续修改 Java/SQL 文件。它与上述 DevWerk Workflow 准入 P0 分开记录。

后续 Maven 测试已能执行 30 个用例，但持续有 4 个断言失败：T-04 第三次失败实际 429、测试期待 101；T-08 用同一用户名与 IP 连续失败，却期待“仅 IP 锁、用户名不锁”，与消息 589 已纠正的跨 IP 用户名计数不符；T-09 成功登录预期实际 429；集成 smoke 同样在第三次失败预期 401、实际 429。Worker 多次修改测试并重跑，仍未收敛。`docs/requirements-traceability.md` 正确记载“用户名跨 IP 汇总”，但测试代码仍有相反断言，`docs/test-plan.md` 还在失败期间把 T-08 等标成 ✅。这些属于当前 Demo 的需求追踪/测试质量缺陷；真实 Maven 回执显示失败，DevWerk 尚未把 Task 判为通过。应在安全返修边界反馈，不直接改用户项目文件掩盖黑盒测试。

**Worker 预算边界**：后端 Agent Run `arun_d9d1c1e52fc54881b3e4109671cc6d2c` 于 10:55 UTC 因 `ExecutionBudgetExceeded: Agent wall-clock budget exhausted` 失败，Task 转为 `recovering / backend_development / paused`，`failure_code=execution_budget_exceeded`。结束前已超过 115 次工具调用；按 Task 汇总约 95 次模型请求、80 万累计 token，最高单次输入约 41.5k，故此处不是上下文窗口溢出。运行仍未完成后端测试，且冻结的 revision 3 含虚假验收命令，不能直接在原 revision 重试。确认全局没有运行中的 Agent Run、Conversation Job 或 Task 后，停止原 8000 进程并后台重启；健康检查再次返回 `ok`，已载入准入补丁，历史 Task/证据保持不变。后续必须通过普通 Conversation Agent 返修，先把旧 Task 明确结案，再发布真实验收配置及 successor；不能原地改冻结 revision。

### P0 候选：源码搜索被编译产物的二进制文件整体中断

**证据**：恢复对话 Job `cjob_55f2681fb943422e908785358fd7b8e0` 通过普通 Conversation API 提交后，Agent 连续调用 `project.files.search` 搜索旧验收占位命令，三次均返回 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xca in position 0`。此时项目包含 Maven `target` 编译产物。`app/v1/capabilities.py::_files_search` 遍历默认 `**/*` 时直接 `ctx.files.read_text(path)`，单个二进制文件解码失败会让整次搜索失败；同文件的 `SystemFiles.search_text` 已显式跳过 `UnicodeDecodeError`。这是正常软件工作流中的工具失效，会妨碍返修定位，不是模型参数错误。

**最小修复方案**：仅在 `_files_search` 对单个文件的 UTF-8 解码失败时跳过该文件，继续搜索其他文本文件；保留显式 `project.files.read` 的严格解码错误，不吞其他文件/权限错误。添加一个含排序靠前二进制文件和靠后文本文件的回归测试，验证仍能返回文本匹配。修复后重启到安全边界再通过真实 Conversation Agent 复测；正在执行的 Job 不热切换代码。

**实施与单测**：`app/v1/capabilities.py::_files_search` 只捕获单文件 `UnicodeDecodeError` 并继续迭代；`tests/test_context_acceptance_regression.py` 新增编译 `.class` 文件排在匹配文本文件之前的回归。该测试文件 **40 passed**。当前恢复 Job 已在运行，内存中仍是旧搜索实现；它随后通过缩小 glob 成功搜索，不影响此缺陷的复现。待 Job 结束后重启复测，不打断活动 Job。

### P0：非法正则使整个 Conversation Job 失败

**证据**：同一恢复 Job 共执行 84 次模型请求、223 次已记录工具调用后，因 `project.files.search` 传入非法正则 `*`，最终状态 `failed`，异常是 `re.error: nothing to repeat at position 0`；Task 未改动，仍为 `recovering/paused`。stderr 的调用栈从 `_files_search -> re.compile` 进入 `CapabilityRegistry._dispatch` 的通用 `except Exception` 后重新抛出，最终结束 `AgentExecutionRunner` 与 Conversation Job。单次无副作用的搜索参数错误不应杀掉整轮 Agent；当前恢复因此被完全阻断。

**最小修复方案**：在 `_files_search` 的 `re.compile` 边界将 `re.error` 转成明确的 `ValueError`，让 Registry 既有参数错误分支返回 `ToolResult(ok=False)`，保留 Job 后续修正机会。相同的 `SystemFiles.search_text` 输入边界也应采用一致转换，避免只修 Project 后在系统文件搜索再次触发。新增 Registry 级回归：非法正则返回失败工具回执，不向 Agent Runner 抛异常；合法模式仍匹配。此修复不改变正则语义，也不默默把 `*` 当字面量。

**实施与单测**：`app/v1/capabilities.py::_files_search` 与 `app/v1/files.py::SystemFiles.search_text` 都仅把 `re.error` 转成 `ValueError`。回归验证 Project Registry 返回 `ToolResult(ok=False)`，SystemFiles 明确抛 `ValueError`；目标测试文件 **41 passed**。先前 Job 已失败，修改不改变其历史。下一步待全局空闲后重启，改用更小的逐步对话：先结案旧 Task，再配置验收，再建立 successor，避免一次对话重复读取大批历史。

**加载后复测**：全局无运行中 Job/Run/Task 时重启 8000 服务，健康检查 `ok`。普通 Conversation 消息 599 / Job `cjob_bb73f777c672491b83aad8d5edea3b18` 只请求结案旧 Task；`project.files.search` 在存在 Maven 产物的目录中成功返回，Job 最终 `succeeded`，`task.fail` 工具成功，旧 Task `tsk_421af58780704981a483260fe648c141` 已是 `failed` 终态且保留原因与冻结 revision。此次对话没有创建新 Task，符合逐步返修边界。后续再配置真实验收及 successor。

### P0：明确授权只更新 Workflow，却被解释成纯讨论

**证据**：消息 603 明确要求“用 workflow.acceptance.configure 发布一个新 revision”，“不创建或启动 Task”；Job `cjob_02dc5b34a7cc49ef987683ee03ddad6a` 的 `requested_mode=auto,start_task=1`，但 `turn_contract_json` 被模型解析为 `act=discuss,phase=discussion,execution_grant_id=null`，工具集中因此没有 `workflow.acceptance.configure`。Agent 最终回复“工具不可用，无法发布”，Revision 仍为 3。这里“不创建 Task”限制的是本轮副作用范围，并不是拒绝更新 Workflow 的授权。`conversation_intent.TURN_INSTRUCTION` 只强调“实施”和 Task 执行，对“已授权 Workflow/TaskPlan 规划变更也是 execution”没有说明，且“纯讨论不可发布”与该句并排，容易让模型误归类。本次因此无法通过 Conversation Agent 修复已冻结的无效验收。

**最小修复方案**：仅在 `app/v1/conversation_intent.py::TURN_INSTRUCTION` 澄清：用户明确要求发布或修改 Workflow、配置验收、保存 TaskPlan、控制 Task 等有状态操作时，应解析为 `act=execute,execution_request=continue`（若已有 scope）并保留用户指定的本轮范围；“本轮不要创建 Task”不能把其它已授权 mutation 变成 discussion。继续要求用户真实授权，不增加关键字式自动分类和新权限层。使用当前消息的等价自然语言在真实服务重新发起一轮，验证 `turn_contract`、工具可用性与 revision 是否更新；工具仍需受现有 `acceptance_diagnostics` 准入。若模型仍误判，再针对 `conversation.turn.resolve` 反馈提供更明确的合同解释，不绕过授权闸门。

**实施与复测中**：`TURN_INSTRUCTION` 增加上述明确约束；意图合同回归 **31 passed**。服务空闲后重启，同 Project 消息 605 / Job `cjob_8586b1fd0aea45a786c52fb05e17483a` 现在被解析为 `act=execute,phase=execution`，获得原 Requirement 的执行授权；不再误报规划工具不可用。是否能完成发布仍需等 Job 结束核对。

### P0 候选：Workflow 检查结果投影丢掉规划必需字段

**证据**：消息 605 虽获得规划权限，Agent 仍对 `workflow.inspect` 大量调用 `agent.result.read`。当前 revision 3 的 `workflow.inspect` 原回执约 18.6 KB；`context_manager.planning_projection` 将所有 Column 的 `transitions`、`writable_paths`、`acceptance_checks` 一起纳入，投影约 7.2 KB，超过默认 4 KB 工具结果预算。`project_result` 最终退化成仅 269 字节的通用引用，连 `definition_hash`、五列检查和 obligations 都丢失。Agent 不得不以每页约 1–2 KB 反复读取原回执；先前返修 Job 已因多次读取及最终非法搜索耗尽 223 个工具调用，没有一次规划 mutation。本次更新验收只需 revision/hash、列键与检查、obligations，这些紧凑字段约 2.9 KB，可以直接提供。

**最小修复方案**：只调整 `app/v1/context_manager.py::planning_projection` 的 `workflow.inspect` 分支：默认投影保留 revision/hash/plan ID、列键及各列 acceptance_checks、acceptance_obligations，省略与本次验收配置无关的完整 transition/instruction/writable path；仍保留 `agent.result.read` 取得完整不可变回执的入口，不改变 `workflow.inspect` 原始输出或 Workflow 数据结构。对 4 KB 默认预算增加回归：多列软件 Workflow 的投影必须保留上述配置字段并小于预算；完整细节仍由原始回执持久化。等待当前 Job 安全结束后再重启，不热切换。

**实施与单测**：仅缩小 `workflow.inspect` 的工具结果投影，原始回执、实体与 `agent.result.read` 不变。新增大图回归；目标测试文件 **42 passed**。用本 Project 的真实 `workflow.inspect` 回执只读验证：原始 18,586 字节，修正后投影 3,744 字节，包含 definition_hash、8 个列键与检查以及 5 个 obligations，落入默认 4 KB 预算。当前运行 Job 仍使用旧内存，待其结束后重启评估实际调用数。

**当前 Job 最终结果**：Job `cjob_8586b1fd0aea45a786c52fb05e17483a` 在第 100 个记录工具调用后因 `ExecutionBudgetExceeded: Agent iteration/tool-call ceiling reached` 失败，revision 仍为 3。接近结束时它第一次调用 `workflow.acceptance.configure`，但提交的 delivery 两项行为检查 `scenario_ids=[]`，未通过 `WorkflowDefinition` 验证；accept 仍是纯 echo，占位也会被新增 `NonBehaviorCheck` 拒绝。建议中的宽泛 `taskkill /IM java.exe` 或 `node.exe` 没有运行，不能用于本 Project 验收。该回合证实旧 269 字节投影造成反复取回规划资料并挤占执行预算；新的紧凑投影需重启后真实复测，不能只靠单测宣称解决。

**加载后复测**：空闲重启，消息 607 / Job `cjob_e4a637833de3426f9e5f6b6ea2532edc` 经正常 Conversation 路径完成 `workflow.acceptance.configure`，仅 15 次工具调用，新 revision `wfrev_f16835435b184126be5ff93620d5cc45`（revision 4）。五列分别冻结 `python backend/scripts/verify_backend.py`、`python frontend/scripts/verify_frontend.py`、`python backend/scripts/verify_integration.py`、`python backend/scripts/verify_delivery.py`、`python frontend/scripts/verify_browser.py`；均保持原场景集与独立 JSON report_path，`launch_validation=deferred`。本地只读 `acceptance_diagnostics` 对 revision 4 返回空列表。入口脚本尚不存在，后继 Task 必须实际创建并运行；静态通过不等于行为验收完成。

### P0 候选：Task Plan 证据候选缺少所属授权配对

**证据**：消息 609 / Job `cjob_e41c2c398b414e7a92c69913f50d1dde` 正在创建 successor。`task.plan.contract` 正确列出 4 个 active scope grant 和唯一仍匹配文件的 `requirements_file` 写入回执，但把它们分为两张无关联列表；原文件回执实际属于最早的 `grant_461b...`。Agent 先后把它配上当前 `grant_0225...`、返修 `grant_9370...`，`task.plan.validate` 均报 `EntryEvidenceFileScopeMismatch`；随后清空证据又报 `EntryEvidenceMissing`。旧 Task Plan 原有正确配对可读，然而新的合同视图未把决定性关系交给模型，多轮验证被浪费。这个场景是修复终态 Task 的正常路径，不是安全越权。

**最小修复方案**：在 `app/v1/capabilities.py::_task_plan_contract` 列出真实 `requirements_file` 候选时，增加该回执所属 Job 的 `execution_grant_id`，并在 `next_action` 明确 `confirmed_scope` 必须使用同一 grant；不改变 `task.plan.validate`/save 的严格性，不接受混配证据。更新 `tests/test_task_entry_admission.py::test_plan_contract_exposes_only_real_entry_evidence` 校验配对；再增加同一 Requirement 连续多个 active grant 的测试，确保较新的无文件 grant 不掩盖旧的有效候选。等待当前 Job 结束后重启，用正常 Conversation 路径继续 successor。

**实施与当前运行**：上述合同候选新增 `execution_grant_id` 与配对说明；对多 grant 的目标测试 **1 passed**。当前 Job 使用旧内存时通过在当前 grant 下重写真实 `docs/requirements.md` 形成匹配证据，两次保存 Task Plan，最终由 `task.successor` 成功创建唯一后继 Task `tsk_47a7ea2395cd4208834ef7657765458a`，引用 revision 4 和旧 Task，Job `succeeded`。新补丁尚未加载，不能把本次成功归因于它；它的作用是下次能直接选择已有匹配证据，减少无效验证和不必要的重写。

### 后继 Task 后端验收现场

后继 Task 依次通过 requirements、domain_design，进入 backend_development。Worker 将错误的 T-08 测试断言修正后，真实 `AuthServiceTest` 10 项通过，并实现 `backend/scripts/verify_backend.py`。普通命令运行该脚本退出码为 0，报告含 S-01~S-10；但正式 `column.complete` 触发的 Runtime 验收三次拒绝：报告中 `test_source.path` 是项目**绝对路径**，`ProjectFiles.resolve` 正确拒绝 `path escapes project base_dir`。Worker 没有修正报告路径，却重复运行脚本并申报完成，最终 `CompletionProtocolStalled` 将 Task 置于 `recovering/paused`，`failure_code=column_completion_protocol_stalled`。此时没有虚假交付；Runtime 的新鲜报告与源文件约束确实拦截。Worker 还错误地用 `task.feedback.record` 把“后端测试通过”记录成开放反馈；后续应通过真正的验收回执解决它，不能把该文字当通过。

**返修步骤（不修改框架）**：在安全边界由 Conversation Agent 使用 `agent.worker.send` 向该后端 Worker 发送精确问题：`test_source.path` 必须为项目相对路径 `backend/src/test/java/.../AuthServiceTest.java`，修改脚本并重跑正式验收；然后用 `task.retry` 从 backend_development 恢复原 Task/原 revision。若 Worker 重试仍无法修复，先记录工具回执及根因，再评估是否需要针对完成失败反馈加入更明确的提示。不能由测试者直接改产品项目文件掩盖黑盒结果。

### P0：现有 Task 返修请求被误当作新 Requirement

**证据**：消息 615 明确指向同一个非终态 Task 与 backend_development，并要求 `agent.worker.send` + `task.retry`。Conversation Agent 却先调用 `conversation.turn.resolve(act=execute,execution_request=begin,focus=current,constraint_change=release_hold)`，scope_summary 改为“修复 Task...”，被 `RequirementConflict` 拒绝：当前 scope_key 已关联原 Requirement。随后它错误地再次 resolve 为 `status`，把本轮边界固定到 `phase=discussion`，Job `failed`，Worker 消息和 retry 都未发送。现有 Task 修复不应创建新的 Requirement，也不需要 `project.scope.revise`；这是此前规划误分类的另一种失败形式。

**最小修复方案**：仅补充 `app/v1/conversation_intent.py::TURN_INSTRUCTION`：用户指向同一 Requirement/Task 要求重试、恢复、修复时应 `act=execute,execution_request=continue,focus=current`，沿用现有 Requirement/授权范围；不把技术修复说明作为新 Requirement objective。如果一次 execute/begin 返回 `RequirementConflict`，应在同一未决边界更正为 continue 并保留用户行动意图，不应改成 status/discuss。保持既有 `conversation.turn.resolve` 权限校验、Requirement 身份和 Task 冻结规则；在下一轮正常 Conversation 复测是否成功发送 Worker 消息与重试。本轮已经固定讨论，不能事后篡改。

**实施与复测**：意图说明已补充，相关回归 **31 passed**；空闲重启后，同 Project 消息 617 / Job `cjob_f25e19bf93874d768dc0a015295c1696` 解析为 `act=execute,phase=execution`，Agent 通过 `agent.worker.send` 向 backend Worker 的现有 Assignment 投递精确修复信息，然后 `task.retry` 在原 Task/backend_development 恢复；消息状态后来成为 `acknowledged`，由新 Worker Run 消费。此轮没有创建 Requirement/Workflow/Task。

### P0：正式验收的具体失败原因未进入 Column 完成拒绝反馈

**证据**：后端 Worker 在重试中修正绝对路径，正式验收随后改报 `AcceptanceEvidenceInvalid: Scenario assertion mismatch: S-07`：报告的 S-07~S-10 写成 `expected=具体行为名, actual=passed`，字符串不相等。但 `column.complete` 只回 `Required acceptance check has not passed: backend_unit_tests`，未包含刚刚执行的 Runtime 检查错误；原始错误留在单独的内部 `project.command.run` 工具调用/验收记录。Worker 看普通脚本退出码 0 后继续重复完成、用 `task.feedback.record` 写“已通过”文字，直到再次 `CompletionProtocolStalled` 暂停。错误报告被正确拒绝，但负责修复的 Worker 未直接获得精确原因，完成闭环卡住。`CompletionAdmissionService.evaluate` 已拿到同一 `ledger` 的最后一次 `acceptance_check` 条目，其中 `facts.result.error` 可读。

**最小修复方案**：仅在 `app/v1/services/completion_admission.py::CompletionAdmissionService.evaluate` 构造 `acceptance_check_failed` 拒绝时附上该 check 最近一次回执的 `error.type` 和限长 `error.message`（例如 S-07 mismatch）；不改变验收规则、脚本执行次数或报告合同。`tests/test_completion_protocol.py` 加入合成 ledger 回归：明确错误进入 `column.complete` 拒绝，缺失检查仍返回一般说明，极长命令输出被限长，避免把整份日志送回模型/Web。当前 Task 处于暂停边界，代码测试后重启，通过原 Task Worker 返修继续验证。

**实施与单测**：`CompletionAdmissionService.evaluate` 在拒绝时附上最后一份匹配检查的错误类型与至多 300 字符消息；没有验收回执时保持原提示。对应协议测试 **12 passed**，验证 S-07 具体原因可见且长错误被限制。当前服务内存尚未加载，后续必须在 Worker 继续时观察正式验收反馈；此补丁不把失败变成通过。

### P0：验收失败须由运行时确定性带入每次模型交互

**实测补充**：后端重试的正式验收曾报 `AcceptanceEvidenceInvalid: Scenario assertion mismatch: S-07`。加载上一补丁后，当次 `column.complete` 确实返回该具体错误，Worker 随后修复并通过；但这个工具回执属于可压缩的历史工具批次，下一次 Agent Run 也只把旧执行记录投影为有字节上限的 evidence 目录。当前 `AgentRunPreparer.prepare` 的 `current_assignment`、`ContextManager.prepare` 的每次 provider 输入均不直接读取持久化的 `v1_acceptance_results`。因此跨重试或上下文压缩时，是否继续看到未解决错误仍取决于模型自行检索/摘要。

**修复边界**：在 `TaskFeedbackRepository` 增加只读查询，针对当前 Task、Workflow revision 与 ColumnRun，按 check_key 读取最新的正式验收回执。只呈现最新状态仍失败的检查，包含 check_key、有限长度的错误类型/信息和原始验收记录 ID；同一 check 后续正式通过即移除旧失败。不得把普通探索命令失败伪装成正式验收，不泄露完整命令日志。`AgentRunPreparer.prepare` 在新的 `current_assignment.input` 写入这个字段；`ContextManager.prepare` 在**每次**模型请求前从数据库刷新该字段，即使当次 `column.complete` 工具批次已被压缩，也由运行时提供最新状态。`current_assignment` 是当前权威合同框架，不属于可删除的工具批次；既有上下文投影只缩减 execution_evidence，不应丢弃此字段。若没有该字段的旧式测试消息，交互层仍可追加一个有限的运行时失败状态框架。不得把内部调试信息输出到 Web 对话。

**回归设计**：合成失败验收记录后构建 Worker Run，断言初始 current_assignment 和 ContextManager 普通/强制压缩的实际 provider 消息都包含明确错误；同一检查随后正式通过后下一次输入必须移除旧错误；第二次失败则以最新错误取代旧错误。验证字段长度有界、只读取当前 Task/ColumnRun 的记录。此测试和现有 completion 协议测试通过后，在服务空闲边界重启，再用真实 Worker 重试观察持久记录与 provider 帧，不以模型回复文字作为验收依据。

**实施及验证**：`TaskFeedbackRepository.latest_failed_checks` 只查询本 Task、冻结 revision、当前 ColumnRun 的每个 check 最新记录，最多八项、错误信息最多 300 字符；成功记录覆盖旧失败。`AgentRunPreparer` 在新 Run 写入 `current_assignment.input.unresolved_acceptance_failures`；`ContextManager.prepare` 在每次 provider 请求前再次从 DB 刷新该字段，普通请求和强制压缩均适用。合成三次记录（失败→通过→新失败）的交互层测试通过，验证当前框架总能带入最新失败、不会保留已解决失败。全套测试 **454 passed**；最终小修后定向重跑 **3 passed**。真实服务加载与重试仍在下步验证。

### 黑盒续测发现：system_test 进程生命周期误判

后继 Task `tsk_47a7ea2395cd4208834ef7657765458a` 已通过 requirements、domain_design、backend_development、frontend_development、integration_review（含返修再审），在 `system_test` 提交 `execution_failed` 后终止。`verify_integration.py` 的一次真实运行因 8080 后端不在运行而退出 1；Worker 用 `project.command.run` 内的 PowerShell `Start-Process java -jar ...` 尝试后台启动，随后的独立命令确认该进程已消失。`process_runner.py`/`windows_job.py` 的命令执行本就有进程树/Job 生命周期隔离，独立命令之间保留后台子进程不是受支持的合同。现阶段优先在产品测试脚本的一次 `project.command.run` 内启动后端、等待就绪、运行 HTTP 断言、finally 清理，由 system_test Worker 交付并运行；不为这个用例放松框架的进程隔离。该 Task 已终态，但 `RecoveryManager.reopen_task` 明确允许失败 Task 按用户授权从指定非终态 Column 重开，保留同一 Task、revision 与此前上下文；续测应由 Conversation Agent 使用 `task.reopen(target=system_test)`，不需要重复创建 successor，也不重复前后端列。

### P0：终态失败通知只有状态机通用错误，没有用户可用的失败原因

**证据**：终态自动 mailbox Job `cjob_709f53ce11b943869f682432debf9a37` 的 `result.reply` 实际为“登录防刷 Demo 全流程交付（返修）：失败；Column transitioned to failed”。但 `system_test` 的持久化 `v1_column_runs.output_json` 明确包含进程生命周期限制、后端 JAR 构建/启动和集成脚本状态。`ConversationService._reduce_notification` 只使用 `v1_tasks.error`，故 UI 的确定性通知丢掉实际原因；下一次用户对话的 `task_summaries` 也只含通用 error。不能把用户获知原因寄托于模型自主读详细 Run。

**最小修复方案**：在任务反馈存储层新增只读、有上限的 `failure_detail(project_id,task_id)`，仅对失败/恢复中的 Task 取最后 ColumnRun 的 column_key、run ID、`output_json` 中明确的原因字段与 Task error；原因文本限长，不把完整 output、命令 stdout/stderr 或内部诊断打印到 Web。`ConversationService._reduce_notification` 在 `task.failed` 通知内容中固定拼入“失败列 + Worker 报告原因”，缺失原因时退回既有 Task error；它仍只是通知，不指挥或代替任何 Worker。构建正常用户对话的 `tasks` 状态时也附该结构化摘要，保证 Conversation Agent 获得失败事实。`conversation_report.render_report` 对引用失败 Task 的 work_result 直接用同一摘要渲染，保证在状态答复中不依赖模型自由文案。回归覆盖：本次类似的通用 Task error + 具体 Column output、无具体 output 时的回退、不同 Project 不能跨读、通知文本不含完整日志/调试内容。

**实施及验证**：`TaskFeedbackRepository.failure_detail` 做 Project 校验、最新列读取与原因字段限长；`ConversationService` 把该结构化事实注入正常用户对话的任务状态，并在 mailbox `task.failed` 的无模型通知中直接拼出失败列和 Worker 报告；`conversation_report.render_report` 的失败 Task 状态答复同样由 Runtime 渲染。成功列普通说明不会被误当失败原因，Web 内容不含命令 stdout。回归以通用 Task error + 具体 Column output 复现，通知、对话输入、work_result 均检查通过，跨 Project 读取被拒。全套测试 **454 passed**，最后小修后定向 **3 passed**。旧通知不追溯改写；下一次失败通知才采用此格式。

**实服务验证**：全局无 queued/running/waiting Job、AgentRun 或 Task 时重启 8000，健康检查 `ok`。新用户消息 633 / Job `cjob_ac76b3d3dba84a99a1194d3c9522ba7e` 的实际存储 provider 用户帧含失败 Task 的 `failure_detail`：`column_key=system_test`、对应 run ID，以及 Worker 保存的进程生命周期原因；说明正常 Conversation Agent 在模型调用前确定性拿到了具体失败事实。该轮 `conversation.turn.resolve` 为 execute；`task.reopen` 成功使原 Task 从 `system_test` 开始新 ColumnRun `run_5b57ce6c294b4aae98b894fb141eb034`，没有创建新 Task/Workflow/Requirement。随后 `agent.worker.send` 将返修指令排入该列 Worker，输入 `winput_745a6d56397d40c5b152158e3456fef2` 已被新 Worker Run `arun_2683dd73184c41f7b532b156f4cc0e00` acknowledged。Conversation Job 最终 succeeded（6 次模型迭代、7 次工具调用）。系统测试实际验收仍需观察；本条只证明交互注入与重开生效。

**续测更正与观察**：新 `system_test` Worker 首先尝试直接改写 `backend/scripts/verify_integration.py`，`project.files.write` 被冻结 `writable_paths=['docs/test-report.md']` 正确拒绝；测试者原消息要求它直接修改脚本，忽略了列边界，现已纠正：由 system_test 保存测试证据并以 `tests_failed` 路由到 integration_review，后者以 `backend_changes_requested` 路由 backend_development 修改脚本。更正消息 635 / Job `cjob_19272d2e53bf4c1fa1f932b1e1c7effc` 成功投递 Worker 输入 `winput_01b9d8be422a465895f1586307ee3a78`，已在同一 Worker Run acknowledged；但该 Conversation Agent 随后对运行中的 Task 两次重复调用 `task.retry`，均被 Runtime 以“active Attempt 不能重试”拒绝，最终 Job 进入 `ConversationProtocolStalled`。这是模型在明确“不要重开/重试”约束下仍随机选择了无效操作；没有产生额外状态变更。Worker 随后已成功登记由 `backend_development` 负责的 `integration_script_single_command_fix` 反馈，等待它完成合法路由。先观察既有 Worker/Workflow 是否能交付，不因这个无效对话操作改动权限或结构。

### P0：开放反馈覆盖 Column 显式失败转移，导致测试失败跳到 delivery

**证据**：重开后 `system_test` ColumnRun `run_5b57ce6c294b4aae98b894fb141eb034` 保存 `exit_code=1, tests_passed=0, tests_skipped=6`，并以合法 `tests_failed` outcome 完成，且登记了 `backend_development` 负责的脚本返修反馈。但 `TaskFeedbackRepository.settle` 不论当前 outcome，先取 Task 中按 rowid 最早的未解决反馈，再以图搜索路由到该反馈的 responsible_column。原有最早反馈是 `missing_readme_md`（delivery），图搜索从 system_test 可直达 delivery，于是 Task 实际进入 `delivery`，绕过冻结的 `tests_failed -> integration_review`。这是明确失败转移被全局旧反馈抢占，存在错误推进/额外执行风险；并非 Worker 没提供失败事实。

**最小修复方案**：只改 `app/v1/repositories/task_feedback_repository.py::settle`：先照旧更新各反馈的验证状态；若当前 Workflow transition 的 `allows_unresolved_failures=True`，保留该 transition 指定的 `next_column`，不让任何旧/新开放反馈覆盖显式返修边。非返修 outcome 仍使用既有反馈路由，终态 acceptance obligations 仍严格核验。新增一个含 system_test→integration_review 失败边、system_test→delivery 成功边、早于测试失败的 delivery 开放反馈的回归，确认 `tests_failed` 必须进入 integration_review；再运行现有跨列返修测试，确保不破坏正常反馈循环。当前线上 Task 已在 delivery，修复需等全局空闲后重启；不能对活动 Task 热切换。若后续 Task 因旧逻辑再次偏移，按既有 `task.reopen` 恢复正确列，不篡改旧 Run 历史。

**实施及回归**：`settle` 在完成反馈状态刷新后，对 `allows_unresolved_failures=True` 的当前显式返修边直接返回冻结目标；其它 outcome 仍按原反馈算法。新增早期 delivery 反馈 + `tests_failed` 的精确回归，确认下一列是 integration_review 且反馈仍开放；现有两次跨列返修回归也通过，目标测试 **2 passed**。全套回归正在运行，服务仍使用重启前的旧实现。

**旧服务实际偏移**：上述 system_test Run 完成后，`column.finished` 事件记录 `outcome=tests_failed,next=delivery`，Task 到 delivery，但因上一 Conversation Job 额外执行的 `task.pause`，Task 变为 `pending/paused`，并未开始错误的 delivery Run。旧历史保持不变。代码修复加载后应由正常用户对话使用 `task.retry(column_key=integration_review,clear_context=false)` 恢复，令该列按明确反馈转到 backend_development；不应无视失败直接 resume delivery。

**加载后恢复**：全局空闲后重启 8000，健康检查 `ok`。新用户消息 640 / Job `cjob_af4b48ffd9244c9892af0d484997515d` 明确只授权一次 `task.retry(column_key=integration_review,clear_context=false)`。Conversation Agent 最初把 `task.retry` 错填进只接受 pause/resume/cancel 的 `control_capability`，合同校验拒绝；随后正确解析 execute/continue，并且该唯一 `task.retry` 工具调用成功，Job succeeded。原 Task 进入 `integration_review`，新 Run `run_5553038da606495da0a7c5f930b5e576` 已运行，先前无效 pending delivery Run 留作历史，不曾执行。观察 review 是否按 `backend_changes_requested` 进入 backend_development，才能验证新路由代码在真实场景生效。

**真实路由结果**：`integration_review` 第一次因还缺浏览器脚本，显式提交 `frontend_changes_requested`，实际进入 frontend_development；前端列只重跑 Vitest，未创建浏览器脚本，随后再进 integration_review。第二次审查显式提交 `backend_changes_requested`；`column.finished` 事件记录 `next=backend_development`，Task 实际进入该列（sequence 15）。这一次 Task 仍有更早的 delivery 开放反馈；旧算法会优先寻找它，故真实结果直接验证新代码保留了冻结返修边。

### P0：仅要求 Worker 消息却产生未授权 Task pause

**证据**：更正消息 635 明确要求“只通知当前 Worker，不新建/重开 Task，不改 Workflow/需求”；其 turn contract 为 `act=execute`，用于授权 `agent.worker.send`。Agent 消息发送成功后，自行调用 `task.retry` 两次（运行中被拒）并调用一次 `task.pause`（成功）。结果该 Task 在 `system_test` 结束后成为 `pending/paused/supervision_action=observed_no_intervention`，停止后续流程。这不是 Worker 测试本身导致的暂停。`_task_pause` 当前只检查同 Project Task，没有验证本轮 turn contract 的 `act=control` 与 `control_capability=task.pause`、`control_task_id`。同一 Conversation 回合“可发送 Worker 消息”被错误地放大为“可暂停 Task”。

**最小修复方案**：仅在 `app/v1/capabilities.py::_task_pause` 的 Conversation 工具边界读取 `ctx.store.intents.job_for_run` 与已提交 turn contract，要求本轮 `act=control`、`control_capability='task.pause'`、`control_task_id` 与调用 Task 相同；不满足时返回明确拒绝，不能产生 pause_requested。保留 Runtime 内部直接 `store.pause_task` 的监督路径，不动其它 Task 控制工具，也不依据消息关键词做假确定性判断。补两例：普通 `execute/continue` 回合即使模型调用 task.pause 也被拒绝且 Task 保持 active；用户明确暂停某 Task 并通过 control contract 时可暂停。更广义的多操作授权语义后续单独审阅，本次只封闭已复现的越界动作。待当前全套回归结束再实施并重跑相关测试；服务安全空闲后加载。

**实施与回归**：`_task_pause` 已要求当前用户 Job 的 `targeted_control` 合同精确绑定同一 Task 和 `task.pause`；否则在调用 `store.pause_task` 之前拒绝。Runtime 内部 supervisor 的直接暂停路径不受影响。新测试验证未定向的 Conversation 上下文无法暂停，以及明确 control 合同下的 `task.pause` 正常成功；目标测试 **1 passed**。旧 Job 635 的暂停是已发生历史，不能由代码补丁追溯取消。

**定向暂停真实回归发现**：为了在 backend Attempt 结束后的安全边界加载下一补丁，测试消息 646 只要求一次 `task.pause`。Conversation Job `cjob_d3dda45edccb4d6a970cd79453d3b3c9` 却解析为 `act=execute,phase=execution,control_capability=task.pause,control_task_id=正确 Task`；现有 `TurnResolution.coherent` 只校验 act=control 时必须给目标，却未禁止 execute 携带控制字段。`conversation.turn.resolve` 因此错误地接受了这个内部矛盾合同，`_task_pause` 随后正确拒绝执行，Job 失败，Task 保持 running/active。用户明确控制请求无法执行，是合同校验与说明缺口，不应放宽 pause guard。

**最小修复方案**：在 `app/v1/conversation_intent.py::TurnResolution.coherent` 规定 `control_capability/control_task_id` 只能与 `act=control` 同时出现；`act=control` 必须两者均有，且 `execution_request=none`。补 `TURN_INSTRUCTION`：用户明确只暂停/恢复指定 Task 时使用 `act=control`、精确 capability/Task ID，不把暂停附在 execute/continue；普通“修复/重试”仍走既有 execute/continue。对话边界用例验证 execute+control 字段在 resolve 之前失败、合法 control 成功且 task.pause 工具执行。该变更只修合同自洽和提示，不自动从用户文本抽取关键词。当前 Job 已失败，需加载后新用户请求重新验证。

**实施与实测**：`TurnResolution.coherent` 已拒绝 `act!=control` 携带任一 Task 控制字段，提示中明确独立 pause/resume 的 control 形态；定向合同测试 **1 passed**。服务尚未重启加载该自洽校验，但新的明确控制消息 650 / Job `cjob_169d0103abee4298b18f90960b2b90dd` 在旧提示下仍正确提交 `act=control,phase=targeted_control,control_capability=task.pause,control_task_id=当前 Task`，随后只调用一次 `task.pause` 并成功；Task 成为 `running/pause_requested`，当前已经在 integration_review 列，待该列结束进入 paused 边界。此实测证明既有 targeted_control + pause guard 的合法路径可用；新校验旨在提前拒绝消息 646 那种自相矛盾的合同。

**第二轮全套回归说明**：路由改动后测试为 **454 passed、1 failed**。唯一失败 `test_agent_wall_clock_exits_even_when_provider_hangs` 在全套高负载时收到 `ExecutionOwnershipLost`，而断言期望 `ExecutionBudgetExceeded`；单独重跑 **1 passed**。该超短 0.1 秒计时测试未涉及反馈路由或 Task 控制补丁，判断为并发负载下的时序不稳定；未据此改动运行时超时语义。针对这次路由及 Task 控制影响面的多文件回归随后执行。

### P0：跨列返修反馈未确定性进入负责 Worker 的模型输入

**实测**：恢复至 integration_review 后，审查列以 `frontend_changes_requested` 进入 frontend_development，并记录/保留 `verify_browser.py` 缺失的开放反馈。但 frontend Worker Run `arun_28a89abba3184df3b86fd2462d590d9d` 只读现有前端文件、运行 `verify_frontend.py`，通过 29 个 Vitest，提交 `frontend_ready`；未创建 `frontend/scripts/verify_browser.py`，文件检查仍不存在。`task.feedback.list` 是可用工具，但 Worker 未调用。当前 `AgentRunPreparer` 的 `current_assignment.input` 仅含普通任务/上游上下文和旧 execution evidence，`ContextManager` 也不查询 `v1_task_feedback`。因此负责列能否看到需返修的具体失败完全依赖模型随机选择检索工具，造成假完成与循环。此前给正式验收失败注入的 `unresolved_acceptance_failures` 只覆盖当前 ColumnRun，不能替代跨列反馈。

**最小修复方案**：在 `TaskFeedbackRepository` 增加只读、有上限的 `pending_for_column(task_id,column_key)`，从持久化反馈中选 `state!='resolved'` 且 `responsible_column==column_key` 或验证 checks 指向该列的项目；优先负责列项，保留反馈 ID、状态、负责列、有限长度的问题描述及冻结 check 引用，不携带完整报告/日志。`AgentRunPreparer.prepare` 将其注入 Worker `current_assignment.input.unresolved_task_feedback`；`ContextManager.prepare` 与正式验收失败一样每次 provider 请求前刷新，反馈解决后立即移除旧项。该字段只给当前 Worker，不通过 mailbox 传话，不修改 Workflow 转移或多 Agent 生命周期。回归：有多个 Column 的反馈时，负责 Worker 在初始请求和强制压缩后都收到具体描述，不相关 Worker 看不到；反馈解决后从下次 provider 输入消失。目标测试与现有上下文/跨列返修测试通过后，等线上 Task 到空闲边界重启，再观察负责后端/前端 Worker 是否确实收到失败帧；不以模型自由回复代替证明。

**实施与定向测试**：`pending_for_column` 查询持久化未解决反馈，负责列优先，最多 8 项，每条描述最多 500 字符；`AgentRunPreparer` 写入当前 Assignment，`ContextManager` 每次 provider 请求刷新或删除过期字段。测试合成负责列与不相关列的反馈，验证初始/压缩后输入只含负责项，解决后立即消失；目标测试 **1 passed**。当前 backend Run 已在旧服务进程中运行，不能把其行为归因于新注入代码；等待安全边界加载后再做实服务确认。

**影响面回归**：上下文生命周期、checkpoint replay、跨列返修、对话意图合同合计 **55 passed**。线上 backend_development 已改写 `verify_integration.py`，实际命令正在发现/修复脚本运行问题；它启动于新反馈注入补丁加载前，故只验证 Workflow 路由及 Worker 现有行为。

**实服务确定性注入验证**：后端列完成、审查列再次把缺失浏览器脚本路由到 frontend_development；按明确用户控制请求暂停于安全边界，全局无活动 Job/Run/Task 时重启 8000，健康检查 `ok`。消息 654 / Job `cjob_e5b08c218e304db78f0f6471fda37099` 以 `act=control,phase=targeted_control` 正确执行一次 `task.resume`，Task 进入新 frontend Run `run_81856262faf74d30b70682da8fe3bec3`、Agent Run `arun_49de719cb7c74b4f8e1ed2c07b2942f0`。只读核对该 Run 实际持久化的 `current_assignment.input.unresolved_task_feedback`，其中有三条负责列为 frontend_development 的开放反馈，均指出 `frontend/scripts/verify_browser.py` 缺失及浏览器验收报告缺口；没有依赖 Worker 自己选用 `task.feedback.list`。这证明初始模型输入路径已在实服务生效；单测另验证每次 provider 请求刷新、压缩后保留及反馈解决后移除。Worker 是否完成产品返修仍需继续观察。

**后续行为**：该前端 Worker 实际创建了 `frontend/scripts/verify_browser.py` 并通过 `frontend_vitest`，进入 integration_review。审查列要求 `docs/browser-acceptance-report.json` 立即存在，又以 `frontend_changes_requested` 返回前端；后一个前端 Worker 虽在初始上下文中收到报告缺口反馈，但只重跑 `verify_frontend.py` 并申报 `frontend_ready`，未运行 `verify_browser.py`、未生成报告。这证实“确定性带入失败事实”不等于“模型确定性选择正确修复动作”。根据冻结 Workflow，`final_browser_acceptance` 属 `accept` 列的正式验收，浏览器报告应由该列执行脚本时生成；integration_review 可以检查脚本存在/语义，不应把下游验收报告尚未出现当作前端列缺陷反复循环。下一步通过正常 Conversation Agent 向正在运行的 integration_review Worker 发送这个已有合同的时序澄清；不修改 Workflow、权限或产品需求。若最终 `accept` 的脚本跳过必需 B-06，仍由正式验收拒绝并按实际反馈返修。

**交流尝试**：消息 661 / Job `cjob_dbe8f5c6e8374f549cebf62ce779ffcc` 明确要求发送 Worker 澄清，Conversation Agent 却解析为 `act=status,phase=discussion`；`agent.worker.send` 被权限层拒绝，Job failed，未改变 Task。消息 663 再次明确执行授权，Job `cjob_606ac128f4b047938994c5a142977657` 正确解析 execute/continue，首次带错 Assignment ID 的发送被拒，随后不指定旧 Assignment 的 `agent.worker.send` 成功，Job succeeded。澄清进入同一 integration_review Worker 的待收消息，等待下一次该 Worker Run 消费。此段表明语义分类仍有随机误判，但授权/Assignment 边界拒绝了错误操作；未为绕过而削弱权限。

### P0：重试交互缺少上次完成拒绝的确定性失败事实

**现场**：Task `tsk_47a7ea2395cd4208834ef7657765458a` 在第 34 次 `integration_review` Run 因 `CompletionProtocolStalled` 暂停。实际触发原因是 Worker 的哈希命令 `readinto(h)` 抛 `TypeError`，`column.complete(accepted)` 被 Runtime 明确拒绝，回执内含失败命令及错误；Worker 没有改用声明的返修 outcome，重复提交相同完成。防循环保护正确。Task 顶层 `error` 只有协议停滞，`failure_detail` 也仅显示该泛化错误；用户在 Web 与 Conversation Agent 交互时看不到哈希命令的具体拒绝原因。原 ColumnRun 可能在 `task.retry` 后从 `interrupted` 变回 `running`，因此按 ColumnRun 状态检索历史也会丢失。Worker 的新 AgentRun 虽可能从执行账本推断失败，但 `current_assignment` 没有独立、可靠的前次拒绝事实。此测试还暴露浏览器脚本把 B-06 标记 `skipped`、按进程退出码批量宣称其余场景 `passed`，以及 Worker 将 600 秒命令上限误说成约 60 秒；这些是待下游正式验收/返修验证的产品交付缺陷，不能放宽正式检查或由测试者直接改项目文件。

**最小修复方案**：在 `TaskFeedbackRepository` 增加只读 `latest_agent_failure(task_id,column_run_id,exclude_agent_run_id)`：按该 ColumnRun 的最近已结束 AgentRun 查找，只有最近一个以失败结束才返回有界 `runtime_error` 与最近一次失败 `column.complete` 的 `error.message`，不读取/暴露命令 stdout；`exclude_agent_run_id` 防止当前运行中的 AgentRun 遮蔽前次事实。`AgentRunPreparer.prepare` 在当前 Assignment 注入 `previous_agent_failure`；`ContextManager.prepare` 每次模型请求前刷新，保证压缩、重试后仍在，后续成功后移除。`failure_detail` 对失败/恢复 Task 补同源 `completion_rejection`；Conversation Gateway 的任务状态帧、无模型通知与已存在的交付报告渲染使用该字段明确呈现拒绝原因，不依赖 LLM 自述。仅增加数据投影，不改完成协议、Workflow、mailbox 职能和 Worker 生命周期。单测模拟同一 ColumnRun 失败 AgentRun→新 AgentRun、压缩与后续成功，验证注入、去重、清除；并验证 Web 通知含具体拒绝、截断且不含原始 stdout。修复后在全局安全边界重启，正常 Conversation 路径 retry 同一 Task，观察真实 Worker 是否收到该字段并处理；不把输入保证误称为动作成功保证。

**实施与回归**：上述字段仅从该 Task/ColumnRun 的持久化回执生成，最多 600 字符的完成拒绝信息，原始 `stdout` 不进入状态或通知。新增同 ColumnRun 失败→重试→成功的上下文测试，并扩展无模型通知测试；相关上下文、完成协议和通知共 **24 passed**。全局无运行 Job/Run/Task 时重启 8000（restart11），`/v1/health` 返回 `ok`。测试消息 679 因调用方误设 `start_task=false` 被系统正确限制为讨论态，不能执行 retry，属于测试参数错误；消息 681 改为 `start_task=true`，Conversation Job `cjob_0373eccf2b214e1698e615c9fb466fa8` 正确解析 execute，调用 `task.retry` 成功，没有新建 Requirement/Workflow/Task。只读检查本次 Conversation Agent 的 `authoritative_project_state.tasks[].failure_detail.completion_rejection` 明确含 `TypeError: readinto() argument must be read-write bytes-like object`；重试 Worker `arun_79b5d416cc9047dd8e6355b760937323` 的真实 `current_assignment.input.previous_agent_failure` 也含同一具体错误和前次 Run ID。这验证了交互层与 Worker 输入的确定性事实传递；该 Worker 的修复行动和浏览器正式验收仍在运行中，不能提前宣称交付成功。

### 黑盒续测：失败事实已传入，但审查 Worker 错误终止交付

**真实结果**：重试 Worker `arun_79b5d416cc9047dd8e6355b760937323` 在同一 `integration_review` ColumnRun 中收到 `previous_agent_failure`，却先再次尝试 `column.complete(accepted)`，被未解决的旧命令失败拒绝；随后记录“浏览器验收 Runtime 限制”反馈并提交声明的 `execution_failed`，Workflow 合法地把 Task 转为终态 `failed`。完成协议没有误放行 `accepted`，但 Worker 对原因的判断错误，且选了终止边而非开发返修边。它声称单命令上限约 60 秒；实际 `ExecutionLimits.command_timeout_seconds=600`。失败的 `verify_browser.py` 命令退出 1、`timed_out=false`，stdout 显示是等待前端端口 5173 **120 秒**后报未启动；单独运行 `java -jar` 是常驻进程，被命令执行器在真实约 600 秒时终止，不能证明浏览器脚本受 60 秒限制。

**独立只读诊断**：项目 `frontend/vite.config.js` 没有显式 host；在当前 Windows 环境手工启动 Vite 后，`http://localhost:5173/` 返回 200，Python `socket.create_connection(('localhost',5173))` 连接到 `::1`，而脚本 `is_port_open` 强制创建 `AF_INET` socket 并连接 `localhost`，实际 `127.0.0.1:5173` 拒绝连接。这解释脚本为何误判前端未启动。临时 npm/Vite 进程已核对命令行并停止。另执行 `node frontend/e2e/login.spec.js` 立即报 `ReferenceError: require is not defined in ES module scope`：`frontend/package.json` 声明 `type=module`，测试文件却使用 CommonJS `require`。`verify_browser.py` 还把 B-06 固定为 `skipped`，仅凭整个 Playwright 退出码批量标记 B-01~B-05 为 passed，且断言条目缺少正式验收要求的 `expected/actual`。因此即使端口探测修好，当前浏览器验收也不能通过；`acceptance_execution.py` 对 skipped 和缺失真实断言的拒绝是正确的。

**后续最小路径**：不修改 DevWerk 完成协议以迁就假报告，也不直接修改测试 Project 交付文件。由于 Task 已终态失败，按现有 Requirement 与 Workflow revision 建立明确的 successor TaskPlan/Task，要求前端列修正 IPv4/IPv6 就绪探测、ESM Playwright 测试与从逐项真实断言生成报告，覆盖冻结 B-01~B-06；若 B-06 需要 15 分钟，则可在 600 秒命令上限内通过可控时钟/短时策略的真实行为测试，不能标记 skipped 或要求人工豁免。后续还需明确审查 Worker 不把下游报告未生成误当当前列缺陷，并让新 Task 通过正式 accept。上述交付文件修复应由 successor 的 Worker 执行，以维持黑盒测试路径；框架级别是否需要约束错误的终止判断，应以这次实证和后继测试结果评估，不能仅因模型误判而重构 Workflow。

**第一次 successor 尝试**：消息 686 / Job `cjob_3603202bb3c0423c83965fa2b1bb492c` 正确解析执行，读取了当前 `task.plan.contract` 与多个历史计划，但未保存修正计划，错误地对 predecessor `tsk_47a7ea2395cd4208834ef7657765458a` 选用 `tplan_bf72422d1e484d5d8da460ede74334c5` 调用 `task.successor`。该计划引用旧 revision 3（`wfrev_31ec...`），包含 echo/findstr 占位验收，Runtime 以 `NonBehaviorCheck` 拒绝，没有新 Task 或假交付。后续请求需明确先调用 `task.plan.save` 保存基于当前 revision 4（`wfrev_f168...`）的修正计划，再调用 `task.successor`；不能复用列在列表里的旧计划。

**扩大回归**：上下文生命周期、Conversation 合同、修复通知、完成协议测试共 **57 passed**（约 3 分钟）。

### P0：`task.plan.validate` 漏检已存在逻辑 Task 身份

**证据**：消息 688 / Job `cjob_46457e10c4d44d77bb2f2e7b3188db0c` 按提示构造 revision 4 的后继计划，经过多轮证据配对后 `task.plan.validate` 返回 `valid=true, diagnostics=[]`。同一计划随即调用 `task.plan.save`，却被 `DuplicateTaskIdentity` 拒绝：计划根部 `repair_of_task_id` 错指第一个失败 Task `tsk_421af...`，而同一 logical_task_key 的最新终态前驱是 `tsk_47a7...`。`validate` 没有执行 `PlanningRepository.create_task_plan` 保存时的现存 Task 身份检查，导致“验证有效→保存失败”并浪费模型循环。旧历史计划误选也在上一轮被 NonBehaviorCheck 正确拒绝；此处是独立的验证/保存合同不一致。

**最小修复方案**：在 `app/v1/capabilities.py::_task_plan_validate` 的只读诊断末尾追加与 `PlanningRepository.create_task_plan` 一致的两个条件：`repair_of_task_id` 必须指向终态 Task 且保留 `proposed_task_ref`；对每个计划 Item 根据当前 WorkflowPlan 的 `task_contract` 算 `logical_task_key`，查 Project 中该键最新 Task，只允许 `repair_of_task_id` 精确等于其 ID 且它为终态、ref 一致。将明确的最新前驱 ID 写入 `DuplicateTaskIdentity` 诊断，帮助 Agent 改正；保存事务内的原检查仍是最终裁决，不移除或放宽。新增回归：先 materialize Task，再让另一个终态旧 Task ID 作为修复目标；`task.plan.validate` 必须 `valid=false`，正确最新 ID 则通过；`task.plan.save` 仍按原规则处理。等待当前 Job 结束后重启，不热切换。

**实施与回归**：`_task_plan_validate` 增加只读终态前驱与最新逻辑身份检查；当其他结构/证据诊断已失败时不追加派生身份判断，以免误报。新测试依次创建两个同一 logical_task_key 的终态 Task，验证旧前驱在 validate 阶段直接报含最新 Task ID 的 `DuplicateTaskIdentity`，正确前驱 validate/save 均成功；规划/入场相关测试 **13 passed**。消息 688 的旧服务 Job 已结束，`task.plan.save` 最终仍被原保存检查拒绝，未生成计划或 Task；需要重启后发起新 Conversation 回归。

**实服务加载与后继计划**：全局空闲时重启 8000（restart12），健康 `ok`。消息 690 / Job `cjob_4169a9f51b6e424aafcb619668910ec4` 的首个 turn.resolve 因新问题缺少问句被拒，纠正后进入执行；它用正确前驱、revision 4 与匹配的 grant/写入回执成功完成 `task.plan.validate` 和 `task.plan.save`，新计划 `tplan_957e1ff7373144699d8757c1f0bdba97` 已持久化。随后 Agent 错把整个 JSON 字符串作为 `task.plan.validate.plan`，收到类型错误，却误报计划缺字段并以无效引用提交 blocked report，Job failed；已保存的计划未回滚，但未创建 Task。消息 692 只要求对该已保存计划执行 `task.successor`，避免重复规划。这个现象表明模型对工具回执仍可误读，Runtime 对非法参数和无效引用保持拒绝；后继创建结果待观察。

**后继创建成功**：消息 692 / Job `cjob_28de82cd7e7e4062802d4226f71001a8` 经 `task.successor` 创建 `tsk_c2872f27727049e6baeb3373412e7259`，`rerun_of_task_id=tsk_47a7...`、Workflow revision 4，已依次执行 requirements、domain_design。此次没有新 Requirement/Workflow；TaskPlan 的先前类型错误未污染已保存计划。

**待观察的上下文污染**：successor 继承 predecessor 全部 `state!='resolved'` 的 `v1_task_feedback`，且 `inherit` 把原 `verifying` 项统一重置为 `open`。只读检查当前新 Task 的反馈中，有“verify_delivery.py bug fixed”“前端 Vitest 29 项通过”等成功/已修复文字，也有旧“README 缺失”、错误的“Runtime 约 60 秒限制”，它们在模型输入里可能与本次真实缺陷并列。已验证的直接事实是这些历史反馈确实被继承；是否导致本次 Worker 错误返修或路由循环仍待下游 Column 证实。若出现该问题，修复应在继承/投影时以冻结检查和更新后的验收证据消除已满足反馈，不能用 mailbox 代替沟通，也不能简单丢弃仍未验证的 B-01~B-06 缺陷。

### 日志调查：测试 stdout/stderr 与 `devwerk.log` 停止写入

**来源与时间**：`data/logs/blackbox-login-20260928-*.stdout.log/.stderr.log` 是本轮黑盒测试者在 2026-09-28 首次后台启动/重启 `startup.bat` 时，通过 `Start-Process -RedirectStandardOutput/-RedirectStandardError` 指定的临时服务控制台捕获文件；2026-09-29 的 `restartN` 文件沿用该手工诊断方式。它们并非 DevWerk 代码新增的日志设计，也不是 Web 调试输出。具体起点可由文件创建时间和本测试的启动记录核对；`startup.bat` 与 Git 源码均未引用 `blackbox-login-*`。这些文件保留了在正式文件日志失效期间的 stderr/console 证据，但体积很大，不应作为永久日志机制。

**根因**：`app/core/logging.py::configure_logging` 自 2026-06 的已有设计使用 `TimedRotatingFileHandler`，`log_dir=./data/logs`、`log_file_name=devwerk.log`；环境启用文件日志，9 月 29 日服务控制台明确打印目标 `D:\workspace\DevWerk\DevWerk\data\logs\devwerk.log`。当前 `devwerk.log` 最后修改于 2026-09-21 20:25:03，末尾日志也在该时刻；之后没有 `devwerk.20260921.log` 等轮转文件。`blackbox-login` stderr 中持续出现 `TimedRotatingFileHandler.doRollover -> PermissionError [WinError 32]`，尝试把 `devwerk.log` 重命名为 `devwerk.20260921.log`。Windows Restart Manager 的只读句柄查询准确返回锁持有者 PyCharm PID 29256，创建于 2026-09-21 14:38。轮转失败后标准处理器的流已关闭、旧 rolloverAt 未推进；每条新日志再次失败，导致正式文件不再追加，而 console handler 仍能写临时 stdout。没有证据表明 DevWerk 主动关闭文件日志；也不应擅自终止用户的 PyCharm。

**最小修复方案**：只在 `app/core/logging.py` 为 `TimedRotatingFileHandler` 增加 Windows 文件占用时的恢复：捕获 `PermissionError`（仅轮转时），重新打开原 `devwerk.log` 继续追加，并把本轮 rolloverAt 推至下一个计划时间，避免每条日志重复抛错；后续日期再尝试轮转。保留正常轮转命名、日志格式和配置。异常本身通过 stderr 打印一次简短诊断，不把命令日志写到 Web。新增 `tests/test_logging_contract.py` 测试：模拟轮转重命名被锁，下一条及后续日志仍写入原文件，且不重复尝试轮转；原有正常轮转测试继续通过。真实服务需等当前 Task/Job 空闲后加载，不能为了修日志中断进行中的黑盒执行。归档文件可能包含跨日期日志，属于单次外部占用期间的降级结果，必须在文档中标明。

**精确起点与实施**：最早一对临时文件为 `blackbox-login-20260928-172431.stdout.log/.stderr.log`，创建于 **2026-09-28 17:24:31**；随后 17:29:32 第二次启动及各次 restart 沿用相同命名。`PyCharm` 进程 PID 29256 创建于 **2026-09-21 14:38**，与当天 `devwerk.log` 停止轮转时间吻合；没有终止它。`_RecoveringDailyFileHandler` 现在只在 `PermissionError` 时重开当前日志并推进 rolloverAt，每个处理器只向 stderr 发一次简短告警；正常日志处理器行为不变。锁定轮转、继续追加、不重复轮转的回归和原有日志测试 **4 passed**。当前服务仍加载旧版本、后继 Task 正在执行，真实文件续写需安全边界重启后再验证。

**Git 历史确认**：`devwerk.log` 的按日文件处理器最初由 `91e91b9`（2026-06-20，彼时路径 `backend/app/core/logging.py`）引入；`blackbox-login-*` 名称不在产品源码或启动脚本中，仅存在于本轮测试记录和手工重定向文件。两者的引入时间、用途和故障链不同。

**真实服务回归**：2026-09-29 11:16（北京时间），确认全局无 queued/running Job、Agent Run、Task 后，核对 8000 端口 PID 的 `startup.bat → uvicorn` 启动链并重启；这次没有 stdout/stderr 重定向，因此没有新增 `blackbox-login-*` 文件。健康接口返回 200，`devwerk.log` 从 **2026-09-21 20:25:03 / 115,053,129 字节**恢复追加至 **2026-09-29 11:16:22 / 115,054,483 字节**，末尾确有新服务启动记录，锁持有者 PyCharm 未被终止。证明文件锁仍在时，新处理器至少能维持主日志追加；轮转归档须等外部锁释放后的下一次午夜才能验证。

**后继前端返修的进一步证据**：successor Task 已走过 requirements/domain_design/backend_development/frontend_development/integration_review/system_test/delivery，又经 integration_review 路由回 frontend_development。当前前端脚本虽写了“双栈探测”说明，实际仍对 IPv4/IPv6 调 `sock.settimeout` + `connect_ex`；真实 Worker 命令在 Java 日志明确 Tomcat 监听 8080 后，用同一探测得到 Windows `10035`，因而再次误判服务未启动。原 `frontend/e2e/login.spec.js` 仍用 ESM 不兼容的 `require`，B-06 仍为 skipped，报告仍有静态预设 `actual`。消息 702 / Job `cjob_ed038abc73724b6c9e0cf9fadc121af7` 按正常 execute/continue 边界成功调用 `agent.worker.send`，向当前 frontend Assignment `assignment_56631324c0d24762b6dc8754979670d6` 投递这三条精确事实和 `socket.create_connection` 的修正方向；消息 ID `winput_778d151075e0443b86871f1befb25bcf` 初始为 pending。等待 Worker 消费和实际测试，不直接改项目交付文件。

**验收 ID 合同不一致（待返修）**：冻结的 Workflow revision 4 `wfrev_f16835435b184126be5ff93620d5cc45` 中，`delivery.readme_windows_startup` 要求报告场景 `S-01`，而当前 `backend/scripts/verify_delivery.py` 生成 `DELIVERY-01/02/03`；`accept.final_browser_acceptance` 要求 `S-01` 至 `S-12`，而当前 `frontend/scripts/verify_browser.py` 只生成 `B-01` 至 `B-06`。`acceptance_execution` 按冻结 ID 逐项要求 `passed` 和真实断言，因此脚本即便退出 0，也无法满足这两列。该错配是生成交付脚本时没有核对 revision 4 的报告合同，不能放松 Runtime 的场景匹配。进一步核对 Task 输入后，`S-01~S-12` 本身是用户需求中冻结的 12 条验收场景；应先让后继 Task 按既有 revision 修正脚本报告和逐项真实断言，而不是改变 Workflow 来迁就脚本的 B/DELIVERY 命名。新验收必须保证 B-01~B-06 对应的浏览器行为及所有 S 场景真实执行，不把 Playwright 的 `test.skip`、静态 `expected/actual` 字符串或 900 秒以上的等待冒充通过。回归以正式 Column 完成回执和报告断言为准。

**对运行中 Worker 的确定性纠偏**：另发现 B-06 代码固定等待 `900` 秒，超过 `project.command.run` 的 `600` 秒单命令上限；即使服务正常，它也无法在正式验收命令内完成。消息 704 / Job `cjob_3d8118fc74b64ac5abaffbcd1285ae37` 已经由正式 Conversation API 接受，请 Conversation Agent 用 `agent.worker.send` 投递 900/600 秒约束、上述报告 ID 错配以及不能跳过或伪造断言的事实；仅在当前前端 Worker 的责任范围修脚本，后续 revision 调整另行进行。需以 Job 回执和 Worker 消费状态确认通知真正送达。

### P0：前端 Worker 在文件读取循环中耗尽 Assignment 预算

**实证**：后继 Task `tsk_c2872f27727049e6baeb3373412e7259` 的第二次 `frontend_development` Agent Run `arun_b9ae8425832e4d11980219452d508b97` 于 2026-09-29 11:11:21 以 `ExecutionBudgetExceeded: Assignment cumulative model/tool/resume budget exhausted` 结束，Task 进入 `recovering/paused/blocked_runtime`。同一 Assignment 累计模型调用 100 次、工具调用 128 次；此次 Run 为 95 次模型迭代、128 次工具调用、约 34 分钟。工具构成：`agent.result.read` 56 次、`project.files.read` 30 次、`project.files.search` 24 次、真正写文件 7 次、命令执行 6 次。末段反复读取同一 `verify_browser.py` 的前 2,000 字符、搜索文件每一行，再用 `agent.result.read` 多次分页读取搜索结果；用户纠偏虽有一条已消费，仍来不及完成。预算拒绝和暂停是正确保护，但这一列没有形成交付。

**可确认的接口缺口与最小修复方案**：`app/v1/files.py::ProjectFiles.read_text` 只能返回从文件开头开始的前缀；`app/v1/capabilities.py::_files_read` 暴露 `max_chars`，却没有 `offset`，也不返回总长度/下一偏移。Worker 想读中间和末尾，只能请求超大结果后再用 `agent.result.read` 穿过投影，容易重复消耗模型调用。仅在 `project.files.read` 增加可选非负字符 `offset`，返回 `offset/next_offset/total_characters/content_complete`；保持原有 `path/content` 和未传 offset/max_chars 时的完整读取语义。工具说明明确提示“相同 path、offset、max_chars 会重复返回同一页，需用 next_offset 继续”。`tests/test_files_contract.py` 增加真实 UTF-8 中文文件分页、到达 EOF、旧调用兼容回归；不提高 Assignment 预算，不改 Worker 生命周期或 Column 架构。该接口是已证实的效率缺口；它不能保证模型一定停止重复工具调用，后续仍需用新 Task 黑盒验证实际调用数。

**恢复边界**：当前 Assignment 已累计达到模型 100 次上限，直接 resume 无法清掉累计预算；待修复加载后，须使用既有 `task.fail` 终态和 `task.rerun`/`task.successor` 创建新 Assignment。进一步核对后，revision 4 的最终验收 `S-01~S-12` 与 Task 输入中的 12 条冻结需求场景一致；错的是脚本报告 ID，因此**不必为了这个缺陷发布新 Workflow revision**。优先让后继 Worker 按现有冻结合同修正交付/浏览器报告，且真实验证每个 S 场景；仅当检查本身与需求事实矛盾时再发布新 revision。暂停的 Task 和残留的 pending Worker 输入不可当作已完成。

**分页修复实施与测试**：`ProjectFiles.read_text_page` 按 Unicode 字符位置返回文件页和 `next_offset/total_characters/content_complete`；`project.files.read` 新增可选 `offset`，未传参数时继续返回完整内容，工具说明指明重复参数会重复同一页。新增直接分页和 capability 路径回归；`tests/test_files_contract.py` 与 `tests/test_logging_contract.py` 合计 **11 passed**。这项修复已落到磁盘，11:16 的服务重启发生在该代码编辑之前，因此服务尚未加载分页能力；待下一安全边界再加载。它不自动重置已耗尽的 Assignment。

**分页修复加载**：11:24 再次确认无活动 Job/Run/Task，核对 8000 启动链后重启，健康接口 200；`devwerk.log` 继续追加至 115,055,837 字节 / 11:24:12。本次服务已加载分页接口，未生成新的黑盒 stdout/stderr 文件。

**保留同一 Task 的更小恢复路径**：复核 `RecoveryManager.retry_task` 与 `WorkflowRuntime.begin_run` 后，`recovering` 且暂停的 Task 可以通过 `task.retry(task_id, column_key='frontend_development', clear_context=true)` 在同一冻结 revision 上产生新的 Column Run/Assignment；新 Assignment 有独立 100 次模型预算，不必把此前已通过的 requirements、backend、system_test 等列从头再跑。当前 Worker 实例 `worker_7972faff31574bf19bee70eef552c116` 已 idle，但其 Session 有 620 条消息，直接 retry 会继承庞大历史。先由该 Project 的 Conversation Agent 调用 `agent.context.compact`，以最后已存消息 ID `12395` 为边界写入覆盖事实的简短摘要（已有前端构建/测试证据、真实浏览器失败、待修复项、冻结场景 ID、900/600 秒约束和不得新增产品 API）；然后同一轮调用 `task.retry` 定位前端列，保持 Task ID、Requirement、Workflow 不变。这比 `task.fail + task.rerun` 更符合当前架构及最小改动原则。若显式压缩或 retry 失败，应记录回执并停止该路径，不能绕过预算检查。

### P0：显式 `task.retry` 复用已耗尽的 Assignment

**真实回归推翻前一假设**：消息 712 / Job `cjob_74f70fb290444f6a8edc3cd741d12c1f` 成功调用 `agent.context.compact`，快照 `ctx_b5ef249cb6844eb5915b7982838cd3ec` 覆盖至消息 12395；`task.retry` 也返回 `pending`。Runtime 随即在同一旧 Column Run `run_2311670e40924a99baa8645e0b8ac408` 创建 Attempt 2，但 `Store.begin_run` 优先复用状态为 `interrupted` 的最近 Run，`AgentRepository.assign` 又复用其已 `blocked` 的旧 Assignment `assignment_56631324c0d24762b6dc8754979670d6`。新 Agent Run `arun_cf490ddded7f4870ba07158694732eb3` 数秒内再次收到相同预算拒绝，Task 回到 `recovering/paused`。因此前段所称“retry 会重建新 Run/Assignment”仅是设计假设，**实际不成立**。该缺陷使显式恢复操作看似成功却无法前进；自动恢复旧 Run 对暂态中断仍需保留。

**最小修复方案（先于代码）**：只在 `app/v1/services/recovery_manager.py::retry_task` 的事务中，针对 `status=recovering`、`failure_code=execution_budget_exceeded`、目标为当前 Column、最近 Run 为 `interrupted` 的**显式用户重试**，创建一个新的 `pending` Column Run，序号递增；不修改 Workflow、Task ID、已完成列和其他暂态恢复。`Store.begin_run` 已优先取最新 pending/interrupted Run，新 Run 会使 `AgentRepository.assign` 创建预算独立的新 Assignment，沿用同一 Worker Session 中刚保存的压缩快照。`tests/test_p0_assignment_regressions.py` 新增真实 SQLite/Runtime 回归：先把旧 Column Run 及 Assignment 置成预算耗尽状态，显式 retry 后必须启动不同 Run/Assignment 且模型预算从零计；普通 provider 暂态恢复仍复用旧 Run/Assignment，保证既有幂等回放。完成测试后重启服务，再按正常 Conversation 路径重试当前暂停 Task；必须以数据库中新 Run ID、Assignment ID、模型调用及最终 Column 回执核验。

**代码与测试**：`RecoveryManager.retry_task` 仅在上述精确条件下原子插入新 `pending` Column Run；新增测试先持久化旧 `blocked` Assignment/`interrupted` Run，再显式重试，并核对新 Run、新 Assignment、相同 Worker 实例和从零开始的模型预算。`tests/test_p0_assignment_regressions.py` 全部 **18 passed**（约 2 分钟），其余暂态中断与幂等回放案例仍通过。真实服务尚需在空闲时加载并复测当前 Task。

**条件收紧**：为了避免 Task 上保留的旧 `failure_code` 在后续其他中断时误触发，还要求最近 `interrupted` Run 的 Assignment 当前为 `blocked`，且其持久化 `result_json.code` 精确为 `execution_budget_exceeded`。新增测试写入真实形状的结果；此项调整后，预算重试和暂态幂等回放聚焦 **3 passed**。不对普通中断新建 Run。

**真实服务恢复回归**：11:39 全局空闲时重启加载修复，健康接口 200，`devwerk.log` 仍追加；消息 717 / Job `cjob_d3662ca8b7a24bdb90441b6b8a511a39` 经 Conversation Agent 正常执行 `task.retry`。同一 Task `tsk_c2872f27727049e6baeb3373412e7259` 当前在 `frontend_development`，**新** Column Run `run_a8ceb401289548059ba10775d2a5684b`（sequence 10）和**新** Assignment `assignment_6a880e031d044885bfaf8385ae2e979d` 已运行，模型计数从 **1** 开始；旧 sequence 9 Run 仍 `interrupted`，旧 Assignment 仍 `blocked` 且模型计数 100。证明预算恢复缺陷已被修复，仍需观察新 Worker 的实际前端验收和 Task 最终状态。

**新 Worker 确定性接收事实**：消息 719 / Job `cjob_4e3f208b3cda41e0b3d4c56626323dcb` 成功通过 Conversation Agent 调用 `agent.worker.send`；Worker 输入 `winput_30cf0e938d0f408c87663923f15091a2` 已由新 Agent Run `arun_62a81b1f960d4731823ca6f77d3ed60f` 确认消费。内容明确现有 revision 4 的 S 场景不需改 Workflow、浏览器脚本须报真实 S-01~S-12、B-06 不能 900 秒等待或跳过、不新增 API、delivery 脚本属下游列，以及文件读取现可分页。继续观察实际文件改动与正式验收回执。

**分页接口的模型可见性缺口**：新 Run 前 17 次模型调用/22 次工具调用里，`project.files.read` 8 次、`agent.result.read` 14 次。它对大型 `verify_browser.py` 等仍使用不带 `max_chars` 的默认完整读取，原始结果超过工具投影预算，被 `context_manager.project_result` 的通用截断逻辑变成引用；模型只好再用 `agent.result.read` 分页。虽然后端已有 `offset/next_offset`，默认路径并未把下一偏移直接展示出来。最小补丁：仅为 `project.files.read` 的超预算回执增加专用投影，保留 `path/offset/total_characters`，将内容裁到既有结果预算内，并给出准确 `next_offset/content_complete`；完整原始结果仍存档可检索。`tests/test_context_acceptance_regression.py` 增加大型 Unicode 文件回执投影测试，要求投影可见下一偏移且第二页通过 `project.files.read(offset=...)` 返回后续字符。普通结果和其它能力的投影保持不变。当前运行服务不会热加载此补丁，待空闲时验证。

**代码与回归**：`context_manager.project_result` 对超预算的 `project.files.read` 回执返回一个连续 UTF-8 安全的首段和准确下一偏移，原始完整回执仍保留；其它能力投影不变。新增真实文件/capability/投影串联测试；分页与文件测试 **8 passed**，完整 `test_context_acceptance_regression.py` **43 passed**。当前前端 Worker 已在运行，不能为加载此优化而中断它。

**运行中交付文件审查（11:46）**：新 Worker 把 `frontend/scripts/verify_browser.py` 扩写为约 30 KB，并开始输出 `S-01~S-12`，但当前脚本自称 `HTTP API Tests`，未启动 Vite 或调用 Playwright；冻结 `accept.final_browser_acceptance` 的目的却是浏览器自动化场景 B-01~B-06。`S-10` 仅验证锁定后短时仍锁定，却在“15 分钟后解除未经自动验证”的备注下将 `status` 设为 `passed`，属于未验证行为的假通过。S-07~S-09 的部分断言 `expected='locked'` 而 `actual='locked=True, HTTP ...'`，即使状态标为 passed，Runtime 的实际 expected/actual 比较也会拒绝。后端已有 `AuthServiceTest.T07_autoUnlockAfter15Minutes_allowsLogin`，可通过构造过去的锁定时段真实验证过期后登录；不能用短时仍锁定代替。应继续通过正常 Conversation Agent 向当前 Worker 投递这三条具体回归反馈；不修改 Runtime 来放宽真实报告要求。

**反馈已送达**：消息 721 / Job `cjob_c14c624f687642fb8bf691c3385360da` 成功；Worker 输入 `winput_46706fdbc8394770b5498978a8b5c7f9` 于 11:51:13 被新前端 Run `arun_62a81b1f960d4731823ca6f77d3ed60f` 确认消费。内容包含 Playwright 缺失、S-10 假通过与断言不相等的具体证据。下一步只依据 Worker 的实际修改、命令回执和正式验收判定是否修好。

**前端脚本真实运行结果**：Worker 随后确认原 Vitest 测试文件 5/5、用例 29/29 通过，但新写的 `frontend/scripts/verify_frontend.py` 在正式 `project.command.run` 运行时退出码 1；其报告把 S-11/S-12 等标为 `skipped`，提示 `No matching tests found in Vitest output`。这是脚本与 Vitest JSON 测试名称的匹配问题，29 项单测通过不足以证明冻结 `frontend_vitest` 场景通过。当前 Worker 正继续返修，Runtime 没有误放行。

**第二轮真实拒绝**：Worker 修复 JSON 名称映射后，`verify_frontend.py` 进程退出码 0、报告把 S-01/S-02/S-11/S-12 标为 passed，但各场景断言的 `expected` 是需求语句、`actual` 是通过的测试名称列表，两者不相等。Runtime 返回 `AcceptanceEvidenceInvalid: Scenario assertion mismatch: S-01`；随后 `column.complete(frontend_ready)` 被 `Required acceptance check has not passed` 拒绝。消息 723 / Job `cjob_109f8d7599eb42fdac039b3461d235e3` 请求通过 `agent.worker.send` 投递该准确回执与最小修正：按实际 Vitest JSON 测试逐项生成预期/结果相等的布尔断言，缺失或失败不许填真；需核对 Job 及 Worker 输入消费。完成协议的拒绝是预期正确行为，不放松校验。

**第二轮反馈送达**：Job `cjob_109f8d7599eb42fdac039b3461d235e3` 成功；Worker 输入 `winput_ba8f7ee7a01844e9b998e4907faa4c18` 已由当前 Run `arun_62a81b1f960d4731823ca6f77d3ed60f` 确认消费。后续需看重新运行的正式检查结果。

**前端正式检查恢复**：Worker 修复 `verify_frontend.py` 的断言数据后，`project.command.run [python, frontend/scripts/verify_frontend.py]` 已返回 `ok=true/status=completed/exit_code=0`，stdout 明确 S-01/S-02/S-11/S-12 passed，逐项数值 expected 与 actual 相等。其后单独尝试的 `findstr` 命令退出 1，不是正式冻结检查；再次运行完整 Vitest JSON 命令返回 0。Task 当前仍在 frontend_development，需等 `column.complete` 和下游正式检查，不把本次单项通过误报为交付。

**前端列完成**：Worker 首次 `column.complete(frontend_ready)` 把 `output` 错传为 JSON 字符串，被 `ContractError` 拒绝；随后重跑正式 `frontend_vitest` 命令并用对象 `output={}` 提交，Runtime 接受。frontend_development 新 Run `run_a8ceb401289548059ba10775d2a5684b` 为 `succeeded`，Task 已转到 sequence 11 的 `integration_review`。浏览器脚本尚未见修正，因此不能视为最终验收通过；需看 review 是否按真实缺口返修，而不是只看前端 Vitest 通过。

**Review 返修归因不可靠**：sequence 11 的 integration_review Worker 实际使用新 `project.files.read(offset=...)` 检查 `verify_browser.py`，但记录的反馈 `integration_review:verify_browser...` 声称“脚本缺少后端启动逻辑”，依据是 `docs/browser-acceptance-report.json` 中 10:42 的空报告。该报告早于脚本 11:46 的修改；当前脚本 main() 明确有 `subprocess.Popen(java -jar)`、`wait_for_service` 和 finally 清理。Review 的现时归因未由新鲜命令证明，且没有指出当前脚本的假 S-10 与 Playwright 缺失。Task 已经由 Review 路由回 sequence 12 的 frontend_development。消息 725 / Job `cjob_8683c8ab5cd14813896185e69f1db949` 请求通过 `agent.worker.send` 向新前端 Assignment `assignment_5269245ac8cd4911816270604ca29012` 说明旧报告时间戳和当前源码，要求先运行现版脚本再按真实失败修复；需核对投递与消费。此处暴露的候选 P0 是 Review 使用过期报告作当前缺陷证据，后续若持续循环，应在反馈证据准入增加新鲜度校验，而不是靠 mailbox 传话。

**纠偏已消费**：Worker 输入 `winput_9092c1033c9b4f829aa54b5372555b41` 已由 sequence 12 的前端 Run `arun_68e84f3e4ff64b9e8eb2928a0be612a9` 确认；它包含旧报告时序、当前后端启动代码和真实浏览器验收缺口。Worker 当前仍在检查脚本/Playwright 环境，尚未生成新鲜浏览器报告；不能判断缺陷已解决。

**依赖选择偏离与纠正**：`frontend/package.json` 和 `npm list @playwright/test` 已证实项目内安装 Node Playwright 1.63.0；Worker 却因 Python `import playwright` 失败，在 `project.command.run` 中执行 `pip install playwright`，成功把 Python Playwright 1.63.0 装入用户级 `C:\Users\hongt\AppData\Roaming\Python\Python314\site-packages`。这不是交付所必需的项目依赖，且发生在纠正消息前；不擅自卸载用户级包。消息 727 / Job `cjob_56b1c318ce644d3fa651631a6d3a4b11` 已要求经 `agent.worker.send` 提醒当前 Worker 使用项目现有 Node Playwright/JS 用例而非继续安装 Python 生态；需核对送达。此类跨环境无效安装消耗时间，但本次主要 P0 仍是正式验收脚本未运行浏览器行为。

**新鲜浏览器运行与旧缺陷复现**：Worker 先完成了用户级 `python -m playwright install chromium`，消息 727 的输入随后被当前 Run 确认消费；又重写 `verify_browser.py` 调用 Node Playwright。其首次真实 `project.command.run [python, frontend/scripts/verify_browser.py]` 退出 1，12:22 新报告为 `Backend failed to start within 120 seconds`、`scenarios=[]`；JAR 实存 50,910,650 字节，`java -version` 正常。当前 `is_port_open` 注释仍称用 `socket.create_connection`，实现却再次退回 `socket.socket; settimeout; connect_ex`，与先前 Windows `10035` 假未就绪故障完全同形。Review 的“服务启动失败”现有新证据，但“缺少启动逻辑”归因仍错。消息 729 / Job `cjob_2201ffc295b4408a90fd27bab23948fe` 已要求通过 `agent.worker.send` 向当前前端 Worker 投递精确的源码与新鲜回执，要求实际改 `create_connection` 并重跑；需核对消费。另新脚本仍把 B-06 `expected_result` 固定为 skipped，即使端口修好也不能满足正式 accept。

**消息 704 的实际回执及测试者误用**：测试者为表达“不创建 Task”在 API 请求中传了 `start_task=false`；但现有 `ConversationRequest`/`conversation_intent_repository` 把该字段定义为本轮**所有执行变更的上限**。该 Job 因而只能是 `act=status, phase=discussion`、`execution_grant_id=null`；Conversation Agent 尝试 `conversation.turn.resolve act=execute` 被 `ConversationMutationDisabled` 正确拒绝，Job 失败，**没有投递 Worker 输入**。这不是 Runtime 意图分类故障，而是测试调用参数与消息目的冲突。同一内容改为明确“执行当前已授权 Task 的 `agent.worker.send`”并传 `start_task=true`，同时在消息中限定不创建新的业务对象，消息 706 / Job `cjob_9e4a835644274f368ac8490392a94b32` 已获 `act=execute` 与现有 grant，待核对工具回执。必须以持久化的 `turn_contract`、工具调用和 `v1_worker_inputs` 三项确认送达，不能以自然语言回复代替证明。

**消息 706 已送达，但须纠正范围**：Job `cjob_9e4a835644274f368ac8490392a94b32` 成功；Worker 输入 `winput_75fc4cf155c54b47b7fb5819a0d1d38f` 在 11:09:20 被当前前端 Run `arun_b9ae8425832e4d11980219452d508b97` 消费并确认。Conversation Agent 自行在投递内容里举了未经证实的 `/api/remaining-lockout-time` 接口，还要求前端 Worker 修改后端 `verify_delivery.py`，超出本列职责，也不属于用户提出的产品需求。消息 708 / Job `cjob_820c1e1b2d2d4fb6bdd4144c85b17ce4` 已请求通过相同执行路径纠正：不得新增该接口，前端只修改其负责的真实测试；后端交付检查与冻结 Workflow ID 在后续正规步骤处理。需核对该纠正的实际投递和消费状态。这也说明跨 Agent 转述不能仅靠自然语言期望保持范围；本次先用精确的 Worker 输入纠正，框架级传递约束要以真实失败证据再决定最小修复。
