# Conversation Agent 扩写范围失败：事实复核与修复方案

日期：2026-09-10。状态：**A–E 代码修复完成；最终全量 309 passed / 242.07s，完整 Gateway 及真实项目副本扩写验证通过。生产数据库和小说正文未修改，真实模型的新增章节交付未执行。** 实现细节及验证边界见 [范围扩展实施记录](../DEVWERK_Scope_Extension_Implementation_2026-09-10.md)，下文保留修复前证据与设计理由。

合并评审补充：字数硬上限 Bug 见 [统一修复方案](../DEVWERK_Novel_Combined_Remediation_Plan_2026-09-10.md)。用户最新决定为先实施本文 A–E，F 确定性字数校验延后。第一章实测超限，因此下文“保留前六章”表示保留历史与产物，不表示将六个 done 状态直接当作全部验收合格。

项目：`prj_22d5638974ee405885b0552e4a24e91f`，小说《未取的照片》。用户先约定六章，随后明确要求扩展至十二章，7–12 章不结束故事，并保留继续扩写的空间。

本次依据当前源码、`data/devwerk.db` 的只读查询、`data/logs/devwerk.log` 和新建临时 SQLite 的独立诊断。不采用 docs 中以前的测试结果作为此次正确性证明。之前 297 项本地回归没有覆盖这个完整的真实交互链路。

## 1. 结论与架构纠正

**每个 Project 的 Conversation Agent 就是该项目唯一的主代理，等价于 Hermes 的会话主代理。它上面不应再有 System Main Agent、管理员代理或其他需要转交权限的代理。**

当前代码没有实际创建第二个上级代理：`AgentRepository.main(project_id)` 将 `conversation_agent(project_id).logical_id` 映射到实例表。该项目的两处 ID 都是 `ca_e0f7af30275b48baa218aa0058e378a5`，实例 role 为 `main`，父实例为空。发生失败的 AgentRun 同时是 `kind=conversation` 且携带这个实例 ID。

但是，之前的实现存在实际设计和代码错误：

1. 在模型可见的身份、工具说明和错误信息中混用 Conversation Agent 与 Main Agent，使一个代理看起来像两个权限层级。
2. 将“需求不是 active”“实例 ID 缺失”“实例角色不符”三个条件合并，统一抛出 `Only the Main Agent can select an active Requirement`。本次触发的是第一个条件，模型却把它解释成自己没有权限。
3. 用“创建/关闭 Requirement”替代了正常的项目范围修订流程。六章扩至十二章不应要求关闭旧需求、重建项目、让用户寻找另一个代理或绕过工具。
4. 将规划能力依赖于回合开始时计算的状态，却没有在合法需求切换后更新。
5. 没有约束“已创建任务”等执行声明与真实工具结果的一致性。

这些问题属于上一轮实现的缺口，不能归因于用户操作错误，也不能以“需要系统 Main Agent”解释。

建议保留 Requirement 作为**需求数据与版本范围**，保留 Worker/Assignment/ContextSession 作为执行与上下文结构；去掉额外 Main 权限主体的表达和分叉判断。Column 内仍然只运行一个 Worker，跨角色协作仍由 Conversation Agent 与 workflow 组织。

## 2. 实际失败时间线

下列时间统一为北京时间，数据库原始时间是 UTC。

