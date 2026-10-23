# 公众号首篇 Agentic 文章：清空运行数据后的黑盒复测（2026-10-22）

## 测试目的

复用 2026-10-18 的公众号场景，从全新 Project 验证：多轮自然语言讨论后，Conversation Agent 用内部 Tool 创建 Workflow 与 Task；Workflow 驱动各 Column Agent 收集资料、精炼、写作、检查并交付一篇 1000–1500 汉字的 Agentic 入门文章。观察可见 Chrome MCP 是否真实调用，记录工具回执、状态转换、文件与内容质量。问题以事实证据归因，不以 Agent 声称完成代替实际结果；本轮先收集所有暴露问题，不为个别场景加入产品特化代码。

## 环境与清理边界

DevWerk 只能运行在 8000。先确认该端口属于本次已识别的 DevWerk 实例，再按用户明确的重启指令停止它。清空仓库内 `data/` 的旧数据库、日志、项目交付物、历史集成与 pytest 生成目录；保留 `config/llm.json`、`config/mcp-servers.yaml`、源码、文档和测试。删除前逐个解析绝对路径并确认位于仓库的 `data/` 中。随后在 8000 重启，保留本轮新数据库、日志和项目产物供用户审阅。若 8000 被另一个未知进程占用，停止并报告，不自行清理。

## 对话与验收

以自然语言逐轮确定读者、篇幅、资料可信度、浏览器查找、文风和交付形式；不替 Conversation Agent 决定 Column 数、边或 Task 参数。通过 Web 对话 API 发送用户消息；Workflow/Task 只能由 Conversation Agent 的 Tool 调用创建。**不在每轮提示 Agent“只讨论”或“可以开始”，让它依据当前用户需求自行判断执行边界；接受并记录讨论、建图、执行或拒绝等任何结果。**检查间隔遵循此前约定，避免实时轮询。验收证据：Conversation Job 与 Tool Ledger、发布的图与 Task Plan、实际 Task/Column Run、Chrome MCP 调用、最终 `done` 状态、源资料与交付文件。若阻塞，则记录具体输入、错误、持久化状态、可恢复性与未验证范围；测试结束保留现场。

## 执行记录

已确认旧 8000 监听进程是本机 Python/DevWerk（PID 38124），按用户要求停止。旧 `data/` 中含旧主数据库、项目、日志、此前隔离测试和 pytest 生成目录；先移除 97 个 pytest 符号链接本身，验证无剩余重解析点后清空 13 个顶层旧运行条目。没有触碰源码、配置、`loops/` 或文档。

DevWerk 仅在 `127.0.0.1:8000` 重启，启动 PID 5648；`/v1/health=ok`，新数据库的 Project 数为 0。Chrome MCP 使用原 `config/mcp-servers.yaml` 中 `chrome_visible` 配置，没有切换到隐藏浏览器或备用端口。

新 Project：`prj_3e7bb05621df4330bb1fcac61e4a093e`，目录 `data/projects/agentic-article-retest-20261022/`。第一轮以 `mode=discuss`、`start_task=false` 提交自然语言讨论，Job `cjob_c1b1ca131111424a80c01f90b19b14e5`。后续状态与回执待按约定间隔核验。

**方法纠偏（用户在第三轮提交后指出）：**第 1–3 轮消息都带了“不要/仍不启动”，且 API 显式传 `mode=discuss`、`start_task=false`；这些结果只能验证明确讨论指令被遵守，不能证明 Agent 独立判断执行边界。从第 4 轮起请求体只发送自然语言 `message`，使用 Web API 默认行为，不再提示是否进入工作，也不通过 API 固定讨论模式。无论 Agent 随后选择什么路径都记录事实，不为追求通过而改写用户意图。

第 1–3 轮 Job 均成功，未创建 Task；Agent 依次讨论读者和资料、1000–1500 汉字正文与来源记录、六列看板及来源复核。第 4 轮只问返工边界，Web 默认 `mode=auto,start_task=true`，Job `cjob_5fed05ff6e414bb5937b095a239ce196` 仍以 `business_outcome=discussion` 结束，Workflow/Task 均不存在。第 5 轮自然语言交付请求 `cjob_f8110f33f5b746b4a4198961e1520d7e` 指出审校只读与就地修正文的矛盾，要求按已定目标交付；同样只传 `message`。后续按实际 Tool/DB 状态判定它是否开始执行。

