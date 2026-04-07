# DevWerk 开发日志

> 本文件记录每个开发任务的进展、决策、待办。格式：任务级标题 + 日期 + 进展 + 决策记录 + 文件清单。
> 当 session 无法恢复时，开发者阅读此文件即可接续进度。

---

## 任务：环境配置 + Plan-Execute 两阶段架构

**日期**：2026-04-05
**session ID**：`agent:agent-architecture:main`（当日）
**状态**：✅ 已完成，等待用户合并到仓库
**用户**：zanghongtu（通过 openclaw-control-ui）
**目标**：让 DevWerk 变成"人类的手"——Plan 阶段诚实读文件研究，用户确认后再执行

---

## 背景

DevWerk 是一个 IDE 插件 + FastAPI backend 的 AI 编程辅助工具。用户是唯一维护者，希望：
1. Backend 支持通过 `.env` 配置 dev/test/prod 环境，LLM API key 不进 commit 的文件
2. 加入 Plan 阶段——AI 先告诉人要改哪些文件，人确认后再写盘
3. Snapshot 机制（`.devwerk/`）已完整实现，不需要改

---

## 决策记录

### 决策 1：Plan 阶段 LLM 是否应有工具调用权限？

- **选项 A（采用）**：有工具权限。Plan 阶段 LLM 可以读文件、研究、产出 plan。这是最诚实的方案，但成本较高。
- **选项 B**：无工具权限。Plan 阶段只靠前端传来的 tree_preview 盲猜。便宜但不准确。
- **选项 C**：混合。Plan 无工具，Execute 有工具。风险是 Execute 阶段发现内容不对无法回头。
- **结论**：采用选项 A。诚实优先，成本后续优化。

### 决策 2：Plan 粒度

- **选项**：文件级 vs 方法级
- **结论**：文件级。方法级过于严苛，文件级粒度已能有效避免冲突（单模块内不跨文件）。

### 决策 3：Confidence 使用

- **结论**：一期先不做强制确认，每步都让用户点确认。Confidence 字段保留但暂不用于过滤。

### 决策 4：Snapshot 存储位置

- **结论**：前端在 `applyResponse()` 前调用 `snapshotTo("before")`，后端不需要额外存储。`.devwerk/` 目录已完整实现。

---

## 文件清单

### 改动文件（需要用户合并到仓库）

#### Backend

| 文件 | 操作 | 说明 |
|---|---|---|
| `backend/app/core/config.py` | 重写 | Pydantic BaseSettings，支持 APP_ENV 三环境，启动时验证 API key |
| `backend/app/main.py` | 重写 | lifespan log，脱敏打印，支持直接 `python app/main.py` |
| `backend/app/routes/ide.py` | 重写 | 新增 `/plan` 和 `/execute` endpoint，原 `/chat` 不变 |
| `backend/app/services/llm_factory.py` | 小改 | 接受 config dict 解耦 |
| `backend/app/services/openai_client.py` | 小改 | 接受 config dict |
| `backend/app/services/ollama_client.py` | 小改 | 新增 enable_schema 配置 |
| `backend/app/models/plan.py` | **新增** | PlanFile / PlanResponse / ExecuteRequest |
| `backend/app/services/planner.py` | **新增** | Planner 类，工具调用 + 提取 plan |
| `backend/requirements.txt` | 小改 | 新增 pydantic-settings>=2.7.0 |
| `backend/.env.example` | 重写 | 完整环境变量说明 |
| `backend/.gitignore` | 修复 | 正确忽略 .env/.env.local/.env.production |
| `backend/startup.bat` | 修复 | PowerShell 语法 → 正确 batch 语法 |
| `backend/pytest.ini` | **新增** | 强制 APP_ENV=test |
| `CONTRIBUTING.md` | **新增** | 环境配置说明 |

#### Frontend（IDEA 插件）

| 文件 | 操作 | 说明 |
|---|---|---|
| `idea-plugin/src/main/kotlin/.../codeEditor/ChatTypes.kt` | 重写 | 新增 PlanFile/PlanResponse/ExecuteRequest 类型 |
| `idea-plugin/src/main/kotlin/.../codeEditor/HttpAiClient.kt` | 重写 | 新增 sendPlan() / sendExecute() |
| `idea-plugin/src/main/kotlin/.../client/AiClientFactory.kt` | 小改 | Tech-Zukunft provider 改用 HttpAiClient |
| `idea-plugin/src/main/kotlin/.../DevWerkFsToolWindowPanel.kt` | 重写 | 完整状态机 + Plan UI |

### 未改动但关键的文件（快照逻辑）

| 文件 | 说明 |
|---|---|
| `idea-plugin/src/main/kotlin/.../DevwerkOperationRunner.kt` | 未改动，snapshotTo("before") → applyResponse() → snapshotTo("after") 顺序正确 |
| `idea-plugin/src/main/kotlin/.../codeEditor/PatchApplier.kt` | 未改动 |
| `idea-plugin/src/main/kotlin/.../codeEditor/FsScaffolder.kt` | 未改动 |

---

## 两阶段流程说明

```
用户输入需求
    ↓
前端 → POST /v1/ide/plan
    ↓  LLM 用工具读文件、研究
PlanResponse { files: [{path, nature, description, confidence}], summary, warnings }
    ↓
前端展示：每个文件一行 + nature 图标 + description + confidence
    ↓
用户点 Execute（全部勾选）或取消
    ↓
前端 → POST /v1/ide/execute { messages, approved_paths: [...] }
    ↓
Backend: beginOperation() → snapshotTo("before") → applyResponse() → snapshotTo("after")
    ↓
前端展示执行结果
```

---

## 已知限制

1. **`/execute` 的 ops 重新生成**：前端传 `approved_paths`，后端根据这些路径重新生成 ops。`approved_ops` 字段目前为空。
2. **`patch_ops` 过滤保守**：包含任何未批准路径的 diff 整个跳过，不会拆解 diff。
3. **session 持久化**：当前 session 完成后需要手动把进展写 MEMORY.md；新 session 启动时读 MEMORY.md 恢复。

---

## 后续待办

- [ ] 用户将本地的 `C:\Users\hongtu\.openclaw\workspace-agent-architecture\DevWerk\` 内容合并到 GitHub 仓库
- [ ] 确认 IDEA 插件 build 通过（需要验证 Kotlin 编译）
- [ ] 测试 Plan-Execute 两阶段：先跑 Ollama，确认 backend 启动正常
- [ ] 考虑是否需要 `approved_ops` 字段（如果后端 re-generate 不满意，需要前端直接带 ops）
- [ ] session 持久化自动写入机制（HEARTBEAT.md 规则已建立）

---

## 环境准备（开发者接续时需要）

```bash
# clone 仓库
git clone https://github.com/zanghongtu2006/DevWerk.git
cd DevWerk/backend

# 创建虚拟环境
python -m venv .venv
source .venv/Scripts/activate   # Windows: .venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 配置环境
cp .env.example .env
# 编辑 .env，填入真实 API key 作为环境变量
# Linux/macOS: export OPENAI_API_KEY=sk-...
# Windows PS:  $env:OPENAI_API_KEY="sk-..."

# 运行
uvicorn app.main:app --reload
# 或 Windows:  startup.bat
```
