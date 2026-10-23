# Task 规划契约丢失修复方案（2026-10-23）

## 证据与边界

Project `prj_1dbf34fc672e4d1d81775caa425060e1` 已发布 Workflow，但 Task Plan 和 Task 均为零。三轮 Agent Run 的原始 `task.plan.contract` 回执含 `input_schema`，实际 Provider 输入却没有该字段。新版本 Tool schema 约 188 KB，首次预算估算约 12.5 万 tokens，实际约 4.4 万；后续几乎每轮压缩。压缩摘要把规划回执限制为 1,600 字节，删除了输入契约。Agent 只读取归档结果首页并反复查询，最终触发停滞保护。

本次只修改通用上下文管理：保持模型可见的当前 Task 规划契约、用 Provider 实际用量安全校准估算。Workflow、Task、状态机、工具授权、Column 和业务模板不变。

## 修改

1. `app/v1/context/context_manager.py`：从本轮已提交的 `task.plan.contract` Tool 回执读取类型化输出，在每次模型请求前写入原本就会刷新的权威 Project 上下文。该位置不参与旧 Tool 回执的 1,600 字节摘要。只保留匹配当前 Workflow revision 的契约；新 revision 使旧契约失效。若包含契约的请求确实超出预算，明确报错，不静默丢失。
2. `app/v1/protocols/conversation/`：在 Conversation 上下文结构中声明类型化契约字段，不拼装临时 JSON。
3. `ContextManager.observe`：第一次请求仍用保守估算；取得 Provider 真实输入 token 数后允许向下校准并保留安全余量。Provider 溢出时现有强制压缩和一次重试继续有效。
4. 单元测试：以含多字段的真实 Task 契约和大量工具 schema 模拟压缩，断言发给 Provider 的输入仍含完整 `input_schema`、revision 和授权范围；覆盖 revision 变更与真实预算不足。

## 验证

先跑相关单元测试及必要的上下文回归测试，再重启 8000 服务并创建全新 Project。黑盒通过条件是 Conversation Agent 自主发布 Workflow、保存 Task Plan、创建 Task；Task 被看板推进，并以真实工具回执和数据库状态判定结果。保留服务、Project、日志与数据库现场。

## 实施与结果

- `ContextPolicy` 新增冷启动比率、真实用量下限及安全余量；`ContextManager.observe` 允许在收到 Provider 用量后向下校准，仍保留 Provider 溢出时的缩减重试。
- 从本轮持久化且成功的 `task.plan.contract` 回执提取类型化契约，按当前 Workflow revision 校验后纳入每轮刷新的权威 Project 上下文。旧 revision 的契约被移除，旧 Tool 回执仍可按原机制压缩。
- `tests/unit/agent/test_context_lifecycle.py` 新增两项针对性回归。该文件 20 项通过；相关上下文、Conversation 与 Tool 协议测试另有 158 项通过。编译及 `git diff --check` 通过。
- 重启唯一 8000 服务，健康检查通过，保留运行现场。第一次新 Project 的第二条测试消息因本地 PowerShell stdin 编码损坏而作废；该结果未用于判断 DevWerk。使用 UTF-8 文件在干净 Project `prj_be6acc7c636244b4b045a2e86fcd34f4` 重新测试。
- 干净 Project 的 Conversation Run 一次发布 Workflow；`task.plan.validate` 两次发现参数问题，第三次通过；随后 `task.plan.save`、`task.create` 成功。Provider 输入日志确认包含 `task_plan_contract` 与 `input_schema`。估算比率由 0.5 降至约 0.28，后续 Conversation 回合未压缩掉契约。
- Task `tsk_9d1a8b1bcc164f7eabc8a75918885fb5` 经 `research → draft → review → draft → research → draft → review` 到达 `done`，末次 `review` 成功提交 `accepted`。中途审校 Assignment 曾触及累计预算，但 Runtime 继续审校并完成；没有人工修改 Workflow 或替 Worker 操作。
- 独立文件核对：`article.md` 含 1494 个 CJK 汉字；正文 16 个来源编号均可在 `sources.md` 中找到，后者含 17 个 URL；未检出 TODO、placeholder 或 example.com。此核对不替代逐链接内容审读。
