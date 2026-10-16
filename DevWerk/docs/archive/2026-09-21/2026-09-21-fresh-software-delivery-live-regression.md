# Fresh Software Delivery Live Regression

**Date:** 2026-09-21 (Asia/Shanghai)  
**Project:** `prj_ea105a23fc6f43389e891fb8ddd1eebd`  
**Workspace:** `D:\workspace\codex-devwerk-project-files\fresh-software-blackbox-20260921-181154`  
**Method:** black-box use of DevWerk public HTTP API; new Project and workspace

## Goal

Verify that the repaired DevWerk can create and execute a complete software-delivery Workflow from a fresh human conversation, and that the produced delivery matches both the Workflow contract and the human requirement.

## Human requirement

- Vue frontend with registration, login, and post-login welcome screen.
- Java Spring Boot backend.
- In-memory application state; no business database.
- Registration and login behavior.
- Brute-force/rate-limit protection with deterministic negative coverage.
- Origin validation for state-changing requests with deterministic negative coverage.
- Backend tests, frontend tests/build, and real browser E2E for register → login → welcome plus accepted negative cases.
- Deterministic start, health/HTTP behavior, stop, port cleanup, and final acceptance evidence.

## Test focus

1. Discussion boundary: no Workflow/Task before explicit authorization.
2. Conversation Agent: selects an installed Loop and creates one independently accepted Task that traverses lifecycle Columns.
3. Column lifecycle: one Worker per Column, observable Assignment and completion evidence.
4. Feedback: failed verification records a durable defect, routes to the responsible Column, is consumed during rework, and is resolved only after fresh verification.
5. Mailbox: passive delivery notification only, no LLM calls or autonomous business mutation.
6. Acceptance: actual behavior checks, not test listing, dry runs, build-only evidence, report reads, or prose claims.
7. Result and cost: final artifacts, terminal states, model/API calls, Tokens, retries, and avoidable overhead.

## Pass conditions

- Fresh Project isolation is maintained.
- At least several ordinary human discussion turns occur before execution authorization.
- No premature Workflow, Task, or source-code mutation occurs during discussion.
- Workflow and Task IDs are persisted before the Conversation Agent reports successful dispatch.
- Required checks execute and defects can traverse a real directed repair loop.
- Terminal success is impossible while a current obligation or feedback item is unresolved.
- No DevWerk P0 remains. Product defects are reported separately without DevWerk severity labels.

## Execution record

### 1. Service and Project initialization

- `GET /v1/health`: healthy; supervisor and Conversation Gateway running; zero pending Jobs.
- Created a new Project through `POST /v1/projects`.
- Project ID: `prj_ea105a23fc6f43389e891fb8ddd1eebd`.
- Dedicated workspace: `D:\workspace\codex-devwerk-project-files\fresh-software-blackbox-20260921-181154`.
- No previous Project, Workflow, Task, conversation, or generated workspace is reused.

_Further steps are appended while the test runs._

### 2. Five-turn requirements discussion

Five user turns were completed before execution authorization. Every turn used `mode=discuss` and `start_task=false`.

| Turn | Conversation Job | Decision/result | Workflow/Task effect |
|---:|---|---|---|
| 1 | `cjob_3597ce0fe9ea4748af50447e61975471` | Identified six material decisions: rate limits, Origin, auth mechanism, API, test scope, delivery form. | none |
| 2 | `cjob_51fea5cf56eb4230a83fa01ba9edbb26` | Fixed registration limit; proposed login limit and Session-Cookie with rationale. | none |
| 3 | `cjob_50c012b6087f4f9cb8ab1913db429884` | Confirmed API/error contract and separated browser E2E from unit/integration coverage. | none |
| 4 | `cjob_548eb69aeba44f238b370750ca640682` | Corrected SPA success semantics and identified Cookie, route guard, probe, and serving gaps. | none |
| 5 | `cjob_144910a496834ee48417118d4d599831` | Produced a concise final implementation proposal and declared no remaining blocker. | none |

Confirmed decisions:

