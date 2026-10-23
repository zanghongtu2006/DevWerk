# Agentic 公众号首篇 Workflow 黑盒记录（2026-10-18）

## 范围与项目

- Project：`prj_b6c7a8b0c5204fe1a94c63dc55fcd520`
- 工作目录：`data/projects/agentic-public-articles-20261018/`
- 用户目标：与 Conversation Agent 讨论系列文章流程；由它建立 Kanban，并用可见 Chrome 收集资料，先交付 1000–1500 汉字的 Agentic 入门文章。
- 本轮没有修改 DevWerk 产品代码，也没有直接写数据库或代替 Column Agent 写交付文件。

## 对话与创建

Conversation Agent 在前两轮讨论读者、来源和 Column 职责。第二轮提出 `collect → extract → draft → review → final` 五列。第三轮用户消息已授权“条件足够就创建”，Agent 仍要求再确认；当时 work intent 保留此前“暂不启动”的 hold。第四轮明确解除 hold 并启动。Agent 通过内部 `workflow.plan.save`、`workflow.publish`、`task.plan.save`、`task.create` 完成编排。创建中有 Workflow 字段和 Task Plan 字段的协议拒绝，它自行调整参数后继续。

- 首版 Workflow：`wfrev_5516aa7b12c44d2abb99976e930ec8cd`
- 首版 Task：`tsk_b43055cfcb9848bdaedf3581dc7d7d2c`
- 五个 Column Run 依次成功；Task 到达 `done`。
- `collect` 的 allowlist 包含 `mcp__chrome_visible__navigate_page`、`mcp__chrome_visible__take_snapshot` 和 `project.files.write`。Collector Run `arun_f19bbd12d5f946139cb30d4b10f5d574` 实际执行 Chrome MCP 导航与快照，先打开 OpenAI 文档，再处理失效的 Anthropic 旧路径，最后进入 Anthropic 的 tool-using agent 教程。OpenAI 原句可在浏览器快照中找到。两份原始来源记录落在 `raw_collect/`。

## 首版交付审计

文件链：`raw_collect/` → `refined_extract.md` → `draft_v1.md` → `review_checklist.md` → `article_final.md`。文章正文约 1122 个汉字，落在目标区间。实际审计发现：

1. 终稿仍保留“三个边界风险”，却写了 `UNVERIFIED-已移除`；该状态与正文矛盾。
2. 终稿尾部混入 Review Checklist 汇总和机器状态标签，不是干净的公众号正文。
3. `review_checklist.md` 的审查日期写成 2025-10-18，实际日期为 2026-10-18。
4. Conversation Agent 曾承诺“Review 会检查 Final 是否修正”，实际有向图只有 `review → final → done`。`final` 的 `acceptance_checks` 和 `acceptance_obligations` 为空，完成取决于该列 Worker 自报 `done`；审阅列在定稿之前，无法独立复核定稿。

第 4 点解释了前 3 点为何能伴随 `done`：文件链能推进，不等于本次 Workflow 所承诺的终稿检查已经发生。它是本次 Workflow 设计与执行缺陷，不能归因为浏览器 MCP 未调用，也不能仅以 Task `done` 宣称内容合格。

## 修复跟进

已通过用户轮次 `cjob_1fdf50688e5b49adbdbbd9dd874c5059` 将上述具体缺陷交给 Conversation Agent，要求它使用内部工具修订 Workflow 并创建后续修订 Task，保留首版历史，不直接改数据库。Agent 识别了缺陷并提出在 `final` 后增加独立 `verify` 列，但错用 `conversation.execution.authorize(request=begin)`；随后 `project.scope.revise` 明确返回 `ScopeExtensionRequestMissing`，要求对现有 Requirement 使用 `request=extend`。Agent 重复相同失败操作，Runtime 以 `ConversationProtocolStalled` 结束此轮，未修改 Workflow、Task 或交付文件。

新轮次 `cjob_03f5f181823f4ac6b46f3b47e642f6b5` 已将准确错误反馈给 Agent，要求按 `extend` 协议继续。结果待核对。
