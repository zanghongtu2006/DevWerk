# 新 Project Workflow 黑盒回归（2026-10-15）

## 边界与通过条件

- 仅经运行中的 DevWerk `/v1` Web API 新建 Project、进行自然语言对话并读取对外状态；不代替 Conversation Agent 设计 Workflow、创建 Task 或操作数据库推进流程。
- 至少五轮讨论后，明确授权 Agent 执行。需求范围：Vue 前端、Java 后端的简易登录及防刷演示；允许数据库，不使用缓存。架构、接口和 Workflow 由 Agent 提出并在对话中确定。
- 通过须同时满足：Agent 实际通过内部工具创建 Workflow 与 Task；Task 由 Column Worker 沿 Workflow 推进至成功终态；交付物满足所定目标、代码实际编译通过，最终报告与真实执行证据相符。Task `done` 本身不是交付质量证明。
- 仅约每五分钟读取一次 Agent/Workflow 状态，不实时轮询。失败时记录首次通用机制断点和事实证据；不以历史 Project 的状态推断本次结果。

## 运行记录

- 当前服务由此工作区代码启动于 `127.0.0.1:8000`；`/v1/health` 返回 `ok`，Conversation Gateway、Runtime Supervisor 均运行。
- 首个 Project `prj_1ea5d25598314f20831ab1de1266742a` 的描述含“黑盒回归”等测试字样，可能污染 Agent 输入，故不计入正式测试，也不再向其发送对话。
- 正式 Project `prj_00ed9c29b5b9475e839e39d7ffd9b609`，目录 `data/projects/login-demo-20261015`。名称及描述只包含产品需求。第 1 轮自然语言 Job `cjob_57ce717a30ef4b348c84fe18ee7fb588` 已被接受，用户提出 Vue/Java 登录防刷小演示，先讨论边界和验收关键问题；尚未读取答复。
- 第 1 轮 Job succeeded。Agent 明确处于需求讨论，未请求执行；它提出先确认防刷机制和登录成功后的页面行为。第 2 轮 Job `cjob_639d97f7faa540ed9b91d52617d6bcb7` 已提交：固定演示账号、临时锁定、成功/失败/锁定页面提示；次数和时长由 Agent 建议，验收应实际验证锁定与到期恢复。
- 第 2 轮 Job succeeded。Agent 建议账号连续错误 5 次后锁定 5 分钟，并提出正常登录、触发锁定、真实等待后恢复三项验收。第 3 轮 Job `cjob_a9e224fdfaa14f4982710243e63659e9` 已确认这些参数，并询问接口对齐和实际页面验证。
- 第 3 轮 Job succeeded。Agent 自行提出先冻结 JSON 登录接口，再做前后端实现与实际页面验证；建议锁定状态只以后端为准。第 4 轮 Job `cjob_204fde41ef004e96b8d22b2be3c7490d` 已同意该建议，将部署与请求方式留给 Agent 选择，并询问启动前是否还有关键未决问题。
- 第 4 轮 Job succeeded。Agent 选择 Spring Boot 同端口提供 Vue 3 页面，建议 Maven 单项目、HTTP 200/401/423 和固定演示账号；它要求确认 Java 框架和状态码。第 5 轮 Job `cjob_c773b37c5d08462e8da1c6251677daed` 已确认上述建议并明确授权实施，要求 Agent 自行安排 Workflow/Task，并以实际运行核对构建、页面请求、锁定和五分钟到期恢复。
- 第 5 轮五分钟观察：Job succeeded。工具账本显示 Agent 调用 `conversation.execution.authorize`、尝试 `loop.apply`（首次数值绑定名不符被拒）、随后 `loop.inspect` 并改正绑定，最终实际创建 Workflow Revision `wfrev_3a7c6b05ca204c06b7bdecd54a7135d1` 和 Task `tsk_202c8156f88b4bd98db10d51f1ccf78b`。Task 观察时 `running`，位于 `domain_design`，无 Task error。Agent 对用户报告“待执行”，未声称交付完成。所选软件 Loop 提供 `requirements → domain_design → backend_development → frontend_development → integration_review → system_test → delivery → accept`，各成功边当前没有额外 `required_checks`；后续必须独立核验 Worker 的实际执行证据与报告一致性。
- 下一次五分钟观察：`requirements` Run 已 succeeded；`domain_design` Run 自 20:57 起仍在运行，Task `running/active`、无错误，21:06 有状态更新时间。尚不足以判断停滞，不干预。
- 再下一次五分钟观察：`domain_design` 于 21:10 succeeded，Task 自动进入 `backend_development`，状态 `running/active`、无错误。设计到开发的边已正常推进。
- 下一次五分钟观察：`backend_development` 于 21:13 succeeded，Task 自动进入 `frontend_development`，无错误。后端到前端的边已正常推进；实现质量尚未独立核验。
- 下一次五分钟观察：`frontend_development` 仍 running/active，Task 无错误，21:24 有状态更新时间。尚未出现重复返工或路由竞争，不干预。
- 下一次五分钟观察：前端列仍 running/active，Task 无错误。对应 Agent Run `arun_4a90e8b8e2ec433fadf0362b9e39fb62` 已有 85 条消息、48 次工具调用；21:30:41 刚成功写入文件，属于持续执行而非停滞。`domain_design` 曾有一次失败的 Agent Run，随后同列恢复成功并推进；需在最终复盘时区分可恢复重试与 P0 阻断。
- 下一次五分钟观察：前端列于 21:32 用 `task.feedback.record` 记录反馈，并以已声明的 `backend_contract_issue → backend_development` 边返回后端；后端修复第 5 次失败应返回 423 的行为，20 个 Maven 测试通过，再以 `backend_ready → frontend_development` 返回前端；前端二次完成后以 `frontend_ready → integration_review` 推进。Task 当前在集成审查、无错误。这是明确反馈与边驱动的有限返工，不是反馈重写目标或抢路由。
- 下一次五分钟观察：`integration_review` 于 21:39 succeeded，Task 自动进入 `system_test`，状态 running/active、无错误。集成审查到系统测试交接正常；尚不能据此认定需求验收通过。
- 下一次五分钟观察：`system_test` 仍 running/active、无 Task error；对应 Agent Run `arun_05a7fd94b52b4cf5b67e102680de6409` 已有 39 条消息、21 次工具调用，最近一条 Assistant 消息在 21:46。可能处于较长的命令或模型等待，暂不判定停滞。
- 下一次五分钟观察：`system_test` 于 21:52 succeeded，`delivery` 于 21:55 succeeded，Task 已自动进入 `accept` running/active、无错误。验收 Agent 已开始读取项目文件。此刻仍须等待最终路由与证据交叉核对，不能把上游 succeeded 直接算作交付通过。
- 下一次五分钟观察：`accept` 于 21:57 succeeded；Task `tsk_202c8156f88b4bd98db10d51f1ccf78b` 为 `done`，无运行错误。五轮自然语言讨论、工具创建、Column 派生、一次定向返工及最终路由均已完成。

