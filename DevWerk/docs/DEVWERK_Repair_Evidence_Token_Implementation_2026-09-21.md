# 软件交付、返修与无效 Token 消耗修复实施记录

日期：2026-09-21。对应 [修改设计](DEVWERK_Repair_Evidence_Token_Fix_2026-09-21.md)。当前状态：代码修复完成，真实模型两轮返修验证通过；软件 Loop 工具集成后的最终全量回归 370 项通过（267.71 秒），无失败、跳过或 xfail。以下区分框架验证与完整产品验收。

## 1. Mailbox 的职责和明确限制

| 对象 | 职责 | 不具备的职责 |
| --- | --- | --- |
| Project Conversation Agent | 根据当前用户请求解释需求、规划、显式控制任务 | 从通知中推导新的用户授权 |
| Mailbox | 持久通知 payload/来源，投递、确认、失败记录 | 调模型、规划、retry/cancel/rerun、选 Worker、转述指令、代理 Agent 通信 |
| Worker input | `v1_worker_inputs` 中显式绑定 Worker/Assignment 的输入，幂等及消费回执 | 自行启动 Worker，给已结束 Assignment 注入指令 |
| Workflow Runtime | 沿冻结有向图执行单 Worker Column，安全交接、复验及终态判定 | 在 Column 内创建多 Agent loop |
| Task feedback | 持久缺陷、责任列、复验义务及来源 | 替代通知或让模型自行宣称缺陷已解决 |

Gateway 在构建模型上下文之前截获所有非用户通知回合，直接生成确定性状态通知并结算投递。Registry 另行拒绝事件回合的业务 mutation，避免以后某条旁路重新开放权限。通知 acknowledged 不设置原失败 Job 的 resolved_by_job_id。未开始的用户 Job 优先于通知 Job。

小说 review、代码测试报告等内容仍保存在原通知 payload 或交付物中。简短通知保留 Mailbox 来源 ID；上下文仅注入事件索引，不自动把整份报告重新发给主 Agent。Column 间报告通过 Workflow artifact/context contract 传递，返修通过 Task feedback 路由。

## 2. Worker、返修、证据和冻结版本

- Worker 与 Session 不销毁。当前 Assignment 只原生回放自身轨迹；之前 Assignment 提供带来源的参考索引，原文可通过 `agent.context.read` 查询。新 Assignment 始终获得完整选定输入，不因 Worker 存在旧 Session 就删去附件。
- 缺陷写入 `v1_task_feedback` 后持续存在。Runtime 只在当前 Column 安全完成的事务里选择已声明的返修边；当前 Worker 不会因为反馈入库被无序替换。
- 软件 Loop 的每个 Worker 显式声明 `task.feedback.record/list`；审查、QA、交付、验收列通过 `task_contract.feedback_columns` 强制保留记录能力。当前 Assignment 上下文提供责任 Column 和冻结检查目录，Worker 不必猜测 check key 或通过 Mailbox 中转报告。
- 复验收据来自 Runtime，不来自 Markdown 中“测试已通过”的描述。收据绑定 Task/revision/Run/Attempt，旧 visit、旧 Attempt 或失效列之前的证据不能满足当前终态义务。
- 验证既适用于 Agent Column，也适用于确定性 capability_sequence Column。无法提供有效证据的 done 提交在终态事务中被拒绝。
- 软件 Loop 升至 1.3.0。实现、系统测试、交付和验收都需要实例化行为验证；`--list` 不能作为直接参数形式的行为检查。任意命令的实际测试覆盖无法由框架形式证明，仍需对照需求检查测试内容。
- `task.successor` 使用明确的新 TaskPlan/revision；`task.rerun` 使用旧计划。后继保留前驱关系、未解决反馈及原验收义务，原 Task 不改写。
- 后继使用统一 `materialize_task_plan`：入口文件/授权快照重新验真、依赖图一次物化、重复请求返回同一后继；不会只创建目标 Task 而把依赖遗留为永远未创建的引用。相同旧 TaskPlan 应使用 `task.rerun`，不能误当 corrected successor。
- `blocked` 报告结束为失败 Job，保留部分成功操作及真实回复；“对话处理结束”不再等同于“业务成功”。

## 3. 无效消耗控制

1. Mailbox 通知不调用模型，拒绝原事故中通知回合自主创建/取消/重跑任务的循环。
2. `conversation.turn.resolve` 默认绑定宿主已有的当前 message ID、intent revision 和用户来源；显式错误值仍拒绝，宿主不推断 discuss/execute 语义。
3. 当前状态只注入 Workflow/计划摘要；完整对象按需读取。可见对话默认保留最多 40,000 字符，保留近期完整消息及归档提示，WorkIntent 决策/约束独立保留。新增 `conversation.history.read` 分页回查。
4. 命令 cwd 越界在效果收据开始之前拒绝，不再误报为已启动命令的未知结果。
5. 合法的重复失败解除引用不再触发完成协议反复纠错；冻结行为检查能区分交付义务与探索命令失败。

历史项目两次报告合计 303 次模型调用、2,625,515 recorded tokens，其中 Mailbox 83 次、746,291 tokens。这是历史开销，不是本次实测“已节省”数字。

