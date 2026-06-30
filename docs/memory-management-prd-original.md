# AI Agent Memory & Context Orchestration PRD + 技术设计文档

> 版本：v0.1 Draft  
> 日期：2026-06-30  
> 适用系统：Kanban + Workflow + Sub-agent 架构的 AI Agent 平台  
> 文档目标：用于团队沟通产品范围、技术方案、数据模型、执行链路和 MVP 落地路径。

---

## 0. 文档摘要

当前系统已经具备 `Project -> Task -> Workflow -> Sub-agent` 的基本执行结构，并已经有 `project memory`、`task memory`、`task session`、`project source map` 和 `project code summary` 等基础能力。

但如果系统要稳定支持复杂工程任务，现有 memory 设计还不够。核心问题不是“要不要记忆”，而是：

```text
1. 不同层级的记忆如何划分？
2. Sub-agent 启动时应该加载哪些 project/task/workflow 记忆？
3. Coding task 如何利用 source map、代码摘要和 task 级代码上下文？
4. Task 执行过程中如何持续沉淀 summary、decision、handoff？
5. Task 结束后，哪些内容可以晋升为 project memory？
6. 如何保证整个过程可恢复、可追踪、可调试？
```

本方案将 memory 系统定义为：

```text
Memory System = 分层记忆管理 + Context Compiler + Memory Writer + Promotion Policy + Debug Trace
```

其中：

```text
Project Memory 负责长期项目认知；
Task Memory 负责当前任务执行认知；
Task Session 负责原始会话材料；
Workflow Memory 负责 sub-agent 编排状态；
Run Memory 负责单次 agent 运行状态；
Context Compiler 负责按角色加载上下文；
Memory Writer 负责把 sub-agent 输出结构化写回；
Promotion Policy 负责把稳定任务结论晋升到项目级。
```

MVP 优先实现：

```text
1. Task Summary 体系
2. Task Code Context
3. Context Compiler
4. Sub-agent Writeback Contract
5. Project Memory Promotion Candidate
```

---

# Part A：PRD

---

## 1. 背景

当前 AI Agent 系统的核心执行模型是：

```text
Project
  └── Kanban
        └── Task
              └── Workflow
                    └── Sub-agent Run
```

系统希望通过 Kanban 管理 project 下的任务，通过 workflow 调度多个 sub-agent 协作完成任务，例如：

```text
Planner Agent
Analyzer Agent
Implementer Agent
Reviewer Agent
Tester Agent
Documenter Agent
```

在 coding task 场景中，project 级别已经具备：

```text
- Project memory
- Project source map
- Project code summary
```

Task 级别已经具备：

```text
- Task memory
- Task session
```

但当前设计仍然存在几个明显缺口：

```text
- Task 没有稳定的 task-level summary
- Coding task 没有 task-level code context
- Workflow 调度状态和 memory 没有明确边界
- Sub-agent 启动时缺少统一 context loading 策略
- Sub-agent 执行结束后缺少结构化 writeback
- Task 结果无法稳定沉淀到 project memory
- 出错时难以回溯：某个 agent 当时到底看到了什么上下文？
```

因此，需要将 memory 从“聊天历史 + 项目摘要”升级为“上下文分发和任务状态系统”。

---

## 2. 问题陈述

### 2.1 用户侧问题

用户在使用 AI Agent 执行复杂工程任务时，经常遇到：

```text
- Agent 做到一半忘记任务目标
- Sub-agent 之间重复分析
- 前一个 agent 的结论没有传递给后一个 agent
- Agent 修改代码时不知道哪些文件相关
- Agent 过度依赖聊天历史，导致上下文污染
- Task 被中断后难以恢复
- Project 级规则、task 级约束和当前用户要求发生冲突时，没有明确优先级
- 一个 task 里形成的稳定项目认知，没有沉淀到 project memory
```

### 2.2 系统侧问题

当前 memory 设计偏粗：

```text
project memory
 task memory
 task session
```

这种设计缺少以下关键能力：

```text
- 任务执行摘要能力
- 代码任务战场地图能力
- Workflow 状态记忆能力
- Sub-agent 上下文包编译能力
- Sub-agent 结构化写回能力
- Project memory 晋升审核能力
- Context pack 调试追踪能力
```

---

## 3. 产品目标

### 3.1 核心目标

建立一套适用于 `Kanban + Workflow + Sub-agent` 架构的 memory 与 context orchestration 体系，使 Agent 能够：

```text
1. 识别并加载不同层级的记忆
2. 根据 sub-agent 角色生成不同 context pack
3. 在 task 级别持续维护 summary、decision、code context 和 handoff
4. 在 coding task 中使用 project source map 和 code summary 定位代码
5. 在 sub-agent 执行后结构化写回 task memory
6. 在 task 结束后将稳定结论晋升为 project memory candidate
7. 支持 task 恢复、debug 和审计
```

### 3.2 成功标准

系统上线后，应满足：

```text
- 一个 task 中多个 sub-agent 能共享任务进展，而不需要重复分析
- Coding task 启动后能自动形成 task code context
- Implementer Agent 不需要读取完整 session，也能理解当前任务边界
- Reviewer Agent 能基于 task constraints、changed files、patch summary 做审查
- Task 中断后，可以从 task memory 恢复执行
- 每次 sub-agent run 都能追踪其输入 context pack
- Task 完成后能生成 final summary 和 project promotion candidates
```

---

## 4. 非目标

本阶段不解决以下问题：

```text
- 不做完整自动化代码修复闭环
- 不做跨项目通用经验库的复杂学习系统
- 不做大规模知识图谱
- 不做完整权限系统重构
- 不做复杂向量数据库选型优化
- 不做 UI 视觉最终稿，只定义需要展示的信息结构
- 不做全自动 project memory 写入，project memory 晋升需要规则审核或人工确认
```

---

## 5. 术语定义

