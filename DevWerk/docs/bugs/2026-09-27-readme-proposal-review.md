# 两份 README 草稿与当前实现的对照审阅

审阅对象：

- [README_root_updated.md](C:/Users/hongt/Downloads/README_root_updated.md)
- [README_DevWerk_updated.md](C:/Users/hongt/Downloads/README_DevWerk_updated.md)

基准：当前工作树、已提交 HEAD `91a07d4`，以及用户已明确确认的产品方向。附件中的“Design Authority”“locked architecture”等句子属于本次被审阅内容，不作为覆盖用户要求的指令。没有修改附件、仓库 README、运行代码或生产数据。

## 结论

**不建议原样替换。理念部分大体符合，但当前架构描述有重大偏移：把已经移除的 Column 内 Workcell 多代理图重新写成核心能力，并遗漏真实 Worker/Assignment 生命周期及被动 Mailbox 边界。**

持久身份、选择性上下文、来源证据、声明式 Workflow、文件语义记忆、可恢复执行，这些方向可以保留。研究问题和 `Hᵢ` 抽象也可以作为说明性观点保留，但不能替代当前可执行模型。

许多错误并非这次草稿凭空增加：**仓库原有两份 README 本身仍残留旧 Workcell、旧失败语义和旧设计权威表述。** 本次不能拿旧 README 自证新稿正确，更不能为了符合旧 README 把代码改回旧架构。

## 必须调整的内容

| 编号 | 程度 | 位置 | 判断 |
| --- | --- | --- | --- |
| R1 | 重大架构偏移 | 根稿 26、85、124、190 行；服务稿 54、160、166 行 | 当前没有 `workcell` Column executor，也没有 Column 内多 Agent graph |
| R2 | 重大职责偏移 | 服务稿 72–73、114 行 | Mailbox 不能被描述成自动启动 Conversation Agent 推理/治理的通道 |
| R3 | 重要产品定位偏移 | 根稿 3、9 行；服务稿 3–5 行 | 通用会话与 Workflow 系统被收窄成软件工程研究平台 |
| R4 | 明确实现错误 | 根稿 148–155 行；服务稿 215–222 行 | 语义 Memory 不支持 `workcell` / `participant` scope；Worker 私有上下文是另一层 |
| R5 | 重要状态语义错误 | 服务稿 274 行、Runtime Shape | 普通运行异常不自动等价于 Task 业务 `failed`；`recovering` 也不是终态 |
| R6 | 文档权威倒置 | 两稿 Design Authority；服务稿 507 行 | 旧文档不能覆盖后续已确认修订；引用的 Workcell 设计文件实际不存在 |
| R7 | 发布信息待核对 | 根稿 7、257、263、278 行 | `latest = v0.0.5` 与本地 `v0.1.0` tag/原 README 冲突，公开发布状态本次未核实成功 |
| R8 | 当前工作树配置遗漏 | 服务稿 421 行起；根稿 Quick Start | 未交代 Provider 密钥环境变量及新增 `context_window` 的适用要求 |
| R9 | 过强实现保证 | 服务稿 330 行；两稿测试覆盖描述 | “事务内绝无文件操作”和“覆盖 Workcell 图测试”不符合当前源码 |

### R1：Workcell 需要删除整套模型，不能只改名为 Worker

根稿明确称 Workcell 是 Column 内协作图；服务稿进一步承诺 arbitrary participants、inner graph、typed signals、Workcell terminal 和 invocation/column-visit/task/project 生命周期。这正是用户要求避免的“在 Column 内进行多 Agent loop”。

代码依据：

