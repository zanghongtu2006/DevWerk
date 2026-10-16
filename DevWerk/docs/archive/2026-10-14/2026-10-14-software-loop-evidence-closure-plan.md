# 软件 Loop 证据闭环修正与全新黑盒回归

## 范围和依据

依据 `2026-10-13-generic-workflow-blackbox-regression.md` 的实际回执：Conversation Agent 自主选用 `software.ddd_delivery` 并创建 Workflow/Task，所有 Column 自动推进到 `done`；源码可编译，JSON 后端 HTTP 测试真实通过。但实际客户端发出的表单请求在交付列得到 415；该列将其标为非阻塞，最终验收列收到此信息仍交付。原 `docs/test-plan.md` 已要求页面登录，执行脚本只覆盖 JSON 后端路径，前端用例被静态代码阅读替代。

本次只改 `loops/ddd-software-delivery/loop.json` 中现有软件 Loop 的 `integration_review`、`system_test`、`delivery`、`accept` 四列指令。保留 Column、边、工具权限、上下文来源、写入范围、接受条件的数据结构以及 DevWerk 核心代码。不得写入登录、HTTP 415、固定技术栈等案例专用规则；规则以冻结需求中的用户关键路径和实际客户端请求为对象。

## 修改步骤

1. `integration_review`：静态检查前后端契约时同时核对客户端实际构造的请求方法、Content-Type、请求体与服务端入口的处理方式。未经执行的兼容性只能记为待 `system_test` 验证，不能在审查报告中写成已证实通过。若已复现不兼容，用既有反馈和责任开发边返工。
2. `system_test`：按冻结用例维护执行与证据映射。需要联调的关键路径，以实际客户端发送的请求形状做在线验证，不能只用服务端支持的另一种输入证明客户端路径。构建或源码检查和真实 UI/端到端执行分别标注；未执行的用例不得列为 PASS。脚本自身失败仍先修脚本；确认产品失败走既有 `tests_failed → integration_review`。
3. `delivery`：复现影响已确认用户关键路径的失败时，将命令、输入、响应和范围影响同时写入 `docs/delivery-report.md`、`task.feedback.record` 及完成输出，并走既有 `delivery_failed → integration_review`。其他输入路径通过不能抵消该失败。真正不影响验收目标的建议可记录但不可冒充成功证据。
4. `accept`：独立核对需求、各列原始证据、完成输出和反馈。对同一目标出现成功声明与失败回执时，应解释冲突并走现有返工边；即使上游标注“非阻塞”或反馈列表为空，也须依据冻结目标判断。静态检查不能改称真实行为测试；未验证的必要用例不能 `delivered`。

## 验证和通过标准

先运行 Loop JSON 加载、现有 Loop contract 与相关工作流测试，确认模板可被加载且路由/权限未变化。然后用全新 Project、至少五轮自然语言讨论同一简单 Vue + Java 登录防刷 Demo；测试者只确认产品范围，不指定 Column 或测试实现，让 Conversation Agent 自行选择 Loop、创建 Workflow 和 Task。观察间隔约五分钟，不实时探测、不人工代其调度；不修改旧 Project。

通过须同时满足：工具实际创建 Workflow/Task；Task 自动推进到 `done`；后端与前端实际构建成功；与客户端真实请求一致的用户关键路径有有效执行证据；最终报告与原始工具回执无冲突。若出现产品缺陷，正确返工并修复后到 `done` 也可通过流程验证；未修复却到 `done` 判失败。缺少实际证据也判未通过。若需加载新模板，应只执行必要的 DevWerk 服务重启，不停止其他进程。

## 修改及静态回归记录

已按上列步骤仅更新 `loop.json` 中四个现有 Column 的 `instruction` 字符串，未改变 Workflow 图或工具列表。JSON 解析通过；`tests/test_loop_contract.py` 与 `tests/test_workflow_transition_authority.py` 共 25 个测试通过。首次运行因受限临时目录无法创建 pytest fixture 而全部报 setup error；改用仓库内唯一临时目录重跑后 25/25 通过，临时目录已删除。`LoopCatalog.get` 每次读取磁盘 `loop.json` 并将其内容计入 digest，因此模板更新无须重启 DevWerk 服务。

## 全新黑盒回归记录

