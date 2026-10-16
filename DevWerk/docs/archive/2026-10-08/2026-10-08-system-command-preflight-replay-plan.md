# system.command.run 命令未找到误报执行结果不确定

## 现场证据

- 项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 的用户反馈 Job `cjob_d66d19eac8b94bc5bbda28b553a651e4` 在已修改数据库配置后尝试重测，却因 `mvnw.cmd` 不在指定工作目录而失败。最终错误是 `ExecutionReplayUncertain: system.command.run outcome is unknown after CommandNotFound`，Job 直接失败，未能恢复暂停的 Task。
- `app/v1/process_runner.py::run_command` 在创建子进程前先执行 `resolve_command`，`CommandNotFound` 明确表示没有进程启动、没有外部副作用。`app/v1/capabilities.py::CapabilityRegistry.dispatch` 已对 `project.command.run` 在写执行收据前做相同预检，却漏了 `system.command.run`。后者先把收据标成 started，再遇到 `CommandNotFound`，于是保守地升级为执行结果不确定。这与事实不符，也堵塞对话自纠。

## 最小修改与验证

1. 在 `CapabilityRegistry.dispatch` 的现有命令预检分支同时覆盖 `system.command.run`，用 `context.system_files.resolve(cwd)` 解析工作目录并调用同一个 `resolve_command`。不改变真实进程启动后异常的不确定性保护，也不扩大工具权限。
2. 在 `tests/test_command_resolution.py` 加一项回归：对不存在的 system 命令，返回普通 `CommandNotFound` 工具错误，checkpoint 标明 `rejected_before_effect`，数据库没有 started 执行收据；现有真实启动/超时测试继续通过。
3. 等当前服务到安全边界后重启加载补丁，再通过公开 Conversation API 在同一 Project 请求恢复与重测。不能因为代码单测通过就宣称黑盒交付通过；仍须核对 Task `done`、新配置构建/测试和最终验收证据。
