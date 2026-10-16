# Hermes 核心分支：Vue/Java 登录防刷黑盒回归

分支 `feature/hermes-conversation-core`，服务已在无运行中 Conversation Job/Task/Agent Run 的边界重启，`/v1/health` 返回 Supervisor/Gateway running。旧测试 Project 与失败记录不修改。新 Project `prj_b2ff5d6dae2243828f5d6ae34a2b3ccf`，路径 `data/projects/hermes-core-login-20260930-093136`。

测试目标沿用前次真实案例：至少五轮用户讨论后才明确授权，交付简单的 Vue 3 + Java 17/Spring Boot 登录与防刷 Demo。仅登录、预置账号、欢迎状态和登出；H2 文件持久化允许，不能依赖缓存服务。IP 与用户名分别跨另一维度累计失败，滚动五分钟三次失败后锁十五分钟，成功清零，401/429/200 与前端提示一致。前后端真实运行、自动测试、Windows 本机说明进入终验。测试者只使用 HTTP Project/Conversation API，不代 Agent 创建 Workflow/Task 或修改交付物。

每轮 Job 结束后记录 `turn_contract`、公开答复、Workflow/Task 数量。前五轮讨论必须没有规划副作用；授权轮观察单 Task 多 Column、Worker Session、反馈和 MailBox 是否健康。执行期间约每三分钟检查一次流程状态；若长时间无进展，先查 Job/Run/工具回执，再决定修复。终态后才评价交付物质量，不以某个 Run succeeded 代替项目交付。遇到 P0 先补复盘与代码方案，再做最小可追踪修复、回归、独立提交，并在新 Project 重新测试。

第一轮已通过正式 API 提交：Job `cjob_5459a114ffb342be9d8ecf46c017d215`，`mode=discuss`，明确不得创建 Workflow/Task/代码。后续依次确认：技术与账号范围；防刷规则及持久化；跨维度计数与成功清零；HTTP/API/前端/验收边界；最后单独授权实施。具体 Job 与结果随测试追加。

## 讨论阶段记录

| 轮次 | Job | 合同与副作用 | 观察 |
| --- | --- | --- | --- |
| 1 | `cjob_5459a114ffb342be9d8ecf46c017d215` | `discussion`，0 Task，Workflow 404 | 明确只讨论，Agent 给出问题清单。 |
| 2 | `cjob_076a68d9c32644e9a73c1c515d4d7b4a` | `discussion`，0 Task，Workflow 404 | 正确继承技术/范围；建议 JWT + HttpOnly Cookie，下一轮由用户选择 HttpSession。 |
| 3 | `cjob_e5e05dfa9213458fa5ee858902cef016` | `discussion`，0 Task，Workflow 404 | 接受 HttpSession、两个独立失败维度与锁定规则。公开答复中“本轮仅讨论，未新建任务”重复两次，属于呈现瑕疵，不影响合同或副作用。 |
| 4 | `cjob_d2774a5b56aa4dc284ab1bc483bd4284` | `discussion`，0 Task，Workflow 404 | 正确继承跨维度计数、成功清零和本机边界；建议以手工验收为主，第五轮明确补充自动测试。呈现提示仍重复。 |
| 5 | `cjob_67382434fc734fe49ebb395d7cd6b60f` | `discussion`，0 Task，Workflow 404 | 保留主体约束并承诺等待授权，但把 `/api/me` 改成 `/api/auth/me`、擅加 `/api/auth/status`，前端草图出现独立路由，第三次失败的 429 与“第四次 429”相矛盾。属未实施的需求漂移，下一轮明确纠正。 |
| 6（纠偏） | `cjob_45500a29b64e49108054e7ebf59a5cdd` | `discussion`，0 Task，Workflow 404 | Agent 准确确认 `/api/me`、无 status/轮询及独立路由、第三次失败当次 429，并等待授权。 |

六轮讨论均由同一 Project Agent Session 处理，无提前 Workflow/Task。第五轮存在自然语言需求漂移，依靠第六轮用户纠偏才恢复；后续须验证实施时是否又把已排除内容带入计划或源码。

## 授权与执行

授权 Job `cjob_6403f387c1e944dfbe316849ef2c566b`：正式 HTTP API `mode=auto,start_task=true`，当前用户消息明确开始实施。要求一个贯穿需求/后端/前端/集成测试/交付的 Task，每 Column 独立 Worker，按六轮讨论和最终纠偏执行，禁止额外产品接口。提交时间约 2026-09-30 09:52 北京时间。后续以持久授权、Workflow/Task/Column 回执为准，不以自然语言承诺为完成证据。