- registration: five attempts per IP per 60 seconds, then HTTP 429;
- login failure: five failures per IP per 15 minutes, then HTTP 429;
- Session-Cookie with HttpOnly and SameSite=Lax;
- local frontend Origin allowlist; invalid Origin returns HTTP 403;
- stable JSON contract for invalid input, duplicate username, invalid credentials, invalid Origin, and rate limit;
- `/api/auth/register`, `/api/auth/login`, `/api/auth/logout`, `/api/me`;
- Vue SPA registration/login/welcome views; `/welcome` calls `/api/me` and redirects on 401;
- Playwright real-browser E2E plus backend integration tests and a deterministic HTTP Origin probe;
- build then `npm preview` on a fixed port;
- PowerShell runner starts both services, waits for readiness, executes all checks, and always removes child processes/ports.

Boundary result: **pass**. The Conversation Agent persisted an execution hold and discussion decisions, but no Workflow revision, Task Plan, Task, or source implementation was created. Replies consistently disclosed “discussion only / no new Tasks.”

### 3. Execution authorization

The next user turn explicitly authorizes implementation against the accepted proposal. It requires the installed `software.ddd_delivery` Loop, one independently accepted Task across all lifecycle Columns, one Worker per Column, executable behavior checks, and directed feedback/retest before terminal acceptance.

### 4. Initial execution attempt

Conversation Job `cjob_09c4edd70192448bb6a36a80f9a47511` correctly resolved the turn as execution and persisted:

- execution grant `grant_ca2a1bd4f96447a0bff151476bbf511b`;
- Requirement `req_1dc4d25e82d844b59625ad476360a6c9`, revision 1;
- accepted requirement baseline artifact `art_1cb5a7206e714be898c6bdf166a26d04`;
- Loop application `loopapp_0605d66d9a9e40298431b54ce3558257`;
- Workflow Plan `wfplan_6e3dee2a9927490abe2729e7c5a60cb0`;
- initial active Workflow revision 1 `wfrev_391057a27f01491abd22f55fa6a3320b`.

The initial Loop Workflow did not yet contain the project-specific frozen acceptance obligations. The Agent attempted to save a Task Plan and received two useful rejections:

1. `acceptance_criteria_by_column` is not a valid Task readiness property;
2. `backend_development` lacked a frozen behavioral command referenced by `workflow.acceptance_obligations`, so the instantiated Workflow had to be published first.

It then attempted to publish the project-specific Workflow. Both `workflow.publish` calls failed with:

```text
ValidationError: Acceptance obligation must reference a frozen Column check
```

The second call repeated the same rejected operation without correcting the invalid obligation reference. The protocol guard terminated the Job with:

```text
ConversationProtocolStalled: Conversation Agent repeated an identical failed
tool operation without changing its arguments: workflow.publish
```

Persisted result after the Job:

- Conversation Job: `failed`;
- active Workflow: revision 1 from `loop.apply`;
- Task Plan: none;
- Task: none;
- user-visible response: zero Tasks created/started and publication validation failure;
- durable partial progress: Requirement, baseline file, Loop application, Workflow Plan, revision 1.

**Preliminary framework finding — P0:** the primary execution path cannot instantiate and dispatch a valid software Workflow from a confirmed requirement. The repeated-operation guard prevents an infinite loop, but it does not repair or translate the validation error, so the core “create and execute Workflow” objective is blocked. The regression continues with one explicit user recovery turn to determine whether the persistent Conversation Agent can correct its own partially completed execution on the next turn.

### Test validity correction

This Project is **excluded from the ordinary-user black-box verdict**. The execution instruction exposed internal product vocabulary and prescribed implementation mechanics (`software.ddd_delivery`, Workflow acceptance checks, Task/Column/Worker topology). That instruction coached the system toward the expected architecture instead of testing whether DevWerk could infer and apply it from an ordinary product request.

The recovery turn also failed after repeating an unchanged rejected `task.plan.save` call, but that result belongs to an architecture-directed diagnostic case, not the clean black-box case requested by the user. The Project and records remain available as diagnostic evidence; no further test action is taken in this Project.