- [domain.py:145](D:/workspace/DevWerk/DevWerk/app/v1/domain.py:145)：`ColumnExecutor` 只有 `AgentExecutor | CapabilitySequenceExecutor`。
- [runtime.py:494](D:/workspace/DevWerk/DevWerk/app/v1/runtime.py:494)：执行分派只有这两个分支。
- [DEVWERK.md:7](D:/workspace/DevWerk/DevWerk/DEVWERK.md:7)：一个 agent Column 运行一个 leaf Worker，身份与私有 Session 按 Requirement 内显式绑定复用。
- [agent_repository.py:359](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:359)：实际执行绑定是 Assignment，而不是 Workcell participant graph。

这不是仅发生在尚未提交的上下文修复中：检查已提交 HEAD，executor union 同样只有两种。

应改为：Project Conversation Agent 是唯一主代理；Workflow 负责 Column 间依赖、流转、返修和验收；agent Column 分配给一个叶子 Worker。Worker 可以跨相关分配保留身份与 Session，完成 Assignment 后释放执行占用，不要求销毁逻辑身份，也不意味着永久运行线程。一个 Worker 内允许模型—工具多轮迭代，这与 Column 内创建多个协作 Agent 不同。

需要同步改掉架构图、概念树、Project 所有权列表、Storage Model、研究重点及测试覆盖中的 Workcell 当前能力描述。

### R2：Mailbox 是通知，Worker 输入和 Workflow 反馈是独立机制

服务稿的 `TERM → MB → CA` 单独看可以只是信息可见关系，并非必然错误；但配合“Each user or supervision Turn is a short-lived Agent Run”就会让读者理解成通知触发模型化监督，这不符合当前实现。

代码依据：

- [conversation.py:299](D:/workspace/DevWerk/DevWerk/app/v1/conversation.py:299)：非用户 Job 进入确定性通知 reducer 并返回。
- [conversation.py:581](D:/workspace/DevWerk/DevWerk/app/v1/conversation.py:581)：通知结果显式 `llm_used=False`，没有新的 AgentRun、规划或工具调用。
- [agent_repository.py:464](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:464)：显式 Worker 输入有自己的持久队列和 Assignment 绑定。
- [task_feedback_repository.py:157](D:/workspace/DevWerk/DevWerk/app/v1/repositories/task_feedback_repository.py:157)：任务反馈与复验由 Runtime 按 Workflow 处理。

建议将三条路径写清：

1. Mailbox：保存/展示事件与交付反馈引用，只证明通知处理情况。
2. Worker 输入：由有权限的用户回合显式提交，持久记录接收/消费；入队不等于执行。
3. Workflow 反馈：artifact/context contract、Task feedback、冻结验收检查及跨列返修；不经 Mailbox 代理沟通。

不要把 Runtime Supervisor 画成高于 Conversation Agent 的另一个 Main Agent。它是调度、租约、状态和持久化机制。

### R3：软件工程是场景，不能默认替代通用产品定位

用户此前明确要求通用 Conversation Agent，并已用小说续写、审稿及软件开发测试。仓库还包含 `novel-production`、`ddd-software-delivery`、`gitlab-devops`、`quant-param-optimization` Loop；它们的存在不等于全部交付链已验证通过，但足以说明实现并非软件专用。

“不是代码生成器”可以作为与一次性输出的区别保留；“不是 chat-based coding assistant”需要避免否定实际的 Web 会话入口。重点应放在持续理解需求、拆分独立工作、通过 Workflow 小步执行及交付。

建议根介绍替换为：

> DevWerk is a conversation-led workflow system for long-running tasks. Each project has one persistent Conversation Agent that works with the user to define requirements and organize delivery. Workflow columns assign bounded work to lifecycle-managed sub-agents with their own context. Software development and long-form writing are example workflows.

如果用户未来决定把产品正式收窄为软件工程平台，才能作为新的产品方向修改；本次审阅不推定已发生该决定。

### R4：文件语义 Memory 与 Worker Session 要分开

