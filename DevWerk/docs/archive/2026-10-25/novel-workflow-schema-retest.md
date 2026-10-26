# Workflow Schema 黑盒复测（2026-10-25）

## 范围

- 在 8000 重启 DevWerk，加载 `workflow.create` Schema 说明修改。
- 新建 Project `prj_c5d70fef7189427bad9329f96d0db9a9`，使用《雨站来信》自然语言讨论两轮，第三轮请求先写第一、二章样章。
- 没有手动调用 Workflow/Task Tool，也没有在用户消息中传入 Tool ID 或参数格式。

## 实际结果

- 第三轮 Conversation Job `cjob_e6e44dfbe780452e817d3e6e4b7e2622` 成功，Agent Run `arun_2ee1f81402484df2a9faa20c94b551c3`。
- `workflow.create` 共调用 3 次。第 1 次因 Workflow 顶层多出 `item`、`transitions` 被拒；第 2 次因顶层多出 `item`、`kind`、`transitions` 被拒；第 3 次成功。
- 落库 Workflow revision `wfrev_631883c0bd5a4b14b8c890383da5123f`，列为 `setup → draft → review → done`（另有失败终态）。
- `task.plan.save` 成功，实际创建 3 个 Task：第一章 `tsk_743dc2c8ae4e48f39494ee4d16744126`、第二章 `tsk_52ab715cb8794bc8aa8e978c8791bc2c`、跨章校阅 `tsk_76def7e61fe34932837336a1dd0ca929`。第一章已进入 `running/setup`，其余待依赖完成。

## 判断

新说明使本次 Agent 能在同一轮中纠正参数、创建 Workflow 并派发 Task；但首次 `workflow.create` 仍未成功，Schema 稳定性目标尚未完全达成。两次错误都属于 Workflow 顶层字段混入，不是 `task_input_schema.required` 类型错误。此前同场景首次创建连续五次无效、第六次重复而停机；本次未复现该停机。

本文件只确认创建和派发事实。章节正文、校阅及最终 `done` 尚未验收；运行服务、Project 数据与日志保留供继续查看。