第 5 轮最终失败于 `LoopColumnProjection.writable_paths.0` 校验：`loop.inspect` 返回的 Loop 列写入路径允许 `{"$ref":"/input/task/input/summary_path"}`，但上下文压缩投影模型只接受字符串。Agent Run `arun_32c202bc08304ac3bd6aed9a68ef117a` 已成功调用授权、计划检查、Scope/Workflow 检查、两次文件写入、一次命令、一次 `loop.inspect(novel.production)`；投影崩溃发生在第 8 次工具调用之后。没有发布 Workflow 或创建 Task。错误是通用运行时协议不一致，不能据此判断文章或 Agent 的执行边界质量。失败现场和两份辅助脚本保留。

### 本轮最小修复计划

责任边界：`loops/` 定义可在 Task 输入绑定的路径；`context` 只负责把工具回执缩减给 Agent，不拥有 Loop 参数解释或路径解析；`runtime` 在 Task 执行时解析引用。因此仅调整 `app/v1/protocols/context/context_result_contracts.py` 的投影契约，允许路径字符串或类型明确的 `$ref` 对象，不在投影时擅自解析。增加一个以真实 Loop 为输入的上下文投影回归测试，先跑聚焦测试，再运行适当单元测试。修复后保留失败 Project，重启已识别的 8000 DevWerk 实例，并用新 Project 复测自然语言对话、建图与 Task 推进。

修复只改上下文投影协议和一条回归测试；聚焦投影测试 2/2、相关上下文与 Loop 测试 49/49 通过。重启前复核 8000 属于本轮 DevWerk PID 5648，仅停止该进程；新实例 PID 32596，仍监听 `127.0.0.1:8000`，Chrome MCP 发现 32 个工具。首次健康请求早于应用启动完成而被拒绝，随后启动日志确认服务就绪。旧失败 Project、DB 与日志均保留。

新 Project `prj_5bd669db8772471c981a51843b520c6c`，目录 `data/projects/agentic-article-retest-20261022-b/`。本次从第一轮起仅提交自然语言 `message`，不传 `mode`、`start_task` 或其它执行边界参数。第一轮问公众号系列和首篇 Agentic 文章的工作流组织，Job `cjob_290291e546f649ef9ee69cce9a1604e1` 已接受；按约定间隔核验结果，不提前替 Agent 决定何时开始。

新 Project 第 1 轮五分钟后为 `failed`，错误 `LLMProviderError: overloaded_error (529)`，属于 Provider 暂时过载。Agent 在过载前自主选择执行：`conversation.execution.authorize` 成功创建 Requirement，随后 `loop.list` 查询写作/调研模板得空、`workflow.plan.list` 得空；尚无 Workflow/Task，也没有交付文件。可见答复只报告 0 Task 与 Provider 错误。此轮证明 Agent 没有被测试提示强制留在讨论；不能把 Provider 过载解释为工作流设计失败。后续用自然语言继续，不预设它必须何时启动。

第 2 轮只补充篇幅、资料与落地文件要求，Job `cjob_b9b72bb114e648e0b3d01490b6a813d6`。十分钟时仍在运行。当前 DB 已有同一 Project 的两个 Workflow revision、0 Task；Agent 先两次将 `project.files.verify.minimum_non_whitespace_characters` 错填为字符串而收到 `invalid_arguments`，第三次建图成功，随后又发布第二个 revision。Task Plan 前三次校验从缺少数组字段修到 `valid=true`，却未保存；后续多次把 `tasks` 错交为对象，校验返回 `list_type`。还出现 `agent.context.read` 超过上限和读取 Worker 范围外消息各一次。以上均为运行中快照，不提前判定终态；等待后续五分钟间隔检查。