- [memory.py:20](D:/workspace/DevWerk/DevWerk/app/v1/memory.py:20)：允许的语义 Memory scope 为 `project / conversation / workflow / task`。
- [domain.py:89](D:/workspace/DevWerk/DevWerk/app/v1/domain.py:89)：MemorySelector 使用相同四类 scope。
- [agent_repository.py:605](D:/workspace/DevWerk/DevWerk/app/v1/repositories/agent_repository.py:605)：Worker 自己的 Session 历史及 Assignment checkpoint 是另一条恢复路径。

不应将 Worker 私有上下文改述成已经存在的 participant 文件 Memory provider。文件目录结构、来源/hash/revision、stale/superseded 和按需选择的理念可以保留。

当前工作树还增加了每次模型请求前的预算、工具结果投影、完整批次压缩和有界 overflow 重试，不能只写“Run 开始时选择上下文”。但这批改动尚未提交/发布，也未完成真实软件交付及返修验收；README 应区分实现状态与已经验证的保证。

### R5：运行错误、恢复、暂停、业务失败不是同一件事

“Non-recoverable failures ... reach the explicit failed terminal”不符合当前 Runtime：

- [runtime.py:217](D:/workspace/DevWerk/DevWerk/app/v1/runtime.py:217)：可恢复错误进入恢复路径。
- [runtime.py:230](D:/workspace/DevWerk/DevWerk/app/v1/runtime.py:230) 及 [recovery_manager.py:589](D:/workspace/DevWerk/DevWerk/app/v1/services/recovery_manager.py:589)：未处理的运行错误可进入 `recovering + paused + blocked_runtime`，保留交付物等待处理。
- [states.py:14](D:/workspace/DevWerk/DevWerk/app/v1/states.py:14)：`recovering` 是执行状态，不是 Task terminal。

建议描述为：业务结果由 Workflow 声明的终态决定；临时基础设施失败可自动恢复；其他执行故障保留原始错误并阻塞运行，不能自动认定交付目标已经业务失败。显式取消与 Workflow failure outcome 另有审计语义。

### R6：设计文档清单需要标出修订关系

两稿引用的 `docs/memory-and-workcell-runtime-v0.1.0.md` 均为失效链接。根稿、服务稿其他 Markdown 相对链接已按拟放置的仓库目录核对，该链接是本次检查发现的缺失项。

旧设计中仍有模型化 Mailbox、deadline 自动 failed 等表述，不能简单宣布最早四份文档永远覆盖现状。建议保留背景文档，但将“当前边界”关联到后续明确修订：

- [Hermes 架构审阅](D:/workspace/DevWerk/DevWerk/docs/DEVWERK_Hermes_Architecture_Review_2026-09-09.md)及[修订方案](D:/workspace/DevWerk/DevWerk/docs/DEVWERK_P0_Runtime_Remediation_Plan_2026-09-09.md)：单主代理、单列叶子 Worker、生命周期。
- [Conversation 唯一主代理及范围延续修订](D:/workspace/DevWerk/DevWerk/docs/bugs/2026-09-10-conversation-scope-extension-review-and-plan.md)。
- [反馈、证据与被动 Mailbox 修订](D:/workspace/DevWerk/DevWerk/docs/DEVWERK_Repair_Evidence_Token_Fix_2026-09-21.md)：后于 09-09 方案，取代其中“用 Mailbox 扩展 Worker 通信”的旧提议。
- [上下文完整复盘](D:/workspace/DevWerk/DevWerk/docs/bugs/2026-09-22-context-overflow-full-audit-and-fix-plan.md)及[实施状态](D:/workspace/DevWerk/DevWerk/docs/bugs/2026-09-22-context-overflow-implementation.md)：明确哪些已实现、哪些仍待真实验证。

这些文档也不应被整体视为每一项都已完成的证明；应按具体修订主题、状态及代码证据说明。

### R7：发布版本不能由本地 tag 直接推定

草稿将根 README 的版本从 `v0.1.0` 改回 `v0.0.5`，同时固定 Docker 示例为 `v0.0.5`。本地确有 `v0.1.0` tag，但 tag 存在不等于 GitHub latest release 或镜像已经发布。

