# 自然语言看板交付：主 Agent 规划停滞修复方案

## 黑盒证据

全新 Project `prj_0a015cf429524ea6a73af2433131d3d8`。五轮自然语言讨论正常，第六轮仅授权交付目标，不指定 Loop、列数、Task 数或工具顺序。Conversation Agent 自行 `loop.inspect`/`loop.apply` 发布 `software.ddd_delivery` 八列 Workflow。它先直接写了约 24 个项目源码/验收文件，再反复创建 Task Plan；曾成功保存 `tplan_237b9ef4b7924ab89f56593aea60539a`，但没有调用 `task.create`。最终 Job 在 82 次模型迭代、176 次工具调用后以 `ConversationProtocolStalled` 结束，Task 数始终为 0。

独立证据：`loop.meta` 的 Loop Input 已明确“每个 Task 是完整交付范围，不是 Column 阶段”，但 `context_manager.planning_projection(loop.inspect)` 只保留参数 schema、task_contract、列名，丢弃 `flow_unit` 与 Loop Input/Use Case；因此主 Agent 看不到模板作者最重要的拆分指导。`task.plan.validate/save` 多次收到 JSON 字符串形式的 `plan`，在业务校验前被 object schema 拒绝。文件证据错误只在最近 20 条写回执中查找匹配路径，而且错误未突出 Task 输入中的目标路径；大量文件写入后候选会空白。主 Agent 对“Worker 交付”与“自己直接写文件”的边界仍未遵守。

## 修改范围

1. `app/v1/context_manager.py`：`loop.inspect` 投影保留外部 Loop 卡的 `use_case`、`selection_guide`、`input`、`output` 以及 Workflow Plan 的 `flow_unit`。`loop.apply` 投影保留同一 `flow_unit`，使模板定义的工作单元可见。把硬编码“测试脚本”指导改成通用的执行证据说明。核心不引入任何软件/小说工作流内容。
2. `app/v1/conversation_intent.py`、`DEVWERK.md`、`app/v1/capabilities.py`：在通用 Agent 协议中明确：若当前用户要求 Workflow/Kanban 交付，先创建 Workflow 和 Task，再由 Column Worker 产出交付物；主 Agent 只准备 Tool Contract 要求的输入/证据，不抢做 Worker 工作。`task.plan.save` 成功后优先用返回的 plan ID 调 `task.create`；Task 是独立可验收的完整 flow unit，不是列阶段。保持主 Agent 的通用文件工具权限，不做权限层级化。
3. `app/v1/capabilities.py`：仅对 `task.plan.validate/save` 的 `plan` 字段做严格 JSON-object 字符串传输归一化，再走原 schema/业务校验；拒绝数组、标量、坏 JSON。这样适应 Provider 的对象字段序列化，不弱化合同。
4. `app/v1/services/task_entry_admission.py`：按 Task 输入目标路径查找匹配的已完成 `project.files.write` 回执（最多返回 5 个），不先截最近 20 条；错误同时说明要求的精确路径。保留现有 Grant、来源与内容哈希校验。
5. `app/v1/capabilities.py` 的可选 `workflow.acceptance.runners` 描述只说明通用验证 Runner 与报告合同，不写“测试脚本”式软件领域引导。

## 验证

- 单测 Loop 投影包含外部 flow_unit/卡片输入，且没有核心软件指导。
- 单测 JSON 字符串 Plan 合法时走原校验，非法输入仍拒绝。
- 单测超过 20 次其他文件写入后，候选仍能找到精确路径回执；错路径错误明确指出输入路径。
- 运行相关合同与 ConversationGateway 测试，然后必要地重启 DevWerk，新建 Project，以同一登录防刷目标完成至少五轮自然语言讨论，再检查自主选 Workflow、创建 Task、自动 Column 推进与真实编译验收。旧失败 Project 不作为重测对象。

## 实施与回归记录