## 独立交叉核验与结论

**流程与交付物状态分开计：**本次真实验证了 Conversation Agent 建立 Workflow/Task，并由单列 Worker 沿有向边自主到 `done`；产品后端 Maven 测试独立重跑 20/20 通过，前端也经独立浏览器操作实际登录成功。然而，**本次 Workflow 自身的验收结果不能算完整通过**：它把没有执行的页面操作标成 `LOGIN-002 PASS`，最终 `accept` 继续报告全部 6 项已验证。

证据链：

1. 冻结需求要求“登录成功后页面显示欢迎信息和登录状态”，第 5 轮用户消息还要求“页面发出的登录请求”以实际运行核对。`docs/requirements-traceability.md` 将其列为 REQ-002/LOGIN-002。
2. `tests/LoginIntegrationTests.ps1` 第 171–184 行的 LOGIN-002 只通过 `Invoke-WebRequest GET /` 取得 HTML，再以正则寻找 `id="app"`、`login-container` 和 `welcome-container`。它没有在浏览器中加载 Vue、填写表单、点击登录、检查页面运行后的欢迎文本，也没有由前端发起 `/api/login`。HTTP 的 LOGIN-001 请求是测试脚本直接发出的，不能替代页面请求。
3. `docs/test-report.md` 将这项静态 HTML 检查写成 `PASS`；`docs/delivery-report.md` 的“页面交互验证”仍只列这些静态元素；`FINAL_ACCEPTANCE.md` 将 REQ-002 与其余五项都标为通过。原始工具调用显示 system_test 反复运行同一 PowerShell 脚本，delivery 只运行 Maven 命令，accept 没有运行命令；没有浏览器操作回执。
4. 已发布 Workflow 的 `system_test.tests_passed → delivery` 与 `accept.delivered → done` 两条成功边 `required_checks` 均为空；列上的 `acceptance_checks` 也为空。因此，当前单一路由实现只执行已声明的边检查，没有可机械拒绝此虚假页面证据的条件。`loops/ddd-software-delivery/loop.json` 的 QA/accept 自然语言指令虽明确要求实际页面操作并禁止静态检查冒充，但三个 Worker 本轮均未遵循到位。
5. 独立于 Workflow，复核者运行 `mvn -q -f backend/pom.xml test` 得到退出码 0，Surefire 三类合计 20 tests/0 failures。随后以已打包 JAR 在测试端口 18080 启动，用实际浏览器打开页面、填入演示账号并点击“登录”；页面显示“欢迎 admin”和当前登录用户。**这证明本次产品的正常登录页面路径实际可用，但不能倒填为 Worker 曾执行过的验收证据。**原脚本对后端 200/401/423 和约 300 秒后的恢复有真实 HTTP 回执；页面登录是流程内未验证的一项。

