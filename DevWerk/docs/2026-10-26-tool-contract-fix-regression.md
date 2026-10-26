# 工具契约修复与回归记录

> 2026-10-26；对应方案：`2026-10-26-devwerk-hermes-tool-contract-full-review.md`。此记录只写实际代码和观测，不以 Agent 自述替代工具回执。

## 已实施

- 新 Conversation Run 的工具展示不再读取最近一次 `tool.catalog.select`；小型 Project inspect/revise 入口稳定展示。`select` 保留为内部兼容项，以供历史待执行操作重放。
- 增加 typed 的 `tool.catalog.describe` 与 `tool.catalog.call`；目标 Tool 从当前 Run 能力及 Registry 解析，`INTERNAL` 和不在 Run 内的目标不能借桥执行。
- Provider 适配保留模型原始入参。Agent 操作边界按目标 schema 规范化，再由 Registry 做原有严格校验；未展示的原生调用在副作用前拒绝。
- `v1_agent_operations.resolution_json` 增量保存外层 Provider 调用与有效目标的映射、参数及 schema 摘要。目标的授权、收据、ledger、Task 启动判断和事实报告沿用既有业务路径；结果使用原 `tool_call_id` 回传。
- 最新 `tool.catalog.describe` 的完整回执在对应桥接调用前保留于下一模型请求；超过描述上限显式报错。历史结果仍按上下文预算投影。
- 桥接重放测试暴露同一 `tool_call_id` 再次写调用记录会触发唯一约束；已在使用既有 operation 结果的恢复路径避免重复写记录。
- 交叉核对 Hermes 与 Anthropic 适配层后，`describe` 改为返回实际发送给 Anthropic 兼容 Provider 的 schema 形状；桥接 digest 和重放检查使用同一形状。

## 已执行的检查

| 检查 | 实际结果 |
| --- | --- |
| 变更前基线的大范围 Agent/Tooling/Conversation 回归 | 343 通过、14 失败；失败主要是旧 `select`/隐藏原生调用预期及 Provider 适配层旧断言。 |
| 更新协议测试后的完整 `tests/unit` | **617 通过**，耗时 18 分 54 秒。 |
| 最后补充的工具边界聚焦检查 | 15 通过。 |
| 桥接重放“目标执行一次”测试 | 初次失败，发现唯一约束问题；修正后 2 个聚焦用例通过。 |
| Provider 可见 schema 与 `describe` 一致性 | 聚焦回归 7 通过，新增精确 schema 对比用例 1 通过。 |
| `compileall` 与 `git diff --check` | 通过；Git 仅提示现有部分文件将 LF 转为 CRLF，无空白错误。 |
| 最终代码的完整 `tests/unit`，允许本机套接字的执行环境 | **618 通过**，耗时 17 分 32 秒。 |

617 通过是在最后两处修正之前的结果；最终代码已再次完成 618 个单测。第一次最终重跑在受限沙箱中停于 Windows `asyncio` 创建 socket pair，超时堆栈指向标准库 `socket.accept`；同一队列恢复用例在允许本机套接字的环境中 3 秒通过，随后完整套件通过。这次沙箱等待不计为 DevWerk 断言失败。

## 真实 Provider 黑盒

- 新 Project `prj_9b125498ab324c87bb725921c7b92e8f`，在用户允许后于 8000 端口运行当前代码。进行了五轮自然语言交流：讨论新人入门说明的范围、角色与未定事项，最后一轮要求 Agent 自行创建 Kanban Workflow 和 Task 交付 Markdown。五轮 Conversation Job 均成功。
- 最后一轮 Run `arun_51d50a43157f4ababc83ed178724678b` 的实际工具回执包含 `tool.catalog.search`、`tool.catalog.describe`、`workflow.create` 成功、`task.plan.save` 成功及 `conversation.reply` 成功。Workflow `wfrev_87958b96704f4f2d95a8e0ffcba6dab3` 含 `draft`、`review` 两列。Task `tsk_c37548fafc174e1a91e0df80fea1c3f3` 到达 `done`；两列 Run 均为 `succeeded`。
- 交付文件为 `data/projects/tool-contract-blackbox-20261026/docs/ONBOARDING-lantern.md`，67 行、1472 字符，明确标记 7 处待补项。Column 生成的 `scripts/check_onboarding.py` 实际执行回执为 `pass: true`、`violations: []`。该脚本失败时也会以进程退出码 0 结束，因此这里依据解析后的 JSON 字段，不依据退出码声称通过。
- 这轮仍有两次可恢复的协议误用：第一次 `task.plan.save` 传入多余的 `workflow_revision_id`，严格校验拒绝，下一次调用成功；Task 已排队后多调用一次 `tool.catalog.call`，被 Conversation Turn 边界拒绝，随后正常 `conversation.reply`。所以本次证明端到端可交付，并不证明所有 Tool 首次调用均正确。
- 从实际 `agent.model_input` 日志解析到 17 次 Provider 输入。首轮展示 22 个工具，定义约 20,073 字节；多数中间请求展示 21 个工具，定义约 18,706 字节；最后两次只展示 `conversation.reply`，定义约 785 字节。模型消息历史从约 30,796 字节增长至约 270,183 字节。工具定义已从旧的全量大 schema 降到小入口，但本轮消息历史仍明显增长；这项观测不等于已解决所有上下文生命周期问题。

测试现场和 8000 端口服务保留，供用户复核。本黑盒仅覆盖一个通用文档交付 Project，不能据此推断所有 Workflow 和 Provider 均稳定。