已按上述五处修改，保留主 Agent 通用文件工具权限与原有 Workflow 状态机。`task.plan.save` 成功回执增加可直接调用的 `task.create` 参数；Loop 投影保留外部卡片的 `flow_unit` 与输入说明。文件证据缺失时按精确 Task 输入路径检索回执，不再受最近 20 次写入限制。

定向测试 3 passed；相关合同、Loop、Task 入口测试 141 passed；ConversationGateway 完整测试 28 passed。代码重新加载后，新黑盒 Project `prj_e79ba8251461427580eda7d6d6032715` 已启动第一轮纯自然语言讨论。后续轮次与真实交付状态待补。

新黑盒 Project 五轮自然语言讨论均成功且未建 Task；第六轮仅授权交付目标与真实编译/重启验收，没有指定 Loop、列、Task 数或 API 顺序。执行 Job：`cjob_3fdb521d85a64195b092e0f050651348`。后续按约五分钟检查 Conversation Agent 规划和看板推进。

## 第二次黑盒失败与下一步最小修复方案

Project `prj_e79ba8251461427580eda7d6d6032715` 第六轮在 12 次迭代、12 次工具调用后以重复失败的 `task.plan.save` 终止，Task 数为 0。上一轮“主 Agent 先写源码”已消失：它先保存自建 Workflow Plan，再发布三个 Column。新的阻塞点是自建 Task Contract 声明了两个 `accepted_scope_snapshot` 入口要求，其中第二条以 memory ID 为 `scope_evidence_key`；当前 Domain schema 接受这种无意义组合，但执行时 `accepted_scope_snapshot` 只允许当前 Requirement 的执行 Grant ID。Agent 将 memory ID 当入口证据，反复收到 `EntryEvidenceScopeMismatch`，最后触发重复操作保护。

下一步仅改通用合同：`app/v1/domain.py` 的 `TaskEntryRequirement` 对 `accepted_scope_snapshot` 禁止 `input_pointer`/`scope_evidence_key`；`TaskContract.entry_evidence_references` 最多允许一条该类型要求，因为一个 Task 只绑定当前 Requirement revision 的一个 Grant。`app/v1/capabilities.py` 的 `workflow.plan.save`/`task.plan.contract` 描述说明入口要求是可选的，`accepted_scope_snapshot` 值必须是 execution grant ID，memory 记录不是该类型。这样错误在保存 Workflow Plan 前给出，而不是等到 Task Plan 才循环。补 Domain 合同单测和相关回归；代码生效后再次新建独立 Project 测试，不修复失败旧 Project。

## 第二次最小修复结果

已落实上述 Domain 合同和工具描述修改。`accepted_scope_snapshot` 不再接受文件指针或 `scope_evidence_key`，一个 Task Contract 最多引用一次该类入口证据。新增合同单测覆盖 memory ID 冒充 Grant、重复 Grant 要求及合法的 Grant + 输入文件组合。相关合同与入口回归共 155 passed，ConversationGateway 完整协议测试 28 passed。下一步重新加载服务，并使用全新 Project 验证实际对话、建板、建 Task 和交付。

## 第三次全新 Project 黑盒验证（进行中）

重新加载服务后创建 Project `prj_f46471eff6af48a5b61b66908222b4bf`。完成五轮自然语言讨论，确认 Vue/Java、三次错误锁定十分钟、H2 持久化、锁定中拒绝且不延时、重启保留、到期恢复、无 token/会话/注册；讨论阶段未建任务。第六轮仅授权目标、由 Agent 自主规划看板并要求真实编译和 HTTP 验收，没有指定 Loop、Column 数、Task 数或工具顺序。执行 Job `cjob_7a16fc0edd17487aa75a83fe5e4355a1` 成功，发布 Workflow `wfrev_299c128766c94cdb89bdad23a63ac378`，创建 Task `tsk_8c02351cb2e047b1a6a02558ac645a5f`，首个约五分钟检查点显示 Task 在 `backend_development` 运行。后续列间推进和交付结果待检查。

