# Workflow 修订新 Project 黑盒复测（2026-10-19）

## 目标和边界

- 新建独立 Project，以自然语言与 Conversation Agent 讨论至少五轮，由它通过内部工具创建 Workflow 和 Task。
- 首版执行后，请它修改同一 Requirement 的看板，在终稿后增加独立核查步骤；核对实际发布的列图、旧 Task 状态和新版 Task 调度。
- 不直接通过 API 创建或修改 Workflow、Task；不以 Agent 的文字声明代替数据库和运行记录。
- 活跃执行约五分钟检查一次；不因测试停止 DevWerk 服务。

## 待记录

- Project ID：`prj_318a37e106cc45ecb3d76312913dd62d`；目录：`data/projects/agentic-workflow-revision-retest-20261019/`。
- DevWerk 启动后 `/v1/health` 返回 `ok`；未停止已有服务。
- 对话轮次、首版图和 Task：待记录。
- 对话第 1 轮：询问目标、读者和做法；Agent 仅讨论关键决策，未创建任务。第 2 轮明确工程师读者、核心认知、1000–1500 字及先讨论查证与推进方式。第 3 轮请 Agent 规划，它提出查证、提炼、写作、检查、定稿五步及交接物。第 4 轮确认实际打开来源页面、检查失败退回，Agent 说流程足够启动，仅待确认例子。第 5 轮确认例子并授权它建立看板、安排 Task 与交付；运行中。
- 修订请求、实际工具调用及图版本：首版图尚未发布，因此本轮无法进入修订测试。
- Task 推进与交付结果：创建轮 `cjob_9b7a5dc4b432498e9356a5592ec16a34` 最终 `failed`，错误 `ExecutionBudgetExceeded: Agent iteration/tool-call ceiling reached`；`/board` 中 `workflow=null`、`tasks=[]`。不把讨论结果或已保存 Plan 计作交付。
- 产品代码改动：本次黑盒测试没有新增产品代码修改；没有停止 DevWerk 服务。

## 创建轮阻塞证据

- 运行 `arun_623e796909744b97b313d6f9e7b6984b` 最终 112 次工具调用，其中 `workflow.publish` 18 次、成功 0 次，`workflow.plan.save` 成功 6 次。Agent 保存过 Plan，但没有把合法图发布为活动 Workflow，也没有创建 Task。
- `workflow.publish` 的失败不是单一的 JSON 传输问题：最早的对象参数被拒于中文 `entry` key；随后对象参数因 Column keys 与已保存 Plan 不一致、`upstream_outputs` 引用未知列、Plan 的 `capability_sequence` 与图中 `agent` 执行器不匹配、sequence 缺少完成 outcome、以及 `input_contract` 要求 Runtime 不提供的根字段而失败。另有多次把整个 `workflow` 对象序列化成字符串，直接遭工具输入契约拒绝。
- Agent 也曾给 `workflow.plan.save` 传不允许的字段或漏填必需字段；它能局部修正，却无法使 Plan 与 Graph 的整体约束收敛。当前暂无 Runtime 状态机拒绝合法换版的证据；测试卡在**首版 Workflow 作者协议**，尚未触及新版静止/换版逻辑。
- 这显示复杂图构造依赖 Agent 在长循环中自行对齐 Plan、图、工具字段，错误回执虽然准确，却不足以使其稳定收敛。若后续要修复，应先针对通用的 Workflow 作者协议与工具输入形态做设计，不应加入文章或浏览器场景专属逻辑。本次只记录黑盒结果，未直接替 Agent 发布图。