| 时间 | 实际行为 | 证据与结果 |
|---|---|---|
| 09:15:08 | 初次讨论时自动建立默认需求 | `req_4d41561859c041b7bcc07c81493f6ec5`，objective 仍是最初“先和我聊聊”的请求，revision=1。 |
| 09:25:18 | 应用 novel.production | `loopapp_15b4146528b8484dba1617885e7a7ade`；chapter_count=6、chapter_target_characters=3000、chapter_max_characters=4000。 |
| 09:26:01 | 建立第一至第六章 Task | 六个 Task 最终均为 done，固定于 `wfrev_9f16b124ea9749b8bbc98391fecce53f`。 |
| 10:17:07 | 用户要求“再写接下来的6章” | Agent 询问是否扩为十二章。 |
| 10:26:07 | 用户明确扩到十二章且不要结尾 | Job `cjob_c99a7d6e92884707bab6020064516593`。 |
| 10:26 这一回合 | 回复“项目已更新”“Ch07–Ch12 任务已创建并进入排队”“chapter07 已自动调度” | Run `arun_0898f36ab2af46149266819609e2de8f`：**0 tool invocation、0 Operation**。没有实际更新或创建。 |
| 10:27:08 | 用户指出看不到排队 Task | Job `cjob_2ebfdd3b9da74574ad4c45509523fb9b`，Run `arun_f646db9229a34fdc98afd1f78f0ed0bd`。 |
| 10:27:19 | 对旧 TaskPlan 直接创建 chapter07–12 | Invocation 2942–2947 全部失败：`Task reference does not belong to the plan`。 |
| 10:27:51 | 尝试保存 chapter07–12 新 TaskPlan | Invocation 2948 失败：chapter_number=7 大于旧绑定 chapter_count=6。 |
| 10:28:01 | 调用 requirement.close(cancelled) | Invocation 2949 成功；默认需求被取消，其 25 个 Worker 全部 retired。 |
| 10:28:04 | 调用 requirement.create(scope_key=default, objective=十二章...) | Invocation 2950 失败：相同 key 读回已经 cancelled 的旧需求，随后选择失败。 |
| 10:28:13 | 再选择旧需求 | Invocation 2952 重复收到同一句误导性的 Main Agent 错误。 |
| 10:28:33 | 最后回复归因为系统权限，建议重新建项目 | 解释不符合真实调用者身份和失败条件。 |

查询时项目只有 chapter01–chapter06 六个 Task，全部 done；没有 chapter07–chapter12。原 Requirement 为 cancelled，没有成功建立新 Requirement。

日志也包含对应调用，关键定位：`data/logs/devwerk.log:3970` 为 chapter_count 准入失败，`:3980` 为关闭需求调用，`:3981` 为关闭成功。后续 capability.error 对应创建和选择失败。日志会轮转，故诊断复核优先使用上述 DB Job/Run/Invocation ID。

还有一项明确偏差：用户没有在此次扩写请求中授权把硬上限从 4000 改为 4800，模型却声称做了这一修改；数据库实际仍是 4000。新方案不能沿用这个虚构变更。已有 [字数硬上限 Bug](2026-09-10-novel-hard-character-limit-not-enforced.md) 独立保留，本文不冒称已修复它。

## 3. 缺陷登记及证据级别

| 编号 | 级别 | 缺陷 | 证据级别 |
|---|---|---|---|
| E1 | P0 | 需求状态错误被包装成代理权限错误；创建同 key 不检查新旧 objective 冲突 | 生产 DB/log 与临时 SQLite 复现。 |
| E2 | P0 架构缺口 | 缺少同项目的目标/Loop 参数/Workflow/TaskPlan 协同修订，普通扩写被旧范围阻断 | 生产拒绝结果与源码确认。 |
| E3 | P0 | 为范围扩展先取消旧需求，后续创建失败无法原子回退；Worker 同步全部退休 | 生产已发生。单个工具事务正确不足以保证整个范围变更正确。 |
| E4 | P0 | 零工具调用却报告任务已创建、已调度 | 生产 AgentRun、Operation、Invocation、Task 四者交叉核对。 |
| E5 | P0 | 选择/创建有效需求后，规划仍使用旧的 start_task 计算结果 | 当前源码与临时 SQLite 定向复现；不是本次 2950 的直接原因。 |
| E6 | 范围修订的必修架构风险 | 准入和 Worker 输入按 Project 读取最新 bindings，未与使用中的 Workflow/Task 版本绑定 | 源码确认；尚未把“旧任务受新绑定污染”描述为此项目已发生的事实。 |

