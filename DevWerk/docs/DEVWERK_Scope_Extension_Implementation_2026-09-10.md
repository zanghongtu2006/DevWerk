# Conversation 范围扩展实施记录

状态：范围扩展 A–E 已实现；2026-09-10 最终全量回归 **309 passed / 242.07s，退出码 0**，真实项目副本验证通过。字数硬上限及历史正文修订延后。

先落地方案，再修改与回归；本文件记录最终实现与新执行的验证结果。

本轮实现选择：

1. Conversation 是唯一项目代理身份，旧 main 仅保留为存储兼容别名，状态与身份错误分开返回。
2. scope 修订保存不可变需求版本和 Workflow → binding 快照关联；支持同项目原子修改目标/参数，旧工作继续使用旧版本。
3. 新用户目标能够显式继续已关闭范围；旧事件不激活新目标。普通范围修订不退休 Worker，退休实例通过显式后继与交接继续工作。
4. 小说方法支持范围完成与故事完结的区分，保留默认旧方法语义和固定包快照。
5. 对话状态通过真实操作回执报告；为正式 Gateway 增加可验证的回复协议，普通讨论保留，对不带事实的执行声明不直接发布。
6. 用临时 DB、故障注入、项目数据副本验证范围扩展；不直接修改原项目和生产 Task 状态。

预计验证：身份/同 key 冲突、关闭后修订、同回合状态刷新、六章到十二章的计划与依赖、旧版本输入不变、失败原子回滚、旧事件隔离、退休角色后继、事实回复、continue 结尾策略及既有回归。

## 1. 本轮结论与范围

没有增加 Conversation Agent 之上的代理。`v1_agent_instances.role=main` 和 `agents.main()` 是上一轮留下的存储标签/兼容接口，其 ID 始终等于 `conversation_agent.logical_id`；模型提示、运行时说明和错误均指向项目 Conversation Agent。Column Worker 仍不能修改全局工作图，这是父代理/执行代理的职责边界，不是 Conversation Agent 之上的权限层级。

本轮修复针对真实项目 `prj_22d5638974ee405885b0552e4a24e91f` 的 E1–E6。原需求取消后不能按原 key 重建、章数绑定不能修订、旧参数污染新旧任务、同回合权限缓存、关闭即退休以及虚构任务状态，统一在这一条路径处理。字数硬校验 F 和历史正文修改按用户要求延后。

## 2. 需求修订协议

Conversation 先调用 `project.scope.inspect` 获取当前 Requirement/Workflow/Loop bindings，再调用：

```json
{
  "requirement_id": "当前需求 ID",
  "expected_revision": 1,
  "expected_workflow_revision_id": "当前 Workflow revision ID",
  "objective": "继续第7至12章，故事暂不完结",
  "binding_patch": {"chapter_count": 12, "ending_policy": "continue"},
  "loop_digest": "loop.inspect 返回的新方法包 digest",
  "reason": "用户明确要求继续扩写"
}
```

`loop_digest` 可省略，省略时继续使用当前冻结包；从旧小说 1.4.0 升级到具备 `ending_policy` 的 1.5.0 时需明确提供。未在补丁中修改的参数保留，示例不会把 4000 改为 4800。

一个 SQLite 事务内完成以下动作，任一步失败整体回滚：核对期望版本与调用者；加载旧冻结包；校验新参数；合并方法变更；发布新 Workflow revision；保存新 Loop application/binding；保存旧/新 Requirement revision；更新当前 Conversation Job 的需求版本；记录 `project.scope.revised` 事件及操作结果。现有持久化 operation/receipt 负责中断后的同一操作回放。

返回 `requirement`、新 `workflow_revision_id`、`binding_id`、`bindings`、原 Workflow ID，且 `task_ids=[]`。**范围修订不等于创建 Task。** 后续必须向新 Workflow 保存仅含新增工作项的 TaskPlan，再调用 `task.create`，由运行时物化整份计划并建立顺序依赖。第七章连接已有第六章；不会为前六章生成替代任务。