## 第三次黑盒：完成协议回执定位缺口

`backend_development` 首次 Worker 运行 `arun_75716e37a98d4570b456158ab6e0c49e` 已写源码并成功执行替代的后端测试命令，但最初在 Project 根目录执行的 `mvn clean test` 失败仍留在执行账本。`column.complete` 四次被拒；错误只给失败命令参数，没有给失败证据 ID。Worker 随后填写了不存在的 `failed_evidence_id`，引发 `unknown_failed_evidence`，最终以 `CompletionProtocolStalled` 失败。看板自动启动了第二次 Worker，Task 当时仍处于运行状态。

这是通用完成协议的信息缺口：Runtime 要求 Agent 在 `failure_resolutions` 精确引用失败证据，却在最近的拒绝消息里省略该 ID；长会话压缩后，Agent 无法可靠找回较早工具回执。最小修复限定于 `app/v1/services/completion_admission.py` 的 `unresolved_execution_failure` 拒绝消息：带出账本中当前未解决失败的**准确 `evidence_id`**，并保留原有命令与修复选项。只提供定位，不自动认定不同命令语义等价，也不调整 Workflow 状态机。补单测断言提示的 ID 与真实失败回执一致；保持已有替代修复仍需后续同能力成功回执和具体理由。代码生效后用独立 Project 复测。

第二次 Worker 也执行了成功的替代命令，但四次 `column.complete` 仍因同一旧命令失败被拒，最终同样触发 `CompletionProtocolStalled`；Task 进入 `recovering/paused/blocked_runtime`。这证明自动恢复无法单靠重新运行消除此信息缺口。已在拒绝消息加入精确 `failed_evidence_id`，相关完成协议测试 27 passed。第三次 Project 的“建板并创建 Task”门槛通过，但任务交付未通过；该 Project 不作为下一次重测对象。

## 第四次全新 Project 黑盒验证（进行中）

服务重新加载后创建 Project `prj_5652ab09beab4110a4538b5869aff57a`，完成五轮自然语言需求讨论，讨论阶段未建 Task。第六轮只授权目标及真实编译、HTTP、重启保留验收；Conversation Job `cjob_903fbd7d3f24489fb4595338ec2f8f85` 成功，发布 Workflow `wfrev_c5517771eb974c39947b748f42b6ae72`，创建 Task `tsk_be9f1d9e45d24296a15cdb5e97a955e4`。首个约五分钟检查点为 `backend_development/running`。后续完成协议与交付结果待验。

后端 Worker `arun_3bfc391577f140098a216da403eb5a90` 的 `column.complete` 曾因早先命令超时被拒；修复后的错误准确给出 `failed_evidence_id`。Worker 将两条失败命令与之后成功的 HTTP 场景测试回执关联，给出具体原因；一次错误地把成功回执当作失败回执也被 Runtime 拒绝，去除后 `column.complete` 成功。Task 已自动进入 `frontend_development`。这是本次完成协议修复在真实流程中的正向验证，最终交付仍待验。

## 第四次黑盒：跨列反馈误判为当前列无法继续

Task 顺利经过前端与集成审查，`system_test` 的七场景脚本实际执行后报告 S7 重启持久化失败；系统测试误判为后端产品缺陷，沿冻结边 `system_test -> integration_review -> backend_development` 返工。后端 Worker 检查源码和测试脚本后定位真正原因：验收脚本 `reset_via_restart()` 重启时调用 `reset_account()`，把锁定数据清零；后端源码和构建无此缺陷。它将测试脚本问题记录为 `system_test` 责任反馈，但由于当前列无权写 `scripts/acceptance/**`，选择了 `execution_failed -> failed`，Task 未交付。