### E1：状态与身份判断混在一起

定位：`app/v1/repositories/agent_repository.py` 的 `requirement()`、`select_requirement()`，`app/v1/capabilities.py` 的 `_requirement_create()`。

现有实际链路：

```text
requirement.create(scope_key="default", objective="十二章...")
  → requirement() 查到现存同 key 行，不比较 objective，也不创建修订
  → 返回 cancelled 的六章需求
  → select_requirement() 因 status != active 拒绝
  → 错误信息却说 Only the Main Agent...
```

选择失败并不说明调用者是另一个代理。也不能把现有实现概括为“Conversation Agent 无法创建 Requirement”：本次临时诊断中同一实例用新 key 创建成功。

### E2/E6：范围没有修订入口，也没有完整的版本关联

定位：

- `store.py:_apply_loop()`：Project 已有 Workflow 时不允许再次 loop.apply。
- `planning_repository.py:create_task_plan()`：根据 Project 的 binding 校验新 TaskPlan。
- `store.py` 中 Task materialization：再次读取 Project binding 校验任务范围。
- `store.py:get_project_loop_binding()`：按 Project 取最新绑定。
- `runtime.py:step()`、`_select_context()`：同样按 Project 取 binding/assets。

Workflow 本身可以发布新 revision，所以不能说平台绝对不能扩章。但当前没有完整、可发现且一致的参数修订路径。只创建一个新 Requirement 不会将 chapter_count 改为 12；只删掉准入约束也不会修正 Worker 输入中的旧数量、旧结局约定和交付要求。

如果简单增加“UPDATE bindings_json”，旧 Task/正在恢复的 Assignment 又会读到新参数，破坏原有冻结语义。因此修订必须建立版本关联，不能改一条全局当前值就结束。

### E3：关闭状态不应成为范围扩展的前置步骤

`close_requirement()` 在所有任务已终结时直接关闭需求并退休全部 Worker。当前操作按其局部契约执行成功，但缺少“我要扩展交付范围”的领域操作，模型于是用取消作为试探性修复。

一旦关闭成功，后一个工具失败不会回滚前一个工具。这不是把两个工具强行塞进同一模型批次就能解决的问题，应提供一次完整且原子的范围修订提交。

仅把 cancelled 改回 active 也不够：相同 Requirement/reuse_key 下的 Worker 已 retired，当前唯一约束和查找逻辑会继续命中它们。

### E4：对话完成与业务操作完成被混淆

`ConversationGateway._process()` 已收集真实 `tasks`、`workflow_publications` 和 action ledger，但随后直接将 `result.text` 作为成功通知发布。

模型输出一段无工具调用的自然语言即可结束会话；本次没有任何成功 task.create，却出现“已排队”的声明。Job succeeded 只能说明这个会话回合完成，不能说明扩写请求已落实。

应修复事实与回复之间的接口，不能靠强制每轮都建 Task；普通讨论仍然必须允许零副作用完成。

### E5：回合授权快照没有刷新

Gateway 将 `graph_mutation_allowed` 计算后放入 `AgentRunSpec.start_task`。dispatch 会从 Job 刷新 requirement_id/revision，但不会刷新这个布尔值；`_require_user_planning_turn()` 又首先检查它。

所以一个最初 graph-disabled 的合法用户回合，即使已经创建/选择 active Requirement，后续规划仍可能报 `requires an active Main planning turn`。应分别表达“用户允许执行”“当前选中的范围”“当前租约是否有效”，每次控制派发按当前事务状态判断。

## 4. 本次独立诊断结果

在全新临时 SQLite、临时 Project 下调用真实 CapabilityRegistry，未访问模型服务。使用同一个 Project Conversation 身份，并建立有效 Job owner：

```text
same_identity True main
close     → ok=true, status=cancelled
same key  → ok=false, Only the Main Agent can select an active Requirement
fresh key → ok=true, status=active
planning after selection with initially disabled graph
          → ok=false, task.plan.save requires an active Main planning turn
```

