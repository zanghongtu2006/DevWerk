# P0：`project.files.list` 的尾部 `/**` 误报空目录

第八次黑盒测试的用户恢复 Job `cjob_f2e5a829094b4a639cbe57cf382a3554` 调用 `project.files.list({"pattern":"backend/**"})`，正式回执为 `paths=[]`。同时 Project 目录下实际已有 `backend/pom.xml`、Java 源码、测试和 JAR。Conversation Agent 因此误判后端目录为空，给 Worker 发送“从零开始”的恢复输入；该 Job 随后在授权与重复操作协议中失败。

根因在 `app/v1/files.py::ProjectFiles._matched_files`：`Path.glob('backend/**')` 对尾部 `**` 返回目录，代码只保留文件，故结果为空。该输入符合 Agent 对递归目录 glob 的常见理解，也与 Workflow `writable_paths=['backend/**']` 的约定一致。

最小修改：只在文件列表/搜索的 `_matched_files` 中把尾部 `/**` 解释为 `/**/*`，保留路径规范化、越界/符号链接检查、排序及限额。精确文件名和其他 glob 保持原语义。于 `tests/test_capability_contract.py` 用直接文件和嵌套文件验证 `project.files.list('backend/**')`，并验证不返回其他目录。单项测试通过后独立提交。
