# QA 证据判定边界修正案（2026-10-13）

> 本文保留先前判断与实施记录以便追踪；最终核心边界与代码结果以末节“再次修正：移除场景化核心合同”为准。先前关于保留 `required_behavior_columns`、固定报告协议的结论已撤回。

## 结论与范围

DevWerk 核心只负责保存工具回执、执行来源、Column 生命周期及 Workflow 有向图转换；不在核心识别 HTTP、密码、锁定窗口，也不增加全领域通用的 `invalid_test/product_failure` 裁判状态。本轮不改核心状态机、`task.feedback.record` 协议或既有验收报告格式。具体场景的测试有效性由所选 Loop 的测试列及其可执行脚本承担，审查列必须复核原始证据后才能归责。

本轮只修正外部 `software.ddd_delivery` Loop 的 `system_test` 与 `integration_review` 指令，以及对应 Loop 合同测试。该约束仅影响新发布的 Workflow；已冻结的历史 Project/Task 不回写。

## 复盘依据

Project `prj_f0c96cdd32134932aa0922c38f85cd6c` 的用户在第五轮同意约 30 秒的**测试锁定时长**，正式默认要求 10 分钟。30 秒不是允许 QA 在截止后仍断言锁定的宽限期。`evidence/s1c.txt` 的 `locked_until=2026-10-12T09:03:47.230770Z`，`evidence/s3.txt` 的请求发生在北京时间 17:03:53，晚于截止约 6 秒。原 QA 脚本没有在 S3 断言前用真实时间检查前置条件，将无效执行误报为持久化缺陷；集成审查继续误退后端。

此前“ S4 使用错误请求密码”的判断也不准确：脚本使用用户确认的 `admin/123456`。当前 `DataInitializer.java` 中预置 BCrypt 哈希与 `123456` 不匹配，实际是后端初始化缺陷；单元测试用新生成哈希和 Mock 响应，未覆盖该预置账号。S3 与 S4 的责任必须分开判定，不能因为一个场景无效就把所有失败归为测试脚本问题。

## 修改设计

1. `system_test`：要求时间敏感的测试入口在**脚本内**记录响应给出的截止时间与实际请求时间，转换成同一时区并在断言前计算前置条件。若请求已越过有效窗口，应在本列记录“本次执行无效”，重新初始化场景并完整重跑；不得把该次 HTTP 结果写成产品失败。固定等待秒数只能用于调度，不能替代实际截止时间判断。测试报告分别列出有效失败、无效执行及重跑结果。
2. `system_test`：成功登录等场景先核对脚本使用的请求凭据与冻结需求/测试夹具一致；凭据符合需求但被产品拒绝时，作为实现缺陷调查，不可因响应失败反推“请求输错”。
3. `integration_review`：收到测试列的返工请求，读取原始请求/响应和脚本中的前置条件判定。无效测试沿既有 `accepted → system_test` 前向边退回测试列；有效且可复现的实现失败才沿既有返工边退到责任开发列。反馈仍是通知/追踪，不能决定目标 Column。
4. `tests/test_loop_contract.py`：验证上述三项职责确实存在于外部 Loop。本轮不修改 DevWerk 核心 Agent 提示。该测试只验证配置合同，不声称证明模型会遵循；后续新 Project 黑盒测试才能验证行为。

## 不做的事

- 不为一个登录演示向 DevWerk 核心新增时间/HTTP/凭据判定器。
- 不把市场调研等非软件工作流强制变成命令型验收。
- 不修改历史 Project 10 的脚本或交付物；它不能代表新 Workflow 的行为。
- 不新增 Column、Agent、Mailbox 职责或路由状态。

## 验证与剩余风险

运行 Loop 合同测试及 JSON/Workflow 定义校验。通过表示新 Loop 有明确的责任约束；它仍是 Agent 指令，不是 Runtime 语义保证。若新 Project 再次出现相同误判，应先检查测试脚本是否执行了前置条件，以及审查列是否读取其回执；届时针对被证实的通用机制缺口另行设计，不预先扩大核心。