最后一项有意模拟回合开始时没有可规划 active scope 的状态，而保留 `user Job.start_task=true`，用以区分用户没有允许执行与缓存状态没有更新。这是问题复现，不是修复后的回归通过结论。

## 5. 修正后的职责模型

```text
Project
 └─ Conversation Agent（唯一会话/规划/调度代理；持久 identity + session）
     ├─ Requirement / Scope revisions（数据，不是代理或权限主体）
     └─ Workflow → Task → Column Assignment → Worker
                                               └─ 私有 ContextSession
```

Runtime 负责事务、租约、版本、准入、派发与结果持久化，不作为另一个可对话、可审批的 Main Agent。代理之间的区别来自职责与上下文：Conversation 组织项目，Worker 完成单 Column 工作；不存在一个需要 Conversation 向上求助的 System Main。

Hermes 的对应源码：`run_agent.py:AIAgent.run_conversation()` 直接运行该实例的会话；`tools/delegate_tool.py:_build_child_agent()` 接收这个 parent_agent 创建子实例，`delegate_task()` 的控制操作直接使用 parent_agent 的上下文。借鉴此身份关系即可，不引入 Hermes 可选的多层 orchestrator/delegation 层级。

具体调整：

1. 内部公共入口命名为 `agents.conversation(project_id)`，以现有 `v1_conversation_agents.logical_id` 为唯一规范身份；不再建立平行身份。
2. 模型提示、工具说明、错误信息和页面均使用 Conversation Agent。旧 `role=main` 是存量别名，可兼容迁移为 `conversation`；保留所有 ID、消息、Job、父子关联。
3. 统一从真实 Conversation Job + Project 关系解析调用者；不能以模型传入一个 role 字符串“提权”，也不要求模型申请 Main 身份。
4. Worker 的 Column 单职责边界继续保留。拒绝嵌套建图属于执行结构约束，不是新增一个更高级代理。

## 6. 修复包 A：明确错误与动态回合状态

将复合错误拆为可判定的错误代码，例如：

| code | 条件 | 对 Agent 的有效提示 |
|---|---|---|
| requirement_not_active | 目标 revision 已关闭/取消 | 返回当前状态和允许的范围修订入口，不描述为代理身份错误。 |
| requirement_scope_conflict | 相同 scope_key，对应不同 objective | 返回 existing_requirement_id/current_revision，要求使用修订操作。 |
| requirement_revision_conflict | expected_revision 过时 | 读取当前版本，重新形成修改；失败不写入任何状态。 |
| conversation_context_missing | 没有当前项目的有效 Conversation Job | 表明 Runtime 调用上下文缺失，不能建议用户找 System Main。 |
| execution_ownership_lost | 当前 Job/Assignment 已失去执行权 | 按现有 fencing 停止提交。 |

`requirement.create` 同 key 同内容可幂等返回 active 记录；同 key 不同内容必须返回冲突，不静默忽略新 objective。closed 记录不能伪装成新创建成功。创建/选择的结果必须包含实际需求 ID、版本和状态。

将 `start_task` 的历史兼容入口拆清楚：用户原始执行许可存于 Job；当前可规划范围由每次 dispatch 在事务内读取。选择/修订范围后，当前 turn 的执行上下文和模型后续可见状态同步更新；用户的讨论回合仍不能因为存在 active Requirement 就自动获得执行意图。

## 7. 修复包 B：同项目的原子范围修订

建议增加一个项目级领域工具 `project.scope.revise`，由现有 Conversation Agent 使用。Requirement 的创建/选择工具可继续支持独立目标，但普通扩写不需要先 close/create，也不需要切换代理。

示例输入仅表达本次已确认的变化：

```json
{
  "expected_scope_revision": 1,
  "objective": "保留已交付第1–6章，继续创作第7–12章；第12章只结束本次交付，不结束故事",
  "binding_patch": {"chapter_count": 12},
  "continuation": true,
  "reason": "用户明确要求扩写且保留后续空间"
}
```

