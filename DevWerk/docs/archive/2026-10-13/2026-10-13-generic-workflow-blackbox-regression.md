# 通用 Workflow 黑盒回归：登录防刷 Demo

## 测试边界

本次只通过运行中 DevWerk 的 `/v1` Web API 新建 Project、发送自然语言消息、读取对话与 Workflow/Task 状态。DB、日志和源码仅用于失败后的取证，不作为替 DevWerk 作设计或推进任务的途径。不修改历史 Project。服务已于 2026-10-13 重启以加载当前代码；重启后 `/v1/health` 为 ok，Conversation Gateway 和 Runtime Supervisor 均运行。

用户场景：通过至少五轮自然语言交流，讨论并交付一个简单的登录和防刷 Demo。前端 Vue、后端 Java；允许数据库，不使用缓存；范围只含登录和防刷。具体接口、Workflow Column、验收方法由 DevWerk Conversation Agent 根据讨论自行提出并通过内置工具建立，测试操作者不代它作架构决定。

通过标准：Conversation Agent 从实际对话使用工具创建适当 Workflow 与 Task；Task 由看板 Column Worker 自主推进至终态；成功终态有与冻结目标相符的可检查交付物，代码可编译，并且实际执行的验收结果与报告一致。仅有计划或文字声称完成不算通过。

观察节奏：对话提交及 Workflow 运行后约每五分钟查看一次对外状态；不实时轮询。若阻塞，先保存 Web 回执、对应 Job/Task/Column 状态及必要的日志、DB 证据，定位第一个通用机制断点。场景专用经验只应属于可选 Loop，不直接固化到核心代码。

## 运行记录

Project：`prj_2cf231473fed44578d579561cc7da2a2`，目录 `data/projects/generic-blackbox-20261013`。2026-10-13 22:11:54（北京时间），Web API 接受第 1 轮自然语言消息，Job `cjob_1e840cadbe804f2490802f282c76ca19`；内容为讨论 Vue/Java 登录防刷 Demo 的范围及验收关键问题。提交时尚无 Workflow 或 Task；这是接受回执，不是 Agent 回答。

第一次设置无效：上述 Project 的描述误写“验证通用 Workflow 能力”，Agent 的答复引用了这个测试目的。该轮不计入正式黑盒验证；这是测试输入污染，不作为 DevWerk 缺陷。正式 Project 改为 `prj_15b248d7f3c74a119029e1c54c3a47c7`，名称“登录与防刷演示”，描述只写产品目标，目录 `data/projects/login-demo-blackbox-20261013`。

正式第 1 轮（Job `cjob_1f86204817184f7e91039aa699fcfc95`）：Agent 以 discussion 成功答复，提出登录、防刷、技术栈和验收选项；未创建 Workflow/Task。正式第 2 轮（Job `cjob_db5235584fdf494f9be11ea7882c9c1d`）已提交：固定演示账号、不注册、连续失败暂时阻断、前端清晰提示、内存数据、无缓存与外部数据库；阈值留给 Agent 建议。

正式第 2 轮结果：Agent 建议按账号连续失败 5 次锁定 5 分钟，并提出演示账号、锁定反馈、登录状态等选择；仍为 discussion，无 Workflow/Task。第 3 轮（Job `cjob_2d68b4d87d3a482ea17137790649df4c`）已提交：确认 5 次/5 分钟、单账号、成功/失败/锁定提示，同时询问必须实际运行哪些验收检查。

第 3 轮结果：Agent 提出前后端联通、连续错误与锁定、到期解锁、前端三态等检查；无 Workflow/Task。答复把“重启服务”与“等五分钟”并列为解锁验证，前者不能证明定时解锁。第 4 轮（Job `cjob_64bcffdc1a55454995c41f8910aa4c6b`）已提交纠正：必须真实等待到期、实际完成前后端构建和 HTTP 调用，不能只写脚本或报告；询问还有无阻碍启动的关键决定。

第 4 轮结果：Agent 接受纠正，列出构建、HTTP、前端状态和到期解锁检查，判定没有功能范围阻塞；但提出“改系统时间或临时改 1 分钟”的替代办法，并再次询问 Java 版本与是否使用 Loop。第 5 轮（Job `cjob_3abe07ec54724fb8ab0eb60c160ae852`）已明确授权通过 Workflow 与 Task 交付；技术栈细节和 Loop 选择由 Agent 自行判断，要求最终 5 分钟配置的真实到期验证，不允许把改时钟/缩短阈值当作该验收结果。

第 5 轮五分钟观察：Job succeeded，Agent 经 `loop.inspect`、`conversation.execution.authorize`、`loop.apply`、`task.plan.validate/save`、`task.create` 等实际工具建立 Workflow `wfrev_7897d6865ee64a6e8297d1f7aa54c7f2` 和 Task `tsk_eae6559a0eb94ac59c99802313d98553`。Workflow 由所选 Loop 给出 `requirements → domain_design → backend_development → frontend_development → integration_review → system_test → delivery → accept`。读取时 Task 为 running，位于 `domain_design`；`requirements` 阶段已推进。Agent 在报告中明确 Task 仍待执行，未声称交付完成。

