# Vue + Spring Boot 登录安全软件开发复测

日期：2026-09-15  
性质：真实 Conversation Agent + Workflow 启动测试记录；截至首个 Column 完成的快照，不是最终软件验收。

## 测试目标与范围

沿用 09-12 案例：Vue 3 + Vite + Element Plus 登录、注册、鉴权欢迎页；Java 17 + Spring Boot 3.x + Maven 后端；H2 文件持久化、BCrypt、30 分钟无状态 JWT；用户名和 IP 独立防刷、注册 IP 限流；JUnit/Spring Boot 接口测试与 Vitest。由测试者和 Conversation Agent 先讨论，再明确授权启动。

有效测试 Project：`prj_8d5d626356124d43bfca175ebe6db0aa`  
项目目录：`D:\workspace\codex-devwerk-project-files\vue-spring-auth-network-rerun-20260915-133032`

本次不修改 DevWerk 业务代码、不清理原用户数据，不监控长期后续 Task。当前工作树中的既有用户修改均保留。

## 实际步骤与证据

| 步骤 | 用户/测试动作 | Conversation Agent 与 Runtime 的实际结果 |
| --- | --- | --- |
| 0 | 启动当前工作树中的 DevWerk，检查 `/v1/health` | Gateway 与 Supervisor 均为 `running`。第一次在受限网络启动时，Provider 连接 MiniMax 报 WinError 10013；该 Job 无业务工具和持久业务副作用。停止受限实例，在允许外联的环境隐藏重启。此环境阻断不计 DevWerk P0。 |
| 1 | 新建有效独立 Project；要求先列出关键决策与交付流程，不启动开发 | Job `cjob_8f95843df52643e1b4f8ec0d3a123641` 成功；Agent 给出讨论回复。Workflow、Task Plan、Task 均为 0。 |
| 2 | 确认技术栈、JWT、注册字段与锁定原则；继续只讨论持久化和防刷 | Job `cjob_ea30507f120d4e96b63e885d53126a69` 成功；Workflow 和 Task 仍为 0。Agent 初提单一 `username+IP` 计数，换 IP 可绕过，因此未采纳。 |
| 3 | 明确纠正：用户名 5 次失败锁 15 分钟；IP 15 分钟内累计 20 次失败阻断 15 分钟；注册同 IP 10 次/小时。另确认 BCrypt、可信连接地址、sessionStorage、JUnit 可注入时钟、Vitest | Job `cjob_9946a67998874596b3c1891a37ff35a9` 成功；Agent 复述双维度策略及测试，Workflow 和 Task 仍为 0。 |
| 4 | 明确说“现在开始开发”，选择现有 `software.ddd_delivery`，要求一个端到端 Task，只有真实派发后才报告 | Job `cjob_5ce37c35eac64160a9283064512ae8f5` 成功；回复仅报告已落地的 Workflow、文件、Task Plan、Task。Workflow `wfrev_1cea7a5d21cd4cba8937fcfedd9f8718`；Task Plan `tplan_353c0383c9f0407a9cdca5f6593815fb`；Task `tsk_c5b1faa3e2f14c1ba6ee05a4e850597c`。 |
| 5 | 只读检查 API、文件和 Column Runs | Workflow 来源 `software.ddd_delivery` 1.2.1，列顺序为 `requirements → domain_design → backend_development → frontend_development → integration_review → system_test → delivery → accept`。只有 1 个 Task 和 1 个 Task Plan；`docs/requirements.md` 已写。Task 在 `requirements` 成功后自动进入 `domain_design`，快照状态为 `running`。 |

第一次网络阻断的诊断 Project：`prj_a18f6f2d99684a14b3900953ed9f5505`，失败 Job `cjob_48b63f784e2b4ec398b2e965b50bfa69`。该 Project 只用于环境排查，不并入有效业务测试。

## P0 级别 Bug 记录

**截至 Step 5：没有在有效项目中观察到 P0。** 09-12 的“讨论时提前应用 Loop、Columns 再拆成 Tasks、保存计划后无法创建 Task”在本次同案例启动链路中均未复现：前三轮零业务建图，明确开工后只建一个交付 Task，首个 Column 成功且自动进入下一列。

这不证明后端、前端、审查、测试和最终交付已通过。它们仍由运行中的 Workflow 继续执行；若后续失败，需要依据对应 Task、Column Runs、Agent Runs、事件及产物另行诊断，不得把当前首列成功写成完整软件成功。

## 非 P0 的模型/需求质量观察

1. 第二轮 Agent 建议的单一 `username+IP` 计数可以被换 IP 绕过。测试者在授权前纠正为用户名与 IP 独立计数；最终需求文件采用修正方案。它是讨论阶段建议问题，**未观察到错误实现**。
2. 第一轮 Agent 文本称每个 Column “由同一 Worker 顺序执行”，与实际 Loop 的独立 Column Agent 设计不符；实际 Workflow 模板列定义仍分离。此处不以模型描述代替 Runtime 证据。
3. `docs/requirements.md` 中“JWT 存储于后端内存（或对称密钥签发）”表述不够精确，与无状态 JWT 目标存在歧义；后续设计与代码验收应以对称密钥签发、后端不保存 token 会话为准。

## 复核入口

- `GET /v1/projects/prj_8d5d626356124d43bfca175ebe6db0aa/conversation`
- `GET /v1/projects/prj_8d5d626356124d43bfca175ebe6db0aa/workflow`
- `GET /v1/projects/prj_8d5d626356124d43bfca175ebe6db0aa/tasks/tsk_c5b1faa3e2f14c1ba6ee05a4e850597c`
- [需求基线](D:/workspace/codex-devwerk-project-files/vue-spring-auth-network-rerun-20260915-133032/docs/requirements.md)