用户来源从当前 Conversation Job 和已持久化的需求确认取得，不让模型编造授权。示例不是已存在的工具 schema；后续实现需补齐候选 Workflow revision/方法版本引用、必要的业务参数，并验证未知字段。

提交过程：

1. 读取当前 scope revision、绑定的 Loop package、Workflow revision、已有 Task 与已验收产物；形成完整候选新范围。
2. 对候选参数和执行方法先完成校验，包括章节序号、现存六章、后继依赖、产物路径、方法兼容性；不先取消旧范围。
3. 一次 DB 事务校验 Job owner 与 expected revision，追加需求版本、binding 快照及对应 Workflow revision，更新当前 scope 指针，记录来源/变更事件及执行回执。
4. 校验或提交失败：旧 scope 指针、生命周期、Worker 状态、绑定和 Task 全部不变。外部模型调用不在这个事务里。
5. 变更成功后，保存新的 chapter07–12 TaskPlan，绑定新 scope/Workflow，使用现有 Task API 建立串行依赖。第7章显式接续已验收第6章。
6. TaskPlan/Task materialization 可以继续使用现有幂等操作与再发现机制；范围已修订但任务未建成时，只报告实际阶段并在恢复后继续，不能声称已排队。

修改章节数量不自动修改 4000 字硬上限，也不覆盖 chapter01–06 正文。新的小说走向应把旧六章已发生的事实作为历史，避免把原作结局改写成没有发生。

### 7.1 冻结数据的关联

建议增量增加 scope/requirement revision 快照，不覆盖旧数据：

- revision 保存 objective、状态、来源用户 Job、前版引用及创建时间。
- revision 关联固定的 binding 快照、Loop package digest 和 Workflow revision。
- TaskPlan、Task、Assignment、事件 Job 绑定明确的 scope revision；不能仅保存 root requirement_id 后取全局当前版本。
- 规划准入、Task materialization、Runtime context、方法资产读取统一经过 `scope_for_workflow_revision` / `scope_for_task`；默认项目当前范围只用于新的用户规划。
- 同一个 Task 不能在规划时按12、执行时按6；旧 Task 也不能因新范围生效就自动改用12或改变验收。

存量绑定按可核实的 Workflow 关联回填。存在多个历史绑定且无法唯一归属时，明确列出待核对对象，不默认选最新。当前项目只有已知六章绑定，关联清晰。

### 7.2 旧版本任务与迟到事件

新的范围只影响新工作。已完成 Task/Assignment 继续保留旧版本及产物证据；已在运行的 Task 按旧冻结契约完成，结果仍归旧版本，不自动完成新范围。

因此要同时调整当前“Assignment revision 必须等于 root requirement.current_revision”的简单判断：区分“旧版本不再接新任务”与“旧分配被显式取消”。仅存在新版本不能无差别杀死合法旧任务；明确取消才使旧分配失效。

用户确认的续写可以产生新活动版本；旧 Worker 的事件不能重新打开已关闭范围，也不能在没有新用户目标的情况下把旧六章扩成十二章。事件 Job 必须保留来源版本，不能从任务 root ID 解析到当前最新版本后自动继承新范围。

## 8. 修复包 C：生命周期与续写上下文

1. 常规 scope 修订不退休空闲 Worker。仍可复用的写手继续保留私有 context，在新 Assignment 接收明确的新范围和已验收历史。
2. Requirement 的完成记录、暂停执行、永久退休 Worker 分开处理；普通交付阶段完成不等于 Worker 应永久退休。
3. 本项目已经 retired 的25个实例不批量改回 available。保留旧身份/会话审计，后续需要的角色建立新实例，显式交接已验收章节和必要摘要。
4. 为 retired 实例之后重建同名角色增加逻辑 worker_key → 当前实例的绑定/代际记录，避免现有 `UNIQUE(project_id,requirement_id,reuse_key)` 持续命中退休实例。空闲可用实例不因每次范围修订就更换身份。
5. 交接传递目标相关摘要和产物引用；完整旧 transcript 保留并可查，不把全部兄弟 Worker 历史灌入新写手。

