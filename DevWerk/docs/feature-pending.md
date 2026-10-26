# 待实现功能（非紧急）

本文件集中记录 DevWerk 暂不进入当前迭代的功能优化。以后发现非紧急 Feature 时，在这里新增条目并保持稳定编号；开始实施前再单独评审方案。阻塞交付的 Bug 不放入本清单。

每条记录包含：现状与问题、预期行为、可能涉及的边界、验收方式、状态。这里的方向不是已批准的实现设计。

## F-001 Toolset 与按需披露

- **状态**：按需披露已在 2026-10-25 的 Conversation 修复中实施；按职能组织 Toolset 仍待评审。
- **现状**：修复前的 Conversation Agent Run 能力快照包含 101 个独立 Tool（69 个内建、32 个可见 Chrome MCP），且每次模型请求同时携带全部 Schema。现已保留完整能力快照，同时默认只披露 20 个直达 Tool；其他 Tool 可经目录查询和选择后按需披露。`task.*` 等前缀仍只做命名分组，Toolset 的职能组织尚未实施。具体数量会随 MCP 配置变化。
- **预期行为**：按通用能力、项目工作、Kanban 管理和外部 MCP 等职能组织 Tool；在不削弱 Conversation Agent 自主选择能力的前提下，减少每轮发送给模型的 Schema 体积。Toolset 用于选择与发现能力，不要求把 `task.*` 强行合并成一个含多种 `action` 的 Tool。
- **剩余方向**：评审是否需要可组合的职能 Toolset；当前目录 `search → select → 原 Tool Call` 已解决全量 Schema 重复发送，不应再另造一套调用权限或无类型的通用 Tool Call。方案与验证边界见 `docs/2026-10-25-conversation-tool-presentation-and-prior-evidence-plan.md`。
- **架构边界**：固定 Tool 的入参、出参和校验仍由类型化协议定义；发现机制不得绕过原处理函数、权限边界和执行回执。Column Agent 只看到 Workflow 分配且允许委派的能力；不能因缩减 Schema 让主 Agent 无法创建或修订 Workflow、Task。
- **验收**：用真实模型分别完成无需看板的多轮直接工作、Conversation Agent 创建 Workflow/Task、Column 使用分配的 Tool、按需找到并使用 Chrome MCP；记录实际 Tool 回执，并量化模型请求中的 Tool Schema 大小与变更前差异。不得加入特定项目类型的提示或流程规则。
