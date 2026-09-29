# Vue/Java 登录防刷 Demo：新 Project 黑盒复测

测试 Project：`prj_87d10ac45d654f26a933fd84662760e1`；根目录：`data/projects/blackbox-login-retest-20260930-032041`。2026-09-30 北京时间开始。8000 服务 `/openapi.json` 返回 200。仅使用同源 Project/Conversation API 驱动 Agent，不直接替它写 Workflow、Task 或交付代码。

同一案例：Vue 3 前端、Java 后端，仅登录和防刷，可用持久化 DB，无缓存依赖，交付简单可运行 Demo。先至少五轮自然讨论，仅在用户明确授权后创建 Workflow/Task。运行时每约三分钟读一次控制面；优先看 Kanban 是否按冻结 Workflow 推进、Worker 生命周期与 Column 间证据交接、mailbox 仅通知且无积压、失败事实是否确定性传入下一步、是否出现无新证据反复返修。流程终态后再审查登录/防刷交付物质量与预设验收是否匹配。

创建时首次 Project `prj_c43b7129f005431ebf08dc7e7ff95dcc` 因 PowerShell→Python 管道把中文项目名转成乱码，尚无对话/Workflow/Task，未用于测试；改为本 Project 的 ASCII 名称。Conversation 消息在 API 调用时用 ASCII Unicode 转义生成 UTF-8 JSON，数据库保存的用户正文正常。

## 对话记录

| 轮次 | 消息/Job | 观察 |
|---|---|---|
| 1 | 消息 `749`，Job `cjob_adf200b531084331b14eb85a5f27a343` | `succeeded`；`act=discuss`、`execution_hold=true`，Agent 列出技术、防刷、验收决策；Workflow 404、Task 0，未提前开工。 |
| 2 | 消息 `751`，Job `cjob_aacbcc2556d04d1d91e7338d2e7b3ac9` | `succeeded`；已持久记录技术与非目标决策，`execution_hold=true`；Workflow 404、Task 0。 |
| 3 | 消息 `753`，Job `cjob_bdde2c6e063144e9beaf01593d9b550f` | `succeeded`；上述双维度语义均写入 `work_intent.decisions`，`execution_hold=true`；Workflow 404、Task 0。 |
| 4 | 消息 `755`，Job `cjob_0685a5c59e5341a9b22a56b8735f8c68` | `succeeded`；Agent 整理基线与 10 项测试，但其中“3 次内每次 401”与“第 3 次 429”矛盾；`execution_hold=true`、Workflow 404、Task 0。 |
| 5 | 消息 `757`，Job `cjob_2744d6ac85544b86a74e533a5d935689` | `succeeded`；Agent 修正 401/429 并整理最终基线，`execution_hold=true`；Workflow 404、Task 0。它把前端验收写作“人工验收”，授权消息将明确要求真实可执行浏览器测试。 |
| 6：执行授权 | 消息 `759`，Job `cjob_8349a072e3e546b6843ef4724a7493a8` | `failed`，`conversation_protocol_stalled`。已成功保存需求基线、应用 software Loop 得到 Workflow revision 1，但 TaskPlan/Task 均为 0。首次 `task.plan.save` 被真实 `AcceptanceContractMissing/ScenarioCoverageMissing` 拒绝；四次 `workflow.acceptance.configure` 因空 `scenario_ids` 或非法列键被拒；后续重复 resolve/loop.apply 也遭拒，最后 `task.plan.save` 把对象误传为 JSON 字符串，重复失败触发协议停止。Run 19 iterations / 20 工具记录；该时间窗使用量 108,646 input + 16,614 output tokens。 |
| 7：交互恢复 | 消息 `761`，Job `cjob_db7019f7a9dc437391db6c0838b6d8eb` | `failed`，`conversation_protocol_stalled`；revision 仍为 1，Task 0。Agent 的 backend/frontend/system_test/accept 已填场景及报告，但在 delivery 把纯 build/package 当作需要场景报告的检查：先 `scenario_ids=[] + report_path`，再改 `evidence_kind=artifact` 仍保留 `report_path`，最后给 artifact 填假 `DEL-*` 场景；Pydantic 依次明确拒绝，第四次同类重复被协议止损。Run 7 iterations / 8 工具记录；24,824 input + 6,524 output tokens。 |

## 状态与结论

测试进行中，但当前 Workflow 只处于初始 revision，**没有可执行 Task**。Runtime 对无行为验收的计划和非法工具输入拒绝正确；Agent 在两轮中都未能根据拒绝回执完成配置，因此流程交付被阻断。第二轮缩小了参数失败面：delivery 列必须有能实际执行断言并产生新鲜 `devwerk.acceptance-report.v1` 的 behavior check；纯 build/package 是 artifact，不能填场景或代替行为检查。进一步发现列写路径与独立浏览器 harness 责任不符，因此停止在当前 revision 上重复配置提示，转入通用规划合同与 Workflow 修订方案。`accepted/queued` 仅是接收消息，不能视为 Agent 完成或交付。不得以先前 Project 的代码、报告或流程结果替代本 Project 的独立黑盒结果。

进一步检查当前冻结 Workflow 的 `system_test` 可写路径只有 `docs/test-report.md`，不能在该列通过文件能力建立拟议的 `e2e/**` 浏览器测试源；delivery 也没有拟议的根目录验收脚本写入所有权。这是交付流程预设与当前需求的不匹配，比第二次 Job 的 delivery 参数错误更早决定了后续风险。详见 `2026-09-30-login-retest-planning-boundary-plan.md`。该缺口不能通过放宽行为证据校验或直接改 Project 文件掩盖。
