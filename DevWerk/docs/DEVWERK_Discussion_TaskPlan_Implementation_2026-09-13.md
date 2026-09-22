# 09-12 讨论误启动与 TaskPlan 准入修复实施记录

状态：本次 09-12 问题已修复并通过回归（2026-09-15）。设计依据：[详细方案](DEVWERK_Discussion_TaskPlan_Fix_2026-09-12.md)。本文只记录本次实际运行结果，不采信历史测试结论。

最终结果：**360 项本地测试通过；真实讨论 60/60（120 个回合）通过；核心流程 5/5 通过，均跨过首个 Column；真实 Provider 空响应恢复 1/1 通过。** 项目 Conversation Agent 仍为唯一主 Agent。完整软件的后续构建、测试、交付未在本次完成验证，生产服务未部署或重启。

## 已实现的行为

1. Web 默认按当前对话意图处理，新增只讨论选项与绑定不可变方案的开始按钮。旧 start_task=true 不再自动创建 Requirement 或授权写入。
2. 项目 Conversation Agent 仍是唯一主 Agent；在同一会话中解释当前用户意图，通过 conversation.turn.resolve 保存回合边界、讨论 hold、决策、未决问题、方案和执行范围依据。不存在上级 Main Agent，也没有独立分类 Agent。
3. 未解析/讨论回合的业务写入、进程和调度控制均在统一 Registry 门禁拒绝。resolve 必须单独提交；成功后才能选择业务工具。Provider 工具列表按持久状态收缩，降低计划 schema 对讨论的干扰。
4. TaskPlan 保存与创建共用静态编译器。错误 exact 路径、非字符串叶子、不匹配的执行策略和重复交付身份在保存时拒绝；创建时仍检查动态状态。一个交付 Task 穿过完整 Column 生命周期。
5. 软件 Loop 升为 1.2.1。入口需要当前需求 revision 的有效执行依据，以及同一依据下写入的需求文件回执；检查文件当前内容与回执摘要一致。requirements Column 输出独立追踪文件，不覆盖接受基线。
6. continue 复用同一范围的有效执行依据；新范围讨论不清除已有 hold，不关闭原有 Worker。最终讨论草稿更新不能提升执行权限。
7. 中断反馈统计本轮真实创建、复用及已启动的任务，保留已保存的工作流/计划与最后具体错误。

关键代码：`app/v1/conversation_intent.py`、`repositories/conversation_intent_repository.py`、`services/task_plan_compiler.py`、`services/task_entry_admission.py`、`conversation_failure.py`。其余入口改动见 Git diff。

## 已运行的确定性验证

| 检查 | 实际结果 |
| --- | --- |
| compiler + capability 首轮专项 | 32 passed |
| conversation + scope 上轮专项 | 37 passed |
| 受影响专项（追加失败反馈后） | 77 passed，2 项旧错误文案断言失败；已更新，待最终重跑 |
| 讨论边界扩展专项 | 17 passed（后续新增测试另行验证） |
| 软件入口证据专项 | 6 passed：有效回执、缺失依据、错误范围、非该范围的回执、文件变化、save 后变化 |
| Web JavaScript 语法、git diff 空白检查 | 通过 |
| 全量测试（09-14 继续时读取完整结果） | 347 passed，350.89 秒；此后局部问题更新与约束来源提示的修改须再回归 |

这些测试验证门禁、原子性与契约，不证明模型自然语言理解正确。

## 真实 Provider 评测及失败保留

使用当前配置 MiniMax-M2.7 / Anthropic，temperature=0.2；临时 DB、临时项目目录，关闭生产 usage/log 文件写入。脚本：`scripts/eval_conversation_intent.py`。`.devwerk/memory` 为宿主生成的记忆视图，单独区别于业务实现文件；业务副作用还同时检查 Requirement、Workflow、TaskPlan、Task、成功工具与持久 phase。