| 术语 | 含义 |
|---|---|
| Project | 一个代码项目或业务项目，Kanban 的上级容器 |
| Task | Kanban 中的任务卡片，承载一个明确目标 |
| Workflow | 某个 task 的执行流程，负责调度 sub-agent |
| Sub-agent | 执行 workflow 某一步的专职 agent，例如 planner、analyzer、implementer |
| Memory | 系统保存的结构化上下文，不等同于聊天记录 |
| Task Session | 某个 task 下的原始消息、回复、工具调用和反馈日志 |
| Task Summary | Task 级别的可执行摘要，包括目标、约束、进展、决策、代码上下文等 |
| Source Map | Project 级代码导航地图，用于定位模块和文件 |
| Code Summary | Project 级代码摘要，按 repo/module/file 粒度保存 |
| Task Code Context | 当前 coding task 相关代码上下文，说明相关文件、原因、当前行为和修改方向 |
| Context Pack | Sub-agent 启动时由 Context Compiler 生成的上下文包 |
| Writeback | Sub-agent 执行完成后写回 task/project/workflow memory 的结构化输出 |
| Promotion Candidate | Task 结束后可以晋升为 project memory 的候选内容 |

---

## 6. 用户角色

### 6.1 项目使用者

通常是开发者、项目负责人或任务创建者。

关注点：

```text
- Agent 是否理解任务
- Agent 是否遵守边界
- Agent 是否能持续完成任务
- 结果是否可审查
```

### 6.2 Workflow Orchestrator

系统内部调度器。

关注点：

```text
- 当前 task 处于哪个 stage
- 应该调用哪个 sub-agent
- 当前 sub-agent 需要哪些上下文
- 上一个 sub-agent 的输出如何传给下一个
```

### 6.3 Sub-agent

具体执行者。

关注点：

```text
- 当前角色是什么
- 当前任务目标是什么
- 当前 task 边界是什么
- 可用项目规则是什么
- 需要读取哪些代码和摘要
- 执行后应该写回什么
```

### 6.4 Reviewer / Maintainer

人类审查者或系统 reviewer。

关注点：

```text
- Agent 为什么这样做
- 它当时看到了哪些 memory
- 哪些结论被写入 task memory
- 哪些内容准备晋升 project memory
```

---

## 7. 核心用户故事

### 7.1 创建普通设计任务

```text
作为用户，
我希望创建一个架构设计 task，
系统能够保存 task brief、constraints 和 task plan，
这样后续多个 sub-agent 可以围绕同一个任务目标工作。
```

验收标准：

```text
- 创建 task 后生成 task_brief
- 用户明确限制被写入 task_constraints
- Planner Agent 执行后写入 task_plan
- Task 页面可展示 task brief、constraints、plan
```

---

### 7.2 创建 coding task

```text
作为用户，
我希望创建一个 coding task，
系统能根据 project source map 和 code summary 找到相关模块和文件，
并生成 task_code_context，
这样 implementer 不需要重新扫描整个项目。
```

验收标准：

```text
- Analyzer Agent 能加载 project source map
- Analyzer Agent 能输出 related_modules、related_files、files_to_avoid
- Task memory 中保存 task_code_context
- Implementer Agent 启动时默认加载 task_code_context
```

---

### 7.3 Sub-agent 之间交接

```text
作为 workflow orchestrator，
我希望每个 sub-agent 执行完成后写出 handoff summary，
这样下一个 sub-agent 可以直接接着做，而不是重新理解全部上下文。
```

验收标准：

```text
- 每次 sub-agent run 结束后生成 writeback
- writeback 可包含 handoff_summary
- 下一个 sub-agent 的 context pack 包含最近相关 handoff
- Task 页面可查看 handoff history
```

---

### 7.4 Task 中断与恢复

```text
作为用户，
我希望 task 执行中断后可以恢复，
系统能从 task memory 和 workflow state 继续执行，
而不是从 session 原始日志重新推理。
```

验收标准：

```text
- Task memory 保存 task_progress
- Workflow memory 保存 current_stage
- 重新启动 workflow 时能恢复 current_stage
- Context Compiler 能根据当前 stage 生成新的 context pack
```

---

### 7.5 Project memory 晋升

```text
作为项目维护者，
我希望 task 中形成的稳定项目结论可以被提出为 project memory candidate，
经过审核后写入 project memory，
这样后续 task 可以复用这些认知。
```

验收标准：

```text
- Task 完成后生成 promotion_candidates
- Candidate 标注 type、content、reason、confidence
- 默认不直接写入 project memory
- 被确认后写入对应 project memory 类型，例如 project_rule、source_map、code_summary
```

---

## 8. 产品范围

### 8.1 本期必须实现

```text
1. Memory 分层模型
2. Task Summary 体系
3. Coding task 的 task_code_context
4. Context Compiler
5. Sub-agent Writeback Contract
6. Context Pack 保存与调试
7. Project Memory Promotion Candidate
```

### 8.2 本期建议实现

```text
1. Task 页面展示 task memory 面板
2. Context pack debug 页面
3. Project memory candidate 审核入口
4. Source map 检索接入 analyzer
5. 最近 handoff summary 注入策略
```

### 8.3 后续版本考虑

```text
1. 跨 task 经验检索
2. 长期 agent experience memory
3. 自动项目规则冲突检测
4. 代码变更后自动更新 source map/code summary
5. 多用户协作权限
6. 向量检索和全文检索混合排序
```

---

## 9. 功能需求

### FR-001：支持多层 memory scope

系统必须支持以下 memory scope：

```text
workspace
project
workflow
task
session
run
```

每条 memory item 必须有明确 scope，不允许写入无 scope 的全局 memory。

---

### FR-002：支持 Project Memory

Project Memory 至少包含：

```text
project_profile
project_rules
architecture_summary
source_map
code_summary
dependency_map
test_strategy
known_issues
historical_decisions
```

MVP 可先实现：

```text
project_profile
project_rules
architecture_summary
source_map
code_summary
test_strategy
```

---

### FR-003：支持 Task Memory

Task Memory 至少包含：

```text
task_brief
task_constraints
task_plan
task_progress
task_analysis_summary
task_code_context
task_decisions
task_handoff_summary
task_test_state
task_final_summary
promotion_candidates
```

MVP 必须支持：

```text
task_brief
task_constraints
task_plan
task_analysis_summary
task_code_context
task_decisions
task_handoff_summary
task_final_summary
promotion_candidates
```

---

### FR-004：Task Session 作为原始日志保存

Task Session 应保存：

```text
用户消息
Agent 回复
工具调用
用户确认
用户反馈
错误日志
```

但 sub-agent 启动时不得默认加载完整 task session。

系统应优先加载：

```text
task_session_summary
recent_key_messages
explicit_user_constraints
```

---

