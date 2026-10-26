# DevWerk 前端代码地图

> 依据：2026-10-24 直接核对 `app/web/`、`app/main.py`、`app/v1/api/routes.py` 和现有测试源码。本图描述现状，非重构提案。

## 入口与依赖方向

`app/main.py:create_app()` 把同一份 `app/web/templates/dashboard.html` 挂到 `/`、`/workbench`、`/dashboard`、`/kanban`、`/tasks`、`/events`、`/settings`，静态资源位于 `/web/static`。HTML 加载 `dashboard.css` 与 ES module `dashboard.js`。`dashboard.js` 依赖 `core/*`、`ui/*` 和 `pages/*`；各页面只渲染传入的状态，不直接发请求。请求汇集在 `core/api.js`，落到 `/v1` 下的 FastAPI 路由。

```text
HTML / CSS
    → dashboard.js（导航、事件、请求编排、状态更新）
        → core/api.js → /v1 HTTP / SSE
        → core/state.js（浏览器内状态）
        → pages/*.js → ui/components.js、core/format.js
```

## 模块职责

| 位置 | 当前职责 |
| --- | --- |
| `templates/dashboard.html` | 全局导航、Project rail、主页面容器、Project 创建对话框与资源入口。 |
| `static/dashboard.js` | `boot`、路由/Project/Task 选择、Conversation 提交、设置保存、SSE 订阅和增量刷新；是前端编排中心。 |
| `static/core/api.js` | `request` 封装超时、HTTP/JSON 错误；`loadProjectBundle` 汇集 Project、对话、状态、projection、事件；`loadTaskDetail`、`openProjectStream`。 |
| `static/core/state.js` | 单个浏览器页面的可变 `state`；路径映射与 Project URL 更新。 |
| `static/core/format.js` | HTML 转义、时间、截断与 Runtime status catalog 的展示映射。 |
| `static/pages/overview.js` | 总览。 |
| `static/pages/projects.js` | Conversation 消息、Project 面板、输入状态。 |
| `static/pages/kanban.js` | Workflow Column 定义和 Task 卡片。 |
| `static/pages/tasks.js` | Task、Column run、Attempt、Agent run、Artifact 和故障详情。 |
| `static/pages/events.js` | Project 事件视图。 |
| `static/pages/settings.js` | 全局设置表单。 |
| `static/ui/components.js` | Badge、空态、骨架、Workflow pipeline、卡片和事件行等复用渲染。 |
| `static/ui/shell.js` | Project rail、顶栏连接/忙碌状态、对话框、toast。 |
| `static/dashboard.css`、`static/styles/*` | 依次导入设计 token、基础、布局、组件、页面样式。 |

## 主要调用链与状态所有权

- 初次打开：`dashboard.js:boot` 获取 `/health`、`/projects`、`/runtime-statuses`、`/settings`，选择 URL 或首个 Project，再通过 `refreshBundle → core/api.js:loadProjectBundle` 读取对话和 Kanban projection；`renderPage` 调用相应 `pages/*`。
- 发消息：`sendConversation` 将 `{message, mode:"auto", user_action:null}` 提交到 `/projects/{id}/conversation`，之后刷新对话及 Conversation 状态。真正的 Conversation job、Agent run 与 Workflow 状态由后端保存；浏览器的 `pendingMessage` 仅是提交期间的本地显示状态。
- 看板更新：`openProjectStream` 订阅 `/projects/{id}/stream`；`queueProjectEvent/flushProjectEvents` 按事件类型更新事件列表、projection、对话状态。切换 Project 时关闭旧流并重订阅。
- 查看 Task：`selectTask → loadTaskDetail` 读取详情与事件；`selectAgentRun` 另读 Agent run 详情。浏览器仅保存当前选中 ID 和显示快照。
- 设置：`saveGlobalSettings` 从表单写 `/settings`；若响应表示已安排重启，前端等待健康检查再重载。配置的持久化由后端 `app/core/global_settings.py` 负责。

浏览器状态集中在 `core/state.js:state`，由 `dashboard.js` 修改；数据库中的 Project、Conversation、Workflow revision、Task、run、event 是后端权威状态。前端 `board` 是 `/projection` 的快照，SSE 是刷新信号，不能把事件列表或局部 UI 状态当成业务状态来源。

## 协议与测试定位

- 前端 API 路径与载荷：`static/core/api.js`、`static/dashboard.js`；对应路由及 `response_model`：`app/v1/api/routes.py`。
- 响应类型：`app/v1/protocols/transport/api_response_contracts.py`、`app/v1/protocols/storage/store_receipt_contracts.py`；状态枚举和 catalog：`app/v1/execution/states.py`。
- Web/API 合同测试：`tests/unit/api/test_api_web_contract.py`；全局设置：`tests/unit/config/test_global_settings_contract.py`。在 `tests/unit/` 清单中未见独立 JS 单元测试；改页面时需要核对渲染与 HTTP/SSE 对接。

## 改动入口与高风险边界

| 要改什么 | 先看哪里 | 同时核对 |
| --- | --- | --- |
| 页面布局/文案 | `pages/<页面>.js`、`styles/pages.css` | 共用 `ui/components.js`、HTML 容器 ID。 |
| 共用状态展示 | `core/format.js`、`ui/components.js` | `/runtime-statuses` 的后端 catalog。 |
| Conversation 交互 | `dashboard.js:sendConversation/refreshConversation*`、`pages/projects.js` | `/conversation` 异步 job、消息顺序、失败时草稿恢复。 |
| Kanban/Task 详情 | `pages/kanban.js`、`pages/tasks.js`、`core/api.js` | `/projection` 与详情响应的区别；SSE 触发刷新。 |
| 新 HTTP 操作 | `core/api.js`、`dashboard.js`、`api/routes.py` | 后端输入/输出模型及边界测试。 |
| 全局设置 | `pages/settings.js`、`dashboard.js:saveGlobalSettings` | 后端 schema、持久化、重启响应。 |

高风险：`dashboard.js` 同时处理异步请求、Project 切换、SSE 和重渲染；改动须防止旧 Project 请求覆盖新 Project（现有 `activeBundleRequest` 与 `projectId` 核对）。页面渲染 HTML 时沿用 `escapeHtml`；不要把 Agent 原文作为未转义 HTML。不要将前端局部快照提升成 Workflow/Task 状态权威来源。