冻结 Workflow 从 `backend_development` 经 `backend_ready -> frontend_development -> integration_review -> system_test` 存在前向可达路径。`task.feedback.record` 工具描述却笼统要求“当前列提交 rework outcome”，没有说明责任列在下游、当前列工作已验证时应沿现有**前向**边交付，而不是把别列写权限当成当前列执行失败。最小修复：仅修改 `task.feedback.record` 的通用说明，以及外部 `software.ddd_delivery` Loop 的后端和系统测试列文字。后端说明有其他列责任反馈时，不越权修改文件，若本列验收已满足则 `backend_ready` 沿冻结图继续；系统测试说明在归咎产品前核实测试脚本和复现路径。保持现有边、权限和状态机原样，不添加新的自动路由。补 Loop/工具合同测试，重新创建独立 Project 做完整黑盒回归。

已按本节实施：核心仅改通用反馈工具说明；软件场景建议留在外部 Loop，版本由 1.4.4 增至 1.4.5。Workflow 图、状态与权限没有变化。Loop 与 Capability 合同回归 55 passed。第四次 Project 的 Workflow 能发现真实脚本失败并按图返工，但无法完成跨列归因后的前向交接，因此最终 `failed`；下一次只用新 Project 复测。

## 第五次全新 Project 黑盒验证（进行中）

Project `prj_637d8b7e6c33432a8790f1e7af751d6d` 完成七轮自然语言讨论，讨论阶段未建 Task。额外两轮用于纠正 Agent 对重启验收链中清理持久化数据的自相矛盾描述；最终明确只有链开始前可初始化，锁定、重启保留、到期恢复之间不得清理或重置。授权 Job `cjob_708b247be57e4bed8ba8d588beb1123e` 成功，发布 Workflow `wfrev_b6ca854d03cb4172a4f3cf01c4b28fb5`，创建 Task `tsk_c0fc85f54edd4e23a9fd56814b1ea1fc`。首个约五分钟检查点为 `backend_development/running`，后续交付待验。

## 第五次黑盒：后端列越界执行全量验收

后端列 Worker `arun_e3a6335026ef47b48614340614d88490` 执行 115 次工具调用后累计预算耗尽，续任 Worker `arun_021c0e2f8cbb44c884ea020c5db22e9a` 执行 86 次工具调用后时长预算耗尽；二者均没有尝试过 `column.complete`。第三次 Worker 继续反复运行完整 HTTP/重启验收脚本。Task 未终止，但无法从 `backend_development` 推进。多次命令和脚本修改都围绕完整验收链，而冻结 Workflow 的 `system_test` 列才负责独立完整行为测试；当前外部 Loop 后端列文字“真实开发测试命令/测试通过”没有把这条职责边界说清，造成巨大工具与模型预算浪费。

下一步只改外部 `software.ddd_delivery` Loop：后端列的完成条件明确为后端编译、单元或定向行为测试有真实成功回执；完整 HTTP、重启和到期验收属于 `system_test`，不应在后端列反复调试整条链或等待系统测试结论。保留必要的定向在线验证能力，不改通用 Agent、Runtime、状态机或权限。Loop 版本增量更新，并补合同断言。该 Project 已经过两次自动预算恢复且第三次仍无完成尝试，作为阻塞样本记录；新版本以独立 Project 重测。

已将上述职责边界写入外部 Loop 后端列，版本增至 1.4.6；Loop 合同测试 19 passed。第五次 Project 的旧 Task 已请求暂停，避免与新 Project 并行消耗模型和占用演示端口；它的完成结果不作为新版本验收。服务已因模板变更重新加载。

## 第六次全新 Project 黑盒验证（进行中）

