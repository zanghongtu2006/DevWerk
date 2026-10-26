# 第二章 `recovering` 诊断（2026-10-24）

Project：`prj_45a13d81e46d487bab029d2f967199ca`；Task：`tsk_be60f30393844ad6aa4a8544ab67ab72`。只读取现有 DB 和源码；未改动该 Project、服务或 Workflow。

## 证据链

1. 第一章 Task 已 `done`，第二章明确依赖第一章 `done`。第一章已登记并保存版本的产物路径是 `chapters/chapter-01/summary.md` 和 `chapters/chapter-01/body.md`。
2. 第二章 `foundation` Column Run `run_a5d0918bef4140abaa7f6aa9a57ea005` 于 `2026-10-24T03:32:25.860+00:00` 成功。下一列 `recap` Run `run_0ea38ae8546041ddaafef9489a0cea5d` 在 `03:32:26.088` 开始，`03:32:26.120` 中断；`agent_run_id` 为空。Task 为 `recovering / paused / blocked_runtime`，错误为 `ContractError: Column recap input rejected value at $: 'accepted_artifacts' is a required property`。
3. 第二章冻结的 Workflow revision `wfrev_9ebb1c9e2a014717b97cd5c1ae0d9022` 中，`recap.context.accepted_artifact_globs` 是 `summaries/*.md`、`chapters/*.md`，而 `recap.input_contract` 在存在依赖时要求 `accepted_artifacts` 同时包含 `^summaries/[^/]+\\.md$` 与 `^chapters/[^/]+\\.md$`。第一章两个实际路径均不符合该选取模式和契约模式。
4. `WorkflowRuntime._input_for` 仅按冻结 Column 的 glob 从已验收依赖版本中选取产物；空列表时不写入 `accepted_artifacts` 字段。`WorkflowRuntime.step` 在创建 Agent Run 之前执行输入契约校验，所以 Agent 没有机会生成或修复提要。

## 根因与当前修改的关系

Loop 模板把摘要和正文的路径形状固定为平铺目录，但 Task Plan 使用嵌套的 `chapters/chapter-XX/{summary,body}.md`。Loop 的 Task 输入契约只验证路径是非空字符串，没有约束它与下游 Column 的 glob/正则一致；现有单测也只使用平铺路径，因此发布时未暴露这类跨契约不一致。第一章可以通过，因为它没有前置依赖；第二章开始必须读取前章产物，才触发缺失。

本轮 Task Plan 提交、Settings 开关、上下文压缩修改**不能解决**该错误。工作区先前对 `runtime.py` 的修改仅调整了依赖范围和根 Task 基线预装，也未改变嵌套路径匹配。重启加载当前代码，冻结 Workflow 仍会因同样原因拒绝第二章 `recap`。此外 Task 目前已暂停，不会自动重试。

## 后续修复边界

需要让 Loop/Workflow 的产物选取与输入契约接受 Task 合法声明的路径，并在 Workflow 发布或 Task Plan 校验时检查相互一致；测试须覆盖嵌套路径及第二章的已验收依赖装配。已有 Task 绑定冻结 revision，修正磁盘上的 Loop 模板不会自动改写这个 Project 的 revision；恢复旧 Task 还需按 Workflow 修订与 Task 恢复协议处理。本轮未执行此项修复或旧 Task 恢复。