第 2 轮十五分钟时终止为 `failed`：Workflow 已保存，但 0 Task。最终 `ConversationProtocolStalled` 指向重复的 `workflow.inspect`，对用户的失败摘要还报告 `task.plan.contract` 连续返回相同内容四次。工具记录中 `task.plan.validate` 曾经返回一次 `valid=true`，后续却重新构造错误形状并反复验证；这是显著的协议使用/上下文连续性问题。需要对照消息、Tool 说明与 Provider 收到的 schema 找根因，不能把 `valid=true` 的校验回执误认为 Task 已保存或执行。

协议对照：`TaskPlanSaveInput.plan` 是完整的 `TaskPlan` 类型，Provider 能看见 `tasks` 为数组；`TaskPlanValidateInput.plan` 却是任意 JSON 对象，Provider 只看见 `additionalProperties: true`，无法由校验工具的输入 schema 获得相同结构。校验工具保留宽松输入是为了报告不完整计划的诊断，但模型表面契约与保存工具不一致，这给反复错误构造留下空间。第二轮消息中 Agent 曾自行断言“validator 拒绝嵌套数组，需要扁平化”，与实际有效回执相反；不能归因于 Python 校验器拒绝合法数组。

第 3 轮用自然语言指出 Workflow 已保存但文章未交付，请继续完成并报告实际核查与不足；没有限定讨论/启动。Job `cjob_e55b366e737f442d8704816955ce8c9f` 成功：Agent 自行修正 Task Plan 形状，`task.plan.validate` 通过、`task.plan.save` 保存 `tplan_867aca7c21d2494b98255db213add2ef`，`task.create` 创建 `tsk_19844140d95e4ebd9dbef313e7bc0a93`。回复明确承认文件尚未产出。DB 随后确认 Task 已从 pending 调度到 `research` Column，状态 `running`；这一步以持久化状态和 Tool 回执确认，不以回复中的“已排队”代替执行。

`research` 实际调用可见 Chrome MCP，账本记录 59 次 MCP 调用（含打开页面和执行页面脚本），不是模型凭记忆编造来源。约五分钟后 Task 变为 `recovering/paused`，Column `interrupted`，交付目录除 `.devwerk/memory` 外暂无研究文件。第 1 次 Column Attempt 因 Provider 529 结束；后续恢复尝试在重建历史动作账本时抛出 `ToolResult.error.type` 缺失，属于另一个通用 P0 阻塞。自动监督 Job 曾尝试 `task.retry`，随后也遇 Provider 529。不能把浏览器调用成功、资料采集完成、Task 完成混为一谈。

### 恢复账本错误的最小修复计划

`app/v1/execution/runtime.py` 的 `_column_action_ledger` 在读到失败的持久化执行回执时新造 `ToolResult(error={"message": ...})`，与 `app/v1/protocols/tools/core/capability_protocol.py` 的 `ToolError.type` 必填契约冲突。修复限于恢复账本构造：保留原工具回执的错误类型，缺失时使用既有 `PriorExecutionFailed` 类别，并以类型化 `ToolError` 序列化。增加一条失败回执恢复回归测试。先跑聚焦测试和相关 Runtime 测试；重启仅本次已识别的 8000 DevWerk 进程，让修复生效。失败 Task 与日志保留，后续由自然语言对话观察 Conversation Agent 如何处理恢复，不直接替它操作 Task。

代码已按计划实施。聚焦回放测试 2/2、相关 Runtime 测试组 67/67 通过。恢复时沿用原 ToolResult 错误类型，未把 MCP 超时当作成功或吞掉失败；持久化回执仅有消息时才使用通用 `PriorExecutionFailed`。尚未做全套回归，需黑盒续测验证 Task 是否能从该错误恢复。

确认无运行中的 Project Conversation Job、Task 为 `recovering/paused`，且 8000 仍是本轮 PID 32596 后才重启；新 PID 38352，`/v1/health=ok`。第 4 轮自然语言报告观察到研究中断，请 Agent 说明并继续交付，不指定具体 Tool 或执行边界。Job `cjob_f6bc1bf4ca034652ae7888bcf185b6b8` 成功：Agent 读 Task、Worker 和文件状态，实测新 MCP 会话只剩 `about:blank`；`agent.worker.send` 排入恢复说明，先试 `task.resume` 被拒，随后 `task.retry` 成功。DB 随后确认同一个 Task 回到 `running/active/research`，未由测试者直接修改 Task。Agent 报告文章及来源文件仍未落地。回复中称“更早的 Provider 529 已修复”，但实际只是上一次请求之后重新尝试，Provider 过载不能凭该说法认定已修复；此处记录为答复措辞不准确。