### FR-005：支持 Workflow Memory

Workflow Memory 应保存：

```text
workflow_definition
workflow_state
current_stage
stage_outputs
agent_assignments
blocking_issues
transition_conditions
```

用于恢复 workflow 和生成 sub-agent context pack。

---

### FR-006：支持 Run Memory

每次 sub-agent run 应保存：

```text
run_id
agent_role
stage
input_context_pack_id
local_plan
tool_results
observations
output
writeback_payload
status
```

Run Memory 默认不直接进入后续 prompt，必须经过 writeback 沉淀到 task memory。

---

### FR-007：支持 Context Compiler

系统必须提供 Context Compiler，根据以下参数生成 context pack：

```text
project_id
task_id
workflow_id
agent_role
stage
token_budget
```

Context Compiler 需要支持三种加载策略：

```text
Always Load
Retrieve Load
On-demand Load
```

---

### FR-008：支持不同 sub-agent 的差异化加载

不同 agent role 应加载不同 memory：

```text
Planner：project profile/rules + task brief/constraints + session summary
Analyzer：source map + code summaries + task plan + constraints
Implementer：task code context + relevant files + decisions + handoff
Reviewer：patch summary + changed files + constraints + test state
Tester：acceptance criteria + changed files + test strategy
Documenter：task decisions + implementation summary + test results
```

---

### FR-009：支持 Coding Task Context 生成

Coding task 必须支持：

```text
1. 根据 task goal 检索 source map
2. 找到 candidate modules/files
3. 读取 project code summaries
4. 必要时读取真实文件
5. 生成 task_code_context
```

Task Code Context 结构必须包含：

```text
related_modules
related_files
files_to_change
files_to_avoid
current_behavior
possible_change
risk_notes
```

---

### FR-010：支持 Sub-agent Writeback Contract

每个 sub-agent 执行完成后必须输出结构化 writeback。

Writeback 至少支持：

```text
task_progress_update
analysis_summary
code_context_update
decisions
handoff_summary
patch_summary
test_state
final_summary
promotion_candidates
```

---

### FR-011：支持 Project Memory Promotion Candidate

Task 完成后，系统应支持从 task memory 中提取 project memory candidate。

允许晋升的类型：

```text
project_rule
architecture_summary
source_map
code_summary
test_strategy
known_issue
api_contract
dependency_map
```

默认禁止自动晋升以下内容：

```text
一次性任务目标
临时用户偏好
某次 run 的工具报错
未经确认的猜测
只对当前 task 有效的文件列表
```

---

### FR-012：支持 Context Pack 调试

系统必须保存每次 sub-agent 启动时的 context pack。

用于回答：

```text
这个 agent 当时看到了什么？
加载了哪些 memory？
没有加载哪些 memory？
是否因为上下文缺失导致错误？
是否加载了过期 memory？
```

---

## 10. 非功能需求

### NFR-001：可恢复性

Task 和 workflow 必须可恢复。中断后应能基于：

```text
task_progress
workflow_state
task_summary
latest_handoff
```

继续执行。

---

### NFR-002：可调试性

所有 sub-agent run 必须可追踪：

```text
input context pack
agent role
stage
output
writeback
memory updates
```

---

### NFR-003：可控上下文长度

Context Compiler 必须支持 token budget。

要求：

```text
- Always Load 内容必须短小稳定
- Retrieve Load 按相关性排序
- On-demand 内容不得默认注入
- 完整文件、完整 session、完整日志只能通过工具按需读取
```

---

### NFR-004：一致性

当 memory 冲突时，优先级为：

```text
1. System / Safety Rules
2. User Current Message
3. Task Constraints
4. Project Rules
5. Workflow Stage Instruction
6. Task Decisions
7. Task Analysis Summary
8. Task Code Context
9. Project Architecture / Source Map / Code Summary
10. Session Summary
11. Historical Experience
```

特别规则：

```text
当前用户要求 > 历史记忆
Task 级约束 > Project 级默认规则
真实文件内容 > Code Summary
```

---

### NFR-005：安全与权限

系统不得让 sub-agent 自由读写所有 memory。

要求：

```text
- Sub-agent 只能读取 Context Compiler 提供的 context pack
- Sub-agent 可以提出 writeback
- Project memory 写入必须经过 Memory Writer 和 promotion policy
- 敏感信息不得自动晋升到 project memory
```

---

## 11. MVP 里程碑

### Milestone 1：Task Summary 基础能力

交付：

```text
- task_brief
- task_constraints
- task_plan
- task_analysis_summary
- task_decisions
- task_handoff_summary
- task_final_summary
```

验收：

```text
- 创建 task 后能生成 brief/constraints
- Planner 后能写入 plan
- Analyzer 后能写入 analysis summary
- Documenter 后能写入 final summary
```

---

### Milestone 2：Coding Task Context

交付：

```text
- 接入 project source map
- 接入 project code summaries
- 生成 task_code_context
- Implementer 默认加载 task_code_context
```

验收：

```text
- Coding task 能列出 related modules/files
- 每个 related file 有 reason/current_behavior/possible_change
- Implementer 不需要读完整 session 也能理解修改范围
```

---

### Milestone 3：Context Compiler

交付：

```text
- Context pack 生成服务
- 按 agent role 加载 memory
- context pack 持久化
- token budget 基础裁剪
```

验收：

```text
- Planner/Analyzer/Implementer/Reviewer 加载内容不同
- 每次 sub-agent run 可以查看 input context pack
```

---

### Milestone 4：Writeback Contract

交付：

```text
- Sub-agent 结构化输出协议
- Memory Writer
- Task memory 更新逻辑
- Handoff summary 传递
```

验收：

```text
- 每个 sub-agent run 结束后有 writeback payload
- writeback 能更新 task memory
- 下一个 sub-agent 能读取上一个 handoff
```

---

### Milestone 5：Promotion Candidate

交付：

```text
- Project memory candidate 生成
- Candidate 审核状态
- 支持写入 project memory
```

验收：

```text
- Task done 后能生成 promotion candidates
- Candidate 不会默认直接写入 project memory
- 人工确认后写入对应 project memory 类型
```

---

# Part B：技术设计

---

## 12. 总体架构

推荐将 memory 系统拆成 5 个核心服务：

```text
Memory Repository
Context Compiler
Memory Writer
Promotion Manager
Context Debugger
```

