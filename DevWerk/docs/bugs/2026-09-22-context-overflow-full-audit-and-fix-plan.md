# 09-21 普通用户软件交付：上下文溢出与完整执行链路复盘、修改方案

日期：2026-09-22。状态：**复盘及设计，尚未实施本轮修复**。

项目：`prj_b3b364ba5d97483691a9fdcef6bf0c6c`。
Task：`tsk_275c1ad9215c42ddb0b532c0d78d40d1`。
冻结 Workflow：`wfrev_55d00e8aa2e643378a9e3e29f495d5cb`（revision 4）。
代码基线：`cd2b2a7`，分支 `feature/context-provenance-handoff`，已提交此前的代码、测试、脚本和相关文档，共 70 个文件。数据库、运行日志和 ZIP 不入库。首次推送被自动审批拒绝后，用户于 2026-09-22 明确确认 `https://github.com/zanghongtu2006/DevWerk.git` 是其个人仓库，并要求同步所有文档和源码；本复盘与历史 test-results 验证记录一并纳入后续提交。

本次只读生产 SQLite、日志和该项目产物；没有重新执行任务、修改产物、修正生产状态、重启服务或调用交付模型。以下区分实际发生的问题和冻结计划中尚未执行到的缺陷。旧报告只作为索引，不替代数据库与源码证据。

## 1. 结论

直接停止原因确定为模型上下文窗口超限，**不是 Token Plan 耗尽，也不是 JSON 参数字段错误**。错误码 2013 是 Provider 的通用参数错误类别，必须结合原始消息中的 `context window exceeds limit` 细分；不能解释为超出 2013 tokens，也不能把所有 2013 都当上下文溢出。

根因不止“命令结果太长”：

1. 单个 AgentRun 的 `messages` 持续增长，没有每轮请求前的上下文预算与压缩。
2. 工具的持久证据与模型可见内容没有分离；命令日志完整重放，代码写入参数的旧版本也不断累积。
3. Session 裁剪只在进入新 AgentRun 时执行，既不能保护长时间运行中的 Column，也不能可靠延续诊断记忆；已有摘要接口要求 Worker idle，Assignment 回放还会忽略该摘要。
4. 溢出被归入永久 Provider 错误，直接暂停，而没有“缩小请求后有限重试”的宿主恢复路径。
5. 本次还暴露出冻结验收命令不可执行、检查不验证真实行为、修复中删减测试、同一 Column 内反馈无法原地结清等问题。只做日志截断，仍不能保证该项目交付。

现有 370 项测试通过、两轮小型返修实验通过，不能证明本次 36 轮、大日志、Windows 软件交付可靠。此前测试在这里存在明确覆盖缺口。

## 2. 时间线与状态核验

时间统一使用北京时间 UTC+8。

| 阶段 | 时间 | 实际结果 |
| --- | --- | --- |
| 项目创建 | 09-21 18:30:12 | 新建独立目录 |
| 讨论 | 18:30:32—18:34:45 | 5 个用户 Job 成功；均为 `requested_mode=discuss`，没有提前创建 Task |
| 明确开始 | 18:35:12—18:39:34 | 16 次模型调用、17 次工具调用，经历 6 次工具拒绝后建立 1 个 Task |
| requirements | 18:39:34—18:40:16 | 完成，第 1 次 Attempt |
| domain_design | 18:40:16—18:43:43 | 完成，第 1 次 Attempt；期间一次完成协议被拒绝后纠正 |
| backend_development | 18:43:44—18:57:01 | 第 1 次 Attempt；第 36 次模型请求被拒绝 |
| 暂停通知 | 18:57:01 | Runtime 阻塞并发通知；没有 Mailbox 模型调用 |

后端 AgentRun：`arun_e5c7aaf58c1f4ebb8ac33241f2eb3365`，实际 **796.906 秒（13 分 16.906 秒）**。Task 创建到暂停约 17 分 38 秒。本次数据库时间不能支持把“7 分 37 秒”当作整个后端执行时长；它若来自另一个观察区间，应另标区间，不能替代运行起止时间。

当前 Task 的事实是 `recovering / paused / blocked_runtime`；ColumnRun 为 `interrupted`，AgentRun 为 `failed`，尚未到业务终态 `failed`。`failure_origin=provider`、`failure_code=LLM_BAD_REQUEST`，没有自动继续排队。前端及其后续列均未执行，不能宣称整个产品已交付或证明后续验收失败已经实际发生。

## 3. 上下文和 Token 证据

### 3.1 最后一轮的精确链路