下一次五分钟观察：Task running/active，`requirements`、`domain_design`、`backend_development` 三个 Column Run succeeded，`frontend_development` running；无 Task error。此时只证明流程推进，尚不证明代码质量或交付完成。

再下一次五分钟观察：仍为 `frontend_development` running/active，Run 序列未变化，无 Task error。暂按耗时中的 Worker 处理，不判定阻塞、不人工干预。

随后五分钟观察：仍为 `frontend_development` running/active。对应 Agent Run `arun_5406120dfa784b15888d01e9a9d1d0c7` 已有 47 条消息、27 次工具调用；最近失败的命令之后又有成功命令，正在尝试启动前端开发服务。属于持续执行而非调度停滞，未人工催促。

再过五分钟：Task 和 Run 状态未变，消息仍为 47、工具回执 27；最后消息时间 23:12:08（北京时间），观察时间约 23:19。可能处于单次模型/工具等待；尚无终态错误，不立即归因为 Workflow 死锁。

下一次五分钟观察：`frontend_development` 已 succeeded，Task running/active，进入 `integration_review`。前述长时间沉默自行结束，没有人工重试、重启或路由干预。

再下一次五分钟观察：`integration_review` succeeded，Task 进入 `system_test` running/active；尚无退回、Task error 或暂停。系统测试是否真正运行了冻结验收，须等工具回执和最终报告交叉核对。

系统测试首次观察：Task 仍 running/active；测试 Agent Run `arun_547c87de5b5a487ebdf0662e40601f26` 已有 49 条消息、27 次工具调用，最近两次失败命令后有成功命令和成功文件读取。说明测试列在运行并处理问题，尚不能视为验收通过。

下一次五分钟观察：Task 仍位于 `system_test` running/active；测试 Agent 消息和工具计数未变化，最后回执时间 23:37:54，观察时间约 23:44。暂视为一次长模型/工具等待，未判定为永远停滞。

再下一次观察：测试 Agent 自行恢复推进，消息 75 条、工具 40 次，最后操作在 23:51:41 成功写入文件；没有 Task error。之后消息增长到 85 条、工具 46 次，实际启动 Java JAR 并请求登录接口。错误密码回 401、连续错误后回 429，但正确密码探测得到状态码 `0`，不能算成功验证。

测试 Agent 写出 `tests/system-test-inline.ps1` 并实际执行了 300 秒的到期等待。第一次脚本退出失败：使用 PowerShell 只读自动变量 `$PID` 作赋值，虽然脚本输出显示构建 PASS、401/429 和 300 秒等待，但正确密码状态码仍为 `0`，退出码非零。第二次修正后再次运行，构建 PASS，但服务就绪探测 60 秒超时，退出码 1。Agent 没有把两次失败报告为通过，而是继续修改测试脚本。此刻 Task 仍 `system_test` running/active，不能判定最终交付。

第三次完整脚本真实等待 300.3 秒，结果明确为 4/6，退出码 1；Agent 继续定位正确密码的状态码 `0`，改用 `Invoke-RestMethod` 的单次探测得到真实 HTTP 200。随后重写测试脚本，第四次完整运行的工具回执 `project.command.run` 序列 57：退出码 0；H1–H6 全部 PASS；锁定后实际等待 300.2 秒，随后正确密码返回 HTTP 200，解锁后错误密码返回 401。`system_test` Agent Run `arun_547c87de5b5a487ebdf0662e40601f26` succeeded，Task 自主沿 Workflow 进入 `delivery`，没有人工重试或修改核心代码。

2026-10-14 00:27（北京时间）观察：`delivery` 和 `accept` 两列均 succeeded，Task `done`。这只说明调度完成；下述交叉核对发现交付目标未满足，本次黑盒结果为**失败**，不能把 Task `done` 或报告中的 13/13 作为真实验收通过。

## 最终交叉核对与失败原因

1. **前端真实请求无法登录。** `frontend/src/App.vue` 第 85–93 行使用 `fetch`，请求头为 `application/x-www-form-urlencoded`，请求体为 `URLSearchParams`。`backend/src/main/java/com/demo/login/controller/LoginController.java` 第 27–31 行同时使用 `@RequestParam` 和 `@RequestBody LoginRequest`。冻结的 `docs/api-contract.md` 同时承诺表单与 JSON，且示例前端使用表单。交付列 Agent Run `arun_d147cd78e4fb494e9a95cf4f111c3e6c` 的工具调用序列 28 实际发出表单请求；Spring 日志多次出现 `HttpMediaTypeNotSupportedException: Content-Type 'application/x-www-form-urlencoded;charset=UTF-8' is not supported`，该命令退出码 1，输出 `BACKEND_STARTUP_FAILED`。随后该列用 JSON 请求验证得到 401，但这不能证明前端的表单请求可用。此问题影响冻结目标中的前端登录，不是可忽略的非阻塞事项。

