# Human-Team Software Workflow Retest — Execution Record

**Date:** 2026-09-16 (Asia/Shanghai)  
**Project:** `prj_df4616276dab4235ae178242fcc7fdc6`  
**Workspace:** `D:\workspace\codex-devwerk-project-files\team-auth-workflow-retest-20260916`  
**Audit evidence captured:** 2026-09-17 (Asia/Shanghai)  
**Report finalized:** 2026-09-20 (Asia/Shanghai)  
**Runtime terminal state:** `done`  
**Audit verdict:** **rejected as a valid delivery**. The source is a runnable prototype and its executed unit/integration checks pass, but the accepted security requirement, planned browser E2E gate, and directed rework lifecycle were not satisfied. See [Final verdict](#final-verdict).

## Executive summary

- DevWerk created one Task from the `software.ddd_delivery` Loop and traversed eight distinct Columns with eight distinct Agent Sessions. All eight Column Runs were persisted as `succeeded`; the Task was persisted as `done`.
- The generated backend starts successfully. An independent probe successfully registered a user, logged in, received one session Cookie, and read `{"success":true,"message":"欢迎回来",...}` from `/api/welcome`.
- Backend Maven tests independently pass **26/26**; frontend unit tests pass **16/16**; frontend production build succeeds.
- The agreed backend `Origin` validation is absent. An independent request with `Origin: https://attacker.invalid` was accepted with HTTP 200 and created a user. This contradicts the confirmed requirement and proves that SameSite alone was incorrectly accepted as equivalent protection.
- The planned Playwright suite was never run by the Workflow. The audit invoked it and all 10 cases were unable to start because the Chromium runtime was not installed. More importantly, the authored suite contains form/navigation checks but no successful register → login → welcome integration flow, duplicate-registration flow, or browser-close session test.
- A user defect report sent while `delivery` was running did not return work to an implementation Column. The manager could not message the completed review Worker, then produced rejected reporting calls, while the original graph continued to `accept → done`.
- Model usage was **183 calls / 1,372,493 recorded total tokens**. Conversation management consumed 933,434 tokens (68.0%), more than twice the 439,059 tokens used by all eight delivery Columns. Mailbox callbacks alone consumed 309,561 tokens in 30 model calls.

## 本报告的核心审计原则

本次测试不以“Task 显示 `done`”或“若干测试命令退出码为 0”作为单一成功标准，而围绕以下三个问题作结论：

1. **DevWerk 是否能够创建并运行 Workflow**：Workflow 是否来自可复用 Loop，是否形成正确的 Column 有向图，是否能执行到终态，Column 产物是否与 Workflow 定义一致。
2. **交付物是否满足人类确认的需求**：若不满足，必须区分是 Workflow 设计没有承载需求、Workflow 设计正确但 Agent 执行疏漏，还是 DevWerk 的调度、反馈、验收机制发生故障。
3. **Token/API 成本是否合理**：既统计绝对消耗，也区分需求讨论、任务执行、Conversation 调度和 Mailbox 通知成本，判断是否存在明显优化空间。

问题分类规则：

- **交付物缺陷**：生成的软件代码、测试或文档自身的问题，不使用 P0/P1。
- **Workflow 设计问题**：本项目 Workflow 没有把已确认需求编译为 Column、transition 或确定性 `acceptance_checks`；不直接等同于 DevWerk P0。
- **DevWerk 框架 Bug**：Runtime、Conversation、Mailbox、调度、状态机或证据机制没有按其设计工作；只有此类问题使用 P0/P1。

## 核心一：Workflow 创建、执行与交付一致性

### 结论：结构执行成功，语义执行不完整，核心一部分通过

| 检查项 | 结果 | 证据与解释 |
| --- | --- | --- |
| 从 Loop 创建 Workflow | 通过 | 应用了 `software.ddd_delivery`；Loop 生成 revision 1，项目修订生成 revision 2 |
| 创建可执行 Task | 通过 | Task Plan 和单一端到端 Task 均成功持久化 |
| Column 图完整运行 | 通过 | 8 个 Column 依序运行，8 个独立 Agent Session，均记录为 `succeeded` |
| 到达明确终态 | 通过（仅状态层） | Task 在 30 分 23.3 秒后进入 `done` |
| 结构化交付物与 Column 定义一致 | 基本通过 | requirements、design、backend、frontend、review、test、delivery、accept 均生成对应文件，共 54 个 Artifact 记录 |
| Column 实际工作与其语义一致 | 部分失败 | `system_test` 未运行计划中的 E2E；`delivery` 未实际启动服务；`accept` 没有确定性检查却宣告全部通过 |
| 有向返修机制可用 | 设计存在、执行失败 | 图中有 review/test/delivery/accept 回退边，但用户提交缺陷后没有重新进入实现和复测 |

因此，DevWerk 已证明可以从 Loop 创建 Workflow、创建 Task、调度多 Column 并到达终态；但没有证明它能保证每个 Column 完成其语义职责，也没有证明复杂返修图能够真正闭环。

### Workflow 定义中实际存在的约束

revision 2 的 Runtime 强制检查只有：

- `backend_development`：`mvn.cmd -f backend/pom.xml test`
- `frontend_development`：`npm.cmd --prefix frontend run build`
- `system_test`：上述 Maven 测试和前端构建

以下内容没有成为机器强制检查：

- Origin 负向安全测试；
- Playwright 浏览器 E2E；
- 后端和前端服务真实启动、HTTP 用户路径及停止；
- `accept` 对每条已确认需求的确定性核验。

`integration_review`、`delivery` 和 `accept` 的 `acceptance_checks` 均为空；所有 Column 的 `output_contract` 基本允许任意对象，transition 也没有 `evidence_requirement`。这使 Agent 的自然语言“已通过”可以直接推动状态流转。

### 核心一问题归因

| 问题 | 归因 |
| --- | --- |
| Workflow 没把 Origin/E2E 编译为强制门槛 | Workflow 设计问题；Conversation Agent 生成项目 revision 时遗漏 |
| QA Agent 没执行测试计划中的 E2E | Agent 执行疏漏，同时暴露 Workflow 门槛不足 |
| Delivery Agent 只写启动说明、没有真实启动 | Agent 执行疏漏，同时暴露 delivery 没有机器检查 |
| Accept Agent 根据报告宣告全通过 | Agent 判断错误，同时暴露最终验收 fail-open |
| 用户反馈无法重新打开责任 Column | **DevWerk P0**：反馈、Assignment 生命周期与有向返修调度没有闭环 |
| Task 最终仍显示 `done` | **DevWerk P0**：最终状态与已知未解决缺陷不一致 |

## 核心二：交付物是否满足人类需求

### 结论：可运行原型，但不满足完整验收

“后端 26/26、前端 16/16、构建成功”和“完整验收不通过”并不矛盾：这些命令只覆盖了已经实现并被测试的局部行为，完整验收还要求所有人类确认的需求和交付证据成立。

| 人类需求/交付目标 | 结果 | 问题性质 |
| --- | --- | --- |
| 注册、登录、Cookie 会话、欢迎页 | 基本满足 | 独立运行探针成功完成注册、登录和欢迎页访问 |
| 账号 5 次失败锁定 15 分钟 | 有代码和单元测试证据 | 后端测试覆盖，未做真实浏览器级链路 |
| IP 每分钟 30 次限制 | 有代码和单元测试证据 | 后端测试覆盖，未做独立 HTTP 压测 |
| 后端校验状态变更请求的 Origin | **不满足** | 交付物安全缺陷 D-1；恶意 Origin 请求实际返回 HTTP 200 |
| 真实浏览器 E2E | **不满足** | 交付/测试证据缺口 D-2；Workflow 未执行，独立复验因 Chromium 缺失无法启动 |
| E2E 覆盖注册→登录→欢迎页、重复注册、关闭浏览器 | **不满足** | 当前 Playwright 文件只覆盖表单、导航和未登录跳转，即使安装浏览器仍不足 |
| 本地启动/停止说明 | 文档满足 | README 存在；独立审计验证后端可启动并停止 |
| 缺陷反馈后定向返修并重新测试 | **不满足** | DevWerk P0；没有形成 review→backend→QA 的实际循环 |

### 是 Workflow 设计错，还是执行过程疏漏

- **Origin 缺失**：两者都有。人类需求已写入基线，但 Workflow 没有生成 Origin 验收门槛；后端 Agent 未实现，Review/QA/Accept 又连续漏检。
- **E2E 缺失**：两者都有。测试计划写了 Playwright，但 Workflow 只冻结 Maven 测试和前端构建；QA Agent 把 `npm run test` 的单测误当完整自动化测试。
- **返修没有发生**：Workflow 图的回退边设计基本正确；主要是 DevWerk 执行机制问题。反馈无法投递给已结束 Assignment，也没有建立持久缺陷后重新激活责任 Column。
- **最终错误验收**：Workflow 的 `accept` 设计过于宽松，同时 DevWerk 没有以未解决缺陷或缺失证据阻止 `done`。

### 应优化 Workflow 的部分

1. 需求基线中的每项可执行验收条件必须编译成 `acceptance_checks` 或结构化证据要求，不能只存在 Markdown。
2. `system_test` 必须冻结真正的 E2E 命令，并在运行前验证浏览器依赖已经安装。
3. `delivery` 应冻结启动、HTTP 探针、停止和端口回收检查。
4. `accept` 必须逐项读取结构化验收矩阵；任何 required 项缺证据时只能输出 `acceptance_failed`。
5. Review/QA 的失败输出应包含责任 Column、缺陷 ID、证据和复测条件，供有向边确定性路由。

### 应修复 DevWerk 的部分

1. 用户或后续 Column 提交的缺陷必须成为持久化 Handoff，而不是只能发送给仍存活的旧 Assignment。
2. 返回旧 Column 时应创建/恢复正确的 Agent 生命周期，并携带缺陷与原工作上下文。
3. 存在未解决的 required defect 时，Runtime 必须禁止 Task 进入 `done`。
4. Conversation Job 的 `succeeded` 必须表示用户要求的操作已完成；仅“模型本轮正常结束”不能算业务成功。

## 核心三：Token/API 成本与合理性

### 总量

| 范围 | LLM 调用 | Recorded total tokens | 占比 |
| --- | ---: | ---: | ---: |
| Conversation Agent | 96 | 933,434 | 68.0% |
| 8 个 Column Agent | 87 | 439,059 | 32.0% |
| **合计** | **183** | **1,372,493** | **100%** |

另外记录了 4,357,122 cached-input tokens；该字段单独统计，不重复加入 `total_tokens`。数据库没有价格表，因此不计算货币成本。

### Conversation 成本拆分

| 来源 | Jobs | LLM 调用 | Tokens | 判断 |
| --- | ---: | ---: | ---: | --- |
| 10 轮需求讨论 | 10 | 41 | 425,725 | 本测试刻意要求，具有业务价值，但单轮上下文增长明显 |
| 2 次用户执行操作 | 2 | 25 | 198,148 | 偏高；包含反复协议纠正和失败工具调用 |
| Mailbox 回调 | 8 | 30 | 309,561 | 明显不合理；通知数量正常，处理方式过重 |

68% 不能全部视为浪费，因为其中 425,725 Tokens 来自刻意安排的十轮产品讨论。但即使去除这些讨论，Conversation 执行与 Mailbox 仍消耗 507,709 Tokens，高于全部 Column 的 439,059 Tokens，占剩余成本的 53.6%。

### Column 成本判断

8 个 Column 共 439,059 Tokens，用于生成完整前后端代码、测试和文档，绝对值不算异常失控。但成本产出比存在问题：

- `integration_review + system_test + delivery + accept` 共消耗 202,319 Tokens，占 Column Token 的 46.1%；
- 这些后置检查阶段仍未发现 Origin 缺失和 E2E 未执行；
- 因而问题不是单纯“生成代码太贵”，而是高成本审查没有形成可靠证据。

### Mailbox 成本判断

Mailbox 共 9 条消息：8 个 `agent.assignment.completed` 和 1 个 `task.done`。全部只投递一次并正确 `acknowledged`，所以**消息数量和生命周期正常**。

不合理的是每次通知都启动完整 Conversation Agent，并加载持续增长的对话、项目、Workflow 和 Task 上下文：

- 8 个 callback Jobs；
- 30 次模型调用；
- 309,561 Tokens；
- 平均每 Job 38,695 Tokens；
- 平均每条消息 34,396 Tokens。

普通 Column 完成通知原则上只需确定性更新投影、状态和简短摘要；只有失败、冲突、需要决策或需要用户沟通时才应调用 LLM。

### 明显优化空间

1. Mailbox 默认走确定性 reducer；可自动确认的完成事件不启动 Conversation LLM。
2. 同一调度周期内合并相邻完成事件，避免每个事件单独建立 Job。
3. Conversation 使用项目状态摘要和增量事件，不重复加载完整历史。
4. Tool Schema 在调用前本地校验；可确定修复的字段错误不应消耗新的模型轮次。
5. `conversation.turn.resolve` 的证据格式应由代码构建，避免 31 次调用中 19 次被拒绝。
6. Review/Test/Accept 读取结构化验证结果，减少重复阅读源码和重复生成自然语言报告。

## 本次复盘问题总表

| 编号 | 问题 | 类型 | 级别/状态 |
| --- | --- | --- | --- |
| D-1 | 后端缺少 Origin 校验 | 交付物缺陷 | 不使用 P0/P1；未满足需求 |
| D-2 | Playwright 未执行且用例覆盖不足 | 交付/测试证据缺口 | 不使用 P0/P1；未满足需求 |
| W-1 | Origin/E2E 未转化为机器验收门槛 | Workflow 设计问题 | 需要优化 Loop/Workflow 生成质量 |
| W-2 | Delivery/Accept 缺少确定性检查 | Workflow 设计问题 | 导致自然语言报告可直接推动成功 |
| DW-P0-1 | 已知缺陷和缺失证据仍允许 `done` | DevWerk 框架 Bug | P0，错误终态/错误验收 |
| DW-P0-2 | 缺陷无法持久化回流并重新激活责任 Column | DevWerk 框架 Bug | P0，复杂工作流返修失效 |
| DW-P1-1 | Conversation Job 操作失败仍记录 `succeeded` | DevWerk 框架 Bug | P1，状态语义误导 |
| DW-P1-2 | Mailbox 正常通知触发完整 LLM 推理 | DevWerk 架构/效率问题 | P1，Token 明显浪费 |
| DW-P1-3 | Tool/协议拒绝率高 | DevWerk 契约问题 | P1，278 次调用中 40 次失败/拒绝 |

### 按三个核心给出的最终判定

| 核心 | 判定 |
| --- | --- |
| Workflow 能否创建和运行 | **部分通过**：创建、调度和终态成功；语义执行、证据门槛和返修闭环失败 |
| 交付物是否满足人类需求 | **不通过**：原型可运行，但安全需求、E2E 和返修验收不完整 |
| Token/API 成本是否合理 | **不通过**：Column 成本尚可，Conversation/Mailbox 明显偏高且存在确定性优化空间 |

## Product-owner discussion before work

No Workflow/Task existed during the following ten `mode=discuss`, `start_task=false` Jobs. Each Job was checked against the visible transcript and database.

| Turn | Job | Decision / observed issue |
| --- | --- | --- |
| 1 | `cjob_26df9e460635431aae1b5480527998a9` | Opened Vue/Spring register/login/welcome/anti-brute-force idea; Agent asked behavior questions. |
| 2 | `cjob_f1d9c132aba146519d388cfea37b3726` | Confirmed username/password/registration rules; Agent proposed JWT in `localStorage`. |
| 3 | `cjob_e71cb619886846e3a89c161f73ea0c2a` | Raised XSS risk and uniform failure response; Agent switched to HttpOnly Cookie. |
| 4 | `cjob_8141763497fb4ee58e03be2e767c5af6` | Chose browser session, five account failures/15-minute lock, IP protection; Agent suggested 30/min but initially only manual tests. |
| 5 | `cjob_696c110a5ec9484ca8975c42f072ff24` | Accepted IP 30/min and welcome username; required independent automated QA and employee-like responsibility routing. |
| 6 | `cjob_9946043ecb804cf69f93029a36cca3e7` | Set local source/start-stop delivery; challenged missing objective build/test gates and required directed Agent rework. Agent incorrectly said the Loop lacked a `requirements` Column and proposed Unix commands on Windows. |
| 7 | `cjob_2a8020a50e97433ebac97b83de0f1b3c` | Agent corrected `requirements` and admitted prompt-written commands were not Runtime checks; still inferred Agent/QA Session continuation without execution evidence. |
| 8 | `cjob_f212238aec6343139999518200b22d9e` | Asked about project-specific Workflow revision with `acceptance_checks`; Agent incorrectly equated an unconfigured Loop template with absence of the Runtime/schema feature. |
| 9 | `cjob_572709e1b79b4e79aa10c542d6b95587` | Supplied verified source facts and asked for minimum checks. Agent accepted the revise-before-Task sequence but gave the command argument as `command` instead of the API's `argv`. |
| 10 | `cjob_8aba488d37bc45fc8638742c8bab4d1f` | Corrected exact `argv`, `cwd="."`, QA's two checks, separate QA/implementation Agent identities, and Workflow Plan ownership. Agent accepted but omitted the required check `key` field; corrected in the authorization message. |

## Authorization and actual graph creation

At 09:45:46, Job `cjob_2a4979fbdfc7464f8e809c4f26ba8846` authorized work only in this order: apply existing `software.ddd_delivery` Loop, publish a project revision with frozen Windows checks, inspect that revision, write accepted requirements, save Task Plan, then create one end-to-end Task. It did not create numbered-stage Columns.

Observed tool calls: eight rejected `conversation.turn.resolve` submissions before one successful resolution; the first `loop.apply` was rejected for omitted required `product_name`, then a corrected application succeeded. Revision 1 `wfrev_f0cb7b0f7a4d4511bfa9ced4cedb198d` came from the Loop. `workflow.publish` then succeeded with revision 2 `wfrev_4e68019b346c43b8aaedf711a80c61d3`; the Agent inspected it before saving the plan and creating Task `tsk_88ca035520db421980b295b2f8e374ec`. At 09:50 local, the Task was `running/requirements`, bound to revision 2.

Revision 2 was independently checked in SQLite. It adds only these acceptance checks and preserves the original directed feedback edges:

| Column | Frozen Runtime check |
| --- | --- |
| `backend_development` | `project.command.run` argv `mvn.cmd -f backend/pom.xml test`, cwd `.`, exit 0 |
| `frontend_development` | `project.command.run` argv `npm.cmd --prefix frontend run build`, cwd `.`, exit 0 |
| `system_test` | Both of the above, exit 0 for both |

## Lifecycle audit criteria

For each Column, collect actual Column Run/Attempt and Agent Run/Session IDs, input/output, tool execution receipts, feedback artifacts, transition outcome, and source/test deltas. The test is not complete until real review/QA feedback reaches an earlier implementation Agent, that Agent performs targeted rework, and a later Column reruns objective checks. Classify the result as a DevWerk P0 only when the platform lifecycle/control mechanism fails; a generated-product defect remains a delivery finding without P0/P1 severity.

## Observed implementation stages

At approximately 10:03 local, the Task progressed `requirements → domain_design → backend_development → frontend_development` without a new Conversation Job. The four Column Agents had distinct logical Session IDs. Requirements Agent `asess_ec3f28389a1b4aa7a953ef3db1e8decf` and design Agent `asess_a896603e65f1425b9baea5123b7c884e` succeeded; backend Agent `asess_0e881af6503240f48faed7d40a0d28be` succeeded; frontend Agent `asess_d595a5d5d351422c9e175f7bd6b11a82` started. A design `project.files.write` call omitted required `content` but was corrected. Backend initially read a nonexistent `pom.xml` and omitted `content` in one write; it recovered and wrote the backend project.

Backend Agent ran `project.command.run` for Maven tests and received exit 0. At `backend_ready`, Runtime independently executed the frozen check `acceptance-33-backend_maven_test`, also exit 0. Maven Surefire XML reports 9 controller tests, 11 authentication tests, and 6 security tests: **26 tests, 0 errors, 0 failures**. This proves the first implementation gate ran, not merely that the Agent claimed code was ready. It does not prove integration or multi-Column rework yet.

Frontend Agent `asess_d595a5d5d351422c9e175f7bd6b11a82` installed dependencies and built successfully. Its first two `npm.cmd --prefix frontend run test:unit` invocations exited 1: the initial run had 7 API-test failures; after a targeted test-file rewrite, the second had 6. The same Column Agent modified `frontend/src/__tests__/api.test.js` again, ran the identical unit-test command a third time, and obtained **16 passed, 0 failed**. It rebuilt successfully and Runtime executed `acceptance-...-frontend_npm_build` with exit 0. At about 10:09 local, the Task advanced to the independently staffed `integration_review` Column (`asess_44ac58ca43474219b098731bf03b6b7a`). This is evidence of **within-Column** correction, not yet cross-Column feedback/rework.

The independent review Agent wrote `docs/pair-review.md` and completed `accepted → system_test` without a directed return. Its only direct command invocation was `npm.cmd --prefix frontend run build` (exit 0); the report's backend-test claim relies on earlier backend evidence, not on a backend command in this review run. The QA Column started with a separate Agent/Session; user-path testing and a real feedback cycle remain to be observed.

The first QA Agent completed `tests_passed → delivery`. Its direct commands were Maven test (exit 0), frontend build (exit 0), and `npm.cmd --prefix frontend run test` (exit 0). The frozen Maven/build checks also passed. However, `frontend/package.json` maps `test` to **unit tests only**; although the prior test plan specifies Playwright E2E, there was no `test:e2e` invocation in QA. `docs/test-report.md` declares all automated tests passed without qualifying that the user-path E2E check was not executed. Separately, the accepted user discussion required backend validation of `Origin` on state-changing requests. Actual `SecurityConfig.java` disables CSRF and no backend `Origin` check exists. The review and QA Agents missed this agreed acceptance behavior. Therefore first-pass `tests_passed` is **not sufficient final acceptance evidence**; the complex-task interaction gate has also not been exercised.

At approximately 10:16 local, while `delivery` was running, a new user Job `cjob_b4847c9874bd40d38eade38380d18851` reported these precise file/test facts to the Conversation Agent and requested an observable review → backend repair → QA recheck route, without direct Conversation-Agent code edits. The request did not alter the Task graph: no implementation Column was reopened, no targeted repair occurred, and the Task continued to `accept → done`.

## First-pass terminal result and defect ownership

The original Task nevertheless traversed `delivery → accept → done`, with **no return edge taken**. This `done` is not a valid product/test success result. `docs/requirements-baseline.md` explicitly requires SameSite=Strict **plus backend Origin validation**, and `docs/requirements-traceability.md` lists `REQ-LOGIN-003` with the same check; no backend code implements it. `docs/test-plan.md` requires Playwright E2E for register/login/welcome, duplicate registration, and browser-close behavior, but QA never invoked `frontend/package.json`'s `test:e2e` script. `FINAL_ACCEPTANCE.md` still says all eleven criteria passed, ignoring those accepted subrequirements and missing E2E execution.

The missing Origin validator and missing/rudimentary E2E suite are **delivery defects/evidence gaps**, not DevWerk P0 labels. The corresponding **DevWerk P0** is that authoritative requirements and required evidence were not enforced, yet Runtime still allowed `accept → done` and presented the result as successful.

The feedback Job attempted `agent.worker.send` to the already completed `integration_review` Worker using an Assignment that did not belong to it; Runtime rejected `Message Assignment does not belong to this Worker`. Conversation Agent then submitted a blocked `conversation.reply` with a successful `task.inspect` operation listed as a failure receipt, which Runtime rejected. The Job ultimately had status `succeeded`, while the visible assistant message reported `conversation.reply: A successful operation is not a failure receipt...` and no Task intervention had occurred. This is a second **DevWerk P0 communication/control failure**: the manager could not inject confirmed defects into the running directed Workflow and the user-visible/job states were misleading.

The `delivery` Agent directly executed only Maven tests and frontend build. Its `docs/delivery-report.md` presents backend/frontend startup commands and example outputs under “可运行性验证”, but no `spring-boot:run`, Vite server start, HTTP health/user path, or stop command appears in this Column's tool evidence. A described command is not a performed run/stop check. Independent startup probing is required before claiming an operable program.

## Persisted Workflow and Agent lifecycle

The Task was created at `2026-09-16T01:50:14.849Z` and reached `done` at `02:20:38.102Z`: **30 minutes 23.3 seconds** elapsed. Each Column had exactly one persisted Assignment, one Worker identity, one Agent Session, one Agent Run, and one successful Column Run. No Column was revisited.

| Seq | Column | Session | Model calls | Tool invocations | Agent duration | Persisted result |
| ---: | --- | --- | ---: | ---: | ---: | --- |
| 1 | `requirements` | `asess_ec3f28389a1b4aa7a953ef3db1e8decf` | 3 | 4 | 48.5 s | succeeded |
| 2 | `domain_design` | `asess_a896603e65f1425b9baea5123b7c884e` | 8 | 9 | 394.1 s | succeeded |
| 3 | `backend_development` | `asess_0e881af6503240f48faed7d40a0d28be` | 16 | 35 | 355.5 s | succeeded |
| 4 | `frontend_development` | `asess_d595a5d5d351422c9e175f7bd6b11a82` | 19 | 41 | 354.6 s | succeeded |
| 5 | `integration_review` | `asess_44ac58ca43474219b098731bf03b6b7a` | 12 | 31 | 193.8 s | succeeded |
| 6 | `system_test` | `asess_bd4e87dd7d634a49b78e794aa4a65197` | 12 | 25 | 124.1 s | succeeded |
| 7 | `delivery` | `asess_4a8e8a6d0a3940fc8f9f51763ed6337a` | 13 | 26 | 242.1 s | succeeded |
| 8 | `accept` | `asess_a9e2f6478e7f4cae820c76b9b2eedbda` | 4 | 13 | 55.7 s | succeeded |

Lifecycle interpretation:

- Agent isolation worked: the eight Columns did not share one giant Agent Session.
- Employee-like identity existed only for the duration of each Column Assignment. Every Assignment ended `completed`; each reported `resumes=1` and `no_progress_awaits=0`.
- The frontend employee did perform real **within-Column repair**: two failing unit-test runs were followed by targeted changes and a passing third run in the same Session.
- The required **cross-Column repair lifecycle was not demonstrated**. Review and QA both accepted on their first visit. When a confirmed defect was later supplied, the prior Worker/Assignment had ended, message delivery failed, and no directed transition reopened backend implementation or QA.
- Therefore the graph proves sequential Agent execution, but not the complex team behavior required by the [lifecycle/interaction gate](../column-agent-lifecycle-interaction-test-gate-2026-09-16.md).

## Runtime command evidence

Seventeen `project.command.run` invocations are persisted:

| Column | Commands and observed results |
| --- | --- |
| `backend_development` | Maven test twice: Agent run and frozen Runtime acceptance check, both exit 0 |
| `frontend_development` | npm install exit 0; build exit 0; unit tests exit 1, exit 1, then exit 0; rebuild exit 0; frozen Runtime build exit 0 |
| `integration_review` | frontend build once, exit 0; no new backend test in this Column |
| `system_test` | Maven test, frontend build, frontend `npm run test`, then frozen Maven/build checks; all exit 0 |
| `delivery` | Maven test and frontend build, both exit 0; no server startup or HTTP probe |
| `accept` | no command execution; acceptance was document/source inference |

The two non-zero command receipts were useful frontend test feedback, not infrastructure failures. They resulted in an eventual 16/16 unit-test pass. The system-test invocation `npm run test` resolves to `npm run test:unit`; it does **not** invoke Playwright.

## Independent delivery verification

The audit ran checks outside the Task's own claims:

1. `mvn.cmd -f backend/pom.xml test` completed with **26 tests, 0 failures, 0 errors, 0 skipped**. The first sandboxed audit attempt could not access Maven Central; with dependency network access allowed, the same command passed. This first failure was an audit-environment restriction, not a product failure.
2. `npm.cmd --prefix frontend run test:unit` completed with **16/16 tests passed**.
3. `npm.cmd --prefix frontend run build` completed successfully, transforming 94 modules and producing `frontend/dist`.
4. `mvn.cmd -f backend/pom.xml spring-boot:run` started Spring Boot/Tomcat on port 8080. A normal register → login → authenticated welcome probe succeeded; login returned one session Cookie and `/api/welcome` returned HTTP 200 with the expected username and `欢迎回来` message. The process then handled shutdown cleanly and port 8080 was confirmed closed.
5. A state-changing registration request carrying `Origin: https://attacker.invalid` returned HTTP 200 and registered the user. This is direct runtime evidence that the promised backend Origin protection is absent.
6. `npm.cmd --prefix frontend run test:e2e` discovered 10 Playwright tests, but all 10 failed before assertions because the required Chromium executable was not installed. After all launch errors were emitted, the audit interrupted the lingering web-server process. No Playwright test passed.

The authored Playwright file itself covers form presence, client-side validation, page navigation, and unauthenticated welcome redirection. It contains **no successful backend registration/login/welcome path**, duplicate-user API path, rate-limit path, account-lock path, or browser-close session expiration path. Installing the browser alone would therefore still not satisfy the agreed E2E scope.

## Deliverable inventory

SQLite contains **54 Artifact records / 197,699 bytes** for this Project:

| Artifact kind/path group | Records | Bytes | Notes |
| --- | ---: | ---: | --- |
| `backend/**` | 24 | 52,670 | Spring Boot source, configuration, and three test classes |
| `frontend/**` | 16 | 34,458 | Vue source, two unit-test files, one Playwright file, and build config |
| `docs/**` | 11 | 82,752 | requirements, design, API, test, review, and delivery reports |
| `README.md` | 1 | 3,601 | local setup plus Ctrl+C stop instructions |
| `FINAL_ACCEPTANCE.md` | 1 | 8,089 | claims all eleven criteria passed |
| `.devwerk/terminal/**` | 1 | 16,129 | terminal execution receipt |

Key documents and source trees exist and are non-empty. The artifact set is substantial, coherent, and buildable. The problem is evidence correctness, not absence of files:

- `docs/requirements-baseline.md` and `docs/requirements-traceability.md` retain the Origin requirement.
- `backend/src/main/java/com/teamauth/config/SecurityConfig.java` disables CSRF, and repository-wide inspection finds no Origin validator.
- `docs/pair-review.md` incorrectly marks disabled CSRF plus SameSite as sufficient.
- `docs/test-report.md`, `docs/delivery-report.md`, and `FINAL_ACCEPTANCE.md` count only 26 backend + 16 frontend unit tests as “all 42 automated tests”; they omit the planned Playwright suite while still claiming full acceptance.
- `docs/delivery-report.md` describes startup behavior without persisted execution evidence from the delivery Column.

## Model, Token, and API-use audit

Usage source: `data/devwerk.db.llm_usage`, filtered by this Project. All 183 provider calls were recorded as successful; tool/protocol rejection is accounted separately below. Provider/model was `anthropic / MiniMax-M2.7` throughout.

| Scope | Calls | Input tokens | Output tokens | Recorded total tokens | Cached input tokens | Provider duration sum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Conversation Agent | 96 | 868,672 | 64,762 | **933,434** | 2,260,621 | 1,922.9 s |
| All Column Agents | 87 | 367,731 | 71,328 | **439,059** | 2,096,501 | 1,476.1 s |
| **Project total** | **183** | **1,236,403** | **136,090** | **1,372,493** | **4,357,122** | **3,399.0 s** |

`total_tokens` is the usage system's input-plus-output total. Cached-input tokens are reported separately and are **not added again** to the official total. If used only as a context-traffic diagnostic, regular input + cache reads + output equals 5,729,615 tokens; that is not asserted as a billable-token number. No model price table was persisted, so a defensible monetary cost cannot be calculated from this database alone.

### Column model usage

| Column | Calls | Input | Output | Total | Cached input |
| --- | ---: | ---: | ---: | ---: | ---: |
| `requirements` | 3 | 16,195 | 2,779 | 18,974 | 4,948 |
| `domain_design` | 8 | 31,827 | 12,122 | 43,949 | 81,978 |
| `backend_development` | 16 | 71,794 | 15,391 | 87,185 | 384,918 |
| `frontend_development` | 19 | 68,106 | 18,526 | 86,632 | 702,822 |
| `integration_review` | 12 | 53,421 | 6,754 | 60,175 | 286,494 |
| `system_test` | 12 | 41,479 | 3,692 | 45,171 | 266,953 |
| `delivery` | 13 | 50,666 | 8,598 | 59,264 | 302,547 |
| `accept` | 4 | 34,243 | 3,466 | 37,709 | 65,841 |

Backend and frontend implementation were the largest delivery stages, as expected, but the entire eight-Column delivery consumed only 32.0% of recorded total tokens. Conversation activity consumed 68.0%. That 68.0% is not pure Runtime overhead: it includes the deliberately long ten-turn product discussion requested by the test. Even after removing those ten discussions (425,725 tokens), action turns plus Mailbox processing still used 507,709 tokens versus 439,059 for all delivery Columns—53.6% of the remaining total. The orchestration side is therefore still disproportionately expensive.

### Conversation-use breakdown

| Trigger | Jobs | Model calls | Recorded total tokens | Cached input |
| --- | ---: | ---: | ---: | ---: |
| Ten pre-work discussion turns | 10 | 41 | 425,725 | 272,886 |
| Two user-authorized action turns | 2 | 25 | 198,148 | 829,005 |
| Automatic Mailbox callbacks | 8 | 30 | 309,561 | 1,158,730 |

The nine Mailbox messages were eight `agent.assignment.completed` notifications plus one `task.done`. All nine were delivered exactly once and ended `acknowledged`; the final callback consumed two messages. The **message count is reasonable** for eight Column completions plus one terminal event, and lifecycle deduplication worked. The **processing cost is not reasonable**: eight callback Jobs made 30 model calls and consumed 309,561 tokens—38,695 tokens per Job, 34,396 per delivered message, or 10,319 per model call on average. Each callback activated the full Conversation Agent with growing session/project context and protocol/tool work, even when the event only required deterministic acknowledgement or a short state summary.

The `api_requests` table contains zero rows for this Project. Consequently “API calls” in this report means the 183 persisted LLM provider calls from `llm_usage`; no independent HTTP-request accounting was available from `api_requests`.

## Tool and protocol efficiency

There are **278 persisted Tool Invocation rows**: 238 succeeded and 40 failed/rejected, a 14.4% rejection/failure rate. AgentRun counters show 274 model-issued tool calls; the four-row difference corresponds to Runtime-executed frozen acceptance checks that are persisted as tool evidence but were not emitted as model tool calls.

| Scope | Calls | Successful | Failed/rejected |
| --- | ---: | ---: | ---: |
| Conversation | 94 | 66 | 28 |
| Requirements | 4 | 4 | 0 |
| Domain design | 9 | 8 | 1 |
| Backend development | 35 | 33 | 2 |
| Frontend development | 41 | 36 | 5 |
| Integration review | 31 | 29 | 2 |
| System test | 25 | 25 | 0 |
| Delivery | 26 | 24 | 2 |
| Accept | 13 | 13 | 0 |

Notable waste/error patterns:

- `conversation.turn.resolve`: 31 calls, only 12 succeeded; 19 were rejected for evidence mismatch, open-scope state, unauthorized event turns, or invalid schema.
- `conversation.reply`: 15 calls, 5 rejected, including incorrect success/failure receipt use during the defect report.
- `agent.worker.send`: 2 calls, both rejected because the target Assignment did not belong to the Worker or had already ended.
- Initial `loop.apply` omitted required `product_name`; one corrected retry succeeded.
- Column file operations had recoverable wrong-path/missing-field errors. Frontend's two failed test commands represented legitimate feedback and led to repair.

All 183 LLM requests succeeded at provider level, so the waste came from Agent/Runtime contract negotiation and post-Column orchestration, not provider retries.

## Event and notification record

The Project generated, among other events, 286 `conversation.progress`, 56 `artifact.written`, 28 each of `agent.started`/`agent.finished`, 20 each of `conversation.planning_started`/`conversation.planning_succeeded`, eight `column.started`/`column.finished`, eight `agent.assigned`/`agent.assignment.completed`, one `task.created`, and one `task.done`.

There were 20 Conversation Jobs:

- 10 explicit discussion Jobs with no execution grant;
- 2 explicit user action Jobs;
- 8 automatic Mailbox Jobs.

All 20 Jobs are persisted as `succeeded`, including the defect-injection Job whose intended operation did not occur. This makes Conversation Job success semantically weaker than “requested project change succeeded” and is one reason the UI/user-facing terminal status was misleading.

## Delivery findings (not assigned DevWerk severity)

### Delivery defect D-1 — Origin validation is missing

Backend Origin validation was explicitly confirmed, written to the baseline, and traced as `REQ-LOGIN-003`. Implementation omitted it. A hostile-Origin HTTP probe demonstrated the missing behavior. This is a security-requirement defect in the generated product.

### Delivery/evidence gap D-2 — Browser E2E is incomplete and unexecuted

The authored browser tests cannot currently start and do not cover the required successful cross-tier flow even after browser installation. This is a delivery/test-evidence gap. It does not invalidate the 26 backend tests or 16 frontend unit tests; it means those narrower suites are insufficient for full acceptance.

## DevWerk findings by severity

### P0-1 — False-positive final acceptance

Review reinterpreted SameSite as equivalent to the missing Origin check, QA did not execute the planned E2E suite, and final acceptance declared all requirements passed. DevWerk allowed `done` without machine-verifiable evidence for accepted requirements. The DevWerk bug is not the product's missing code itself; it is loss of authoritative requirement/evidence provenance across Columns and an acceptance transition that did not block on the gap.

### P0-2 — Confirmed defect could not enter the directed rework graph

The user reported exact defects before final acceptance and requested review → backend repair → QA recheck. Messaging a completed Worker/Assignment failed, no durable defect/Handoff was attached to an active or reopened Column, and no graph transition occurred. The Task nevertheless reached `done`. Under the complex-task gate, absence of actual feedback delivery, targeted employee repair, and objective retest is a P0 lifecycle failure.

### P1-1 — DevWerk accepts unsupported evidence reports

Review, test, delivery, and acceptance documents contain statements unsupported by their Column command receipts: all automated tests passed, CSRF protection is sufficient, and startup was verified. The generated statements themselves are delivery-report quality issues; the DevWerk P1 is that evidence storage and final acceptance do not distinguish an Agent assertion from a verified Runtime receipt, so downstream acceptance trusted unsupported claims.

### P1-2 — Conversation and Mailbox overhead dominates useful work

Conversation activity used 96 model calls and 933,434 tokens, versus 87 calls and 439,059 tokens for all delivery Columns. Part of this is explained by the intentional ten-turn product discussion. However, nineteen failed turn-resolution calls and eight LLM-heavy Mailbox callbacks materially increased cost without improving the product. Callback delivery count/lifecycle was correct; callback reasoning and repeated full-context loading were disproportionate.

### P1-3 — Contract rejection rate is high

Forty of 278 tool evidence rows failed or were rejected. The Column side generally recovered; the Conversation side did not reliably translate rejection into a truthful terminal result. Contract ergonomics and deterministic validation should be improved before adding more autonomous behavior.

## Final verdict

**Do not accept this run as a successful complex software delivery.** The correct result is:

- **Implementation value:** usable local prototype; source exists; backend runs; core backend tests, frontend unit tests, and frontend build pass.
- **Runtime bookkeeping:** Task=`done`, eight Column Runs=`succeeded`.
- **Product acceptance:** **failed** because a confirmed security behavior is absent and required E2E evidence is missing.
- **Workflow acceptance:** **failed** because review/QA false-positive evidence reached final acceptance and confirmed feedback could not trigger directed rework.
- **Lifecycle gate:** **failed** because there was no successful cross-Column feedback → targeted repair → retest cycle.
- **Efficiency:** **failed for optimization** because orchestration/Conversation used more than twice the recorded tokens of delivery execution.

Before this scenario can count as passing, a new run must at minimum: preserve Origin validation as a machine-checkable requirement, freeze a real browser/integration acceptance command with its runtime dependency prepared, reject unsupported report claims, persist a defect that can reopen the responsible implementation Column, deliver that defect to the responsible Agent lifecycle, and require QA to rerun objective checks before `accept` may produce `done`.
