# Hermes 核心分支黑盒复测 9：验收合同与恢复链修复后

状态：进行中。服务在 8000 端口加载至 `192ca8f`，使用与第八次完全相同的六轮讨论及第七轮授权输入。前六轮逐轮检查 Conversation Job 成功且 Workflow/Task 数量为零；授权后观察 Task、Column、验收、Mailbox 通知与自主恢复，最后再评估交付物。

Project `prj_4859dd2f5bd74077a313f2b109258fb4`，目录 `data/projects/hermes-core-login-retest9-20261001-101339`。前六轮 Job 为 `cjob_d7f213f9b2994db089eaf87454f9e052`、`cjob_eaa2261584a0462d9f1b110443c45c59`、`cjob_bd72d005129244d7887b832f88ae21bd`、`cjob_cd302a1c17f74ec897a575208b9c8dcf`、`cjob_dd84201d518a4f23b135a90619caf253`、`cjob_1e23408c1c6c476b8944874b930a1b8c`，全部 succeeded；每轮检查 Workflow/Task 均为 0。第七轮授权 Job `cjob_aff8d2aede7749b58189d8757c289000` succeeded（20 次模型迭代、23 次工具调用），创建单 Task `tsk_4f65dc0a7adb4da4a02454c3d1f39c9f`；`requirements`、`domain_design` 已 succeeded，现运行 `backend_development`。

授权 Run 先三次尝试 `workflow.acceptance.runners`，分别因脚本格式/报告参数不符合要求被拒，随后使用 `workflow.acceptance.configure` 发布直接执行的 `scripts/run-backend-tests.bat` 和 `scripts/run-frontend-tests.bat`。复查工具顺序确认：Conversation Agent 在发布前已创建这两个脚本，因此 `6053a03` 修复的直接脚本识别缺口不是本次失败的直接原因。backend/frontend Column 只允许写各自目录，后端 Worker 无权修正已存在的根目录脚本。另有 system_test 的内联 PowerShell 空场景报告、delivery/accept 的文件存在性检查；独立报告参数的准入修复见 `9ea680f`，本次冻结 Task 不热改。

后端 Worker 的 Maven 测试经修复后已成功；工具调用 81 尝试写 `scripts/run-backend-tests.bat`，被本列 `backend/**` 写入边界拒绝，调用 82 改写 `backend/reports/backend.json`，与冻结报告路径 `reports/backend.json` 不一致。随后 Worker 在自身目录创建可运行的替代脚本，26 个 JUnit 测试通过，生成根目录的 13 场景报告；但冻结验收仍调用旧脚本，正式执行的进程退出码虽为 0，stderr 显示 Maven 命令失败，且未新写报告，因此 Runtime 以 `AcceptanceEvidenceInvalid` 拒绝 `backend_ready`。这确认问题是已冻结的脚本不可由本列修正，不能用替代脚本代替正式合同。

后端首轮 Run `arun_c2387d15db1c470d98620c48cf810500` 最终 95 次迭代、120 次工具调用，因 Assignment 累计模型/工具/恢复预算耗尽 failed，Task 进入 `recovering/paused`。自动监督 Run `arun_2c1937c6bbf4457fac72c8367361b785` 先因状态不符被拒绝 `task.reopen`，随后成功 `task.retry` 同一 Task。新后端 Run `arun_97707534e6d5422ea061fbbd6570c84d` 已启动；其 Assignment 上下文含 `retry_failure`。没有新建 Task，MailBox 仅作为监督通知入口。继续观察第二次后端执行与列验收。

第二次后端 Run 在 19 次迭代、35 次工具调用后遇到 Provider `overloaded_error (529)`，标记 `LLM_OVERLOADED`。Runtime 在同一 Assignment `assignment_5b22667fafb84c728d1b125cb36f1eac` 上启动续跑 Run `arun_cd98b156c2504cdc93c3e3965620b718`，Task 仍 `running/backend_development`。这是 Provider 暂时过载的恢复，不归为 DevWerk P0；需继续看是否可推进。

续跑 Run 最终提交 `requirement_gap`，其输出明确指向上述脚本可写范围与验收失败，而非需求歧义。Workflow 因此依次重新运行 `requirements` 和 `domain_design`；这两列均确认无缺口，随后再次进入 `backend_development`。这是已经观察到的错误回流，不能当成有效修复。当前第 7 个 Column Run 正在执行，仍需观察终态。