五分钟后 DB 确认 `research`、`distill` 两列都为 `succeeded`，Task 在 `draft/running`。项目目录已有 `research/sources.md`（约 11.9 KB）、`research/notes.md`（约 10.0 KB）和 `articles/01-agentic-primer.md`（约 5.6 KB）。这些是文件存在证据，尚未代表文章通过 `review` 或符合篇幅与事实约束；继续等待图的自然推进。

再过五分钟，`draft` 为 `succeeded`，Task 已进入 `review/running`；文章文件约 5.7 KB。尚无 `review` 出边决定，也无终态。

下一次检查，`review` 已以 `needs_revision` 成功完成并沿预定边返回 `draft`，不是运行时抢路由。审校报告 `articles/01-agentic-primer.review.md` 指出三处可定位的正文问题：无来源支撑的“更稳/更便宜/更好调试”比较判断、把 `agentic systems` 窄化为仅自主 Agent、退出条件引文混用；还建议补充来源清单的 URL/访问日期。报告同时记录了字数和代码围栏核对。Task 当前第二次 `draft/running`，待观察返工、再审与终态。

再过五分钟，第二次 `draft` 为 `succeeded`，Task 已进入第二次 `review/running`。尚无第二次审校回执。

第二次 `review` 又以 `needs_revision` 返回 `draft`。报告保存在同一路径并扩展为约 19.2 KB，明确指出第一次返工虽修了部分表述，但第 3 节仍有未经已落地来源支撑的“自主 Agent 更适合……”选型结论；文章末尾缺便于读者核对的 URL，约定的 `sources/01-agentic-primer-sources.md` 仍未建立，当前只有 `research/sources.md`。这次退回有定位和修改清单，但同源问题连续两次出现，后续需判断 draft 是否真正消费审校反馈、是否形成无效回环。

第三次 `draft` 完成后进入第三次 `review/running`。静态对照当前 Workflow 定义还发现一个规划契约冲突：Conversation Agent 对用户承诺 `sources/01-agentic-primer-sources.md` 与 `docs/method/article-workflow.md`，但 `research` 指令只要求 `research/sources.md`，`draft` 只要求文章正文，`method` 指令要求 `docs/reusable-pipeline.md`；图中没有明确负责前两个承诺路径的 Column。审校反复指出来源交付路径缺失，却只能沿 `needs_revision → draft` 路由，而 `draft` 的职责原文并未要求生成来源文件。此为 Agent 自己编排的交付契约不一致，尚不能归因于 Runtime 路由错误；需要观察 Agent 是否能监督修正。

第三次 `review` 最终通过，`method` 也完成，Task `tsk_19844140d95e4ebd9dbef313e7bc0a93` 在 DB 为 `done`；总路线为 `research → distill → draft → review → draft → review → draft → review → method → done`。实际文件包括文章、审校报告、`research/sources.md`、`research/notes.md`、`docs/reusable-pipeline.md`。独立检查确认文章无代码围栏、审校报告最终为 `pass`，报告称正文约 1250 汉字，且明确列出“最终交付前仍需解决”的来源文件路径与方法文件路径两项。Task 到 `done` 时这两个对话承诺的文件仍不存在：`sources/01-agentic-primer-sources.md` 与 `docs/method/article-workflow.md`。这是 **Workflow 图的完成条件未覆盖最初交付清单、且 Conversation Agent 的口头承诺与 Column 指令不一致**；Runtime 正确沿图到终态，但不能因此宣称用户约定的全部文件已交付。需继续通过自然语言请 Conversation Agent 自己核对和补齐，不由测试者直接写文件或手动改状态。