整体链路：

```text
User / Kanban Task
        ↓
Workflow Orchestrator
        ↓
Context Compiler
        ↓
Context Pack
        ↓
Sub-agent Run
        ↓
Writeback Contract
        ↓
Memory Writer
        ↓
Task Memory / Workflow Memory / Run Memory
        ↓
Promotion Manager
        ↓
Project Memory Candidate / Project Memory
```

---

## 13. Memory 分层模型

### 13.1 Scope 层级

```text
workspace
project
workflow
task
session
run
```

### 13.2 Scope 职责

| Scope | 职责 | 是否默认注入 |
|---|---|---|
| workspace | 用户/团队长期偏好 | 少量注入 |
| project | 项目长期知识 | 按需注入 |
| workflow | 当前 task 的流程状态 | 当前 workflow 注入 |
| task | 当前 task 的执行认知 | 默认注入核心内容 |
| session | 原始消息日志 | 只注入摘要和关键消息 |
| run | 单次 sub-agent 状态 | 不默认注入 |

---

## 14. 数据模型设计

### 14.1 memory_items

统一 memory 表。

```sql
CREATE TABLE memory_items (
  id UUID PRIMARY KEY,

  workspace_id TEXT,
  project_id TEXT NOT NULL,
  task_id TEXT,
  workflow_id TEXT,
  run_id TEXT,

  scope TEXT NOT NULL,
  -- workspace / project / workflow / task / session / run

  memory_type TEXT NOT NULL,
  -- project_profile / project_rule / architecture_summary / source_map / code_summary
  -- task_brief / task_constraint / task_plan / task_progress
  -- task_analysis_summary / task_code_context / task_decision
  -- task_handoff_summary / task_test_state / task_final_summary
  -- workflow_state / run_observation / promotion_candidate

  key TEXT NOT NULL,
  content JSONB NOT NULL,

  text_for_embedding TEXT,
  importance INT DEFAULT 3,
  confidence NUMERIC DEFAULT 0.8,

  source_type TEXT,
  -- user / agent / tool / file / system

  source_ref JSONB,

  status TEXT DEFAULT 'active',
  -- active / superseded / archived / deleted / candidate / approved / rejected

  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now(),
  deleted_at TIMESTAMP
);
```

推荐索引：

```sql
CREATE INDEX idx_memory_scope
ON memory_items(project_id, task_id, scope, memory_type);

CREATE INDEX idx_memory_project_type
ON memory_items(project_id, memory_type)
WHERE task_id IS NULL;

CREATE INDEX idx_memory_task_type
ON memory_items(task_id, memory_type);

CREATE INDEX idx_memory_workflow
ON memory_items(workflow_id, memory_type);

CREATE INDEX idx_memory_run
ON memory_items(run_id, memory_type);
```

如使用 PostgreSQL，可后续加 `pgvector`：

```sql
ALTER TABLE memory_items ADD COLUMN embedding vector(1536);
CREATE INDEX idx_memory_embedding ON memory_items USING ivfflat (embedding vector_cosine_ops);
```

---

### 14.2 context_packs

保存每次 sub-agent 启动时的上下文。

```sql
CREATE TABLE context_packs (
  id UUID PRIMARY KEY,

  project_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  workflow_id TEXT,
  run_id TEXT,

  agent_role TEXT NOT NULL,
  stage TEXT NOT NULL,

  included_memory_ids JSONB,
  included_sources JSONB,
  compiled_context JSONB NOT NULL,

  token_budget JSONB,
  context_hash TEXT,

  created_at TIMESTAMP DEFAULT now()
);
```

用途：

```text
- 调试 sub-agent 行为
- 对比不同 run 的上下文差异
- 分析 agent 失败原因
- 审计 memory 加载是否符合策略
```

---

### 14.3 agent_runs

记录 sub-agent run。

```sql
CREATE TABLE agent_runs (
  id UUID PRIMARY KEY,

  project_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  workflow_id TEXT,

  agent_role TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  -- pending / running / succeeded / failed / cancelled

  context_pack_id UUID,

  input JSONB,
  output JSONB,
  writeback_payload JSONB,
  error JSONB,

  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now()
);
```

---

### 14.4 task_sessions

Task session 保存原始交互材料。

```sql
CREATE TABLE task_sessions (
  id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  task_id TEXT NOT NULL,

  role TEXT NOT NULL,
  -- user / assistant / tool / system

  content TEXT,
  payload JSONB,
  message_type TEXT,
  -- message / tool_call / tool_result / confirmation / feedback

  created_at TIMESTAMP DEFAULT now()
);
```

Task session 不直接作为主要上下文注入，应通过 summary 和 key messages 进入 context pack。

---

## 15. Memory Type 设计

### 15.1 Project Memory Types

```text
project_profile
project_rule
architecture_summary
source_map
code_summary
dependency_map
domain_glossary
known_constraint
known_issue
test_strategy
historical_decision
api_contract
```

---

### 15.2 Task Memory Types

```text
task_brief
task_constraint
task_plan
task_progress
task_analysis_summary
task_code_context
task_decision
task_handoff_summary
task_patch_summary
task_test_state
task_final_summary
promotion_candidate
```

---

### 15.3 Workflow Memory Types

```text
workflow_definition
workflow_state
workflow_stage_output
workflow_blocking_issue
workflow_transition_rule
agent_assignment
```

---

### 15.4 Run Memory Types

```text
run_local_plan
run_observation
run_tool_result
run_output
run_writeback
run_error
```

---

## 16. Project Memory 结构设计

### 16.1 project_profile

```json
{
  "project_id": "devwerk-web",
  "name": "DevWerk AI Agent Web",
  "type": "ai-agent-web-platform",
  "description": "Kanban + Workflow + Sub-agent 架构的 AI Agent 系统",
  "frontend": "React",
  "backend": "Spring Boot / Python FastAPI",
  "primary_language": "TypeScript",
  "main_goal": "让 Agent 具备可持续执行工程任务的能力"
}
```

---

### 16.2 project_rule

```json
{
  "rules": [
    {
      "rule": "Sub-agent 不允许自行读取全部 memory，必须通过 Context Compiler 获取 context pack。",
      "reason": "避免上下文污染和不可控读取。",
      "priority": "high"
    },
    {
      "rule": "Coding task 中 Implementer 必须优先基于 task_code_context 工作。",
      "reason": "避免重复分析和误改文件。",
      "priority": "high"
    }
  ]
}
```

