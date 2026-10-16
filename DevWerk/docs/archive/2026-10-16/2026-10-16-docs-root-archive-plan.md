# v0.1.0 文档根目录归档方案（2026-10-16）

## 边界

v0.1.0 已发布；`docs/` 根目录不再把该版本的设计稿和发布过程文档列为现行规范。此次只移动历史文档、修正文档链接和两份项目 README，不修改应用代码、测试实现或 Release 标签。

## 逐项归类

| 归档类别 | 根目录文件 | 原因 |
| --- | --- | --- |
| v0.1.0 总体设计 | `conversation-agent-design-v1.md`, `generic-conversation-agent-and-declarative-column-runtime.md`, `kanban-workflow-design-v1.md`, `single-agent-column-runtime-v0.1.0.md` | 旧版本的产品及架构设计，含“第一事实”等时效性声明 |
| Agent、Session 与上下文 | `agent-core-completion-evolution-v0.1.0.md`, `agent-tool-rejection-recovery-v1.md`, `conversation-agent-generic-tool-loop-v0.1.0.md`, `conversation-agent-orchestration-soul-p0-design.md`, `conversation-session-gateway-v1.md`, `context-session-efficiency-v0.1.0.md` | 旧设计、演进方案或 P0 提案，不能无审核地作为当前协议 |
| Workflow 与资源 | `kanban-recovering-runtime-v1.md`, `loop-runtime-v1.md`, `loop-task-plan-decoupling-v1.md`, `mailbox-lifecycle-p0-design.md`, `novel-loop-assets-v1.md` | 旧运行合同及特定 Loop 场景说明 |
| 配置、存储与测试 | `global-settings-v1.md`, `store-decomposition-v1.md`, `v1-test-contract.md` | 旧版配置/存储设计和发布前测试合同 |
| 导航页 | `docs/README.md` | 根目录不保留第二层文档索引；删除，不作为设计史归档 |

以上共 18 份设计或合同文档，集中移动到 `docs/archive/2026-10-16/v0.1.0-design/`，保持它们之间的同级相对链接。三份 `docs/releases/` 文件属于 v0.1.0 发布历史，移动到 `docs/archive/2026-10-16/v0.1.0-release/`。既有 `docs/archive/README.md` 仅是日期导航且已过时，一并删除。

## 链接和 README

1. 更新仓库及服务 README：以当前源码结构描述能力；将旧版设计和发布验证链接明确标作历史记录；不再引用 `docs/README.md`。
2. 修正被移动文档内部指向仓库、服务、测试或既有归档记录的相对链接；保持同组同级文档链接原样。
3. 检查仓库中对旧路径的引用，包括 Markdown 与代码中的文件名；只改确需更新的引用。

## 验证

- `docs/` 根目录无 Markdown 文件，旧 `docs/releases/` 无遗留；历史文件都在归档目录。
- 全部仓库 Markdown 本地链接可解析，原设计记录之间的相互链接可解析。
- `git diff --check` 通过；变更范围仅文档与 README。

## 执行记录

- 已移动 18 份设计/合同记录与 3 份发布记录；删除 `docs/README.md` 和已过时的 `docs/archive/README.md`，并移除空的 `docs/releases/` 目录。
- 18 份旧设计均增加归档状态提示，避免其原有的“Normative”“第一事实”等措辞被理解为发布后仍有效。
- 仓库及服务 README 改为直接说明当前结构，只链接归档后的发布记录；旧设计互链和其他跨目录链接已按新位置修复。
- 对此次修改的 README 和移动文件进行 Markdown 相对链接检查：0 个失效链接。既有归档记录中指向本机临时目录的历史证据链接不在此次整理范围。
- `git diff --check` 通过；未修改应用代码、测试断言或 Release 标签。文档移动无需重复运行 Python 回归。
