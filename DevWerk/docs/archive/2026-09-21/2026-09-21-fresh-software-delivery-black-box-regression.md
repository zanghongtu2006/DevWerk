# Fresh Software Delivery Black-box Regression

**Date:** 2026-09-21 (Asia/Shanghai)  
**Project:** `prj_345cec29958c45d9a94897afd239aeda`  
**Workspace:** `D:\workspace\codex-devwerk-project-files\fresh-software-regression-20260921-143111`  
**Method:** public HTTP API and persisted runtime evidence only

## Purpose

Run a completely fresh black-box test after the latest fixes. The test does not reuse an existing Project, Workflow, Task, conversation, or generated workspace.

## Delivery scenario

Build a small client-server login product:

- Vue frontend with registration, login, and a post-login welcome screen;
- Java Spring Boot backend;
- fixed in-memory users and no business database;
- registration and login behavior;
- brute-force/rate-limit protection with deterministic negative tests;
- Origin validation for state-changing requests with a deterministic negative test;
- executable backend and frontend tests;
- real browser E2E for register → login → welcome plus accepted negative cases;
- operable start/stop/health verification and final acceptance evidence.

## Process under test

1. Discuss material requirements with the Project Conversation Agent.
2. Confirm the acceptance boundary and authorize execution.
3. Verify that the Conversation Agent selects an installed Loop, publishes a Workflow, saves one independently accepted Task Plan, creates a Task, and reports only persisted effects.
4. Observe every Column lifecycle and verify that each Column has one Worker/Assignment at a time.
5. Verify that failed checks create durable feedback, return to the responsible Column, are consumed by the current Assignment, and are resolved only by fresh verification evidence.
6. Verify that Mailbox remains passive and does not consume LLM calls or mutate delivery state.
7. Audit final artifacts against the human requirement and Workflow acceptance contract.
8. Record model calls, tokens, retries, feedback, acceptance evidence, and terminal states.

## Pass criteria

- A fresh Project has no inherited Workflow, Task, conversation, or artifact state.
- Discussion does not create Workflow/Task state before explicit execution authorization.
- Execution creates a Workflow and real Task through the installed Loop.
- User work is not starved by Mailbox notifications; Mailbox notifications use zero model calls.
- Current Assignments cannot be short-circuited by stale completion state.
- Defects travel back to the responsible implementation Column and result in actual rework/retest.
- Required behavior checks execute; listing tests, dry runs, build-only evidence, and prose claims do not satisfy E2E/delivery/acceptance.
- Terminal success requires all current acceptance obligations to be satisfied.
- No DevWerk P0 remains. Delivery defects are classified separately from framework defects.

## Execution record

### 1. Service startup

DevWerk started in development mode on port 8000. Startup reported `auto_resume_previous_tasks=false`; existing historical Tasks were paused. The new Project did not yet exist during startup and therefore had no inherited scheduled work.

### 2. Fresh Project creation

Created via `POST /v1/projects`:

- Project: `prj_345cec29958c45d9a94897afd239aeda`
- Name: `Fresh Software Delivery Regression 20260921-143111`
- Dedicated workspace: `D:\workspace\codex-devwerk-project-files\fresh-software-regression-20260921-143111`

The Project was created with a test-specific description and general project-manager instruction. No Workflow or Task was supplied outside DevWerk.

_Further observations are appended as the test proceeds._

### 3. Pre-discussion state

Before the first conversation turn:

- `GET /workflow` returned no active Workflow;
- `GET /tasks` returned no Tasks;
- the dedicated workspace contained only Project initialization state;
- no old Project entity was used as an input to this test.

### 4. First requirements discussion

Submitted a discussion-only turn through `POST /conversation` with `start_task=false` and `mode=discuss`:

> Build a small registration/login system with Vue and Spring Boot, without a business database. Discuss requirements and acceptance first; do not start development. Identify only the material unresolved questions.

Persisted entities:

- Conversation Session: `ca_f4649d1c480f4c1da9572a469736bbc1`
- Conversation Job: `cjob_0efefad42d4a47c8a542c786931774c3`
- Agent Run: `arun_4f878085c29e40ea8e9eff12c6b955ba`
- User message: 523
- Assistant failure message: 524

### 5. Actual result

The Job reached terminal `failed` in approximately 1.7 seconds. The Conversation Agent Run recorded:

- status: `failed`;
- iterations: 1;
- tool calls: 0;
- durable progress: false;
- Task IDs: none;
- Workflow: none;
- failure: connection could not be established to the configured LLM provider (`WinError 10013`).

The public event stream recorded the complete sequence:

1. `conversation.queued`;
2. `conversation.planning_started`;
3. `agent.started`;
4. `conversation.progress` with `provider_wait`;
5. `agent.finished` with `failed`;
6. `conversation.message` containing the user-visible failure;
7. `conversation.planning_failed`.

Post-failure inspection confirmed:

- zero Tasks;
- no active Workflow;
- no Task Plan or Workflow Plan created;
- Project quiescent with one unresolved failed Conversation Job;
- no Mailbox notification or autonomous mutation;
- no false success response.

## Consumption

| Item | Observed value |
|---|---:|
| Conversation Jobs | 1 |
| Agent Runs | 1 |
| Model request iterations | 1 |
| Tool calls | 0 |
| Column Agent runs | 0 |
| Tasks | 0 |
| Mailbox Jobs | 0 |
| Recorded input/output tokens | 0 / unavailable because the request did not reach a model response |

The failure happened before a model response, so there is no valid Token sample and no software-delivery workload to compare.

## Assessment

### What passed

- Fresh Project isolation was proven.
- Discussion-only input did not create a Workflow or Task.
- Provider failure became a truthful terminal Job, Agent Run, event sequence, and user-visible conversation message.
- No partial mutation, Mailbox loop, or false completion was observed.

### What was not exercised

- requirements discussion;
- Loop selection and Workflow publication;
- Task creation and dispatch;
- Column Worker lifecycle;
- defect feedback and directed rework;
- executable acceptance obligations;
- final delivery and acceptance;
- meaningful Token/call optimization.

These were not skipped by test design; they were unreachable after the first Conversation Agent dependency call failed.

## Verdict

**Regression result: blocked / inconclusive for the repaired workflow features.**

DevWerk started and its public API behaved consistently, but the fresh test could not advance beyond the first Conversation turn because its configured model dependency was unreachable from the execution environment. This is recorded as an environment/dependency blocker, not a DevWerk P0 and not a successful workflow regression. A rerun from this same fresh Project is valid once the service process can reach its configured provider; no cleanup or inherited Task state is required.