Project `prj_e1137567a57447f0ac15f7c8fc936636` 完成六轮自然语言讨论，讨论阶段未建 Task；其中纠正了 Agent 口头方案中混乱的 API 示例与 `failedAttempts`/`remainingAttempts` 语义。授权 Job `cjob_bee0919af14b4037ae3be20a2ed59862` 成功，发布 Workflow `wfrev_242ba8e095b64bcc8e88a47e1415dee7`，只创建一个完整交付 Task `tsk_5c6b51aa2dbd4a9886f91e03cf4d0192`，而不是按口头提案拆成阶段 Task。首个约五分钟检查点在 `backend_development/running`。后续列职责与交付待验。

下一检查点后端已通过 Maven 定向测试/构建并完成 Column，Task 自动进入 `frontend_development`。与第五次 Project 在后端反复执行整条 HTTP 验收链、连续耗尽预算相比，新版 Loop 的列职责边界在这一阶段已生效；最终验收仍待验证。

## 第六次黑盒：集成审查列越界做在线服务测试

Task 完成后端、前端，集成审查发现 API 字段命名不一致，沿冻结边返回后端；修正后再次进入集成审查。该列已取得后端编译、单元测试和前端构建的成功回执，但又尝试用 `cmd /c` 前台运行 `mvn spring-boot:run` 并串接 curl，命令超时。完成协议要求修复超时回执是合理的；Worker 随后错误地引用失败回执作为成功修复，触发 `CompletionProtocolStalled`，Task 进入 `recovering/paused/blocked_runtime`。本列 `writable_paths` 只允许 `docs/pair-review.md`，无法编写稳定的在线验收入口；而冻结 Workflow 的 `system_test` 专门拥有 `scripts/acceptance/**` 并负责完整 HTTP 测试。

下一步仅收紧外部 Loop `integration_review` 列说明：职责为审查前后端代码、API 契约及已有运行证据，可运行编译/单元/构建检查；若没有现成的有界在线入口，不自行尝试前台启动服务，不把系统测试列的完整 HTTP 场景作为本列通过前提。保留原有缺陷反馈与返工边；`system_test` 仍负责真实完整行为验收。Loop 增量版本与合同测试后，在全新 Project 复测；不修改核心状态机或自动豁免失败回执。

已将该边界写入外部 Loop，版本增至 1.4.7；Loop 合同测试 19 passed。第六次 Project 的建板建 Task、后端到前端及一次有证据返工均正常，但集成审查超时导致 `blocked_runtime`，不作为交付成功样本。

## 第七次全新 Project 黑盒验证（进行中）

Project `prj_49c386302b3c43729260aa1a22ad81a3` 完成五轮自然语言需求讨论，讨论阶段未建 Task。用户授权消息只指定交付目标与真实编译、联调、HTTP 验收，并说明验收程序由 DevWerk 在实际运行环境执行，用户只经 Web 获取交付。Conversation Job `cjob_d022da266a294047a49c5e07e90e5ec5` 成功，发布 Workflow `wfrev_b2c4fb518fb24b8092d41c6a647d46b6`，创建一个完整交付 Task `tsk_0d3058910e564c139763e5350d5adc2c`。首个约五分钟检查点 Task 已由前两列推进至 `backend_development/running`。后续交付待验。

## 第七次黑盒：说明文档误使前端行为回执失效（修复前方案）

Task 依次完成 `requirements → domain_design → backend_development → frontend_development → integration_review → system_test → delivery`，系统测试列实际执行七场景 PowerShell HTTP 验收，返回 `exit_code=0`、7/7 PASS；后端 Maven 测试和前端 Vite 构建也成功。最终 `accept` Worker 提交验收后，Runtime 三次报 `Required behavior evidence missing: frontend_development`，Task 停于 `recovering/paused/blocked_runtime`。因此本次不能算交付通过。