## 本轮实施与验证

- `software.ddd_delivery` Loop 更新到 `1.4.10`，只改 `system_test` 和 `integration_review` 指令，不改 Column、边、权限或 Task 合同。
- `tests/test_loop_contract.py` 增加脚本内前置条件、无效执行重跑、审查复核原始证据及正确输入不得反推错误的合同断言。
- `venv/Scripts/python.exe -m pytest tests/test_loop_contract.py -q --basetemp=data/pytest-tmp`：19 passed；Loop 被加载并通过现有定义校验。首次运行因沙盒默认临时目录不可创建而未执行测试，改用项目 `data/` 后通过。
- 尚未运行新 Project 黑盒测试；本次不能认定 P0 已清空。

## 2026-10-13 核心边界复核与追加修正

先前称 `required_behavior_columns` 和固定验收报告会强加于所有任务，这一说法不准确：两者均由 Workflow 显式选择，默认不启用。删除它们会破坏已经选择该合同的软件 Workflow，且不能解决 S3 测试时序错误。本轮不移除或重命名这两项可选能力，也不把软件规则迁入 DevWerk 核心。

真正的通用性缺口在反馈：`task.feedback.record` 只接受冻结检查或 `runtime.behavior`，定性审查（例如市场调研的来源不足）没有可用的验证路径。追加修改限以下代码：

1. `app/v1/capabilities.py::task.feedback.record` 和 `TaskFeedbackRepository.record` 允许 `checks=[]` 表示人工/Agent 复核义务；保留有冻结检查的现有合同。没有检查时，必须留下责任列、具体问题及来源 Column/Task。不能把无检查反馈当成自动通过。
2. `TaskFeedbackRepository.apply_completion_evidence` 在责任列完成修复后，将无检查反馈留在 `verifying`；新增 `task.feedback.resolve`，由后续正在执行的复核 Column 显式确认修复、留下核验说明。工具校验当前 Task、活跃 Column、修复运行先于复核运行；不能自我通过或在修复前通过。现有带检查反馈仍按冻结检查自动核验。
3. `TaskFeedbackRepository.terminal_blockers` 与 `done` 约束继续阻止未解决反馈直接交付；不改 Workflow 有向边、不用反馈改写路由、不增加 Mailbox 职责。复核列若需要返工，仍调用现有声明的 outcome。
4. 增加针对定性反馈的回归测试，并重跑现有 Worker 选定行为/验收测试，确认软件 Loop 的命令回执义务未被削弱。

这项修正让市场调研可使用定性反馈，同时保留软件 Workflow 自行选择强验证的能力。它不使 Runtime 自动判断调研结论的真伪；明确复核仍由当前 Column Agent 负责。

### 追加实施与回归

- `app/v1/repositories/task_feedback_repository.py` 允许无冻结检查的反馈，但只有 Workflow 存在独立复核列时才能记录；责任列完成后状态保持 `verifying`，后续独立列显式 `task.feedback.resolve` 才能关闭。原有带检查的自动核验路径保留。
- `app/v1/capabilities.py` 提供 `task.feedback.resolve` 工具并说明两种反馈模式；`app/v1/runtime.py` 将该合同告知相关 Worker；`app/v1/agent_tool_execution.py` 在终态预检中阻止未核验定性反馈直接完成，避免提交阶段才抛错。
- `tests/test_worker_selected_acceptance.py` 增加无命令型市场调研工作流回归：初稿、定性返工、改稿、独立复核、显式关闭反馈、最终交付；并验证没有复核列时拒绝创建不可关闭的定性反馈。
- 相关四组测试第一次运行结果为 118 passed、1 failed；失败是工具描述丢失原有 `declared forward outcome` 合同措辞。已恢复原措辞并对失败用例与新增用例重跑，3 passed。首次 Loop 合同测试另为 19 passed。尚未做新 Project 黑盒测试。