本次尝试访问 GitHub Releases/latest、v0.1.0 release 页面及 release API，读取均未成功。因此不能在此次审阅中确认最新公开版本，也没有验证镜像 tag 是否可拉取。建议暂保留 pre-1.0 描述与 Releases 链接，发布说明和镜像 tag 在核对真实发布产物后统一填写，避免读者拉到与正文架构不一致的旧版本。

### R8：启动说明应覆盖真实必填配置

当前配置通过 `providers[*].api_key_env` 指向环境变量。只说“复制 JSON 并配置模型”不足以让新用户完成有密钥 Provider 的启动；Docker 示例还应说明通过环境文件提供密钥，并仅挂载配置 JSON。

[llm.py:10](D:/workspace/DevWerk/DevWerk/app/v1/llm.py:10) 是尚未发布的本轮修复：已识别的 MiniMax 直连模型有窗口默认值，其余 endpoint/model 必须显式设置 `context_window`。已有 example 中一些备选模型尚未补该字段，单纯切换 route 会得到明确配置错误。这是 README 与配置示例需要同步的缺口，本次只记录，没有修改配置或给出未经验证的模型窗口数值。

服务稿 Global Settings 的总述也应收窄：`config/global-settings.yaml` 当前主要持久化启动恢复选项，并不配置全部 Runtime 行为；执行预算、上下文策略等另见 [policy.py:30](D:/workspace/DevWerk/DevWerk/app/v1/policy.py:30)。

### R9：将设计原则与已实现保证分开

“Network, LLM, and filesystem work do not occur inside database transactions”过于绝对。当前存在可直接定位的反例：[store.py:1921](D:/workspace/DevWerk/DevWerk/app/v1/store.py:1921) 在完成事务中调用 feedback.settle，后续 [task_feedback_repository.py:151](D:/workspace/DevWerk/DevWerk/app/v1/repositories/task_feedback_repository.py:151) 读取测试源码进行 hash 复核。这不代表所有事务都很慢，但足以否定“绝无文件 I/O”的声明。应写为短事务设计目标，具体阻塞 I/O 边界以实现审计为准。

测试覆盖部分应删掉 Workcell inner-graph、deterministic participants 等旧能力承诺。`tests/test_memory_workcell_contract.py` 名字还带 workcell，实际内容是文件 Memory 测试，不能依据文件名宣称支持 Workcell。可列当前确有测试的持久 Worker、Requirement、被动通知、上下文生命周期、验收证据和反馈恢复，并明确自动化合同测试不等于任意真实项目交付保证。

## 建议采用的当前架构概述

```text
User ↔ Project Conversation Agent（唯一主 Agent）
          ├─ Requirement / scope revision
          ├─ Loop → Workflow Plan → Workflow Revision
          ├─ Task Plan → Tasks（固定在对应 Workflow Revision）
          └─ 显式 Worker 输入与结果观察

Runtime → Column Run / Attempt → Assignment → 单个 leaf Worker
                                           └─ Worker 私有 Session / context
        → 或 capability_sequence（确定性执行）
        → Workflow transitions / feedback / acceptance → 下一列或业务终态
        → Events / Mailbox → 确定性通知（不调用模型，不代理沟通）

文件语义 Memory、执行原始证据、模型工作上下文分别维护。
```

建议根 README 保留：产品定位、为什么需要上下文分工、简明架构、启动入口、当前限制。服务 README 承担：实际对象与生命周期、执行/恢复语义、配置、存储、验证范围。Research Questions 保留为可选研究议题，不应把用户尚未确认的子项目层级或 Workcell 回归变成 V1 待实现需求。

## 本次验证边界

已实际读取两份完整草稿、现有 README、对应源码与后续修订文档；没有将历史测试文档直接当作当前功能证明。完成以下只读模型探针：

