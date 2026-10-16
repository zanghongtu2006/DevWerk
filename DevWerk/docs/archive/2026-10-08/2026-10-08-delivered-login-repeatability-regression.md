# 登录防刷交付后的连续验收复测暴露旧服务复用

## 现场

- 全新测试 Project `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 的原 Task `tsk_6d60783ed2b84d0ebd552feb60b95c61` 于 10:59 到达 `done`。交付列、验收列均有真实 Java 测试和 HTTP 验收成功收据，前端列有 Vite 构建成功收据。
- 交付后在项目目录连续独立执行 `python tests/acceptance_test_runner.py` 两次：第一次 exit 0、HTTP 7 passed/2 skipped；第二次 exit 1、4 passed/3 failed/2 skipped。第二次 A1/A2/A3 收到先前锁定状态 `code=2`，而非新的进程初始状态。
- 第二次脚本在 0 秒内接受 `/api/health`，随后新 JAR 的 `backend_server.log` 报端口 8080 已占用；`netstat` 确认 PID 7120 的 Java 进程仍监听该端口，开始时间对应第一次独立复测。脚本的 `wait_for_port` 和 `wait_for_health` 只确认某个服务就绪，没有确认它属于本次 `Popen`；`terminate/communicate` 也没有保证启动树及端口释放。由此第二次请求错误地落到旧进程，并读取上一轮锁定状态。
- 此现象来自交付项目的测试脚本和它在普通本地 shell 下的进程生命周期；DevWerk 的 `project.command.run` 在 Windows 使用 JobObject 清理命令树，不能据此把问题归咎于 DevWerk 进程执行器。测试结束后未强制结束 PID 7120，以遵守用户对非必要进程停止的限制。

## 修复目标与边界

通过公开项目对话让 Conversation Agent 按现有看板建立必要的修复工作，而不手工改交付目录或已完成 Task。修复的验收条件是：脚本不复用旧服务作本次证据，能确认自身启动实例的健康与初始化状态；正常和失败退出均清理自己的实例；连续运行至少两次均得到相同可执行断言通过结果，Java 测试与 Vue 构建仍通过。已完成的原 Task 保持不可变；如需后续修复，应由 Conversation Agent 按 Workflow/Task 工具建立独立且有明确范围的修复 Task。

实际进程清理由拥有者完成；不以删除数据库或杀掉无关进程伪造可重复性。新的测试结果需覆盖 `docs/test-report.json` 与最终验收报告之间的一致性。
