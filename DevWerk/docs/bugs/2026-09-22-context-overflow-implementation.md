# 上下文溢出修复实施与回归记录

设计依据：[完整复盘与修改方案](2026-09-22-context-overflow-full-audit-and-fix-plan.md)。状态：本地修正与相关自动化回归通过；完整真实交付/返修仍未通过，外部模型续跑待授权。不修改生产 DB、冻结任务或用户项目产物。

实施顺序：上下文投影/逐轮预算/有限恢复 → Assignment 摘要恢复 → Windows 启动 → 验收及反馈 → 规划集中诊断 → 定向与全量回归、隔离验证。每一部分的实际结果在完成后补记。既有 Web 不增加界面。

原始工具回执始终保留；模型投影和摘要不参与验收真值判定。旧任务是否恢复与如何更换错误冻结合同，遵循设计第 11 节，不能随服务启动自动修改。

## 已落地的实现

### 1. 每轮请求预算与工具结果投影

- 新增 `app/v1/context_manager.py`，在每次 Provider 请求前对消息、工具 schema 和协议余量统一估算，预留模型输出窗口。超过软阈值后以完整 assistant/tool 批次压缩，连同重复写代码的旧 arguments 一起处理；不拆开工具调用及回执配对。
- 原始工具回执、assistant arguments、执行 ledger 保持完整。Provider 使用限长投影；退出码、错误、来源引用保留。`agent.result.read` 按源 Run/tool-call 分页读取原始回执，项目和 Worker 范围仍受约束，Unicode 分页按实际返回字符推进，避免跳过被裁剪的内容。
- 工具结果预算默认 4,000 估算 tokens；摘要预算默认 4,000。实际命令捕获上限仍是独立的进程保护，未改成模型窗口大小。
- 模型配置支持 `context_window`；MiniMax 的已知直连端点 `api.minimaxi.com` / `api.minimax.io` 可识别当前 M2.7 系列，其他端点/模型要求显式配置。删除 Anthropic 客户端强制输出额度至少 65,535 的限制。
- Anthropic 上下文计算包含 input + cache read + cache creation；OpenAI 的 input 已包含缓存时不重复相加。估算使用 UTF-8 大小、协议余量和实际 usage 向上校准，**不是精确 tokenizer**。
- 明确的 context overflow 单独归为 `LLM_CONTEXT_WINDOW_EXCEEDED`，不根据 2013 数字猜测。最多在请求 hash 和尺寸确实下降后再请求一次；不重跑已有工具副作用，再次失败则按 context_exhausted 暂停。

### 2. Assignment 内维护和恢复

- 新增 `v1_context_checkpoints`，保存 Assignment 范围、摘要、覆盖到的 source message ID、版本和估算校准信息。只压缩完整批次；写入前重验控制令牌和 Assignment ownership。
- 同一 Assignment 重启/恢复读取 checkpoint + 边界之后的完整回放，不再先按 80K 字符逐消息截断。其它 Assignment 的历史仍是引用，不替换当前工作合同。
- 维护摘要使用原 Agent 的模型、空工具集，不创建 Worker、Assignment 或代理交流。计入模型调用预算；失败走有界确定性回退，不递归摘要。摘要不能生成验收成功或 feedback resolved。
- 用户/Worker 输入携带持久消息边界；旧的非工具观察在快照中作为参考保留。主动运行期间和恢复后都执行同一预算检查。

### 3. Windows 命令启动

- `command_resolution.py` 在副作用前解析可执行文件/PATHEXT；原生可执行文件保留 argv，`.cmd/.bat` 使用独立适配，并继续由 Windows Job Object 管理子进程和真实退出码。
- 支持带空格的 shim 路径及带空格、`&` 的普通参数。批处理参数中的双引号、`%`、`!` 和换行会明确拒绝，要求显式选择解释器，避免静默改变参数含义；这项限制不施加到原生程序参数。
- 验收检查支持 `launch_validation=preflight/deferred`：前者在计划准入时仅验证命令解析，不执行命令；后者允许后续列生成入口。实际执行前均重新验证。

### 4. 验收真值及同轮反馈

- 软件 Loop 更新到 1.4.0。Task 输入冻结 `acceptance_scenarios`；必需行为列声明 scenario_ids、report_path、命令、purpose 和 invalidation obligations，最终列覆盖全部冻结场景。
- Runtime 执行冻结命令后验证报告为本次新写、JSON schema 版本正确、必需场景 passed 且非空断言相等、测试源码 SHA256 匹配。记录 execution_key、Task/Attempt、报告摘要和源码绑定；测试源码随后变化会使旧验证失效。
- 报告文件存在、exit code 0、curl 传输成功均不能单独满足这些约束。新增准入检查提前拒绝明确“只列目录/只检查存在”和显式跳过测试的命令；这不是对任意脚本语义的证明，仍由现有独立审查/系统测试列核对实际代码及断言对象。
- 测试允许重构，但冻结场景不能因删除失败测试而消失。合同文档作为 Loop asset 随版本冻结，所有列可直接读取，不依赖对话中的解释。
- Feedback 记录验收回执的持久观测边界。同一 Run 内修复后，只有边界之后、当前 Attempt 的新验证才可结清；旧回执不可重用。跨列返修保持原有路由和 Worker 生命周期，Mailbox 不参与模型调用或调度。

