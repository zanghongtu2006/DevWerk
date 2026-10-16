# Hermes 核心分支黑盒复测 3：规划控制面修复后

服务在无运行 Job/Run 时重启，加载提交 `646508b`（授权工具生命周期）与 `3906cb3`（归档结果分页）。新 Project `prj_411cc4300702426092245f2d8e0da99b`，目录 `data/projects/hermes-core-login-retest3-20260930-155149`。复用上一轮相同七条用户消息（原消息 ID 777、779、781、783、785、787、789），前六轮通过正式 API 逐轮讨论，最后单独明确授权。不代主 Agent 建 Workflow/Task，也不修改测试交付物。每轮检查真实 Job/Workflow/Task 状态；Kanban 执行期间约三分钟检查一次。

| 轮次 | Job | 结果 |
| --- | --- | --- |
| 1（讨论） | `cjob_9a11b6d8154d46969f80318f4dd58ba3` | succeeded；Task 0。 |
| 2（讨论） | `cjob_a3af9f04b01040bd826b03bed57c52be` | succeeded；Task 0。 |
| 3（讨论） | `cjob_3ccd1eee83344bd4907238572fcd32c2` | succeeded；Task 0。 |
| 4（讨论） | `cjob_9d3af3922f144a718e4b949ff24d4967` | succeeded；Task 0。 |
| 5（讨论） | `cjob_5a6c013c23974e02b877ec2f18d1ee7a` | succeeded；Task 0；Workflow 404。 |
| 6（纠偏讨论） | `cjob_2df1d49775844793a35997b58082bf10` | succeeded；Task 0；Workflow 404。 |
| 7（明确授权） | `cjob_593fef01847549a2abbaa68f42257478` | failed；Grant 与 Workflow 已落地，Task 0。 |

授权轮最终错误：`ConversationProtocolStalled: Conversation Agent repeated an identical failed tool operation without changing its arguments: workflow.acceptance.configure`。23 个持久工具回执中，`workflow.acceptance.configure` 调用八次均失败；前几次缺 `scenario_ids`/`report_path` 成对字段，一次 `project.command.run` 缺 `argv`，几次尝试 Maven `compile`、文件存在判断或 `echo` 充当行为验收，被 `NonBehaviorCheck` 正确拒绝。第 21 次 `task.plan.validate` 仍显示五个必需 Column 缺行为验收，且第二次改写需求文件后继续引用旧写入收据，另有 `EntryEvidenceFileChanged`。模型也在成功授权后重试授权、已建 Workflow 后重试 `loop.apply`；虽然 Provider 工具目录已收起授权工具，模型仍可从同 Run 历史回忆并构造该调用，Dispatcher 的不可升级边界正确拒绝了它。`automation/workflow` 路径返回 404 不是 Workflow 缺失，`loop.apply` 与 `workflow.inspect` 回执已证明 Workflow 存在；该 HTTP 路径并非当前 Workflow 查询端点。

本轮没有进入任何 Column，因此不能评价 Worker/邮箱或 Vue/Java 交付质量。下一项独立方案见 `2026-09-30-acceptance-runner-authoring-fix.md`。
