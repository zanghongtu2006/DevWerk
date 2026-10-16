# 项目全文搜索遍历依赖与构建产物，阻塞看板

## 现场证据

- 全新项目 `prj_b8794b4f7b4643dfb5009fdec9a97fb6` 的 `system_test` Run 序号 20 在 09:27:22 调用 `project.files.search(pattern="test.*\\.(py|java|js|ts)$")` 后超过六分钟无工具结果或 Agent 进展，Task 一直 `running/system_test`。服务日志最后停在该能力的 input；同一项目已有 `frontend/node_modules`、`.npm-cache`、`frontend/dist`、`backend/target` 和解压的 JAR 文件。
- `app/v1/capabilities.py::_files_search` 先调用 `ProjectFiles.list_paths("**/*", limit=1000)`。`app/v1/files.py::ProjectFiles._matched_files` 在应用 limit 前对整个递归 glob 做 `sorted(...)` 并构造完整列表，因此限制返回 1000 个文件并不限制遍历/排序工作；随后逐个 `read_text`，没有跳过依赖和构建目录，也没有在搜索循环中检查 `ExecutionControl`。稀少匹配可能长时间占住单 Agent 列。

## 最小修复

1. 给 `ProjectFiles` 增加仅供项目全文搜索使用的惰性文件迭代，默认 `**/*` 搜索用 `os.walk` 逐层遍历，跳过明确生成目录（`node_modules`、`.npm-cache`、`target`、`dist`、`.git`、`venv`、`__pycache__`），在达到现有 `max_file_list_size` 后停止。显式窄 glob 仍走原有路径匹配，避免改变已有文件列表/上下文行为。
2. `_files_search` 在每个候选文件前调用现有 `ExecutionControl.check()`；对二进制文件和超大文件跳过，避免一次解码大 JAR/数据库文件。保留原有 `limit` 和返回 schema，不添加新搜索能力。
3. 单元测试构造源码与大量生成目录文件，验证默认搜索能找到源码、忽略生成目录并有界返回；验证窄 glob 的现有行为；跑相关文件/能力/运行时测试。

## 黑盒恢复

- 修改和测试完成后，在确认没有其他运行中 Project/Job 的前提下精确重启 DevWerk；当前卡在只读搜索的 Column Run 可以安全中断。通过公开 Project 对话在同一 Task 的 `system_test` 列恢复，继续每约五分钟检查直至交付。不能把服务健康或 Task `running` 当作进展。
