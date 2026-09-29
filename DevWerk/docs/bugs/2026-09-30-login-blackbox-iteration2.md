# 登录防刷黑盒复测：迭代 2

Project `prj_dd06c3b6cfbc4a25b5dc0fc20da346d1`；目录 `data/projects/blackbox-login-iteration2-20260930-070537`。旧失败 Project `prj_87d10ac45d654f26a933fd84662760e1` 保留；本轮使用加载了分步验收配置和 software Loop 1.4.1 的 8000 服务。代码方案、上一轮根因和单测见 `2026-09-30-login-retest-planning-boundary-plan.md`。

案例不变：Vue 3/Vite、Java 17/Spring Boot、H2 文件库、HttpSession/Cookie、预置 BCrypt 测试账号，仅登录与用户名/IP 双维度防刷；滚动 5 分钟内第三次失败立即 429，锁 15 分钟，前两次 401；无缓存、注册、验证码。先至少五轮纯讨论，确认 Session/API/浏览器及后端行为测试边界，再明确授权一个跨列交付 Task。测试脚本只调用正式 Project/Conversation API，不代 Agent 写 Workflow、Task 或交付代码。

运行期每约三分钟读取一次 Job/Task/Column/Assignment/mailbox 控制面摘要；正常慢 LLM 不实时轮询。每次终态或确定性停滞后立即保存失败工具回执、源码/Loop 合同与实际实体状态；先写复盘，再改代码和测试，安全重启后另开全新 Project。只有 Task 到终态后评价交付物质量及验收预设，不以中间产物或 API accepted 代替交付。

## 讨论阶段结果与第四轮阻塞

前三轮 Job `cjob_255435f4c25c4a5a92ae01937e98a703`、`cjob_554709d290624834a85bae4b7a8cb525`、`cjob_f24c8aebf8974251a481bc4d86721061` 均成功；各轮后无 Workflow/Task，`execution_hold=true`。第四轮 `cjob_aef57c0dd3be449fb45a98dedbce4f63` 失败，Agent Run `arun_4d8cc370d9ac477fa22876d49f681efe` 的第一次 `conversation.turn.resolve` 返回 `NewQuestionMissing: lockout-count-reset needs question text`，第二次原样重试被 `ConversationProtocolStalled` 阻断。该轮没有写入新的 intent revision，也没有 Workflow/Task；失败被显式展示，不是业务执行授权。

根因：第三轮将 `lockout-count-reset`、`count-window`、`blacklist-strategy` 直接记录为已经由用户决定的 decision，并未为这些键创建 open question。第四轮 Agent 合理地想同时保存新决定、将这些同名议题标记 resolved，但 `merge_intent_updates` 把一切不存在的 question key 当作新问题，要求必须附上 question 文本。`resolved` 已决议议题与“创建一个新问题”语义不同。错误回执明确，模型仍原样重试；重复失败防护发挥作用，却使可由确定性合并规则恢复的讨论提前结束。

## 本次最小修复方案（代码前记录）

1. 仅修改 `app/v1/repositories/conversation_intent_repository.py::merge_intent_updates`：不存在于 open_questions 的 `resolved` 更新，只有在同名 `user_decided` 或 `delegated_choice` decision 已存在时才视作幂等的完成标记并跳过；不存在对应 decision 时保持 `NewQuestionMissing`。不自动创建问题，不放松开放问题、执行授权或证据校验。已存在问题的状态更新仍按原规则保留文本与来源。
2. 在 `tests/test_conversation_intent_contract.py` 覆盖同一回合 decision+resolved、前一回合已决议后 resolved、无对应 decision 仍拒绝三种情况；执行原 conversation 边界回归。
3. 检查全局无活跃 Run，重启 8000 服务；保留失败 Project 原样，使用相同五轮 case 创建第三个全新 Project。授权执行后再观察 Kanban、通知与交付终态。

## 修复验证

合并规则和针对性单测已落地；`test_conversation_intent_contract.py`、`test_conversation_contract.py`、`test_conversation_partial_effects.py` 共 65 项通过。重启前全局无排队/运行中 Conversation Job、Agent Run 或 Column Run；8000 服务已重新启动，`/v1/health` 返回 200，supervisor 与 Gateway 均为 running。下一轮必须新建 Project，不能用本失败 Project 冒充从头通过。