同 key 的 `requirement.create` 只有同目标且仍 active 时才是幂等重用；不同目标或关闭状态返回 `requirement_scope_conflict`，明确指向修订入口。关闭状态和代理身份错误分开处理。

## 3. 不可变版本与旧工作

新增 `v1_requirement_revisions` 和 `v1_workflow_scopes`，为 Task/TaskPlan 增加 `requirement_revision`。Workflow revision 显式关联 Loop binding，不再用项目最新 binding 解释全部历史任务。

- 参数、方法资产、任务图准入与 Runtime 上下文均按 Task 的 Workflow revision 读取。
- 新 TaskPlan 的所有 Task 在物化时一次绑定需求版本，修复此前仅给 `task.create` 返回的首个 Task 绑定需求的遗漏。
- 正常 Workflow 方法修订继承其绑定；scope 修订生成独立新绑定。
- 旧 Assignment 保留自己的需求版本和阶段契约。新范围发布不会单凭 revision 增加剥夺旧 Assignment 的执行权；Task lease、Assignment generation、Worker 单写者检查仍保留。
- 消息触发的 Conversation 绑定源 Task 的需求版本，不能借 `requirement.select` 跳到新范围；旧版本回合不能创建新范围工作。
- AgentToolBatchExecutor 的执行前检查和 CapabilityRegistry 的派发都从持久 Job 刷新当前状态；不能只改能力层。`start_task`、触发类型和需求版本改变后，同一回合即可继续修订/规划。

升级迁移为增量迁移。优先使用旧 Loop application 的精确 Workflow ID，后续旧 Workflow 以不晚于其创建时间的 binding 回填；没有可追溯绑定时不套用未来 binding。旧需求没有历史版本表时保存已有版本快照，不伪造更早版本。

方法合并使用旧模板/新模板/项目当前方法三方差异：未发生模板变化的自定义保留；新模板变化与项目自定义冲突时返回 `scope_revision_conflict`，事务回滚，不覆盖用户定制。本轮副本验证覆盖了该真实项目从 1.4.0 到 1.5.0 的合并。

## 4. Worker 生命周期

关闭需求仍需所有 Task/Assignment 已结束，并使尚未消费的消息明确失败；**关闭需求不再自动把所有 Worker 永久退休**。普通 scope 修订维持 Worker identity/session。关闭范围不能继续发送执行输入，重新修订为 active 后可继续使用仍 available 的 Worker。

已被旧代码退休的实例不自动复活。新增 `agent.worker.replace(worker_id, summary)`：仅接受当前需求内空闲且 retired 的 Worker，建立新身份、新私有 session 与显式交接摘要；`v1_agent_worker_slots` 让后续相同 worker_key 的 Assignment 选择后继。旧实例、旧 session 和旧消息不改写、不复制为新权威指令。一个 Column 仍只执行一个 Worker，不引入 Column 内多代理循环。

## 5. 对话事实报告

正式 ConversationGateway 启用结构化最终回复；底层 AgentCore 的独立调用保留兼容默认。工具调用仍走原生工具协议，只有结束回合时的回复为 JSON：

```json
{"mode":"work_result","task_ids":["真实 Task ID"],"tool_call_ids":["当前回合真实工具调用 ID"]}
```

`work_result` 引用的 Task 必须属于当前项目，工具调用必须存在于当前 AgentRun 且成功。运行时从 DB 渲染任务的待执行/执行中/恢复中/已暂停/失败/已交付状态；不采纳模型 `message` 中自述的完成状态。范围修订、保存计划与建立任务分别报告，`task.create` 物化的整批任务一起展示。

普通 `discussion` / `proposal` 保留自然语言讨论；`blocked` 支持引用失败工具回执。无结构的最终回复、虚构 ID 或失败回执冒充成功会返回纠错信息；最多三次，仍不满足则该回合失败，不把原文执行声明发布到用户聊天。已提交的真实操作不会因回复格式失败撤销，后续应 inspect 后继续，避免重复创建。