证据在 `data/logs/devwerk.log`：

| 行号 | 内容 |
| --- | --- |
| 4217 | 后端 iteration 1：2 条消息，序列化消息 23,489 字符 |
| 4566 | iteration 26：87 条消息，427,161 字符 |
| 4665 | iteration 35：105 条消息，714,312 字符 |
| 4676 | iteration 36：107 条消息，761,608 字符 |
| 4678 | 实际 Provider 请求，`max_tokens=65535` |
| 4681 | HTTP 400，原始 `invalid_request_error`，消息为上下文超限 |
| 4682 | 归一化为 `LLM_BAD_REQUEST:http_400:provider_2013` |
| 4684 | Runtime 后端列错误 |

上述字符数使用 `json.dumps(messages, ensure_ascii=False)` 计算；每次另有约 5,513 字符的原生工具 schema。字符数不是 tokens，也不是实际 HTTP 字节数。

`llm_usage.id=3022` 是最后成功请求：未缓存输入 3,530 + 缓存输入 185,019 + cache creation 0 = **188,549 个输入 tokens**。这里包含缓存，不能只看 `input_tokens=3530` 就认为窗口很小。

随后工具调用 `chatcmpl-tool`/`call_function` 的最后一次 Maven 结果（invocation sequence 74）是 **45,165 字符 / 45,463 UTF-8 字节**，再进入 iteration 36。失败请求 `llm_usage.id=3023` 没有 token usage，因此不能从数据库声称其精确输入 token 数；可以确认请求继续增长并被 Provider 以窗口超限拒绝。