DevWerk `/v1/health` 为 `ok`，Supervisor 与 Conversation Gateway 均在运行；`GET /v1/loops/software.ddd_delivery` 已返回四项新指令。未重启或停止服务。新 Project `prj_b98643e78bc84aea9e211c1d0494fde4`，目录 `data/projects/login-demo-loop-regression-20261014`，描述仅为产品目标。第 1 轮自然语言消息已提交，Job `cjob_b4d563514b924d0dbdab927bd8ef9547`；请求先讨论 Vue + Java 登录防刷演示的范围和验收，不开始开发。后续按约五分钟间隔观察。

第 1 轮 Job succeeded，Agent 仅讨论登录、防刷、数据、前端及验收问题；尚未建 Workflow/Task，但额外建议 IP 限流和较多功能。第 2 轮 Job `cjob_cbebf77c88554dc3a02305c3f730c1e3` 已提交，用自然语言收敛为固定账号、内存数据、仅按账号连续失败后锁定，排除注册、记住我、验证码、IP 限流和登录日志，并请 Agent 建议阈值。

第 2 轮 Job succeeded，Agent 建议 5 次失败、锁定 5 分钟；但建议稿又增添了组件库、剩余次数、倒计时和欢迎页。第 3 轮 Job `cjob_6cfc7a14315c41f7ab4d53d17ef64b00` 已提交：接受 5/5，明确只要简单表单和三种提示，排除额外 UI 功能；要求实际页面登录与真实到期等待，并询问验收标准是否有关键缺口。尚未由测试者指定 Loop、Column 或脚本。

第 3 轮 Job succeeded，Agent 接受简化 UI，但提出 API 格式、端口、Java 版本及验收方式等确认问题。第 4 轮 Job `cjob_7e32fb3579ba4b968df4370197ebe815` 已提交：授权 Agent 自行确定接口与构建细节，要求前后端一致；验收必须实际运行构建、页面登录、后端请求及真实 5 分钟等待，不接受仅交脚本或人工待办。

第 4 轮 Job succeeded，Agent 回答本机 Java 21、Maven 3.9.6、Node.js 22.22.1 和 npm 11.15.0 可用，需求已锁定，等待启动确认。第 5 轮 Job `cjob_47370c0d856a43b5bae2fd7ff29c080e` 已提交：以自然语言授权开始，让 Agent 自行选择现有 Loop 或设计 Workflow、创建 Task 并推进交付；只重申产品排除项和不许将未执行检查写为通过。测试者未提供 API 参数、Column 清单或具体实现方案。

第 5 轮 Job succeeded。Conversation Agent 自选 `software.ddd_delivery`，实际创建 Workflow `wfrev_5b2624bbafe24c0aa19056d13724cd59` 和 Task `tsk_351bd88166dc4e1ab62ee016e97754a0`，答复如实说明交付“待执行”。首次观察 Task `running/backend_development`。通过 `/v1/projects/{id}/workflow` 确认新 Workflow 的 source Loop 为 `software.ddd_delivery`，且四项新指令已经进入快照；不是沿用旧 Project 的 Workflow。

后续约五分钟观察：`requirements`、`domain_design` succeeded，`backend_development` running/active，无 Task error。再两次观察后端仍运行，但其 Agent Run `arun_271dbdaf263d4a1686b9021076b155d6` 从 75 条消息/45 次工具增长到 103 条消息/60 次工具；最近失败命令后仍有成功读取，说明继续工作而非调度死锁。未人工介入。

后端开发继续运行并修正单元测试：一次失败回执显示锁定发生时点的预期与实现不一致；随后 35 项测试通过。第一次 `column.complete(backend_ready)` 因 7 个此前失败命令回执未关联解决而被 Runtime 拒绝，错误明确要求成功重试、`failure_resolutions` 或声明失败。五分钟后该列自行成功完成；`frontend_development` 也 succeeded，Task 自动进入 `integration_review` running/active。没有人工修补或推进。

`integration_review` succeeded，报告检查前端实际构造的 JSON 请求与后端处理入口，Task 进入 `system_test`。该列实际运行后发现 Spring Boot JAR 因缺少组件注解无法启动，6 个行为场景均未验证；没有虚报通过。但系统测试 Agent Run `arun_3893656969614bd989be93e6a204166a`、`arun_792990a3e2244df4ac671d1495e2d92a`、`arun_55098cb2b359482f982fff88c6aea55d` 连续三次将明确的后端实现缺陷提交为 `execution_failed`，没有选择已有的 `tests_failed → integration_review` 返工边。Runtime 自动重试同列三次后 Task 进入 `failed`。此 Project 保留作失败证据，不做人工修补。