## 2026-10-13 再次修正：移除场景化核心合同

用户指出：显式启用不等于通用。此前关于保留 `required_behavior_columns` 和固定验收报告的论证错误。本节取代上文“不移除”结论。目标是让核心只执行 Workflow 声明的工具检查、记录回执并依有向图推进；由 Workflow/Loop 自己定义什么是有效交付证据。

改动边界：

1. 从 `TaskContract`、计划编译、Runtime 完成合同及反馈仓库移除 `required_behavior_columns`、`runtime.behavior` 和“成功运行一条命令即可代表行为验证”的合成回执。软件 Loop 不再声明该字段。
2. 从 `AcceptanceCheck` 和 `acceptance_execution` 移除 `evidence_kind`、`scenario_ids`、`report_path`、专有 `devwerk.acceptance-report.v1` 解析。冻结检查仍按所声明的 capability/arguments 执行并保存真实工具结果；报告格式、断言和退出码由所选检查命令及 Workflow 定义。移除生成固定报告检查的 `workflow.acceptance.runners` 工具；保留通用的 `workflow.acceptance.configure`。
3. 反馈只引用真实冻结检查，或使用独立复核列的定性反馈；不再允许引用虚构的 `runtime.behavior`。历史已冻结且依赖被删字段的 Workflow 不做静默迁移；新 Workflow 不得生成它们。本次不增加软件、小说或调研专属规则。
4. 修改既有测试以验证通用检查真实回执、定性反馈、Loop 加载及 Workflow 推进。任何软件专项证据质量由外部 Loop/脚本承担；核心不会声称 6/6 PASS。

### 实施与验证

- `app/v1/domain.py` 移除行为列、场景输入指针与固定报告字段；`app/v1/services/acceptance_execution.py` 只分发声明的 capability 并返回原始工具结果。
- `app/v1/runtime.py`、`app/v1/repositories/task_feedback_repository.py`、`app/v1/store.py` 移除 `runtime.behavior` 合成回执及终态行为列义务。`app/v1/services/completion_admission.py` 不再用命令名称猜测行为测试；保留失败回执必须有后续成功回执和显式解释的通用规则。
- `app/v1/capabilities.py` 移除固定报告 runner 快捷工具及派生义务；`workflow.acceptance.configure` 仍可配置冻结工具检查，是否成为终态义务由 Workflow 显式声明 `acceptance_obligations`。外部软件 Loop 删除核心专用字段，仍在自己的 Column 指令中要求实际测试。
- 编辑已有检查时，未显式传入新义务则保留仍能引用同名检查的旧义务，删除的检查对应义务一并移除；新增检查不会自动变成终态义务。对应 3 项定向回归通过。
- 同步修正外部软件 Loop 的验收指导资产，删去“Runtime 解析专有 JSON 报告”的过时陈述，明确由脚本退出码表达失败。Loop 合同测试再次运行 **19 passed**。
- 删除只验证已移除协议的旧测试，保留并更新通用配置、工具执行、反馈与完成协议测试；新增成功/失败命令回执的通用验收测试。相关 8 组测试 **134 passed**，补充的 Workflow 转移、Task 编译和 Agent 恢复测试 **19 passed**。
- 尝试全套 528 项测试；前 9 项通过后，下一项 Web 合同测试长期无输出，已中断。未据此宣称全套通过，也未重启或停止 DevWerk 服务。尚未进行新 Project 黑盒交付验证。

历史冻结 Workflow 若含上述删除字段，现行严格模型会拒绝重新加载；本轮不为旧测试 Project 添加隐式迁移。外部检查脚本必须用退出状态真实表达失败，Runtime 不再解析或替它推断报告内容。这是通用性边界变化，不等于自动保证任意领域的交付质量。