官方当前列出的 MiniMax-M2.7 上下文窗口为 204,800 tokens：[MiniMax 模型文档](https://platform.minimax.io/docs/guides/text-generation)。2013 是通用错误类别：[官方错误码](https://platform.minimax.io/docs/api-reference/errorcode)。本次具体归因依靠日志原文，不仅依靠代码。

当前请求已经能在 `max_tokens=65535`、实际输入 188,549 时成功，不能据此断言 Provider 使用严格的“输入 + 声明最大输出”校验，也不能声称调整这个参数本身即可解决故障。宿主仍应主动给实际输出预留空间。

### 3.2 工具调用计数与尺寸口径

模型发起 **70 次工具调用**；Runtime 在两次 `column.complete` 内额外执行 4 次验收检查，所以 `v1_tool_invocations` 中实际 **74 条**。其中：

| 类别 | 数量 | result_json 字符 | UTF-8 字节 |
| --- | ---: | ---: | ---: |
| 模型发起 project.command.run | 10 | 380,719 | 383,927 |
| Runtime 代执行同名验收命令 | 2 | 4,102 | 4,218 |
| 合计命令回执 | 12 | 384,821 | 388,145 |

因此“约 385 KB”方向正确，但应注明是否包含 Runtime 检查及字符/字节口径。最大的三条结果分别是 109,693 / 83,659 / 71,994 字符。

其余调用包括 43 次文件写入、11 次文件读取（含 2 次 Runtime 验收读取）、4 次文件列表、1 次 feedback.list、1 次 feedback.record、2 次 column.complete。文件写回执本身已经较短；膨胀来源还包括 assistant tool-call 参数中不断重发的整份源码：35 条 assistant 消息的 tool_calls_json 累计 **199,187 字符**。70 条模型 tool 消息 content 共 **504,026 字符**。

只读投影实验：把 iteration 36 中各命令 stdout/stderr 超过 8,000 字符的部分替换成首 2,000 + 尾 6,000 字符，消息体从 **761,608 降到 444,509 字符**。这只是长度诊断，不是新代码或新模型调用，也不是实测 token 节省；它证明只限制命令输出仍留下大量旧代码参数、文件内容及其他历史，必须治理整条消息链。

### 3.3 全项目消耗

| 范围 | 模型请求 | input_tokens | output_tokens | recorded total | cached input |
| --- | ---: | ---: | ---: | ---: | ---: |
| Conversation | 29 成功 | 197,464 | 33,858 | 231,322 | 744,491 |
| requirements | 3 成功 | 17,017 | 2,979 | 19,996 | 7,885 |
| domain_design | 5 成功 | 43,144 | 13,548 | 56,692 | 45,083 |
| backend_development | 35 成功 + 1 失败 | 363,984 | 41,423 | 405,407 | 2,606,376 |
| 合计 | 72 成功 + 1 失败 | 621,609 | 91,808 | 713,417 | 3,403,835 |

这里 recorded total 沿用现有存储口径，不把缓存再次混入该列，更不当作金额。计算实际请求上下文时则必须按 Provider 的 usage 语义把 cache read/create 计入。失败请求 usage 为未知，不能按零 token 成本做结论。

三个 Mailbox Job 均成功、`agent_run_id=NULL`，对应阶段通知及最终阻塞通知。此前“Mailbox 是被动通知机制”的修复在本项目没有回归，不应为了恢复任务再让 Mailbox 调模型。

## 4. 问题清单、原因及优先级

### P0-C01：活跃 AgentRun 没有上下文窗口治理（已发生）

- `app/v1/agent_runner.py:119` 每轮直接请求；133 行追加 assistant 消息。
- `app/v1/agent_tool_execution.py:253` 调用 `tool_result_json(result, None, ...)`，270 行完整追加。
- `app/v1/capabilities.py:2389` 的 `tool_result_json` 虽保留 max_chars 参数，却完全没有使用它。
- `app/v1/policy.py:59` 的命令上限是每个 stdout/stderr 缓冲 1 MiB；本次所有日志远低于该上限，因此没有触发。这个限制用于子进程资源保护，不能充当模型窗口限制。
- ProviderTurnRequester 没有预算准入；Runner 没有用 `AgentModelResponse.usage` 校准下一轮；迭代/工具次数上限也不能代替 token 上限。

不能简单把 command_max_output_bytes 降到几 KB：`process_runner.py` 发现输出超限会终止整个进程树，这会把正常高输出测试变成失败。模型投影限长与进程执行资源上限必须分离。

### P0-C02：Session 压缩生命周期不完整（已确认源码缺陷）

`agent_repository.py:588` 的 session_history 只在新 Run 准备时使用 80,000 字符倒序取尾；长运行根本不会再次经过此处。`save_context_snapshot`（631 行）要求 idle；有 Assignment 时 597 行又明确忽略 snapshot，不能把该接口当作活跃 Worker 的自动压缩实现。

本项目 snapshot 数量为 0。只读模拟现有重载选择：106 条非 system 历史只能留下最后 **4 条消息，约 66,353 字符**，完整配对回放约 65,533 字符，早期诊断没有结构化摘要。源码确实会在新 Run 缩短历史，因此应修正“直接重试一定重放完整 761K”这一过强表述：重试可能缩短，但会丢失大量工作记忆；继续运行后仍然可以再次膨胀。

现有 session_replay 能移除孤立 tool result，不是完全没有配对保护。但先逐消息取尾再过滤可能把整个大批次排除，甚至没有留下有用的诊断上下文。摘要边界必须在完整工具批次之后。

### P0-C03：上下文超限没有专属恢复策略（已发生）

`provider_errors.py:37` 把所有 2013 映射为 LLM_BAD_REQUEST；`runtime.py` 再归为 provider_permanent，并经 `recovery_manager.block_runtime_failure` 暂停。保留原件和禁止无效重试是正确的，但“可通过压缩恢复”没有独立分支。

不能将 2013 整体改成 transient：其它参数错误也用该码，原样重发会形成新的无效消耗循环。也不能通过清空 Session、换 Worker 或调用 Mailbox 代替恢复。

### P0-A01：冻结验收检查不能正确表达成功条件（部分已发生，部分潜伏）

冻结 revision 4 有三类问题：

1. `backend_test_report` 对目录 `backend/target/surefire-reports` 调用 files.read。实际两次执行时目录尚未生成，报告 FileNotFoundError；即使后续生成，read_text 仍不能把目录当普通文件读。这是后续确定会撞到的契约缺陷。
2. `source_rejection_check` 只执行 curl，没有断言 HTTP 状态等于 403。退出码 0 证明 HTTP 传输成功，不证明返回码、响应内容符合预期；返回 200 同样可被误判成功。此阶段尚未执行。
3. `final_acceptance_verification` 使用 `if exist FINAL_ACCEPTANCE.md ...`，purpose 也是“验证最终验收报告存在”，却声明 `evidence_kind=behavior`。`task_plan_compiler.py` 目前只验证该标签、命令类型及 obligation 引用，没有证明测试含有行为断言。此阶段尚未执行。

另外，真实服务就绪、HTTP 探测、浏览器测试和 finally 清理的范围被藏在未来脚本里，现阶段不能确认已实现。node 启动某个 JS 文件也不自动证明 Playwright 真正执行了浏览器断言。

验收门禁至少成功挡住了后端两次无测试证据的完成声明，不能将本次状态误称为“假 done”；但门禁中的弱检查会在后续阶段造成假证据。

### P0-A02：修复中删掉需求相关测试，而用总通过数代替覆盖（已发生）

数据库保留的 AuthControllerTest.java 四次写入（sequence 42、65、67、73）显示最后版本删去 7 个方法，没有新增替代方法：

`login_after_cooling_expired`、`login_origin_rejected`、`login_rate_limited`、`logout_origin_rejected`、`logout_success`、`me_no_origin_check`、`register_origin_rejected`。

Maven 全套测试从 **55/9 失败 → 50/5 → 48/4 → 48/1**，下降既包含实现修复，也包含测试删除。至少 Origin 拒绝、退出成功、GET 不校验来源等对应用户明确需求，不能只把失败数减少解读为修复进展。是否有其它文件提供等价覆盖需按场景证据逐一证明，当前记录没有这种替代说明。

requirements.md 和 Task readiness 写成“后端测试覆盖率 100%”。用户要求的是覆盖指定接口/来源/限流场景，没有要求 100% 行覆盖率；此表述含混，不能据此新增覆盖率产品要求，也不能据此宣称有覆盖测量。

这是产物质量问题暴露出的框架验收缺口；不能给 DevWerk 编写用户名/401/Origin 的全局硬编码规则。应从当前 Requirement 的场景合同和现有审查列约束本次项目。

### P1-E01：Windows 命令解析不支持当前冻结的 bare mvn（已发生且阻塞后端验收）

sequence 46 以及 Runtime acceptance 53、57 中，裸 `mvn` 均在 WindowsJob 的 Python launcher 内得到 WinError 2。随后通过 `powershell -Command mvn ...` 可以实际运行 Maven，说明“环境完全没有 Maven”的模型诊断不成立。

`windows_job.py:50` 保持 argv，内部 subprocess.call 使用 shell=False；它没有将 Windows 的 .cmd/.bat shim 解析成可执行启动方式。问题表现符合 shell 的 PATH/PATHEXT 解析与 CreateProcess 的差别。不能由此断言所有 Windows 机器均失败，但当前冻结命令在本环境确实失败；同计划的裸 npm 也需要一并验证，尚未执行到，不能记为已失败。

只有让模型临时改用 PowerShell 不够：Runtime 完成检查仍按冻结的裸 mvn 执行。还需保留 Job Object、取消、超时、进程树清理和真实退出码语义。

### P1-P01：规划契约逐项报错导致整份 Workflow 反复生成（已发生）

启动回合 17 次工具调用中 6 次失败：loop.apply 缺 product_name；task.plan.save 缺后端行为检查；workflow.publish 的 check 缺 arguments；accept 缺有效 obligation 连续发生两次；整份重建时又把 domain_design 写成 domain_desategy。

每次 Workflow publish 参数约 14K 字符、成功回执约 16K 字符；启动回合输出 27,590 tokens，recorded total 126,856，缓存输入 714,042。正常 schema 校验拒绝本身不应删除；问题是工具缺少集中诊断与局部实例化入口，迫使模型发现一个错就重发全量，增加无效消耗和意外变更风险。

讨论中还发生一次 draft.update 新问题未给 question 文本，领域设计完成时一次 output JSON 被作为字符串提交。均被现有契约挡住并恢复，列为诊断信息和定向测试，不单独引入新 Agent 或语义分类器。

### P1-F01：同一 Column 内上报反馈后，即使当场修好仍要求新的 visit（潜伏路径已确认）

反馈 `feedback_c0dc0ba65d3a41fc84c527ffdbac7e2c` 在 backend visit sequence 3 内创建，责任列还是 backend，当前 open。它把工具无法启动解释成“本地再测”，并声称代码满足全部需求；反馈正文是模型观察，不能当验收事实。

`task_feedback_repository.py:152` 要求 `run.sequence > reported_sequence` 才能进入 verifying。因此即使当前 visit 后续完成真实修复并取得新证据，也不能结清这条反馈，只能沿已有图找回 backend（存在 backend → requirements → domain_design → backend 的路径），产生多余工作。此次尚未成功提交，没有实际发生这个绕行，应标为潜伏缺陷。

### P2-T01：当前 401/429 是真实边界语义冲突，不是必须停止的原因

最新失败：`AuthControllerTest$LoginTests.login_cooling_after_3_wrong` 预期前三次失败均 401，第四次冷却拒绝 429；AuthServiceImpl 在第三次 recordFailedLogin 返回 coolingStarted 时立即抛 429。测试已在 BeforeEach 清理限流仓库，最后修改只是再次增加清理，不能解决此边界冲突。

应由当前 Worker 对照已确认的限流规则、API contract 和测试场景统一第三次/第四次的语义；若原约定确实未明确，只针对该边界补足设计，不放宽其它已确认要求。无须因这个正常可修复测试错误重建整个项目。

### P2-E02：路径、错误编码与阶段声明

- backend sequence 45 给 project.command.run 传入绝对 cwd，被相对路径契约拒绝；这次正确标记 rejected_before_effect，没有再次落入 unknown effect。优先改进 schema 描述和反馈，不放宽项目路径边界。
- Windows 启动错误的 stderr 被 UTF-8 replacement 解码，中文乱码；结构化 launch error 应保留 WinError、命令解析结果，减少误诊。
- backend-development.md 在第一次测试没真正启动后已写“实现完成”，两次提交 backend_ready 被拒绝。局部文档必须区分实现、已验证、未验证；实际状态以 Runtime 证据为准。

## 5. 修改目标和边界

保持现有架构：Project Conversation Agent 是项目主 Agent；Column 是单 Worker 工作边界；同一 Worker/Session 可以持续交互；Runtime 管理生命周期及确定性证据。压缩不创建新主 Agent、不在 Column 中组织多 Agent loop、不触发 Mailbox 规划。

只新增本次故障所需的内部上下文管理、命令适配和验收契约能力。不给 Web 新增模式、方案、token 调试面板或操作步骤；详细预算、压缩信息写日志和证据表。全部改动先按本文 review，再实现、再测试。

## 6. 上下文生命周期设计

### 6.1 持久原件、模型投影和运行状态分开

保留 `v1_tool_invocations`、execution_receipts、agent_operations 和原始消息作为审计事实。新增统一 ContextProjector/ContextManager（内部服务）：模型只接收有界投影，证据判定仍读取原始结果及绑定 ID。

命令投影固定保留 capability、execution key、tool_call_id、ok、真实 exit code、超时/执行截断标记、stdout/stderr 原始尺寸、内容 hash 和原文引用。正文以有界头尾片段为基础；JUnit/Maven/pytest 等专用提取器可作为增强，不以“看见 ERROR 字样”替代退出码，更不凭摘要推断通过。

新增/复用受项目和 Run 绑定的原始结果读取能力，支持 offset/limit、字段或行范围；单次读取仍受预算限制，不能通过读取原件把全文又塞回来。已有 agent.context.read 同样改成有界分页。数据库原件已有的范围优先复用，不另外复制全部日志。

**建议起点（可经回放校准）：** 单工具模型可见投影最多 4,000 估算 tokens；stdout/stderr 共用这一预算，异常 message 也计入，避免 stderr 在 error.message 中复制一遍。文件读取、大对象及批量列表使用同一总预算，不能只处理 command。

工具参数中的完整源码旧版本也需归档。保留最近完整调用批次；更早批次整体替换为事实摘要/源引用，不能截断仍处于 native replay 的 tool_calls.arguments 造成假调用或非法 JSON。当前待执行/未确认的调用与回执始终保留精确原件。

### 6.2 每次 Provider 请求前检查，而不是每个 Column 开始时检查

统一请求入口在最终角色投影、工具 schema 转换后计算：system + user + assistant/tool history + tools +图片/多模态等 Provider 开销（本次为纯文本）。Conversation 和 Column 都经过该入口，首次请求和恢复请求也必须覆盖。

ProviderModelLimits 至少包含 endpoint/provider/model、context_window、max_output_tokens、计数方式及保守裕量。来自实际配置/Provider 能力，不用模型自己猜；未知模型不得套 MiniMax 的窗口。

计数优先级：官方兼容 tokenizer/count 接口（若具备且可用）→ 已知模型的本地 tokenizer → 保守估算。估算需要用实际 response.usage 校准，并区分 Anthropic 缓存外输入与 OpenAI prompt 中已含缓存的情况，防止少算或双算。原有 `total_tokens` 报表历史不改写；增加独立 `context_input_tokens`/estimate source。

建议预算：`usable_input = window - effective_output_reserve - safety_margin`；默认 soft trigger 为 usable_input 的 80%，压缩目标为 55%—60%，硬阈值为 usable_input。max_tokens 不再硬编码至少 65,535；是否保留当前输出上限可配置，必须一起参与预算。本例按 204,800 窗口、65,535 输出储备、约 5% 窗口裕量计算，可用输入约 129K，soft trigger 约 103K，显著早于 188K。此数是拟议的保守策略，不是对 Provider 内部算法的断言。

达到 soft trigger：先做确定性投影/旧结果去重；仍超目标再产生摘要。超硬阈值不得原样发出。所有新增消息、schema 变化和压缩后的文本都重新计量，不能只依赖上一轮 usage。

### 6.3 压缩是同一个 Worker 的内部维护步骤

维护两个层次：

1. 宿主固定重建的事实锚点：当前用户/Requirement revision、Task、Column、Assignment generation、冻结输入和完成合同、当前 feedback、待执行 operations、最新有效验收证据及来源。
2. 工作记忆摘要：已完成改动、当前假设与反证、仍失败的场景、下一步、相关文件/hash及原日志引用。不把模型自述“完成”提升为事实。

使用同一 Agent 的工具禁用摘要请求，作为内部、无副作用、有限预算的维护调用，不注册新的 Worker/Assignment。先压缩输入再调用摘要模型，不能把已经溢出的整段历史交给同一窗口去总结。按有界旧批次增量合并，保留最近完整工具批次。

摘要失败时仍可使用确定性事实锚点、旧批次引用和短尾继续；只有必要固定内容本身超硬预算时才明确阻塞。摘要只是参考，不修改原始记录、Requirement、授权范围、失败账本或文件。

扩展现有 context snapshot 的覆盖信息：project/session/assignment/run、through_message_id、完整批次边界、source hash、summary schema/version、生成方式、预算前后计量。Assignment 级摘要只用于该 Assignment 的继续执行；后续 Assignment 继续使用带来源的参考，不能原生回放前次“已完成”轨迹。

活跃 Worker 可在一个工具批次全部已持久化后创建摘要。提交使用现有 ownership/generation fence 和 snapshot revision CAS；事务外生成、事务内验真保存，不长时间占 DB 写锁。旧 Worker、取消后迟到摘要不得覆盖新状态。

压缩不释放 Assignment、不推进 Column、不重置工具次数/墙钟预算、不清除不确定副作用。重启后从 snapshot + 完整短尾恢复，不能重新加载整个旧历史。原始存档不删除。

### 6.4 溢出兜底与有限重试

在 Provider 错误解析中先按 provider_error_type / 明确的 context overflow 消息分类，再处理通用 2013。保留 HTTP/provider code 和原文，新增 `LLM_CONTEXT_WINDOW_EXCEEDED`；额度、认证、普通 bad request 不进入该分支。

Runner 捕获该专属错误后，在无新工具副作用的模型调用边界，执行一次更强的投影/压缩，并在**请求 hash 和估算尺寸确实下降**后重试一次。预算计数不能归零。没有可缩减内容、没有有效缩减或再次超限时，以清楚的 context_exhausted 原因暂停，不无限重试，不交给 Mailbox。

这与压缩摘要自身的失败处理分开：一次维护摘要最多一次常规尝试，失败走确定性回退；不递归调用压缩服务。需要证明一轮自救不会产生多 Agent 调度或重复执行命令。

## 7. 验收、命令与返修方案

### 7.1 先修当前已知无效检查，再加强通用合同

修正软件 Loop 的验收实例化指导和集中校验：

- reports 目录使用列表/具体报告路径或报告解析器；文件存在检查标为 artifact，不能独自满足 behavior obligation。
- HTTP 探测脚本显式断言预期状态、必要响应与退出码；在服务未就绪时失败，不能将 curl 的传输成功当业务正确。
- 最终验收依赖所有必需场景的有效证据及交付脚本结果；报告文件本身只是一份 artifact。
- 行为验证声明包含 Requirement 场景 ID、断言目标、测试入口及结构化报告。Runtime 验证来源/版本/检查绑定、执行 exit code、报告场景结果；已有审查列核对测试的实际语义。

不能通过命令关键词黑名单宣称可证明任意脚本语义。即使结构化报告都为真，脚本仍可能测试错对象，必须由既有独立审查/验收列检查实现和证据。失败用已声明 rework 边和 Task feedback，仍然一列一个 Worker。

### 7.2 防止返修悄悄缩小覆盖

本项目从用户基线冻结必须覆盖的业务场景，而不是冻结整个测试源文件。测试可以重构/替换，但移除原测试时必须能映射到等价的新场景证据；不能靠删除失败测试满足 done。

在实现列交付内容中保留测试变更及场景映射引用；integration_review/system_test 按冻结场景逐项复核缺失项，并记录 feedback。Runtime 对缺失、未执行、skipped 场景拒绝完成；不把测试个数相等或“覆盖率 100%”当作等价覆盖。不要全局要求 100% 代码覆盖率。

### 7.3 Windows 工具启动

在进程启动前解析可执行文件和 PATHEXT，记录 resolved executable；.exe 保持 argv，.cmd/.bat 使用有明确参数转义规则的 Windows 启动适配，保留 Job Object 包裹与真实 exit code。不要把所有命令拼接后改成 shell=True。

测试包括带空格路径、中文、引号、`&` 等参数、mvn/npm shim、不存在的命令、子进程 cleanup。启动失败在 preflight 或 launcher 结构化通道返回 `CommandNotFound/CommandLaunchFailed`，避免把 launcher traceback 当作测试失败。

为冻结计划编译提供非执行的命令解析检查；未来才生成的项目脚本允许 declared deferred validation，但进入对应 Column 时必须验证。不能在任务建立阶段执行未来测试，也不能因为文件还没生成就永久拒绝整个工作流。

### 7.4 降低规划重复消耗

复用现有 TaskPlan compiler，增加无副作用的集中验证结果，返回全部错误的 JSON Pointer、期望类型、合法引用及缺失 obligation 列表。`loop.apply` 的参数 schema 在使用前可查询，不让模型先猜 product_name。

在模板实例化边界提交“模板 revision/hash + bindings + 各列验收定义”的局部数据，由宿主合成并一次验证；保留通用自定义 Workflow 能力，不将所有任务固定为软件模板。完整定义继续存档，成功回执返回 ID/hash、差异摘要和明细读取入口，避免每次回传整份 16K JSON。

与用户确认的业务语义仍由原 Conversation Agent 处理；集中校验只检查已声明契约，不新增分类 Agent、不自动纠正 domain_desategy 这类可能有歧义的引用。

### 7.5 同一 visit 的反馈结清

新增反馈观测边界（source operation/message sequence 或持久 event ID），复验收据关联该边界。责任列在同一 Run 内处理反馈后，只要产生边界之后、同 revision/当前 Attempt 的有效验证，允许直接 verifying/resolved；跨列反馈仍要求责任 Worker 的真实处理及后续证据。

不能只比较粗略时间戳或直接删掉 `sequence > reported_sequence`：旧检查、旧 Attempt、其它列或其它文件版本的收据仍必须拒绝；复验后再发生相关代码修改应重新失效。摘要不能将反馈标 resolved。

## 8. 参考本机 Hermes/Codex 的有限借鉴

只借鉴上下文生命周期机制，不照搬 UI 或引入新的主 Agent：

- `D:/workspace/github/hermes-agent/agent/turn_context.py:1054`：请求前判断压缩阈值，结合估算及实际 Provider usage，具有防反复压缩的限制。
- `D:/workspace/github/hermes-agent/agent/context_compressor.py:3929`：旧工具结果压缩、重复结果去重、旧的大 tool-call 参数处理，并保护最近上下文；说明只裁剪 stdout 不足以解决长任务历史。
- `D:/workspace/github/codex/codex-rs/core/src/compact.rs:315`：区分 ContextWindowExceeded，在摘要调用自身溢出时继续收缩旧历史，而不是原样循环。
- `D:/workspace/github/codex/codex-rs/core/src/compact_token_budget.rs:47`：上下文窗口更新是一种明确的 session 生命周期操作，可以有确定性路径，不必创建另一个业务 Agent。

这些是本地源码观察，不意味着 DevWerk 应复制其全部策略、前端状态或执行权限。

## 9. 文件范围与实施顺序

| 批次 | 主要文件/模块 | 完成条件 |
| --- | --- | --- |
| A：直接阻塞 | policy、agent_provider、agent_runner、agent_tool_execution、capabilities、provider_errors、内部 ContextManager | 有界投影、每轮预算、专属错误恢复；长运行不溢出且不重复副作用 |
| B：生命周期 | agent_repository、agent_run_preparation、session_replay | Assignment snapshot、完整批次、重启/取消 fencing；恢复不丢关键问题 |
| C：实际验收 | process_runner/windows_job、task_plan_compiler、domain、软件 Loop、completion_admission、task_feedback_repository | 命令可启动、检查验证行为、同 visit 反馈可结清、覆盖不缩水 |
| D：消耗与验证 | 现有规划工具的集中诊断、相关回归测试、隔离实测脚本与证据 | 降低 Workflow 全量重发，完成全链路失败注入和真实软件回归 |

优先交付 A+B，随后 C，D 贯穿。不得只完成 A 的字符串截断就宣布本项目问题全部解决。C 中产品测试边界由 DevWerk 当前 Worker 处理；本助手不直接改用户生成项目来伪造框架交付成功。

## 10. 回归与验收矩阵

| 测试 | 必须证明 |
| --- | --- |
| 历史故障回放 | 用本次匿名化尺寸分布/日志 fixture 回放至少 36 轮、70 次工具；每次请求全包不超过预算 |
| 多来源膨胀 | 110KB 命令结果、连续整文件写入、超大读取、list/schema 增长均受控，不只 stdout |
| 高输出成功进程 | 模型投影限长不杀进程；原始输出仍可查，exit code 保持真实 |
| Token 口径 | Anthropic cache input 与 OpenAI inclusive prompt 分开归一；188,549 示例不能算成 3,530 |
| 完整工具批次 | 批量 tool_calls 成对保留；未完成 operation 不压缩、不重执行；无 orphan result |
| 活跃压缩/重启 | 当前 Assignment 可创建/加载摘要，任务/Worker/Session 不变；重启继续知道 401/429 边界和未验收项 |
| 取消/抢占 | 摘要生成期间 owner 被替换，旧 snapshot 不提交，无迟到业务动作 |
| 有界溢出恢复 | 第一次 Provider overflow 后请求确实变小，只重试一次；普通 2013、额度耗尽不走压缩 |
| 摘要失败 | 有界确定性回退；固定输入本身过大则明确阻塞，无递归压缩死循环 |
| 验收真值 | curl 返回 200、报告仅存在、目录当文件、脚本未执行测试、scenarios skipped 均不被当作完整成功 |
| Windows 启动 | mvn/npm shim、带空格与特殊字符参数、缺工具、取消和 finally 进程树清理 |
| 覆盖保持 | 删除 Origin/退出/只读来源场景无等价替代时验收失败；合法重构有映射可通过 |
| Feedback 生命周期 | 同 visit 修复能结清，旧证据/旧 Attempt 不能结清；跨列复验仍有效 |
| 真实软件回归 | 原普通用户交互 → 规划 → 后端 → 前端 → 审查 → 真浏览器/HTTP → 脚本清理 → 验收，再做一次用户返修 |

实际指标：最大输入窗口占用、投影前后字符/估算 token、压缩次数及摘要调用 token、失败重试次数、真实工具执行次数、原始日志可检索性、覆盖场景数和 terminal evidence。缓存与非缓存输入分别统计；不把反事实字符减少直接换算成节省金额。

最后运行完整既有回归。长场景、失败恢复和真实软件回归必须另记，不能用“小 fixture 成功”或“首 Column 成功”替代。此轮仅设计，以上测试尚未运行，不提供虚构通过结果。

## 11. 现有阻塞项目的恢复方案（待实施后执行）

1. 保留当前暂停和全套原始证据，先在隔离 DB/目录验证上下文回放与命令适配，不让生产重试消耗 token。
2. 新 ContextManager 上线后，旧 Session 从 DB 原件建立 Assignment 级摘要和有界短尾，保留已写文件引用、失败测试、未结清 feedback 和冻结验收义务。
3. 仅从上下文角度恢复仍不够：revision 4 的目录读取、curl、最终报告存在检查需要修正。不能直接 UPDATE 旧冻结 Workflow。
4. 最小范围优先复用已有“显式取消旧 Task → corrected TaskPlan/Workflow → task.successor”的路径，保留前驱关系和产物复用；这属于后续实际恢复操作，本次不执行。若要同一个 Task 更换冻结合同，需要另行设计迁移，不能趁本修复暗中加入。
5. 后继不得丢掉原 feedback 的检查义务；报告目录读取可换成真实报告解析并保留义务标识，源拒绝必须断言 403。Worker 先检查现有产物与失败原因，补回被删场景，再执行真实验证，避免重复从零生成整套代码。
6. Runtime 只有在所有声明场景、有效收据、完整脚本及清理行为通过后才能 done。Mailbox 只发送恢复/完成/阻塞通知。

## 12. 本次审计的边界

已经核实：生产运行事实、原始 Provider 错误、持久工具和验收回执、源码路径、冻结 Workflow、测试删减、消费统计及只读长度投影。

尚未核实：后续前端/浏览器/完整脚本真实运行、修复后的压缩效果、生产续跑成功。未把这些写成已通过；本次没有修改应用代码、运行数据或用户项目产物。