### 5. 规划诊断与无效消耗

- `task.plan.validate` 提供只读集中诊断，包括类型/引用、全部缺失验收列、场景覆盖、入口证据、依赖图和命令 preflight，不保存计划、不执行未来测试。
- `workflow.acceptance.configure` 接收当前 revision + definition_hash，只替换指定列的检查和 obligations；事务内检查基线版本，保留其余指令、图结构及旧 Task 的冻结版本。成功回执只返回 ID/hash/变更列。
- 规划类回执使用专用投影，保留完整必要 schema、参数绑定合同和引用，避免通用字符串裁剪诱发反复读取整份 Workflow。新工具 schema 仅携带实际引用的两个定义，避免重复传送整份 Workflow schema。

## 已执行验证与暴露的问题

本节为真实执行记录，最终全量及完整软件交付状态仍需后续补齐，不能把以下结果作为全面验收通过。

1. 第一批定向回归：65 passed。
2. 首次全量：386 passed、4 failed。失败是历史分页/新增只读工具契约的旧断言；修改为遍历全部有界页面和更新精确工具列表，未删除原有状态/副作用检查。
3. 新增上下文及验收回归的一批：27 passed（包含 36 次约 110KB 命令结果、旧大代码 arguments、固定输入超限、一次自救、第二次超限暂停、摘要失败、取消 fencing、重启快照、真实报告失败注入和同 visit 反馈）。后续又补充命令准入、Unicode archive 分页、摘要期间取消测试；最新结果见文末。
4. 长循环回归曾再次抓到摘要累计过大：已将摘要整体限长，并给保留的最近批次预留摘要空间，随后 36 轮测试通过。不能仅限制单个 stdout。
5. 真实模型隔离回归曾抓到已投影 Loop 回执再次投影时把 asset 字符串当作对象，导致 TypeError：已修复重复投影并新增用例。
6. 隔离项目 `prj_ba6540b9f12442b08a839da0a0d9a129` 已完成讨论、规划和 requirements 列；截至观察点实际模型调用 19 次，最大 input+cached 40,958 tokens，累计 input 84,215 / cached 386,796 / output 11,153。该项目冻结了跳过测试打包和 dir 作为行为检查，人工停止回归并补齐提前拒绝，**未算作交付成功**。

隔离脚本：`scripts/eval_software_context.py`。每次生成独立 `data/evals/software-context-*` 目录，保存 DB、源文件、回归 report.json 和 Provider usage。未修改生产 DB、未重启生产服务、未恢复或改写原项目 `prj_b3b364ba5d97483691a9fdcef6bf0c6c` 的冻结任务。

## 尚需完成的验证

- 完整真实模型 Vue/Spring 交付、独立 HTTP/浏览器验证与服务清理，再基于原产物做一次用户返修。现有失败/中止运行不计成功。
- 汇总新旧请求上下文和 token 消耗；不同任务的总消耗不能当作严格同条件节省比例。

## 续修设计（2026-09-27，先记录再实施）

已核对上一轮全量结果：405 passed，358.66 秒。隔离项目 `prj_5d6eff76b723432dbb4432cb5e4c34fc` 完成 requirements/domain_design，在 backend_development 遭遇 Provider 600 秒读取超时，处于 active/recovering。这不是 context overflow。DB 已有两个上下文 checkpoint，预算估算分别从 136,125 降至 71,898、132,295 降至 69,069。产物存在，但没有完整软件交付或返修通过证据。

本轮只处理该回归暴露的具体缺口：

1. 隔离脚本遇到未来 next_retry_at 时不能把无执行的轮询累计成 Column visits。按照重试时间等待，每次等待不超过 30 秒；只将状态/执行发生变化计为执行步，并设置独立的整体时间界限。增加指定隔离目录恢复能力，校验 DB 的 Project 与目录一致，不允许指向生产 DB；恢复时使用正常 Conversation/Runtime API，不直接改写冻结任务、状态或生成的代码。保存阶段与恢复记录，保留前次失败证据，避免重新讨论/规划浪费 tokens。
2. 已冻结的 `cmd /c "cmd /c if exist ... (echo OK)"` 仍属于文件存在性检查，不能满足行为验收。新计划准入将识别有限层数的 cmd 包装和这一明确的存在性/echo 形式，以及只到 compile 阶段的常规 Maven 调用。npm build、带自定义 Maven plugin/profile 的命令可能实际执行测试，不猜测或禁止其语义，而是在规划指导中明确要求断言与新报告。包含真正断言入口的组合命令不按简单黑名单拒绝；对任意脚本仍依赖实际报告验证和独立审查。错误需给出改成“执行断言且生成新报告的入口脚本”的具体修正提示。
3. 在精简 Loop 投影中直接保留验收入口的要求，避免 Agent 只看到 scenario/report 字段而把 build/existence 误当作行为验证。未来生成入口必须落在负责 Column 的可写目录；构建可作为附加 artifact 检查。
4. checkpoint 附加 archived_notes 后再约束整个摘要大小，并测试多段中文观察与极小摘要预算。原始消息仍完整存档，压缩摘要不作为验收证据。