DB 工具账本显示前端列 `run_da0bacbf6c9a4d4ebd656e1a6aae02b7` 在源码写入后成功执行 `npm --prefix frontend run build`（invocation rowid 4997），之后只写了 `docs/frontend-development.md`（rowid 4999）；该列没有 `runtime.behavior` 回执。`app/v1/repositories/task_feedback_repository.py::_behavior_affecting_write` 目前只豁免文件名含 `report` 的 `.md`，把普通说明文档视作行为改动。`record_column_evidence` 只查最后一次“行为改动”之后的命令，于是漏掉真实成功构建；`apply_completion_evidence` 在最终验收正确拒绝缺失回执。失败源头是文档分类过窄，不是 LLM 虚报测试，也不是路由图错误。后续列没有修改前端源码，构建证明仍有效。

最小修复：仅在 `_behavior_affecting_write` 中把 `docs/` 下的 Markdown 说明和根目录 `README.md` 认作非行为写入；其他路径继续保守判断，尤其源码、脚本及测试输入仍能使回执失效。保持每列证据约束、冻结 Workflow、状态机与最终验收门槛不变。在 `tests/test_worker_selected_acceptance.py` 扩充既有参数化用例：成功命令后写 `docs/frontend-development.md` 仍可生成回执，而后写源码仍不可。跑定向回归后重启服务，以新的 Project、至少五轮自然语言对话重新完成 Workflow/Task/编译/七场景交付测试。

已按方案修改证据分类和既有参数化回归；行为回执用例 16 passed，相关验收/Runtime 回归 84 passed。下一步服务重启后用全新 Project 验证实际交付。

## 第八次全新 Project 黑盒验证（进行中）

服务因证据分类修复重新加载后，新建 Project `prj_0288711c7dfe47fca6d7c0fb11c17891`。五轮自然语言讨论确认登录防刷、重启保留、30 秒测试锁定期、Vue 3/Vite 和真实 HTTP 验收；讨论期间 Task 数始终为 0。第六轮回答 Agent 最后两个问题并授权其自行建板、建任务和交付，Conversation Job `cjob_50dae55b80dc4425b6239edfc4011ab9` 已接受。后续按约五分钟检查点观察建板与 Task 推进；最终必须核对编译与 HTTP 回执，不能只读 Worker 总结。

## 第八次黑盒：后端列越界试运行与逐项披露失败回执（修复前方案）

授权 Job 成功调用 `workflow.*`、`task.*` 内部工具，发布 Workflow `wfrev_41262e4ab5194585a22b2c382c17b056` 并创建 Task `tsk_64ddbf6de0af4ca8822e7c5f5fd77689`。看板依次完成需求和设计列，后端源码写入后 `mvn clean test` 已成功（24 tests）且打包成功。但后端 Worker 又尝试前台 `java -jar`、跨工具保留后台进程、手拼 `curl`、运行尚未创建的前端目录，产生多条失败命令。这里既超出外部 Loop 已规定的后端阶段边界，也引入与编译/单测无关的完成协议债务。五次 `column.complete` 均被拒，Task 停在 `backend_development/recovering/paused/blocked_runtime`。这是第八次测试失败，不是最终交付。

`CompletionAdmissionService.evaluate` 虽正确要求每条失败命令有成功重试或显式修复对应，但当前每次拒绝只暴露**第一条**尚未解决的失败回执。Worker 每修复一条后，下一次提交才看见另一条；`CompletionAttemptGuard` 固定五次拒绝后停止，即使每次都在新披露的失败上取得进展，也可能耗尽次数。本次五次拒绝分别揭示 Maven 断言失败、前台服务超时、错误端口的 curl、PowerShell 启动参数失败等，形成逐项披露与固定上限的协议矛盾。

最小修复分两处，不变动看板状态机和通用权限：

1. `app/v1/services/completion_admission.py` 在首次 `unresolved_execution_failure` 拒绝时同时列出所有当前未解决失败的准确 `evidence_id`（保留第一条的错误和命令上下文，并对列表限长）。这样 Worker 可一次性引用现有成功回执或选择冻结图中的失败/返工 outcome；不自动豁免任何失败。`tests/test_completion_protocol.py` 增加多失败回执断言。
2. 外部 `loops/ddd-software-delivery/loop.json` 的 `backend_development` 指令明确：编译、单元与本列定向测试成功后立即交接，不在本列启动服务、运行完整在线验收或尝试尚未创建的前端；在线 HTTP/重启验收交由 `system_test`。增量 Loop 版本与合同断言。此修复只针对当前模板列边界，不在 DevWerk 核心预置软件业务步骤。