对授权返修 Run 的原始 context 做只读投影对比：52,265→30,701 字符（仅 Workflow/计划/Mailbox 字段投影，不包含模型工具 schema，也不等于 token 或费用）。完整 Workflow 字段 12,705→131、WorkflowPlan 列表 6,481→120、TaskPlan 列表 2,748→119 字符。

## 4. 回归证据

### 确定性测试

- 早期定向：92 passed，1 条旧 Mailbox 自动返修断言失败，已改为验证零模型调用和显式返修。
- 之后定向：29 passed。
- 第一轮全量：364 passed，1 条旧 blocked=succeeded 断言失败，已按新的业务失败契约更新。
- 新增两次跨 Column 返修测试通过：实际文件变化、真实 Python 子进程失败、缺陷落库、定向返修、冻结检查通过；两个 Worker 各保留一个 Session，并分别产生三个 Assignment。这里模型响应是确定性替身，不冒充真实模型验证。
- 第二轮全量：369 passed in 320.02s。后继入口一致性定向 15 passed，全量 370 passed in 269.01s。软件 Loop 工具集成后定向 33 passed，最终全量 **370 passed in 267.71s**。均无 skip/xfail，最终进程退出码 0。执行命令：`venv\Scripts\python.exe -X utf8 -m pytest tests -q --show-capture=no`。

### 真实模型故障注入

脚本：`scripts/eval_worker_repair.py`。证据目录位于测试契约要求的 `D:\workspace\codex-devwerk-project-files`，使用全新 DB 和工作目录。

首轮 `worker-repair-20260921T001946Z` 未通过，保留失败数据。Review 成功提交缺陷；实现 Worker 修复代码后尝试了不适用的 pytest，再使用 unittest 成功，随后被探索失败/旧证据引用反复阻塞。一次 `pip install pytest` 返回全部 already satisfied，没有证据表明安装了新依赖。修订后的验收脚本只允许冻结的本地 unittest 命令，错误命令在执行前拒绝，并为实现列添加真实冻结检查。

第二轮 **通过**，证据为 [report.json](D:/workspace/codex-devwerk-project-files/worker-repair-20260921T050737Z/report.json) 和同目录 `runtime.db`：

| 项目 | 实际结果 |
| --- | --- |
| 模型 | MiniMax-M2.7 |
| Project | `prj_8d33e8abd3824fe7947a210160677168` |
| Task | `tsk_bf4bc52e4c33421590632326c3634616`，最终 done |
| 路径 | Review→Work→Review→Work→Review→done |
| 反馈 | 2 条均 resolved，关联真实 Runtime verification ID |
| 生命周期 | 2 个 Worker，2 个 Session，5 个 Assignment（实现 2、审查 3）；同角色 Session 保留 |
| 时间 | 05:07:39.017→05:09:16.200 UTC，97.183 秒 |
| 模型调用 | 20 次，5 个 Agent Run 全部 succeeded |
| 工具 | 模型工具调用 21 次；另有 Runtime 冻结验收调用 3 次；`column.complete` 5 次全部成功，无反复拒绝 |
| Mailbox | 6 条消息 acknowledged；通知模型调用 0；未生成 Conversation Agent Run |
| recorded tokens | input 37,011 + output 4,674 = **41,685** |
| cached input | 80,227，单列，不加进上面的 recorded total |

对照首轮失败实验：21 次调用、40,816 recorded tokens、117,336 cached input，任务停留 recovering，仅完成首次 Review。两轮完成的工作量不同，不能声称总 token 按某百分比下降；可证实的是修正后用 20 次调用完成了两轮实际反馈/返修，完成协议没有拒绝循环，Mailbox 没有模型开销。

该实验验证框架反馈与生命周期，**不等于原 Vue/Spring 项目或完整浏览器交付验收通过**；生产项目未重启、未执行其 queued Job、未手改其交付物。

## 5. 历史数据与部署边界

启动时批量迁移旧 Worker Mailbox 并将未消费行改为 failed 的方案被自动审批拒绝，理由是会改写历史持久状态。本次采用新增表、不修改旧行的替代方案；Worker inspect 可查询历史只读输入。

只读生产 DB 核查：旧 Worker Mailbox 有 12 条 failed、2 条 pending。两条 pending 为 ID 289、290，属于 `prj_8d5d626356124d43bfca175ebe6db0aa`，并非本次用户报告的项目。未消费内容不冒充已处理；部署前需单独审查是否以显式 Worker 输入重新提交。没有实施历史迁移、部署或恢复生产任务。

既有冻结 Workflow 不被自动改写。原事故任务若需要使用修正后的检查，应显式建立 corrected TaskPlan 和 successor；不能把当前 active Workflow 偷换给旧 Task。

## 6. 日志交叉核对

09-20 测试跨越日志轮转，相关末段位于 `data/logs/devwerk.log`。行 528 可定位 Mailbox Run `arun_91273c644de24b97a5ca1ceedff21900` 与 Job `cjob_d0a751e2704b4822befec41d961f8e5a`；行 538 包含 cwd 越界后被包装为 `project.command.run outcome is unknown after ValueError` 的原始错误。后续多次上下文引用该错误，不能将日志重复出现次数当作独立命令执行次数；本记录的调用/token 数采用 DB 回执及 llm_usage。
