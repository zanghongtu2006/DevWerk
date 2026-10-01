# P0：命令超时的错误回执与真实结果矛盾

第八次黑盒测试后端 Worker 的 `project.command.run` 工具调用 119 执行验收脚本超过 600 秒，正式结果 `timed_out=true`，但进程树关闭后直接进程返回码为 0，工具错误文本却为 `command exited with code 0`。Worker 虽可从完整 `output` 查到超时，但 Agent 对错误本身的解释会误判。`output_truncated=true` 且返回码为 0 时也有同类问题。

根因是 `app/v1/capabilities.py` 的 `_command_run` 与 `_system_command_run` 正确把超时和输出截断判为失败，却始终按退出码构建错误消息。进程管理本身已通过 Windows Job 关闭子进程树；本次只修正工具回执，不改执行时限、进程管理、Workflow 或 Agent 策略。

在两个命令能力中按 `timed_out`、`output_truncated`、非零退出码的优先级生成消息；错误对象保留 `CommandFailed` 类型与退出码，增加同名布尔字段，使现有消费者兼容并能明确区分失败原因。于 `tests/test_capability_contract.py` 用模拟命令结果覆盖两种能力的超时及输出截断回执；保留真实非零退出码测试。单项测试通过后独立提交。