---

### 16.3 source_map

Source Map 是项目级代码导航地图。

```json
{
  "version": "2026-06-30T10:00:00",
  "modules": [
    {
      "module": "memory",
      "paths": [
        "backend/app/services/memory_service.py",
        "backend/app/models/memory.py",
        "src/components/memory/MemoryPanel.tsx"
      ],
      "responsibility": "管理 project/task/session/run memory，并生成 sub-agent context pack。"
    },
    {
      "module": "workflow",
      "paths": [
        "backend/app/services/workflow_engine.py",
        "backend/app/routes/workflow.py"
      ],
      "responsibility": "调度 sub-agent 并维护 workflow state。"
    }
  ]
}
```

Source Map 使用方式：

```text
task goal
  ↓
检索 source_map
  ↓
定位 candidate modules/files
  ↓
读取 code_summary 或真实文件
  ↓
生成 task_code_context
```

---

### 16.4 code_summary

建议按 repo/module/file 三种粒度保存。

#### repo summary

```json
{
  "level": "repo",
  "summary": "该项目是一个 AI Agent Web 平台，前端展示 Kanban、Task、Workflow、Agent Run，后端提供 task、workflow、memory、agent 调度接口。"
}
```

#### module summary

```json
{
  "level": "module",
  "module": "memory",
  "summary": "memory 模块负责 project memory、task memory、task session、run trace 的读写，并在 sub-agent 启动前生成 context pack。",
  "main_files": [
    "backend/app/services/memory_service.py",
    "backend/app/models/memory.py"
  ]
}
```

#### file summary

```json
{
  "level": "file",
  "path": "backend/app/services/memory_service.py",
  "summary": "提供 memory CRUD、按 scope 查询、生成 task context pack、写入 task summary 的能力。",
  "exports": [
    "load_project_memory",
    "load_task_memory",
    "build_context_pack",
    "update_task_summary"
  ],
  "dependencies": [
    "MemoryRepository",
    "TaskRepository"
  ]
}
```

---

## 17. Task Memory 结构设计

### 17.1 task_brief

```json
{
  "task_id": "task_123",
  "project_id": "devwerk-web",
  "title": "设计 AI Agent 分层记忆系统",
  "goal": "为 Kanban + Workflow + Sub-agent 架构设计 project/task/session/run 多级 memory 和加载策略。",
  "task_type": "architecture_design",
  "status": "in_progress",
  "priority": "high",
  "created_from": "user_message"
}
```

---

### 17.2 task_constraints

```json
{
  "allowed_scope": [
    "memory architecture",
    "project/task/session/run context loading",
    "coding task source map/code summary integration"
  ],
  "out_of_scope": [
    "具体 UI 视觉设计",
    "完整代码实现"
  ],
  "user_requirements": [
    "需要 project 和 task 不同等级 memory",
    "agent 启动时需要加载 project + task 相关记忆",
    "coding task 需要考虑 source map 和代码摘要",
    "当前还没有 task 级别摘要，需要补上"
  ]
}
```

---

### 17.3 task_plan

```json
{
  "plan": [
    {
      "step": "analyze_current_design",
      "agent_role": "planner",
      "status": "done"
    },
    {
      "step": "design_memory_layers",
      "agent_role": "architect",
      "status": "doing"
    },
    {
      "step": "design_context_loading",
      "agent_role": "orchestrator",
      "status": "pending"
    },
    {
      "step": "design_schema",
      "agent_role": "backend_designer",
      "status": "pending"
    }
  ]
}
```

---

### 17.4 task_analysis_summary

```json
{
  "problem": "当前系统只有 project/task memory 和 task session，但没有区分 workflow memory、run memory、task-level code context，也缺少分层加载策略。",
  "current_design": [
    "Kanban 管理 project 下的 task",
    "Workflow 调度 sub-agent",
    "Project 级别已有 memory、source map、代码摘要",
    "Task 有 session，但缺少 task summary"
  ],
  "proposed_direction": [
    "把 memory 按 scope 拆为 workspace/project/workflow/task/session/run",
    "把 project source map 作为代码导航索引",
    "把 task code context 作为本任务相关文件摘要",
    "每个 sub-agent 启动时由 Context Compiler 生成 context pack"
  ]
}
```

---

### 17.5 task_code_context

Coding task 的核心结构。

```json
{
  "related_modules": [
    "memory",
    "workflow",
    "task"
  ],
  "related_files": [
    {
      "path": "backend/app/services/memory_service.py",
      "reason": "负责读取 project/task memory，并构造 sub-agent context pack。",
      "current_behavior": "当前只读取 project memory 和 task session。",
      "possible_change": "增加 task summary、workflow state、task code context 的读取。",
      "risk": "如果直接读取完整 session，会造成上下文污染。"
    },
    {
      "path": "backend/app/models/memory.py",
      "reason": "定义 memory 数据模型。",
      "current_behavior": "已有 project/task scope。",
      "possible_change": "增加 workflow/run/session summary 类型。"
    }
  ],
  "files_to_change": [
    "backend/app/services/memory_service.py",
    "backend/app/models/memory.py"
  ],
  "files_to_avoid": [
    {
      "path": "backend/app/services/auth_service.py",
      "reason": "与当前 memory task 无关。"
    }
  ],
  "open_questions": [
    "是否需要把 context pack 独立成一张表？"
  ]
}
```

---

### 17.6 task_decision

```json
{
  "decisions": [
    {
      "decision": "source map 保持在 project 级别，而不是 task 级别。",
      "reason": "source map 是项目导航资产，跨任务复用；task 只保存 relevant files 和 task code context。",
      "made_by": "architect-agent",
      "created_at": "2026-06-30T10:30:00"
    },
    {
      "decision": "task session 不直接注入所有 sub-agent，只注入摘要和最近关键消息。",
      "reason": "避免上下文过长和无关聊天污染。",
      "made_by": "orchestrator-agent"
    }
  ]
}
```

---

### 17.7 task_handoff_summary