边界：这不是任意自然语言的语义证明器。`discussion`/`proposal`/无回执 `blocked` 的文本仍依赖模型遵守“讨论、提案、待答问题不能夹带已执行事实”的协议；本轮确定性保障针对正式执行报告的 ID、回执和状态，以及原事故中的零工具纯文本成功声明。未宣称所有措辞上的幻觉已被消除。

## 6. 小说方法

`novel.production` 1.5.0 新增 `ending_policy=continue|conclude`，默认 conclude 保持旧使用方式。基线、正文、审查、交付均读取冻结 bindings：continue 的范围末章只完成本批交付，保留未决主线；旧大纲的结尾安排不能强迫新范围完结。历史已验收章节只作为历史，不回写正文。旧范围交付检查其编号范围内的材料，后续范围文件不作为“数量超出”错误。

没有添加 F 的确定性字数计算器，没有降低正文门槛，没有修改 4000 上限，也没有把旧超长章节重新认定为合格。

## 7. 已执行验证与复现

专项第一次整合回归：`test_scope_extension.py`、`test_conversation_contract.py`、`test_p0_assignment_regressions.py`，**47 passed / 71.20s**。这是补充更多报告测试之前的中间结果；最终结果另列，不引用旧 docs 的通过结论。

真实数据副本命令：

```powershell
.\venv\Scripts\python.exe -X utf8 scripts/verify_scope_extension_snapshot.py
```

源数据库以 `mode=ro` 连接，用 SQLite backup 生成临时副本；只在副本中修订和物化计划，临时项目路径也重定向，未执行 Worker。结果记录于 `test-results/scope-extension-project-snapshot.txt`：

| 项目 | 结果 |
|---|---|
| 原 Requirement | cancelled / revision 1 |
| 修订后 | 同一 ID / active / revision 2 |
| 方法版本 | 1.4.0 → 1.5.0 |
| 原 Task | 6 个，完整记录相等 |
| 新增 Task | 第 7–12 章，共 6 个 |
| 字数上限 | 4000 保留 |
| 结尾策略 | continue |
| 原 binding | 完整记录相等 |
| 生产状态改写 / Worker 执行 | 0 / 0 |

这证明真实历史结构能走完修订和任务图创建；不等价于真实模型已生成并验收完新增六章。生产服务尚未重启，原项目仍保留此次验证前的业务状态。

### 最终验证

最终专项：范围扩展、完整 Conversation Gateway、对话契约和 API 审计入口，**38 passed / 36.25s**。包含五步模型交互：scope.inspect → scope.revise → task.plan.save → task.create → work_result；运行时确认同一需求 revision 2、六个新增 Task、一个新 Workflow，最终报告待执行，忽略模型夹带的“已运行”文案。

最终全量命令：

```powershell
.\venv\Scripts\python.exe -m pytest -q --disable-warnings --tb=short --junitxml=test-results/scope-extension-2026-09-10.xml
```

结果：**309 passed / 242.07s，退出码 0**。证据：[JUnit](../test-results/scope-extension-2026-09-10.xml)、[全量输出](../test-results/scope-extension-full.txt)、[真实项目副本结果](../test-results/scope-extension-project-snapshot.txt)。`git diff --check` 以仓库 CRLF 配置校验通过。

全量回归前已纠正两层回合状态缓存，并更新旧契约测试以表达新的 Conversation 报告协议、默认 ending_policy 和关闭但不退休的语义；没有通过删除这些场景或忽略失败完成验证。

交付后，加载新代码的服务在原项目收到扩写请求即可使用修订入口，不需要更换项目或转交另一个代理。本轮没有替用户运行新增章节，生产需求仍保持原状态；真实模型的后续写作交付应在该项目中继续验证。
