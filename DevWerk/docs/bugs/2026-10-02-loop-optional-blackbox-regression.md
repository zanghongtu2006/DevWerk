# Loop 可选与 Kanban 作者工具：隔离黑盒回归

状态：执行中。使用隔离端口 8001、隔离数据库 `data/evals/2026-10-01-loop-optional-blackbox/devwerk.db`，未操作用户的 8000 服务。通过 `/v1/projects` 和 `/v1/projects/{id}/conversation` 模拟 Web 请求；脚本只在测试端发送请求与读取状态，不属于用户交付路径。

## 项目一：恢复已创建的四列 Task

- Project `prj_0d2e7f5068e14db09c0545bcd017b6ef`：六轮仅讨论登录 Demo，再授权创建无 Loop 的四列 Workflow 与 Task `tsk_88749529a9f24c568243c52a5029a50a`。Column 为 `backend_implementation`、`frontend_implementation`、`integration_review`、`acceptance_testing`。
- 后端 Worker 首次因为 `column.complete` 的可选空对象输出格式而停止；修复后第一次恢复又因执行授权之后重复 `task.retry` 被误判而停止。分别由 `e0f6339` 与 `accbdfe` 修复。
- 第二次恢复的后端 Worker 原本可完成 Maven 测试，但四次较早的完成拒绝和后续已修复的命令失败仍使它在第五次提交时被暂停。`90987b3` 收窄拒绝计数重置与 Worker 自选检查的修复规则；53 项相关单测、完整 520 项单测通过。第三次由 Conversation Agent 授权、重试同一 Task 后，后端及前端 Column 均成功，Task 推进到 `integration_review`。
- 集成 Worker 在 `project.command.run` 前台运行 `mvn spring-boot:run`，触发 600 秒命令超时，随后改用 Maven 测试等方式探索验证。该命令选择造成长时间浪费，但集成列最终成功，Task 已推进到 `acceptance_testing`。验收 Worker 也曾尝试 `spring-boot:run`，需继续观察它能否使用有退出条件的检查完成验收；若再失败，按真实失败回执复盘，不能仅凭时间推断 P0。
- `app/v1/process_runner.py::run_command` 在调用完成时回收子进程树，这是现有确定性生命周期；但 `project.command.run` 的公开描述没有交代这一点。验收 Worker 连续尝试“调用一启动服务、调用二访问 HTTP”，服务无法跨调用存在。最小修复方案是只修改 `app/v1/capabilities.py` 的该工具描述：前台服务会超时、子进程不会跨调用保留；如需 HTTP 验收，在同一个有界命令内启动、等待就绪、执行断言并清理，或使用会自行退出的测试命令。保持执行器及权限模型不变，并加工具目录描述回归。当前服务为避免中断两个 Task 暂不重启，该说明将在下一次隔离黑盒启动时生效。

## 项目二：从新 Project 重放同一案例

- Project `prj_d0e210ee28414908ae370984815895b4`：重放完全相同的六轮讨论和第七轮授权。六轮期间无 Task。授权轮的 `workflow.plan.save` 曾四次被 schema/领域契约拒绝，第五次保存成功；Agent 后续调用 `loop.apply`、`task.plan.contract/validate/save` 与 `task.create`，已创建 Task `tsk_df63ca677bc941d08c332ecd5e93fdb6`。该项目选择了 Loop 模板，符合“模板可选”路径；是否顺利派生 Worker 与完成交付仍待回归。
- Agent 在 Task 创建前写了 `SPEC.md`、`CREDENTIALS.md` 两个项目文档，没有写测试脚本。此行为属于当前通用 Conversation Agent 的直接工具权限；不应把“通用 Agent 可写文件”误判为必须预写脚本的旧门槛。
- 截至观察点，Task 已依次完成 `requirements` 与 `domain_design`，正在 `backend_development`。

## 测试环境隔离注意

两个 Project 的文件目录与数据库对象独立，但同一台 Windows 主机上的 Java 进程和默认 HTTP 端口并不隔离。项目一集成 Worker 曾按进程名清理 Java；因此若并发时项目二的 Java 测试失败，必须先排除测试相互干扰。下一轮完整黑盒应串行运行，不能把并发资源冲突误报为 DevWerk 流程故障。

## 待验证

1. 两个 Task 的 Column 转移、失败反馈、责任列重验、Mailbox 通知及最终状态。
2. 交付完成后，再核对 Vue/Java 登录、防刷、401/429、两条 API 与无登出端点等约定；区分流程缺陷与交付物质量问题。
3. 记录无效工具调用、模型/工具调用数与长命令的成本。仅在有阻塞证据时再改代码，保持小范围提交。
