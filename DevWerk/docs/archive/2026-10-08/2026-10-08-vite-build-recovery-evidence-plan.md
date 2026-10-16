# Vite 构建成功不能消除先前失败命令

## 现场证据

- 全新项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 前端重跑 Run 序号 18：先前 `npx.cmd vite build` 因 npm 缓存 EPERM 失败，直接在项目根调用 Vite 又因找不到 `index.html` 失败。随后 `cmd /c cd /d frontend && node_modules\\.bin\\vite.cmd build` 成功，真实退出码 0，产出新 `dist`。
- `column.complete(frontend_ready)` 仍被 Runtime 以早期命令失败拒绝，Task 进入 `recovering/paused`。`app/v1/services/completion_admission.py::_recognizable_verification_command` 用于判断后来成功的命令能否消除早期命令失败；它识别 `npm run build`，但没有识别 Vite CLI 的 `build` 子命令，因此把真实成功构建当作无关命令。

## 最小修复

1. 仅在 `_recognizable_verification_command` 中补充 `vite`/`vite.cmd`/`vite.exe` 的 `build` 子命令，继续要求命令真实完成且 exit 0。现有 `known_nonbehavior_command` 仍拒绝 `dir`、`type`、`echo` 等文件检查；不改完成状态机、证据签名或 Workflow 路由。
2. 在完成协议单元测试中覆盖：直接 Vite 构建及 `cmd /c cd frontend && ...vite.cmd build` 可抵消先前失败；`cd && dir` 仍不能抵消。运行相关回归。
3. 在 Task/Job 均暂停的安全边界加载补丁，通过公开 Project 对话恢复同一 Task 的前端列，真实重跑构建后继续看板，直到最终验收和 Task `done`；不直接改 Task 数据库。
