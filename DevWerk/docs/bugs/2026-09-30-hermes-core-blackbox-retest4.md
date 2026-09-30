# Hermes 核心分支黑盒复测 4：行为验收窄入口后

服务在零运行 Job/Run/Task 时重启，加载提交 `083ec79`。新 Project `prj_e858364269594cd1836e64a83ca335b9`，目录 `data/projects/hermes-core-login-retest4-20260930-163528`。正式 HTTP API 原样复用前次七条消息：六轮逐轮讨论时 `start_task=false,mode=discuss`，每轮检查 Job 成功且 Workflow/Task 为零；第七轮明确授权 `start_task=true,mode=auto`。交付过程不代 Agent 建计划或修改代码，执行期约三分钟观察一次，终态再看质量。

| 轮次 | Job | 结果 |
| --- | --- | --- |
| 1（讨论） | `cjob_bf886a1de2834096a13cfa9f092110cf` | succeeded；Workflow 0，Task 0。 |
| 2（讨论） | `cjob_864bbbd6498645699085bcd496474d5f` | succeeded；Workflow 0，Task 0。 |
| 3（讨论） | `cjob_cd37d884a4234dbf9e0b910cb2e98829` | succeeded；Workflow 0，Task 0。 |
| 4（讨论） | `cjob_b01aa36292e3402291e86340b9ce60fb` | succeeded；Workflow 0，Task 0。 |
| 5（讨论） | `cjob_92f6e7d441b941409874c72a83d3bb80` | succeeded；Workflow 0，Task 0。 |
| 6（纠偏讨论） | `cjob_a4e6af4f9e9044f68e92e1ad2fe5e8e9` | succeeded；Workflow 0，Task 0。 |
| 7（明确授权） | `cjob_4e6690dfb6f842efa1e44d52a876b37d` | succeeded；一个 Task 已创建并进入 Column。 |

最终授权 Job succeeded，Grant `grant_68dd9a7ca8374e4b947ce9890901f35d`，一个完整流程 Task `tsk_ffe1acb22a334a79b53f398974650242` 创建。`requirements` 与 `domain_design` 各由独立 Worker 成功完成；`backend_development` 三个独立 ColumnRun/Assignment 分别约 94、95、95 模型迭代后因 Assignment 累计预算耗尽中断，Task 现为 `recovering/blocked_runtime`，前端以后列未启动。主 Agent 收到 Mailbox 后做过两次真实 `task.retry`；第一次成功向被阻塞 Worker 发送诊断，第二次 `agent.worker.send` 因目标非当前 Assignment 被拒绝，随后仍完成 retry。第三次阻塞通知未继续无限重试。Mailbox 是事件通知，没有被当作 Worker 对话。源代码与文档仍在 Project 目录，未代 Agent 修改。

三个 backend Worker Run 共执行 84 次 `project.command.run`，其中 62 次失败；命令回执原始总量约 5.7 MB，主要是反复 Maven 测试。第三次 Run 最后已取得一次 28 项 Maven 测试通过，但准备 `backend_ready` 前预算耗尽。冻结 `backend_development` 行为验收命令由新窄入口创建为 `mvnw.cmd test -Dspring.profiles.active=test --report reports/backend-tests.json`；Maven 本身不接受此 `--report` 且不会写要求的 `devwerk.acceptance-report.v1`。Worker 后来识别出这一点，尝试手工补报告，但冻结的 Runtime 命令仍会失败。`frontend_development` 同样冻结 `npm run test:unit --report ...`，有相同风险；系统测试、交付、终验部分原始检查也缺可靠的报告生成入口。因此目前阻塞不是单纯预算太小：计划准入允许了*语法上含报告路径、语义上不能产出报告*的命令，Worker 无法改已冻结的 Workflow。最小修复方案见 `2026-09-30-behavior-runner-script-contract-fix.md`。
