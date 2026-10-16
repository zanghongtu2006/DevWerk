# 移除未经请求的 Conversation 界面内容

## 来源核查与修改方案（修改前记录）

用户要求删除红框中的“本轮方式”和“当前方案”区域，其余行为保持不变，并补充最小修改及禁止擅自增加需求的开发纪律。

来源：09-12 讨论/TaskPlan 修复设计中的第 13 节和交互工作项把后端意图契约扩展成了 Web 模式选择及开始方案控件。`docs/DEVWERK_Discussion_TaskPlan_Implementation_2026-09-13.md` 第 9 行明确记录“新增只讨论选项与绑定不可变方案的开始按钮”。这是修复过程中额外加入的交互需求，不应由实现者自行扩展。Git diff 显示这些代码仍属于未提交修改；文件最后已有提交为 `09aabd8`（2026-08-16），不包含这些控件。因此可定位到 09-12 设计、09-13 实施记录，不能据此声称存在某个精确引入提交或时刻。

原显示条件：

- `renderProjects` 在选中项目后始终显示“本轮方式”，发送中禁用；选中只讨论时传 `mode: discuss`，否则 `auto`。
- `work_intent.execution_hold` 为真时显示红框内的讨论提示。
- `work_intent.proposal` 存在时显示“当前方案”、方案正文和“开始这份方案”按钮。按钮构造 `start_proposal` action（绑定方案 ID/hash），在输入为空时自动填入开始执行的消息。
- Conversation 卡片已有四个显式 grid row；新增三个节点没有指定 row，由 CSS Grid 自动布置，随状态条和条件节点的有无占据空位或隐式行。这解释了它们可能出现在消息上方或输入框下方。

限定修改：

1. `app/web/static/pages/projects.js` 删除上述三段模板；保留已有状态条、消息、输入框、侧栏和样式。
2. `app/web/static/dashboard.js` 删除控件监听和开始方案分支；普通发送继续使用当前默认请求 `{ message, mode: "auto", user_action: null }`，不恢复旧的 `start_task: true`，不修改后端意图/授权判断。
3. （2026-09-22 更正）开发纪律仅约束本次协作中的 Codex，不是 DevWerk 的产品需求、运行时规则或通用 Agent 设计；撤销此前写入仓库 `AGENTS.md` 的三条纪律。

验证：两份 JavaScript 语法检查；使用现有 Web/API 与对话意图回归测试，不新增镜像实现的测试。核对移除后页面模板与 Git 原版本一致，其他既有前后端变更保留。不启动/重启生产服务、不修改生产数据库。

## 实际修改与验证

已完成两份前端源文件修改及本记录。此前额外加入 `AGENTS.md` 的开发纪律已于 2026-09-22 撤销，未将其加入 DevWerk 运行时或提示词。`projects.js` 与 Git HEAD 内容一致；`dashboard.js` 相对 HEAD 只保留此前后端意图修复所需的普通发送请求契约。没有修改 CSS、后端或数据库。

- `node --check app/web/static/pages/projects.js` 和 `node --check app/web/static/dashboard.js` 通过。
- `venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_web_contract.py tests/test_conversation_intent_contract.py -q --show-capture=no`：**38 passed in 40.46s**，退出码 0。
- Web 源码中已无这些控件的文案、选择器和事件引用；`git diff --exit-code -- app/web/static/pages/projects.js` 通过，确认页面模板恢复原版本。
- Git diff 空白检查通过。未进行运行中生产页面的浏览器验收，未重启生产服务。