第 5 轮只以自然语言指出上述路径差异，要求核实并处理，不提供 Tool 名、状态转换或讨论/启动开关。Job `cjob_0ec55f88c27047a386ef27914d398a09` 为 `failed`：Agent 成功调用多次 `project.files.list/read`、`task.inspect`、`workflow.inspect`、`task.plan.contract/list` 和 `agent.result.read`，但没有调用授权、计划、Task 或文件写入工具；在相同 `task.inspect` 重复读取后触发 `ConversationProtocolStalled`。DB 中该轮 `turn_contract_json={}`、无 execution grant。没有证据证明修改 API 拒绝了请求，因为它根本没有尝试修改；这轮失败属于 Conversation Agent 在已完成 Task 后的决策/工具使用停滞，重复读取保护只负责终止浪费，未能产生可交付的修复路径。两个缺失文件仍未出现。

## 本轮问题清单与判断

1. **P0，已修复并复测：Loop 投影崩溃。** `LoopColumnProjection.writable_paths` 只接收字符串，而真实 Loop 使用 `$ref` 路径对象；第五轮旧 Project 因此在 `loop.inspect` 后直接失败。改为类型化的字符串/引用联合，49 个相关测试通过；新 Project 随后成功创建 Workflow，确认这条阻断解除。
2. **P0，已修复并复测：失败执行回执恢复崩溃。** `_column_action_ledger` 重建 `ToolResult` 时漏掉 `error.type`，使一个 MCP 错误升级为 Runtime 的 `ValidationError` 并将 Task 停在 `recovering/paused`。改为类型化错误，67 个 Runtime 测试通过；Conversation Agent 的 `task.retry` 后原 Task 从 `research` 继续到 `done`。
3. **未解决，Task Plan 工具表面契约不一致。** `task.plan.save` 暴露完整 `TaskPlan` schema，`task.plan.validate` 只暴露任意对象；第二轮曾拿到 `valid=true` 却没有保存，继而反复提交 `tasks` 为对象，最终停滞。第三轮在自然语言追问后修正并建 Task。建议对两个 Tool 统一可见的计划结构，同时保留校验工具报告不完整草稿的能力；先做协议设计与 focused 测试，避免场景特化。
4. **未解决，承诺的交付路径未进入图的完成契约。** Agent 口头承诺的 `sources/...`、`docs/method/...` 与 Column 指令的 `research/sources.md`、`docs/reusable-pipeline.md` 不一致。`review` 明知两项交付未完成仍以正文 `pass` 放行，`method` 完成自己的另一路径后图到 `done`。这不是 Runtime 擅自改路由；应由 Workflow 作者在发布前对齐 Column 产物、用户交付清单和完成条件，或在发现差异后通过 Conversation Agent 正常修订/补交。
5. **未解决，已完成 Task 的补交请求陷入只读循环。** 第五轮 Agent 取得完整事实却没有授权或执行任何变更，连续读取触发停滞保护，用户未获得补交。需定位 Conversation Agent 对“原交付缺项、原 Task 已 done”的通用后续动作选择，并确保它在无法执行时清楚报告原因；当前证据不足以称为权限拒绝。
6. **环境/效率观察。** MiniMax Provider 多次返回 `overloaded_error (529)`，非 DevWerk 生成的错误；`research` 首次尝试有 59 次真实 Chrome MCP 调用、在写来源文件前因 Provider 过载终止，存在较长研究过程未及时落地中间材料的风险。它在重试后完成，未据此加入针对文章的 Runtime 优化。可见 Chrome 会话在服务重启后只剩 `about:blank`，网页状态不等于持久研究成果。

## 验收结论与现场

本轮达成“至少五轮自然语言对话、Conversation Agent 自主建图建 Task、Workflow 派生 Column Agent 并最终到 `done`”；可见 Chrome MCP 有真实调用和来源文件，文章通过自身审校并无代码围栏。**完整用户交付未通过**：两个明确承诺的路径缺失，且第五轮补交请求失败。保留 `data/devwerk.db`、`data/logs/`、两个本轮 Project 目录、最终文件和 8000 服务供复核；不清理、不改写失败现场。