| 隔离目录（位于 Windows Temp） | 结果与促成的修正 |
| --- | --- |
| devwerk-intent-eval-790f_19q | 首轮回复格式失败；增加已解析边界后不重复提交 resolution 的指引及 draft_update |
| devwerk-intent-eval-e9hecf87 | 决策枚举与 revision 错误；增加准确冲突提示与 turn.inspect |
| devwerk-intent-eval-igu7wmsj | 将无执行输出为 null 后重复失败；仅对该字段定义 null=none，不允许 null 授权 |
| devwerk-intent-eval-20wtsayg | 首轮成功；评测将宿主 memory 文件误计为业务文件，已纠正评测范围 |
| devwerk-intent-eval-1g24aqcw | 第二轮错误执行，已建立 Workflow。此为真实语义失败，绝不计作通过；随后增加分阶段工具列表及当前回合边界提示 |
| devwerk-intent-eval-5184mo17 | 前两轮讨论均成功且零业务副作用；第三轮明确开始后把 artifact ID 当作文件 execution_key，计划准入失败。继续修正回执可发现性 |
| devwerk-intent-eval-rbe64pon / f9s_qetr | 扩展评测出现多次 final resolution/draft JSON 格式失败；中止批次保留现场，随后引入已确认纯讨论的自然语言结束路径 |
| devwerk-intent-eval-twdqass9 | 两轮讨论通过；开工时已有问题只更新 key/state 被要求重复正文，随后用户摘录抄错而失败。改为局部问题更新与消息 ID 来源引用 |
| devwerk-intent-eval-ip2lv5ub | 多个讨论变体通过，但 variant-2-0 的“继续”误触发并实际创建 1 个 Task；中止批次，P0 仍打开。检查发现当前请求封装可覆盖历史指令的冲突表述，已按设计第 16 节修正 |

2026-09-14 继续实施的实际结果：

| 隔离目录 / 检查 | 实际结果 |
| --- | --- |
| e4dhu_ns | “继续”负例 3/3 通过；后续扩大批次再次失败，因此本批不能证明稳定 |
| gfs52_gw | 前两轮通过，第三轮最终创建 1 个 Task，但最终回复格式失败，Job failed |
| evhpchgm | variant-2-0 再次误启动并创建 Task；批次中止，保留失败 |
| zimg1tu7 | 同 Agent 解析上下文投影后，“继续”负例 3/3、共 6 个讨论回合通过，零业务副作用 |
| 4hr4uhag | 两轮讨论通过；第三轮创建合法 Task 后错误尝试手动发起 Worker，随后自行实现代码。主动中止，未计通过 |
| chvbauhs | 核心序列 1/1 通过：两轮讨论零业务副作用，明确创建一个 Task，状态查询无重复创建；requirements Column 已完成并进入 domain_design |
| s2jk1glg | 31 个完整样例通过、23 个失败、1 个只记录首轮；94 个已记录回合无误启动。失败为 21 次 Provider 529、2 次空响应；服务持续过载后主动中止，不计整批通过 |
| 全量确定性（解析投影/结束工具之前） | 350 passed，371.54 秒 |
| 结束工具与 conversation 专项（通知过滤修正后） | 29 passed，39.01 秒；含混合 reply+write 零副作用测试 |
| 全量确定性（回复中断恢复修正之前） | 354 passed，383.64 秒 |
| 回复恢复、conversation 与 Assignment 故障专项 | 49 passed，103.65 秒；先以新增测试复现恢复路径错误与空响应，再修正并通过 |
| eh5ibpbn | 串行核心重跑 0/5，均首轮即 Provider 529；不能当作语义失败或通过，保留外部阻塞证据 |

本轮实现补充详见设计第 17–20 节：解析阶段暂时投影历史；恢复正常工具后仍为同一 Agent；结束工具提交事实报告；Task 创建回执明确异步 Runtime 交接；空响应有界恢复；回复中断重放重新渲染当前事实；讨论草稿允许同回合多次版本更新。真实失败没有删除，继续修复及回归。

## 2026-09-15 继续验证

上轮最后的文档写入被自动审批服务因 Codex 套餐额度拒绝，未绕过；本次用户要求继续后恢复正常写入。模型服务亦已恢复响应。修正草稿版本记录与旧开发库的单 Job 唯一限制，并冻结代码开始全量回归。迁移仅在隔离测试库验证，没有对生产数据库手动执行。

最终真实批次已经完成：`qp7vgslz` 为核心序列及首 Column，`mdt9cy5c` 为 20 个多轮讨论变体各 3 次。脚本同时记录全部 `app/v1` Python 源码摘要；明确开始轮额外检查主 Agent 仅写入口基线，状态轮不得出现业务副作用。

适配器修正之前的全量确定性回归：**359 passed，401.18 秒**。包括草稿多次更新/幂等/旧表兼容、回复中断恢复与当前状态重渲染、连续空响应有界失败；55 个 Python 文件 AST 解析通过。`qp7vgslz` 核心完整通过；追加 4 次核心重复在 `83v3fe1q`。三个批次的运行时代码、提示词/schema、Loop 摘要已核对一致。

核心序列最终 **5/5 通过**（`qp7vgslz` 1 次 + `83v3fe1q` 4 次）：每次两轮讨论零业务副作用，明确开始后创建一个 Task，Conversation Agent 只写该 Task 的入口基线，状态查询无新增业务副作用，首 Column 完成并进入 domain_design。模型仍有字段/回执 ID 被拒绝后自行修正的中间调用；没有把这些拒绝包装成执行成功。