09:55 左右，Job `running`，持久 `phase=execution`，Grant `grant_98d3639d1ea0488abbe94129247557e5`。Workflow revision 1 和 WorkflowPlan 已保存，8 个 Column（requirements、domain_design、backend_development、frontend_development、integration_review、system_test、delivery、accept），Task 尚未创建。09:58 左右，主 Agent 曾在 `workflow.acceptance.configure` 收到 `NonBehaviorCheck` 和 scenario/report_path 校验失败，继续修改后，最新成功 revision `wfrev_ee1da040ba764dec98e7d16941f16b4e` 返回 `ready_for_task_plan=true, remaining_diagnostic_count=0`。此时属于可纠正的工具参数迭代，尚未观察到流程阻塞。WorkflowPlan/Definition 未出现额外 status endpoint、错误 `/api/auth/me` 或 JWT 字面内容，但具体 API 应继续查 TaskPlan 与源码。

10:01 左右，授权 Job `succeeded`；TaskPlan `tplan_c919971370424761b6cb5b270b595cad` 已保存并物化为**一个** Task `tsk_80a31d6420134d6e8cc785296a4d040d`。`requirements` Column Run 成功，`domain_design` 正在运行。TaskPlan 明示 `/api/me`，`non_scope` 包含 JWT、轮询端点和错误 `/api/auth/me`，没有见到前轮的产品接口漂移。Job 公开 reply 标题称“待执行”，但 Task 持久状态已为 `running`；后续以 Task 状态为准，并检查最终用户通知是否准确。

10:04 左右，`domain_design` Assignment 持续产生成功的文件工具回执。与 `requirements` 的 Worker 实例、Assignment 和 Agent Session 均不同，证明前两列没有共用一个模型上下文。10:07 左右，`domain_design` 已成功，Task 自动推进至 `backend_development`，无用户/测试者手工改 Task 状态。

10:10 左右，`backend_development` 仍运行，Assignment 累计 23 次模型调用、48 次工具调用；两次 `mvn test -q` 分别失败，原始回执约 340 KB 和 76 KB，Worker 正依据失败继续修复。该 Assignment 的 Context checkpoint revision 1 已把模型输入估计从 117,479 压到 73,603 tokens，摘要约 6 KB；说明大输出留在持久回执中而非无限堆进当前模型上下文。此时尚不能判定最终测试或 Context P0 已通过，需继续观察后续迭代与 Provider 错误。

10:14 左右，后端仍运行，累计 32 次模型请求、57 次工具调用；Context checkpoint revision 2 记录估计输入 427,058 → 58,394 tokens，随后继续模型调用，没有当场 Provider overflow。最新 Maven 失败包含 401/429 防刷断言不符（15 tests，9 failures，2 errors），属于待 Worker 修的真实交付缺陷；Runtime 没有据此放行该列。需关注是否形成无进展重复、耗尽模型/时间预算及通知恢复是否能处理。

10:20 左右，后端仍运行，约 57 模型请求、80 工具调用，其中写文件 40 次、读文件 20 次、命令 8 次。最近工具调用正在阅读/搜索源码并读取回执，非同一失败参数无脑重试。最新一次 `mvn test -q` 的失败为 `SessionConfig.java` 引用了缺失的 Spring Session 类，Worker 正处于编译纠错；先前 401/429 测试尚未通过。继续观察预算与列完成，避免把“有活动”误判为已收敛。

## 本 Project 的终态与复盘

后端首次 Assignment 于 02:25:58 UTC 用尽 100 次模型请求、122 次工具调用，`execution_budget_exceeded` 使 Task 进入 `recovering/blocked_runtime`。Project Agent 的监督 Job `cjob_d64c161b4acb45e0bbb210466588aa1d` 成功读取任务与失败回执并 retry；但其自身继续执行约 100 轮、108 次工具调用，期间多次重复成功 retry（最终五条成功回执，均属于同一通知 Job），又两次尝试向 blocked Worker 发消息却被过严准入拒绝。这个 Job 既浪费上下文又把原实现的两次自动恢复额度耗尽。新后端 Assignment 确实获得独立的 100 次模型预算，首次从 9 failures/2 errors 改进到 6 failures/0 errors，但于 03:03:36 UTC 再次预算失败。Task 最终 `recovering`、后端 Column sequence 3/4 均 `interrupted`；其余前端/集成/交付列从未启动。Gateway 无活跃 Job，通知只报告真实失败，没有虚报交付。

交付物的具体业务缺陷也已定位：`AntiBruteForceService.createLockAndThrow` 在同类内部调用，`REQUIRES_NEW` 没经过 Spring 代理；抛出锁定异常会回滚锁记录，测试中第三次失败后 `isLocked=false`。这是本次 Worker 反复修不好的交付缺陷，不能靠放宽 Runtime 验收消除。框架问题则是主 Agent 无法把诊断输入交给 blocked Worker、同通知重复 retry 和监督空转；详细设计与最小修复分别见 `2026-09-30-supervision-duplicate-retry.md` 和 `2026-09-30-supervision-worker-handoff-bound.md`。修复提交 `fa63c91`、`8ae8c4f`；第二项专项 8 passed，Conversation/Assignment/Context 扩大回归 131 passed。旧 8000 服务仍加载修复前代码，待确认全局空闲后重启。下一轮必须新建 Project，复用同一六轮讨论/纠偏与授权，不修改本交付物来冒充黑盒成功。