```json
{
  "from_agent": "analyzer-agent",
  "to_agent": "implementer-agent",
  "handoff_summary": {
    "what_was_done": [
      "确认 memory_service.py 是 context pack 入口。",
      "确认当前缺少 task summary 加载。",
      "确认 project source map 已存在。"
    ],
    "recommended_next_steps": [
      "新增 task_summary memory 类型。",
      "修改 build_context_pack 方法。",
      "增加 task_code_context 生成逻辑。"
    ],
    "risks": [
      "不要把完整 task session 每次塞入 prompt。",
      "不要把 task 临时结论直接晋升为 project memory。"
    ]
  }
}
```

---

### 17.8 task_final_summary

```json
{
  "task_id": "task_123",
  "outcome": "completed",
  "summary": "本任务完成了 AI Agent 分层 memory 和 context orchestration 的设计。",
  "major_changes": [
    "新增 workspace/project/workflow/task/session/run 六层 memory 模型。",
    "定义 task summary 和 task code context。",
    "定义 Context Compiler 和 Memory Writer。",
    "定义 project memory promotion policy。"
  ],
  "remaining_work": [
    "实现数据库 schema。",
    "实现 Context Compiler MVP。",
    "接入 source map 检索。"
  ]
}
```

---

## 18. Workflow Memory 设计

### 18.1 workflow_state

```json
{
  "workflow_id": "wf_memory_design",
  "task_id": "task_123",
  "workflow_type": "architecture_design",
  "current_stage": "design_memory_layers",
  "stages": [
    {
      "stage": "analysis",
      "agent_role": "analyzer",
      "status": "done",
      "output_ref": "task_analysis_summary"
    },
    {
      "stage": "design",
      "agent_role": "architect",
      "status": "doing"
    },
    {
      "stage": "schema",
      "agent_role": "backend_designer",
      "status": "pending"
    },
    {
      "stage": "review",
      "agent_role": "reviewer",
      "status": "pending"
    }
  ],
  "transition_rules": [
    "analysis 完成后必须更新 task_analysis_summary。",
    "design 完成后必须生成 task_decisions。",
    "schema 完成后必须生成 migration_notes。"
  ]
}
```

---

## 19. Context Compiler 设计

### 19.1 目标

Context Compiler 负责在 sub-agent 启动前，根据：

```text
project_id
task_id
workflow_id
agent_role
stage
token_budget
```

生成一个最小、相关、可审计的 context pack。

---

### 19.2 加载策略

#### Always Load

每次 sub-agent 启动都必须加载：

```text
project_profile
project_rules
task_brief
task_constraints
workflow_current_stage
agent_role_instruction
```

#### Retrieve Load

根据 task goal、agent role、stage 检索：

```text
source_map 命中的模块
相关 code_summary
相关 task_decisions
相关 handoff_summary
相关 docs
类似 task final summaries
```

#### On-demand Load

不默认注入，只通过工具读取：

```text
完整源代码文件
完整 task session
完整历史 run trace
完整测试日志
完整上传文档
```

---

### 19.3 Agent Role 加载矩阵

| Agent Role | 必载内容 | 检索内容 | 按需内容 | 主要输出 |
|---|---|---|---|---|
| Planner | project profile/rules, task brief/constraints, session summary | historical decisions | full session | task_plan, workflow recommendation |
| Analyzer | task brief/constraints/plan, source map | code summaries, related docs | source files | task_analysis_summary, task_code_context |
| Implementer | task brief/constraints, decisions, code context | relevant handoff | source files | patch_summary, changed_files |
| Reviewer | task brief/constraints, decisions, patch summary | project rules, test strategy | changed file contents | review_result, risk_notes |
| Tester | task brief, acceptance criteria, changed files | test strategy | test logs | test_state |
| Documenter | final diff summary, decisions, test state | promotion candidates | full task memory | final_summary, changelog |

---

### 19.4 Context Pack 结构

```json
{
  "context_pack_id": "ctx_001",
  "project": {
    "profile": {},
    "rules": {},
    "architecture_summary": {}
  },
  "task": {
    "brief": {},
    "constraints": {},
    "plan": {},
    "progress": {},
    "analysis_summary": {},
    "decisions": [],
    "code_context": {}
  },
  "workflow": {
    "current_stage": "implementation",
    "previous_stage_outputs": []
  },
  "session": {
    "summary": "",
    "recent_key_messages": []
  },
  "knowledge": {
    "retrieved_code_summaries": [],
    "retrieved_docs": []
  },
  "agent_instruction": {
    "role": "implementer-agent",
    "goal": "根据 task code context 做最小修改。",
    "output_contract": {}
  }
}
```

---

### 19.5 Context Compiler 伪代码

```python
def build_context_pack(project_id, task_id, workflow_id, agent_role, stage, token_budget):
    context = {}

    # 1. Always Load
    context["project_profile"] = load_project_profile(project_id)
    context["project_rules"] = load_project_rules(project_id)
    context["task_brief"] = load_task_brief(task_id)
    context["task_constraints"] = load_task_constraints(task_id)
    context["workflow_state"] = load_workflow_state(workflow_id)
    context["agent_instruction"] = load_agent_instruction(agent_role, stage)

    # 2. Role-based Load
    if agent_role in ["planner", "architect"]:
        context["architecture_summary"] = load_architecture_summary(project_id)
        context["task_session_summary"] = load_task_session_summary(task_id)

    if agent_role == "analyzer":
        context["source_map"] = load_source_map(project_id)
        context["code_summaries"] = retrieve_code_summaries(project_id, task_id)

    if agent_role == "implementer":
        context["task_code_context"] = load_task_code_context(task_id)
        context["task_decisions"] = load_task_decisions(task_id)
        context["recent_handoff"] = load_latest_handoff(task_id, to_agent="implementer")
        context["relevant_files"] = load_relevant_file_contents(task_id)

    if agent_role == "reviewer":
        context["patch_summary"] = load_patch_summary(task_id)
        context["changed_files"] = load_changed_files(task_id)
        context["test_state"] = load_test_state(task_id)

    # 3. Rank and Trim
    context = rank_and_trim(context, agent_role, token_budget)

    # 4. Persist for Debug
    context_pack_id = save_context_pack(
        project_id=project_id,
        task_id=task_id,
        workflow_id=workflow_id,
        agent_role=agent_role,
        stage=stage,
        context=context,
        token_budget=token_budget,
    )

    return context_pack_id, context
```

---

## 20. Memory Writer 设计

### 20.1 目标

Memory Writer 负责接收 sub-agent 的 writeback payload，并将其写入正确的 memory scope。