定向回归后仅因代码和 Loop 改动重启服务，再以全新 Project、五轮自然语言讨论复测建板、自动列推进和最终真实交付。

已落地两处最小修复：首次拒绝现在给出当前所有未解决失败回执 ID（最多 16 条并报告总数），不改变任何失败的修复要求；外部软件 Loop 版本 1.4.8 将后端列限于编译、单测/定向验证，服务在线验收归 `system_test`。完成协议与 Loop 合同回归 39 passed。第八次 Task 维持失败样本，不对旧 Project 重试；下一次重新建 Project。

## 第九次全新 Project 黑盒验证（进行中）

Project `prj_0723b2a4c4a34d0a921836e1c063cca9` 完成五轮自然语言需求讨论，讨论期间 Task 数 0。第五轮 Agent 自行提出的草案与已确认边界有三处偏差（锁定时禁用提交、把 H2 称为 SQLite 兼容、Shell 脚本和用户主目录存储不适合当前 Windows/项目隔离），第六轮用户自然语言纠正并授权执行，未替 Agent 指定代码架构。Conversation Job `cjob_cfe3293eaed44b429953ee1b88f5553e` 成功，发布 Workflow `wfrev_a489a51afc2b4838af6b0ba518028aaf`，创建一个交付 Task `tsk_d052b5790aaa4cc685a6a04b48571b7e`。首个五分钟检查点 Task 已从需求列推进到 `domain_design/running`；最终交付待验。

## 第九次黑盒：只读命令失败无法由只读成功命令修复（修复前方案）

后端列已自动完成，Task 进入前端。前端 Worker 的两条 `dir` 命令因 Windows 参数/路径写法失败；随后目录列举与前端构建成功，但 `column.complete` 五次被拒。第一次拒绝已同时给出两条失败回执 ID，证明上次“逐项披露”修复生效。Worker 用后续成功的 `dir` 回执及具体原因填写 `failure_resolutions`，却被 `CompletionAdmissionService.evaluate` 的 `nonbehavior_resolution_evidence` 拒绝。当前实现对**任何**失败的 `project.command.run` 都禁止用“非行为命令”作替代修复，连原失败操作本身就是只读列举也一样。重复尝试后第一名 Worker `CompletionProtocolStalled`，下一名 Worker仍遇同一问题；Task 已请求暂停，不能作为交付成功样本。

这是完成协议分类不对称，不是前端产品失败。最小修复只改 `app/v1/services/completion_admission.py`：当失败命令本身是 `known_nonbehavior_command` 时，允许同 capability、同 effect_kind、时间在后、成功且显式说明原因的非行为替代命令；失败的是构建/测试/行为命令时，继续拒绝以目录列举、清理等非行为命令代替。`tests/test_completion_protocol.py` 补一正一反回归；不改状态机、证据 ID 或验收要求。回归后重启，并用全新 Project 复测。

已按方案修改，正例“失败目录列举→更正参数后成功列举”可完成，反例“失败构建→成功列举”仍被拒；完成协议与行为证据测试 38 passed。第九次 Task 的失败样本已请求暂停，接下来只测试新 Project。

## 第十次全新 Project 黑盒验证（进行中）