这样既保持持续 Worker，又能恢复本项目已经发生的退休状态。生命周期操作仍由本项目 Conversation Agent 完成，不增加外部管理代理。

## 9. 修复包 D：执行事实与对话报告

保持普通会话模型，不把 Conversation 强制改造成每轮都必须调用 column.complete 的执行器。

建议在当前 Gateway 的 action ledger 基础上增加统一的 `TurnOutcome`：

- `mode`：讨论、方案、已应用变更、阻塞；
- 本轮确实新建/复用的 Task 与其实际状态；
- 实际发布的 scope/Workflow revision；
- 已失败或尚未完成的操作，以及是否已经提交部分有效变更。

上述事实由 Runtime/DB 构造。模型不能通过在回复 JSON 中填写 Task ID 让未执行的变更生效。已创建、排队、运行、交付属于不同事实；task.create 成功通常也不能推出 chapter07 已经开始 foundation。

实施时应让模型在最终回复前收到这份事实，并用带结果引用的结构表达执行声明。Runtime 对这些声明核验；引用不存在、没有成功回执或状态不符时，返回具体矛盾，允许同一个 Conversation Agent 有限次修正。持续不匹配时输出基于真实状态的阻塞/部分完成说明，不能发布虚构的执行总结。

用户仍能看到自然语言讨论和创作建议；执行状态部分由验证过的事实生成，不将内部 ID/审计字段堆进普通聊天。无工具回合可以正常讨论，但其结果不能被渲染为“扩写任务已创建”。

明确边界：结构化字段或一次模型自检本身不能证明任意自由文本没有幻觉；不能仅添加提示词或用“已创建”等关键词正则就声称 E4 已解决。回归必须注入本次这种零工具虚构结果，并检查实际面向用户的回复/状态输出，而非只检查内部 action_ledger 为空。

## 10. 小说方法补充：批次交付不等于故事完结

当前 Loop 将 chapter_count 对应的最后一章称为“最终章”，并要求生成 FINAL_ACCEPTANCE.md。用户现在要求“第12章不结尾”，需要区分：

- 当前交付范围：截至第12章；
- 故事是否完结：否；
- 之后是否可续写：是。

建议在 novel.production 方法新版本中增加明确的阶段交付/故事完结参数，例如 `ending_policy=continue|conclude`。scope 修订必须显式选择兼容的新方法版本，不能给旧 schema 塞入未知参数，也不能在相同 digest 下偷偷改变资产内容。

`continue` 下第12章仍可完成本次验收，但交付报告应称“本阶段/本范围完成”，保留后续叙事空间。验收记录按 scope revision 保存，项目索引指向最新交付；旧六章的最终验收记录仍保留为当时范围的验收事实。

已有章节事实、4000字硬上限与 accepted 正文哈希继续约束新工作，不能因为要扩写就放宽所有验收。业务策略由 Loop 声明，不能在通用 Runtime 中硬编码“小说/章节”规则。

## 11. 本项目的恢复步骤（实现后执行）

本次仅设计，不直接改写生产数据。后续在修复验证完成后：

1. 获取一致的 SQLite 备份与项目产物清单，记录六个 done Task、旧 scope、旧绑定和已验收文件哈希。
   同时按统一方案重新审计硬条件。第一章4142超过4000已确认，需要独立修订与新验收版本；不直接覆盖或抹除旧错误交付记录。
