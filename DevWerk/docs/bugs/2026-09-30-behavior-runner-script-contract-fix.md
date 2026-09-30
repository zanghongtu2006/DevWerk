# P0：冻结行为验收命令不能生成报告，Worker 在开发列耗尽预算

状态：方案先行；依据新 Project `prj_e858364269594cd1836e64a83ca335b9` 的 1 个 Task、3 次 backend Assignment、真实命令回执及当前 Workflow revision。只处理交付阻塞，不扩展 Agent 架构或放松验收。

## 根因链

六轮讨论保持零副作用，第七轮主 Agent 成功派生 Task，前两列推进正常。主 Agent 在 `workflow.acceptance.runners` 第二次调用中提交 `mvnw.cmd test ... --report reports/backend-tests.json` 和 `npm run test:unit --report ...`。`app/v1/capabilities.py::_workflow_acceptance_runners` 仅检查 argv 含 report_path token，于是冻结为行为检查；但 Maven 不识别 `--report`，普通 npm test 也不会自动产出 DevWerk 所需 JSON。`known_nonbehavior_command` 只拒绝确定无行为断言的命令，正确不推测 `mvn test` 是否执行测试；它无法静态证明报告副作用。Worker 的完成路径执行冻结检查，无法自行更改 Workflow revision；第三次 Run 取得 Maven 28 tests passing 后意识到 `--report` 无效，却在修补中耗尽预算。增加预算只会延长错误规划的追逐。

另外，`backend_development`/`frontend_development` 应各拥有可写的测试入口脚本，`system_test` 可写 `scripts/acceptance/**`，后续 delivery/accept 可重跑已经生成的入口，但每列须输出各自新鲜报告。现有 Loop 指令没有明确告诉 Worker“实现冻结的报告入口并尽早调用完成”，使其在 Maven 失败循环中花费大量迭代。第二次监督指向旧 Worker 而未带入诊断是次要问题，需单独判断是否阻止下一次交付；本次不把它和计划缺陷集中修改。

## 最小修复

1. `app/v1/capabilities.py::_workflow_acceptance_runners` 增加*可判定*约束：argv 必须调用位于 Project 内的专用脚本（Python `.py`、Node `.js/.mjs/.cjs`、PowerShell `.ps1`、shell `.sh` 或 batch `.cmd/.bat`），不能把 Maven/npm/构建命令本身加 `--report` 当作报告入口。脚本路径须是 argv 中独立的 Project 相对路径，且在 report_path token 之前；保留现有“报告路径也是独立参数”、`launch_validation=deferred`、真实运行时检查及旧高级接口。失败回执明确指出应由对应 Worker 创建并执行该脚本。不要根据脚本文件名假定测试已经通过。
2. `app/v1/capabilities.py` 的窄入口描述与 `DEVWERK.md` 规划指引明确：`argv=["python","backend/acceptance.py","--report","reports/backend.json"]` 合法，`mvn test --report ...`/`npm test --report ...` 非法；每个 required behavior Column 用窄入口配置。`loops/ddd-software-delivery/loop.json` 只补充 backend/frontend/system_test 的指令：阅读冻结 acceptance_check，创建其脚本并运行真实断言，脚本每次写结构化新鲜报告；基础测试成功后尽快调用 `column.complete`，避免无目标重复测试。不改 Column 图、能力、预算或其它 Loop。
3. `tests/test_context_acceptance_regression.py` 对 Maven/npm 伪报告入口、无独立脚本路径、越界脚本、合法 Python/Node/PowerShell 入口及旧 configure 兼容做回归；现有 TaskPlan admission 和 Runtime 验收测试保持。测试须证明新入口拒绝静态可判定的错误命令，同时不声称脚本实际内容已通过。单独提交。

## 验收

无在途 Run 时重启，开新 Project 逐轮复用原七条消息。要求六轮零 Workflow/Task，授权后 Task 真建，backend Worker 执行正确的冻结脚本及报告并推进 frontend；若 Worker 报脚本/断言失败，按真实报告检查，而不是放松检查或改历史测试 Project。继续按约三分钟观察流程；仅在终态检查产品质量。若下一次仍停在别处，单独记录下一根因。