原则：

```text
- Sub-agent 不直接写数据库
- Sub-agent 不直接写 project memory
- Sub-agent 只能提交 writeback
- Memory Writer 根据类型、scope、policy 更新 memory
```

---

### 20.2 Writeback Contract

```json
{
  "run_id": "run_789",
  "task_id": "task_123",
  "agent_role": "analyzer",
  "stage": "analysis",
  "task_updates": {
    "progress": {
      "status": "analysis_done",
      "note": "已完成 memory 架构分析。"
    },
    "analysis_summary": {},
    "code_context": {},
    "decisions": [],
    "handoff_summary": {}
  },
  "workflow_updates": {
    "current_stage": "implementation",
    "completed_stage": "analysis"
  },
  "run_updates": {
    "observations": [],
    "tool_results": []
  },
  "project_memory_candidates": []
}
```

---

### 20.3 Memory Writer 伪代码

```python
def handle_agent_writeback(run_id, writeback):
    validate_writeback_schema(writeback)

    update_run_memory(run_id, writeback.get("run_updates"))

    task_updates = writeback.get("task_updates", {})

    if task_updates.get("progress"):
        upsert_task_memory("task_progress", task_updates["progress"])

    if task_updates.get("analysis_summary"):
        upsert_task_memory("task_analysis_summary", task_updates["analysis_summary"])

    if task_updates.get("code_context"):
        upsert_task_memory("task_code_context", task_updates["code_context"])

    if task_updates.get("decisions"):
        append_task_memory("task_decision", task_updates["decisions"])

    if task_updates.get("handoff_summary"):
        append_task_memory("task_handoff_summary", task_updates["handoff_summary"])

    if task_updates.get("final_summary"):
        upsert_task_memory("task_final_summary", task_updates["final_summary"])

    if writeback.get("workflow_updates"):
        update_workflow_memory(writeback["workflow_updates"])

    if writeback.get("project_memory_candidates"):
        create_promotion_candidates(writeback["project_memory_candidates"])
```

---

## 21. Coding Task 执行链路

### 21.1 标准流程

```text
1. 用户创建 coding task
2. 系统生成 task_brief / task_constraints
3. Planner 生成 task_plan
4. Analyzer 加载 project source_map
5. Analyzer 检索 project code_summary
6. Analyzer 必要时读取真实文件
7. Analyzer 写入 task_analysis_summary
8. Analyzer 写入 task_code_context
9. Implementer 加载 task_code_context + relevant files
10. Implementer 输出 patch_summary / changed_files
11. Reviewer 审查 patch
12. Tester 执行或生成测试建议
13. Documenter 写 task_final_summary
14. Promotion Manager 生成 project_memory_candidates
```

---

### 21.2 Source Map 与 Task Code Context 的关系

```text
Project Source Map = 长期代码导航地图
Task Code Context = 当前任务战场地图
```

Source Map 不应该直接塞给所有 sub-agent。它主要给 Analyzer 用。

Analyzer 根据 source map 生成 task_code_context 后，Implementer 和 Reviewer 主要使用 task_code_context。

---

## 22. Task Summary 更新策略

Task summary 不应该只在任务结束时生成，而应该随 workflow stage 更新。

| 触发点 | 写入内容 |
|---|---|
| task_created | task_brief, task_constraints |
| planning_done | task_plan |
| analysis_done | task_analysis_summary, task_code_context, risk_notes |
| before_implementation | implementation plan, files_to_change, files_to_avoid |
| after_patch | patch_summary, changed_files, rollback_notes |
| after_review | review_findings, required_fixes, risk_level |
| after_test | test_commands, test_results, known_failures |
| task_done | task_final_summary, promotion_candidates |
| task_blocked | blocking_reason, required_user_input |

---

## 23. Project Memory Promotion Policy

### 23.1 可以晋升的内容

```text
新的架构决策
新的项目规则
新的模块职责
新的 API 契约
新的代码组织方式
新的测试命令
稳定 known issue
稳定 workaround
source map 变化
code summary 变化
```

### 23.2 不应该晋升的内容

```text
一次性任务目标
临时用户偏好
某次 run 的工具报错
尚未确认的猜测
未合并的方案
只对当前 task 有效的文件列表
```

### 23.3 Candidate 结构

```json
{
  "candidate_id": "cand_001",
  "task_id": "task_123",
  "target_memory_type": "project_rule",
  "content": {
    "rule": "Context pack 必须由 orchestrator 生成，sub-agent 不允许自行读取全部 memory。"
  },
  "reason": "这是本次任务形成的系统级设计原则。",
  "confidence": 0.9,
  "status": "candidate"
}
```

### 23.4 晋升流程

```text
Task done
  ↓
Documenter 生成 promotion candidates
  ↓
Promotion Manager 校验类型和冲突
  ↓
进入 candidate 状态
  ↓
人工确认或规则自动确认
  ↓
写入 project memory
  ↓
旧 memory 如冲突则标记 superseded
```

---

## 24. API 设计草案

### 24.1 创建 task memory

```http
POST /api/projects/{projectId}/tasks/{taskId}/memory
```

Request：

```json
{
  "scope": "task",
  "memory_type": "task_analysis_summary",
  "key": "latest",
  "content": {},
  "source_type": "agent",
  "source_ref": {
    "run_id": "run_123"
  }
}
```

---

### 24.2 获取 task context

```http
GET /api/projects/{projectId}/tasks/{taskId}/context?agentRole=implementer&stage=implementation
```

Response：

```json
{
  "context_pack_id": "ctx_001",
  "compiled_context": {}
}
```

---

### 24.3 创建 sub-agent run

```http
POST /api/projects/{projectId}/tasks/{taskId}/runs
```

Request：

```json
{
  "workflow_id": "wf_123",
  "agent_role": "analyzer",
  "stage": "analysis"
}
```

Response：

```json
{
  "run_id": "run_123",
  "context_pack_id": "ctx_001",
  "status": "running"
}
```

---

### 24.4 提交 writeback

```http
POST /api/runs/{runId}/writeback
```

Request：

```json
{
  "task_updates": {
    "analysis_summary": {},
    "code_context": {},
    "handoff_summary": {}
  },
  "project_memory_candidates": []
}
```

---

### 24.5 获取 promotion candidates