收尾检查增加了 `app/services/anthropic_client.py` 的空 assistant 消息过滤（设计第 21 节）。上述真实批次审计到的空 assistant 记录为 0，因此该补丁不改变这些样例发出的消息内容。专门的真实 Provider 恢复验证位于 `devwerk-empty-reply-eval-rg0n1x63`：只将第二次模型响应注入为空，其余调用使用原配置真实 Provider；该验证与完整自然生成样例分开统计。适配器变更后再次运行全量测试：**360 passed，373.27 秒**；Git diff 空白检查与修改的 Web JavaScript 语法检查亦通过。

真实讨论变体最终 **60/60 通过，共 120 个用户回合**，没有创建 Requirement、Workflow、TaskPlan、Task 或业务文件，没有成功的业务写入/进程/控制副作用，每轮均正常完成并保持讨论。核心序列与讨论变体均使用 MiniMax-M2.7 原配置，没有换模型；有限样本通过不等于对任意自然语言的数学保证。

空响应注入验证 **1/1 通过**：审计中保留 1 条空响应，随后经真实 Provider 完成回复；共 4 次回合模型请求，其中 1 次为人工空响应注入。无业务副作用。

可直接复核的证据：

- [核心首批 summary](C:/Users/hongt/AppData/Local/Temp/devwerk-intent-eval-qp7vgslz/summary.json)；[核心重复 summary](C:/Users/hongt/AppData/Local/Temp/devwerk-intent-eval-83v3fe1q/summary.json)。各 original-* 目录内的 results.json、status.json、entry.json 和 runtime.db 保存逐轮结果与首 Column 证据。
- [60 个讨论样例 summary](C:/Users/hongt/AppData/Local/Temp/devwerk-intent-eval-mdt9cy5c/summary.json)；[模型与代码摘要](C:/Users/hongt/AppData/Local/Temp/devwerk-intent-eval-mdt9cy5c/metadata.json)。各 variant-* 目录内保留独立 DB 和逐轮结果。
- [空响应注入结果](C:/Users/hongt/AppData/Local/Temp/devwerk-empty-reply-eval-rg0n1x63/empty-reply/results.json)。这是故障注入验证，不并入上述自然生成样本。

## Review 与运行说明

- 业务边界主要审阅 `conversation_intent_repository.py`：持久用户来源、revision CAS、幂等 resolve、执行范围校验、事件不扩权；Web 的只讨论模式为确定性上限，自然语言 auto 仍依赖同一个 Agent 的理解。
- Task 入口主要审阅 `task_plan_compiler.py` 与 `task_entry_admission.py`：统一静态准入、已有字符串叶子 exact 替换、重复身份拒绝、真实 grant 与基线写回执绑定；不凭模型填写的确认布尔值授权。
- 会话收尾主要审阅 `conversation_report.py`、`agent_runner.py` 与 `agent_tool_execution.py`：真实事实渲染、已讨论的自然答复、混合批次先拒绝、空响应有界恢复、回复中断重放。
- 新版软件 Loop 仅用于新绑定；旧 Workflow revision 保持冻结。启动服务的正常初始化负责新表与开发版本兼容迁移。本次未启动、重启或部署生产服务。
- 复测命令：`venv\Scripts\python.exe -X utf8 -m pytest tests -q --tb=short --show-capture=no`；真实评测分别运行 `scripts/eval_conversation_intent.py --suite original --repeat 5 --step-entry` 和 `--suite variants --repeat 3 --workers 3`。真实评测使用临时项目，不能当作原用户项目已修复或完整软件已交付的证明。

完整轨迹在上述目录的 `original-0/runtime.db` 与 `results.json` 中；后续脚本也记录模型参数和 prompt/schema/Loop 摘要。失败样例保留，不能只挑通过样例。

## 验收范围与限制

- 已满足：全量确定性测试 360 项无失败。
- 已满足单次流程：明确开始后保存并创建一个完整交付 Task，状态查询不重复创建，requirements Column 完成并进入 domain_design。
- 已满足：核心序列 5/5；20 个多轮变体各 3 次，共 60/60。历史失败批次保留，不并入当前版本的通过统计。
- 完整软件构建、测试、最终交付未验证；创建 Task 不代表软件已交付。

本次没有修改生产数据库、用户测试项目内容或启动/重启生产服务。历史冻结 Workflow 不做批量迁移。新增持久表在服务正常初始化时建立。