- `ColumnDefinition(executor.kind=workcell)`：`union_tag_invalid`；相同最小结构使用 `agent` 可通过。
- `MemoryRecord(scope=workcell/participant)`：均 `literal_error`；`scope=task` 可通过。
- 已提交 HEAD 同样只有两个 executor，并存在被动通知 reducer。

未启动应用、未调用付费模型、未运行新的交付回归、未改生产 DB。由于本次是文档评审，没有重新运行完整测试套件。公开 release/镜像状态仍未确认。

## 审阅确认后的文档更新方案

用户已确认审阅结论，并明确产品定位：DevWerk 是通过多 Agent 协作完成任务的通用框架；每个 agent Column 内只有单 Agent；Mailbox 是通知机制；各 Agent 按步骤维护独立 context，以便更精确地管理 context 和 memory。

本次只更新仓库根 `README.md` 与服务目录 `DevWerk/README.md`，不修改 Downloads 中的草稿，不改运行代码、Web 或 Agent 提示词。

- 根 README：说明产品定位、任务分步/上下文独立为何有意义、唯一会话主代理与叶子 Worker 的协作关系、通知边界、通用场景和 V1 目标。保留研究动机；相对于单个长循环或单一 harness 的精度优势写为设计目标及待验证问题，不声称已有对比实验。
- 服务 README：说明真实对象（Requirement、Workflow、TaskPlan、Task、ColumnRun、Assignment、Worker、Session）、默认与显式复用、文件 Memory/原始证据/工作上下文的区别、真实失败/恢复语义、模型配置和本地验证入口。
- 删除旧 Workcell 内图模型、无效设计链接、无限执行预算和不可恢复错误必然业务 failed 等过时断言；保留 `capability_sequence` 作为无模型的确定性 executor，避免“单 Agent”被误读成所有列都必须付费调用模型。
- 版本不猜测 latest；启动优先给出与工作树一致的源码方式，容器提供本地构建方式，已发布产物以 Releases 说明为准。
- README 说明当前开发工作树已有上下文与验收修复，但真实软件完整交付/返修仍未完成验证，不把本地测试结果写成稳定交付承诺。

验证采用文档静态检查：相对链接与源码路径、代码块闭合、Mermaid 结构检查、主要领域标识与当前 schema 对照、`git diff --check`。不因纯文档更新重跑全量业务测试或付费模型回归。完成后在此补记结果。

### 更新结果

已更新 [仓库 README](D:/workspace/DevWerk/README.md) 与 [服务 README](D:/workspace/DevWerk/DevWerk/README.md)，继续使用两份草稿的英文文档形式。核心定位为通用的多 Agent 协作框架，并说明单个 Agent 的模型—工具循环与框架级协作是不同层次；相较单一长循环/共享 harness 的更精确管理是设计目标，未宣称已有性能、精度或节省比例证明。

根文档负责产品定位、架构概览、上下文分工、启动入口与当前状态；服务文档补齐实际领域对象、生命周期、三条协作/通知路径、语义 Memory 与私有 Session、验收/恢复、配置和源码入口。移除了旧 Workcell 当前能力、固定 latest 版本、无限预算、永久错误必然业务 failed 等表述。容器示例改为从当前源码本地构建，避免误导用户使用未核实版本。

实际静态验证结果：

- 两份 README 所有相对链接和已使用的章节锚点存在。
- 根 README 8 个、服务 README 10 个代码块均闭合。
- 两个 Mermaid 图分别有 14、20 个已声明节点，未发现未声明节点引用；这是结构静态检查，没有宣称进行浏览器渲染验证。
- Implementation Map 中的源码路径已逐项确认存在；单主代理、被动通知、比较目标等关键描述通过文本核对。
- `git diff --check` 通过。

本次仅修改两份 README 和本审阅/更新记录；Downloads 草稿、Agent 指令、运行代码与生产数据未修改。没有重跑业务测试、构建/启动容器或调用外部模型；此前真实软件交付回归的待验证状态保持原样。