```http
GET /api/projects/{projectId}/memory/candidates?taskId={taskId}
```

---

### 24.6 审核 promotion candidate

```http
POST /api/projects/{projectId}/memory/candidates/{candidateId}/approve
```

---

## 25. UI 信息结构建议

### 25.1 Task Detail 页面

建议增加 Memory/Context 面板：

```text
Task Detail
├── Brief
├── Constraints
├── Plan
├── Progress
├── Analysis Summary
├── Code Context
├── Decisions
├── Handoff History
├── Test State
├── Final Summary
└── Promotion Candidates
```

---

### 25.2 Agent Run Detail 页面

```text
Agent Run Detail
├── Agent Role
├── Stage
├── Status
├── Input Context Pack
├── Tool Calls
├── Observations
├── Output
├── Writeback Payload
└── Memory Updates
```

---

### 25.3 Project Memory 页面

```text
Project Memory
├── Profile
├── Rules
├── Architecture Summary
├── Source Map
├── Code Summaries
├── Test Strategy
├── Known Issues
├── Historical Decisions
└── Promotion Candidates
```

---

## 26. 错误处理与冲突处理

### 26.1 Memory 冲突

当 task memory 与 project memory 冲突：

```text
当前 task 内优先使用 task memory。
不直接覆盖 project memory。
产生 conflict warning。
```

### 26.2 Source Map 过期

如果 source map 指向文件不存在：

```text
- 标记 source_map stale
- Analyzer 降级为文件系统搜索
- 生成 source_map_update_candidate
```

### 26.3 Code Summary 与真实文件不一致

```text
真实文件优先。
code_summary 标记 stale。
生成 code_summary_update_candidate。
```

### 26.4 Writeback 不合法

```text
- 拒绝写入
- 记录 run_error
- 要求 sub-agent 重新输出结构化 writeback
```

---

## 27. 安全边界

### 27.1 Sub-agent 读取限制

```text
Sub-agent 不能直接读取全部 memory。
只能读取 Context Compiler 提供的 context pack。
完整文件、完整 session、日志等通过受控 tool on-demand 读取。
```

### 27.2 Project Memory 写入限制

```text
Sub-agent 不得直接写 project memory。
只能提交 promotion candidate。
Project memory 写入必须经过 Promotion Manager。
```

### 27.3 敏感信息处理

```text
- 默认不晋升用户隐私和密钥信息
- 默认不保存 access token/API key
- 日志中如包含敏感信息，应脱敏
```

---

## 28. 可观测性

至少需要记录：

```text
context_pack_created
agent_run_started
agent_run_finished
writeback_received
memory_item_created
memory_item_updated
promotion_candidate_created
promotion_candidate_approved
promotion_candidate_rejected
context_pack_trimmed
memory_conflict_detected
```

每条事件建议包含：

```json
{
  "event_type": "context_pack_created",
  "project_id": "project_123",
  "task_id": "task_123",
  "workflow_id": "wf_123",
  "run_id": "run_123",
  "agent_role": "analyzer",
  "memory_ids": [],
  "created_at": "2026-06-30T10:00:00"
}
```

---

## 29. 测试策略

### 29.1 单元测试

```text
- MemoryRepository CRUD
- ContextCompiler role-based loading
- rank_and_trim
- Writeback schema validation
- Promotion policy validation
```

### 29.2 集成测试

```text
- 创建 coding task -> 生成 task_code_context
- analyzer writeback -> implementer context pack 包含 handoff
- task done -> 生成 promotion candidates
- candidate approve -> 写入 project memory
```

### 29.3 回归测试场景

```text
- Task 级约束覆盖 project 级默认规则
- Source map 过期时降级搜索
- Code summary 与真实文件冲突时真实文件优先
- Sub-agent writeback 格式错误时拒绝写入
- Workflow 中断后从 current_stage 恢复
```

---

## 30. 落地建议

### 30.1 第一版不要做复杂智能检索

优先采用规则加载：

```text
planner 加载固定集合
analyzer 加载 source map + code summaries
implementer 加载 task code context + relevant files
reviewer 加载 patch summary + constraints
```

等链路跑通后，再引入 embedding / reranker / graph。

---

### 30.2 不要让 task session 成为主上下文

Task Session 只作为原始材料。

主上下文应由以下内容构成：

```text
task_brief
task_constraints
task_plan
task_analysis_summary
task_code_context
task_decisions
task_handoff_summary
```

---

### 30.3 Project Memory 写入要保守

Task 结束后的结论先进入 promotion candidate。

只有稳定、跨任务有效的信息才进入 project memory。

---

### 30.4 先做可调试，再做智能化

Context pack 必须保存。

否则后续无法解释：

```text
为什么 Agent 改错文件？
为什么它忽略用户约束？
为什么它重复分析？
为什么 reviewer 没发现问题？
```

---

## 31. 关键开放问题

团队需要进一步确认：

```text
1. Project memory 是否需要人工审核才能写入？
2. Source map 是由扫描器生成，还是由 Agent 总结生成？
3. Code summary 的更新触发条件是什么？
4. Context pack token budget 默认是多少？
5. Task session summary 是实时更新，还是 stage 结束时更新？
6. Promotion candidate 的审核入口放在 task detail，还是 project memory 页面？
7. 不同 sub-agent 是否允许 on-demand 读取文件？权限如何控制？
8. 是否需要支持多用户协作下的 memory ownership？
```

---

## 32. 最终结论

本设计的核心不是给 Agent 增加“更多记忆”，而是建立一套稳定的上下文治理机制：

```text
Project Memory 解决长期项目认知；
Task Memory 解决当前任务执行认知；
Task Session 保存原始交互材料；
Workflow Memory 保存流程状态；
Run Memory 保存单次执行过程；
Source Map 帮助 coding task 定位代码；
Task Code Context 帮助 sub-agent 理解当前任务战场；
Context Compiler 控制每个 sub-agent 看到什么；
Memory Writer 控制 sub-agent 执行后沉淀什么；
Promotion Policy 控制哪些 task 结论可以进入 project memory。
```

MVP 应优先完成：

```text
1. Task Summary
2. Task Code Context
3. Context Compiler
4. Writeback Contract
5. Promotion Candidate
```

这五个能力完成后，系统会从“带记忆的聊天 Agent”升级为“可持续执行工程任务的 Agent Orchestration System”。