2. **测试覆盖绕开用户路径。** `tests/system-test-inline.ps1` 使用 `application/json` 测 H1–H6，因此其 6/6 回执对 JSON 接口有效，不能证明前端使用的表单协议有效。`docs/test-report.md` 把 F2–F4 的 CSS/源码阅读标为 UI PASS；F5 则直接援引后端 H5/H6，没有真实 UI 操作或前端请求回执。`docs/delivery-report.md` 也把前端开发服务器 HTTP 200 和样式存在写成 UI PASS。这些证据不足以支持最终报告所称“实际 UI”及“13/13 真实执行”。

3. **已知失败未进入返工路径。** 交付列的 `column.complete(delivery_ready)` 结构化输出包含 `遗留问题_非阻塞`，其中明确描述表单请求返回 415，并指向 `integration_review`；但 `docs/delivery-report.md` 的“已知限制”没有记录这个问题，也没有 `task.feedback.record`。Workflow 已有 `delivery_failed → integration_review` 有向边。交付列将影响核心用户路径的已复现失败主观标为非阻塞，选择了 `delivery_ready → accept`。

4. **验收收到失败信息却忽略。** `accept` Agent Run `arun_316a71d42b5440a3bbb89c11fe33f6b6` 的输入消息第 2 条包含完整的上游交付输出和 415 描述；它还读取了需求基线、测试报告、交付报告、集成审查报告与反馈列表。最终仍报告无阻断缺陷并 `complete delivered`。这不是旧 Project 污染，也不是 Mailbox 丢通知；是当前 Project 内两个 Column 的证据判断失误，加上交付报告漏写已知问题。`task.feedback.list` 为空只是上游没有记录，不足以推断产品没有缺陷。

独立复核：测试完成后另行执行 `mvn -q test -f backend/pom.xml` 与 `npm run build --prefix frontend`，两者退出码均为 0。构建通过是事实，但不消除上述接口不兼容。首次在受限 shell 中执行前端构建曾因文件访问被拒而失败；在获准的本地环境重跑通过，该受限环境错误不算交付缺陷。

## 原则约束下的下一步

### 用例设计、脚本与放行的责任区分

原始 `docs/test-plan.md` 不是把问题设计漏了：H1/H2 的 `curl -d` 以表单方式发送，F2–F5 明确要求打开页面、点击登录并核对显示，测试总览还列出完整登录的端到端测试。缺口发生在执行转换：`system_test` 实际生成的 `tests/system-test-inline.ps1` 固定使用 `application/json`，只实现后端 H1–H6；F2–F5 未按计划执行浏览器或前端真实请求，而被源码/CSS 阅读替代。脚本早期的 `$PID` 赋值和就绪检查错误已由该列自行修复，最终 6/6 是有效的 JSON 后端测试，但其覆盖范围不足以宣称前端路径通过。

更关键的是，失败并非完全没测出来。`delivery` 的表单请求真实触发 Spring `HttpMediaTypeNotSupportedException`，但该列把 415 写进完成输出的“非阻塞”字段，未写进交付报告，也未沿已有返工边返回。`accept` 输入中包含这条 415，仍宣称 13/13。因而本次问题是**测试执行偏离计划 + 已发现失败被误分级并错误放行**；不是原始测试计划完全没有前端用例，也不是最终 JSON 脚本的 PASS 结果本身造假。

本轮**不修改 DevWerk 核心、不修补这个已完成的测试 Project、不把登录协议写死在通用 Agent 中**。机械调度、工具建 Workflow/Task、列间自动推进以及 JSON 接口测试均已被真实验证；未被验证、且实际失败的是用户端到端路径和终态验收可信度。

最小修正应先落在可选的 `loops/ddd-software-delivery/loop.json`：集成审查和系统测试以**客户端实际发出的请求格式**覆盖冻结的用户关键路径；代码阅读只能记作静态审查，不能宣称 UI 实际通过。交付列若复现影响冻结目标的失败，须在报告与反馈中一致记录，并用现有 `delivery_failed → integration_review` 边返工；验收列要独立对照需求判断上游已知失败的影响，不能仅接受“非阻塞”标签或 0 条反馈作为通过证据。这些是该可选软件 Loop 的职责约束，不应成为 DevWerk 通用核心的场景分支。

修正后应新建 Project，仍用自然语言完成至少五轮讨论，交由 Agent 自行建 Workflow/Task，并验证前端实际请求、真实编译、完整返工路由与最终交付；不复用本次 Project 的状态或记忆。