先执行上述定向回归，再运行全量。真实软件测试沿用隔离数据，旧错误合同通过显式 Conversation 请求形成可执行修订/后继任务，不能手工篡改验收使其通过。Provider 不可用或交付失败须如实记录，不能以单元测试替代真实交付结果。

### 本轮已验证事实

- 定向回归 37 passed（28.83 秒）：嵌套 cmd 存在性检查、保留真正断言组合命令/自定义 profile/npm script、摘要附加中文观察后整体预算、隔离脚本等待重试不计执行步、无进展独立超时、恢复目录与 Project 一致性校验。
- `git diff --check` 通过，仅有 Git 的 LF/CRLF 提示。
- 上述真实隔离项目最终持久用量：Conversation 39 次（全部成功），input 112,846 / cached 1,054,187 / output 20,240，最大完整输入 41,583；Column 34 次（33 成功、1 Provider 超时），input 146,478 / cached 759,673 / output 30,610，最大完整输入 46,279。合计 73 次，input 259,324 / cached 1,813,860 / output 50,850。失败请求的缺失 usage 不当作已测得实际消耗。
- 真实隔离项目的两个 checkpoint 均为 same_agent_summary，分别出现在不同 scope，各 revision=1；不是同一 Assignment 连续两次压缩。这个项目只有 7 次命令回执，共 5,695 字符，不能替代大日志回归。大日志证据来自另行执行的 36 轮 / 70 次工具回归，原始回执保存在隔离测试 DB。
- 真实交付未通过的事实保留：后端实际只有编译证据，旧报告把各业务场景都写成 compile_success；Runtime 拒绝 column.complete。没有把伪行为报告算作有效交付。
- 续跑命令被自动审批拒绝，原因是向既有 MiniMax Provider 发送此隔离项目上下文/源码缺少本次明确授权。已向用户发出授权问题，等待答复；拒绝后未换路径或绕过执行。隔离 DB 和生产 DB 均未因这次被拒操作发生修改。本地回归继续。

### 代码复核新增的最小修正

本地复现发现 Windows `Path('.\\local-shim.cmd')` 会归一化掉 `.`，现有 launcher 错将其交给进程当前目录的 PATH 查找，忽略项目 cwd。修正仅让原始 argv 的 `./`、`.\\` 明确相对路径在指定 cwd 解析；保留裸命令的 PATH/PATHEXT 行为。增加真实 shim 运行、原生程序中文/引号 argv 和缺少可执行文件的回归，不修改项目代码或 Web。

继续核对规划消耗发现，35 轮启动规划中有 13 次工具拒绝，不能宣称规划总消耗已降低。其中 4 次 task.create 使用了未保存、模型虚构的 `tpln_*` ID，却收到 `Work belongs to a different Requirement`。源码 `_bind_requirement` 把“没有记录”和“已存在但属于另一 Requirement”合并报错，诱导 Agent 反复创建/切换 Requirement、重新 resolve，而真正修复应是 task.plan.save 后使用返回的 `tplan_*` ID。

本轮补充最小诊断修正：分开不存在与真实范围冲突；未保存的 TaskPlan 明确提示使用 task.plan.save 回执，task.plan.validate 只诊断不持久化。维持既有授权/范围校验和真实跨 Requirement 拒绝，不替模型猜 ID、不自动保存/创建。回归须证明“失败诊断不改变 Requirement/TaskPlan/Task → 正常保存得到真实 ID → 创建成功”的完整路径。该修正是否减少真实模型轮数仍待外部回归验证。

### 本轮最终本地回归结果

| 执行点 | 范围 | 实际结果 |
| --- | --- | --- |
| 嵌套验收命令、摘要整体预算、隔离恢复脚本修正后 | `pytest -q` 全量 | **412 passed，437.05 秒** |
| Windows 显式相对入口修正前 | 新增 launcher 用例 | 2 failed / 2 passed；两个失败分别为 `./` 与 `.\\`，真实复现缺陷 |
| Windows 入口修正后 | command_resolution、context_lifecycle、context_acceptance_regression、p0_runtime_regressions、capability_contract、repair_notification_contract | **116 passed，142.42 秒** |
| 缺失 TaskPlan 诊断修正后 | task_entry_admission、conversation_intent_contract、scope_extension、persistent_agents | **57 passed，57.90 秒** |

412 项全量在最后两处最小续修之前运行；之后分别执行以上受影响范围回归，不将各次测试数相加宣称新的全量数量。没有 skip/xfail 掩盖本机失败；Windows 专项在本机实际执行。最终 `git diff --check` 通过。

本轮修改未增加 Web 内容、调试展示或开发纪律类产品配置；AGENTS.md 无修改。生产服务未重启，原阻塞生产 Task 未恢复；未提交或推送这批实施代码。下一步为获准后执行已有隔离续跑，核查真实断言/报告、独立浏览器与 HTTP 行为、进程清理及用户返修；仍须保留失败事实，不能把本地回归当作完整项目交付验收。