第一轮模板效果：防止了没有真实执行就标为通过，但没有让产品启动失败进入返工。原因是 `system_test` 指令虽写“确认产品问题后 complete tests_failed”，仍未明确“产品自身构建或启动失败属于产品问题，不能当作环境不可验证”。下一次只加强这项通用结果边界，Workflow 图与核心代码仍不变，然后用新的 Project 测试；不把 Spring 注解写进模板。

第二次模板小改仅在 `system_test` 指令中明确：交付物自身构建、启动或接口失败属于产品问题，须携原始回执走 `tests_failed`；`execution_failed` 只用于与交付物无关的工具、权限或外部环境故障。没有增加新状态或边。Loop/工作流相关 25 个测试再次通过，临时 pytest 目录已删除。

第二个新 Project `prj_0e7d0096e8e04cac8fa675b772c2fa6a` 的前 3 轮自然语言讨论确认了同一最小范围与实际验收。第 4 轮只确认测试标准，Conversation Agent 却提前尝试自建 Workflow；其图含不可达 `frontend` 列，`workflow.publish` 同参数重复失败，Job `cjob_6005496d005349e691fab35cba607880` 因 `ConversationProtocolStalled` 结束。第 5 轮自然语言要求它依据错误自行修正或选用现有 Loop；Agent 成功创建合法的自建 `build → verify` Workflow 和 Task `tsk_a15800b862b846ab9ed94513678fc3a7`，另有待运行 Task `tsk_04181648ef6d46de8b23129fb54b0d8c`。此 Workflow **未选用修改过的 Loop**，所以该 Project 不能证明第二次模板修正的效果。保留其自主运行，不干预。下一回归须明确要求选现有软件交付 Loop，以直接测试模板。

第三个新 Project `prj_3d04b72a72e8470798f2b6bf4b71c6fe` 的首轮虽要求先讨论，Conversation Agent 在默认 `auto` 模式错误地执行 `conversation.execution.authorize(begin)`，于 `2026-10-14 13:02:17 +08:00` 创建了目标为“澄清范围与验收条件，暂不进入开发”的 Requirement `req_d8a4f7d1d97c47c995ccc2e3a4b49bf9`，却没有创建 Workflow。第 5 轮用户真正授权交付时，同一 draft 的目标已变，`begin` 遇到 `RequirementConflict`；Agent 改用 `extend` 得到 `pending_scope_revision` grant，但 `project.scope.revise` 的参数要求已有 Workflow revision，且实现直接读取 active Workflow。第 6 轮再次授权仍无法越过此状态，Job `cjob_204ad4fc80e646b58bddc2e92db38d96` 因重复 `loop.apply` 被判 `ConversationProtocolStalled`。这是通用讨论/授权边界和无 Workflow 修订路径缺陷，不能证明模板好坏；本轮模板修改不夹带核心修复。另行记录这一核心缺陷。下一次新 Project 的讨论轮明确使用现有 `mode=discuss`，交付授权轮用默认执行模式；自然语言需求与验收目标保持不变。

第四个新 Project `prj_e7f1da2db0f940e28bcd4a065352da51` 通过本地 HTTP API 以 UTF-8 提交消息。前四轮是自然语言讨论，使用服务现有 `mode=discuss` / `start_task=false`，均 succeeded，且均没有创建 Requirement/Task；讨论确定单一预置账号、内存数据、5 次失败锁定 5 分钟、锁定期间正确密码也不可绕过、最小 Vue 页面与真实运行验收。第四轮 Agent 自行检查现有软件 Loop 并判断适用。第五轮用户明确授权使用该 Loop 建立 Workflow 与交付 Task；Job `cjob_55de5f64a413422599cc7a6d3e2439c7` succeeded，实际创建 Requirement `req_5e1f58fc1b5e44d58ae5e8bc7eec6c86`、Workflow `wfrev_113ae5050644495487bfbe0f40fce7ec` 与 Task `tsk_b17a9101a04b4099afc08990a40694bd`。数据库绑定确认使用 `software.ddd_delivery` 1.4.10，首次观察 Task 正在 `domain_design`。Agent 没有采用其讨论时提议的四列裁剪，而是采用包含 `integration_review`、`system_test`、`delivery`、`accept` 的完整模板，故本 Project 能验证当前模板的实际效果。后续按约五分钟间隔观察。