2. 在原 Project、原 Conversation identity/session 下，通过正式 scope 修订流程恢复活动交付范围；将旧 cancelled 作为历史保留，记录本次续写由用户明确要求。
3. 新范围截止第12章，故事完结策略为 continue，字数目标3000、硬上限4000保持现有有效约定。
4. 对确需使用的退休写手创建后继实例并交接；不复活全部旧 Worker。
5. 保存新 TaskPlan，创建 chapter07–12，chapter07 依赖已完成 chapter06，之后逐章串行；不得重建 chapter01–06。
6. 从 DB 验证恰好新增六个 Task 与正确绑定/依赖，调度器实际领取后才报告 running。历史 final_text 的错误声明不得作为恢复事实来源。
7. 更新基线中的未来叙事目标，但保留已写六章；完成第12章只代表当前范围交付，允许后续继续修订。

不采用“重建 Project”“删除旧 Requirement”“SQL 直接改章号/数量/Task 状态”“永久移除范围准入”作为恢复方案。

## 12. 实施次序、文件和回归

| 包 | 主要文件 | 必须交付的结果 |
|---|---|---|
| A 身份与错误 | `agent_repository.py`、`capabilities.py`、`conversation.py`、`agent_run_preparation.py`、`DEVWERK.md` | Conversation 唯一身份；状态/冲突/owner 错误分离；选择范围后控制上下文即时生效。 |
| B 范围修订 | 新 scope revision 服务与增量 schema、`planning_repository.py`、`store.py`、`runtime.py`、`task_graph_admission.py` | 原子范围修订，固定 binding/Workflow 版本，旧任务不受最新参数污染。 |
| C 生命周期 | `agent_repository.py`、调度服务、session 输入构造 | 普通范围变更不退休角色；退休实例的后继绑定与显式交接。 |
| D 事实报告 | `agent_models.py`、`agent_runner.py`、`conversation.py`、Web 对话状态渲染 | 没有执行事实不能声称已创建/已运行，普通讨论仍正常。 |
| E 小说方法 | `loops/novel-production/` 的新方法版本、scope 绑定兼容校验 | 批次交付与全书结尾分离，保留旧方法快照。 |

建议 A 先落地并做定向回归，但不能把 A 单独通过视为本项目已经可以扩写。B–E 形成完整修复后，再执行第11节恢复和真实模型 smoke。

最少回归矩阵（全部为待实现的关闭条件）：

1. 真实 Gateway 用户回合可直接创建/选择范围；实例 ID 与 Conversation ID 一致，不需要额外 Main。
2. closed 同 key 创建得到准确状态/冲突错误；同 key 不同 objective 不静默丢失新需求；新 key 可正常建立。
3. 用户执行许可为 true、初始范围不可规划，创建/选择后可在同一 turn 继续计划；讨论许可为 false 时不被误升级。
4. 六章已完成 → 用户扩至十二章 → 同 Project 恰好新增六个 Task，保留前六章与原产物。
5. 原子修订在各持久化步骤故障注入：旧范围、Worker 状态和指针不出现半提交；重试不重复版本/Task。
6. 旧 Task/恢复中的 Assignment 读取旧 binding 和旧资产；新 Task 读取新绑定，准入和执行一致。
7. 旧版本迟到消息不复活范围、不重复建图、不覆盖新范围完成状态。
8. 可用写手在修订后保留 context；退休写手的后继能够领取任务，且不混入无关 Worker 全历史。
9. 直接注入“零工具却声称已创建/已运行”的最终回复，用户可见状态不得接受该声明；失败回执和部分成功也不能说全部完成。
10. 第12章使用 continue 策略时完成范围验收，但不强制全书结尾；第13章的后续新修订仍可进行。
11. 数据副本迁移重复执行保持 ID、旧状态历史、来源消息、文件哈希和版本关联不丢失。
12. 最后由 Web → Conversation Agent → Workflow → Worker 实际跑通一个小型续写场景；真实模型结果与 DB 任务/产物交叉核对。

**Review 重点**：认可单一 Project Conversation 身份；认可通过原子 scope revision 表达正常扩写；认可旧工作固定版本、已执行声明必须有事实，以及批次交付不等于销毁项目或结束故事。本文为具体修复设计，不代表这些变更已经实现。