根因不是新旧 Project 串扰，也不是 `feedback.settle` 再次改写目标。状态路由严格遵从已声明边；缺口发生在 `system_test` 将 HTML 字符串检查误判为页面行为、`delivery` 继承此判定，以及 `accept` 没有把冻结需求与原始执行方式交叉核对。通用 Runtime 不应猜测软件页面行为；若下一轮要确定性阻断，必须由 Workflow 在成功边声明可执行且能证明该需求的检查，或在无法执行时让 Worker 选择失败/未验证的既有出边，而不是重新加入 Workflow 外的全局终态门槛。

本次仅做新 Project 黑盒回归及独立复核，未修改 DevWerk 核心、Loop、正在运行的历史 Project 或本次交付物源码。服务仍保持运行。

## 2026-10-16 页面补充验证

用户确认已看见独立浏览器操作得到的“欢迎 admin”页面并确认可以实际登录。随后测试者在同一交付 JAR 的 18080 测试端口，以浏览器页面继续操作：退出登录后连续输入 4 次错误密码，页面每次显示“用户名或密码错误”；第 5 次错误密码后页面显示“账号已被锁定，请5分钟后再试”；锁定期间再通过页面提交正确密码，仍显示锁定提示。之后保持服务运行，不修改系统时间或锁定参数，实际等待超过五分钟，再次从同一页面提交正确密码；页面显示“欢迎 admin”和当前登录用户，页面记录登录时间为 2026-10-16 01:19:30。**产品页面的正常登录、错误提示、锁定及到期恢复均由独立浏览器操作验证通过。**

最终判定须区分两层：交付产品的本轮功能黑盒复核通过，Maven 测试 20/20 通过；DevWerk 的 Workflow/验收证据链仍有一项缺陷——它在自己未进行页面操作时，就将 LOGIN-002 写为 PASS 并放行 `done`。独立补测不能追认上游 Worker 当时没有执行的动作。本次未修改 DevWerk 或交付物源码，DevWerk 8000 服务与演示 18080 服务仍在运行。