第四个 Project 的 Task 最终自动到 `done`，八列均 succeeded，Java 单元测试 22/22、Vite 构建成功。`system_test` 先出现脚本探活失败但 0/0 错报 ALL PASS；Worker 没有放行，而是修脚本。之后 14/15 与 14/17 两次真实运行暴露脚本的用例计数重置错误；Worker 自行修正，最终 `project.command.run` 回执为退出码 0、21/21 PASS，含实际约 5 分钟等待。没有错误退回产品实现列，也没有把前两次脚本失败当作产品缺陷。这验证了模板对“脚本失败与产品失败分开、未执行不能 PASS”的正面效果。

但冻结需求 `docs/requirements.md` 同时列出页面提示并要求“验证页面登录、后端请求”。实际 21/21 只发出与前端请求形状一致的 HTTP 请求；前端仅被构建和静态审查，没有浏览器页面交互与可见提示的运行回执。`FINAL_ACCEPTANCE.md` 仍将这六条路径全标通过，Task 进入 `done`。这是模板对“等价接口测试”和“实际页面操作”两个证据对象区分不足的剩余缺口，不能把该 Project 计为完整验收通过。第二次小修正仍只改同一个 Loop 的 `system_test`、`delivery`、`accept` 三列说明：冻结目标明确要求页面交互时，必须有真实客户端操作及可见结果的执行证据；等价 HTTP 请求只证明接口，不可代替页面验收。若执行环境缺少该能力，应标为未验证而不宣称通过。继续保持 Workflow 图、核心代码与权限不变；另用全新 Project 回归。

第二次小修正已写入 `loop.json`，仅替换上述三个 `instruction` 中的证据边界句，JSON 解析通过、Loop/路由相关测试 25/25 通过。第五个新 Project `prj_87e0c377f385460da4c257d600f699e3` 继续使用相同产品目标。前四轮以 UTF-8 自然语言在 `mode=discuss` 中确认范围与验收，均 succeeded，无 Requirement/Task；第五轮授权，Conversation Agent 自行选择 `software.ddd_delivery`，创建 Requirement `req_200693a31fa44a4c8da868b8d8b7d3a4`、Workflow 及单一 Task `tsk_d94045a16b194cbb97ebdb4ec86af8dc`。数据库绑定的 Loop digest 为 `e6e3948025976d5665543b7ea39735a241c5ae8a0f0041583bf56472f0983d2b`，为本次新模板快照。首次观察 Task 自动进入 `domain_design`，后续仍按约五分钟间隔检查。

第五个 Project 的需求、设计、后端、前端、集成审查列均 succeeded，随后 `system_test` 真正运行了 HTTP AC-01～AC-06；单元测试 19/19，AC-06 脚本等待约 300 秒。冻结规格 `requirements/SPEC.md` 的验收场景同时要求页面提示，但该列的 `column.complete(tests_passed)` 仅引用 HTTP 回执，未提供客户端页面操作及可见结果的运行回执。说明新增模板文字仍未稳定形成语义上的验收边界，不能认定完整通过。

本次 `tests_passed` 未被 Runtime 放行，但直接拒绝原因是另一项协议：该列积累 13 条失败的 `project.command.run` 回执，成功结案需逐条同参重试，或在 `failure_resolutions` 中关联后续同能力的成功回执。首次提交完全缺失该关联；第二次引用了并非失败回执的 ID；后续仍未清偿。`CompletionAttemptGuard` 在同一 Agent Run 中第五次拒绝后抛 `CompletionProtocolStalled`，系统对该列自动重试三次均同样失败。最终 Task 为 `recovering/system_test`、`failure_code=column_completion_protocol_stalled`，未进入交付或 `done`。这是准确的流程阻塞，不是产品缺陷或模板测试通过。`tests_passed` 是 Workflow 声明的列出边，指向 `delivery`，而非全局 Task 状态；只有冻结场景全部有相应执行证据且失败回执已清偿才能用。产品失败应走 `tests_failed → integration_review`；因工具/环境无法执行必要页面验收，应保留未验证原因并走 `execution_failed → failed`，不能将 HTTP 结果转写为页面通过。

结论：本轮模板改动使上一 Project 避免了 0/0 假通过和脚本失败误退后端，但没有保证页面证据的语义校验；继续堆叠提示词无法稳定解决完成协议的回执关联。本次不扩张到 DevWerk 核心修改，也不人工推进第五个 Project。后续若要追求自动交付，需独立处理通用完成协议可用性和验收证据类型的确定性校验，设计须保持 Workflow 显式声明、场景无关。