Project `prj_f0c96cdd32134932aa0922c38f85cd6c` 五轮自然语言讨论完成，讨论阶段 Task 数 0。第五轮 Agent 提出以 VS Code REST Client 的 `acceptance.http` 作为自动验收，这无法满足“用户只经 Web、Worker 实际运行”的冻结目标；第六轮用户用自然语言纠正为当前 Windows 环境下真实命令执行，并授权 Agent 自主建板和分配 Task。Conversation Job `cjob_8430833121754691b294786689d29447` 成功，发布 Workflow `wfrev_e9820adbde8a470ba59b39231908e9c5`，创建一个 Task `tsk_c703effbfc29441492d70c179cf7b60a`。首个约五分钟检查点仍在 `requirements/running`；继续按间隔观察流程。

第十次 Task 顺利完成后端、前端及集成审查；集成审查发现后端初始化重置持久化数据，沿有向边返后端修复，反馈 resolved 后重回前端、集成和系统测试。系统测试实际执行 PowerShell HTTP 脚本，确认首次启动配置未建表，沿 `system_test → integration_review → backend_development` 反馈返工。这说明本次列间反馈和有向路由有效。后端返工 Worker 编写 `backend/verify-backend.ps1`，多次运行得到前三场景通过、重启/到期场景不稳定；其持续修改脚本与排查端口，超过 75 次工具调用，最终因 `ExecutionBudgetExceeded` 进入 `recovering/paused/blocked_runtime`。`feedback_d66d1567aa254a41840c9b3175134cfc` 要求后端列自身的 `runtime.behavior` 回执，故本次在线验证有真实的本列修复需求；不能简单放宽验收或把构建成功当启动修复完成。

下一步先通过 Web Conversation Agent 发自然语言恢复请求，验证它能否读取失败、决定重试或合理调整 Workflow，并让新的后端 Worker 从已有源码/脚本继续，而不是由测试者直接修交付物。若恢复仍在同一失败上耗尽预算，再基于反馈、命令回执和源码确认 DevWerk 的最小 P0 修复点；本次 Task 尚未交付通过。

Conversation Agent 在 Job `cjob_a3abac59e44d4138872f37ab252169a7` 中实际调用 `task.inspect`、`workflow.inspect` 和 Worker 结果读取，先试 `task.reopen`、`task.resume` 被状态合同拒绝，随后 `task.retry` 成功。新 Worker 完成后端修复并推进前端、集成；系统测试指出 S4 锁定到期仍返回 403，后端依据 `evidence/s4.txt` 发现请求发生在 `15:30:09`，而 `locked_until` 折算为本地 `15:30:43`，即脚本在到期前 34 秒断言。后端新增针对解锁的测试并沿前向边交接，说明管理恢复和列间交接可用。

但系统测试重跑再次误判：`evidence/s2.txt` 的锁定截止 `09:03:47Z`（本地 `17:03:47`），`evidence/s3.txt` 的重启请求 `17:03:53` 才发出，已过锁定期，因此 S3 返回非锁定不能证明持久化失效。`evidence/s4.txt` 又以错误密码请求，却把“登录成功”设为期望，测试输入与期望不一致。QA 将两项归咎后端，集成审查未核对时间窗和请求输入，继续沿 `integration_review → backend_development` 误退。Task 本次测试未交付，已请求暂停，避免重复消耗模型预算。

最小修复仍限外部 `software.ddd_delivery` Loop，不改 DevWerk 核心或状态机：`system_test` 列在报告产品缺陷前核对每一步实际请求输入、时区/时间戳和时间窗口，重启耗时若超过测试窗口须在用户允许的测试配置内延长并完整重跑；测试期望与输入不一致时在本列修脚本，不发产品返工。`integration_review` 接到测试失败时，先用测试回执复核时间窗和请求输入；若属于测试脚本缺陷，沿既有 `accepted → system_test` 前向边交还 QA，而非归责实现列。保持既有图与权限，Loop 增量版本与合同回归后新 Project 测试。

已仅修改外部软件 Loop 两列指令，版本增至 1.4.9；Loop 合同回归 19 passed。第十次 Task 是失败样本，已请求暂停；新版本以独立 Project 重测。
