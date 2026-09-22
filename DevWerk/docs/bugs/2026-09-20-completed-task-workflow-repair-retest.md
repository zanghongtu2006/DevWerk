# Completed Task Workflow Repair Retest

**Date:** 2026-09-20 (Asia/Shanghai)  
**Project:** `prj_df4616276dab4235ae178242fcc7fdc6`  
**Original Task:** `tsk_88ca035520db421980b295b2f8e374ec` (`done`)  
**Baseline Workflow:** revision 2, `wfrev_4e68019b346c43b8aaedf711a80c61d3`  
**Related audit:** [Human-Team Software Workflow Retest](2026-09-16-human-team-software-workflow-retest-results.md)

## Objective

Test whether Conversation Agent can use the existing Project discussion, artifacts, completed Task, and audit findings to:

1. distinguish a terminal immutable Task from repair work that still must be scheduled;
2. discuss and publish a stronger Workflow revision or Column acceptance contract;
3. choose a supported recovery path: reopen/rerun the completed Task if valid, or create a new repair Task when terminal history must remain immutable;
4. dispatch real implementation, test, delivery, and acceptance work rather than only describe a plan;
5. prove that confirmed defects can enter a directed repair loop and produce new objective evidence.

## Required repair scope

- Implement backend Origin validation for state-changing requests and add an executable negative test.
- Replace the unit-test-only QA claim with executable browser/integration evidence for register → login → welcome and the accepted negative cases.
- Make delivery prove start, HTTP behavior, stop, and port cleanup.
- Prevent final acceptance from succeeding when a required check or unresolved defect lacks evidence.

## Test sequence

1. `discuss` turn: ask Conversation Agent to inspect current terminal state and propose the supported repair strategy without mutation.
2. `discuss` turn: require exact Workflow revision changes, deterministic checks, feedback routing, and Task identity semantics.
3. authorized turn: permit Workflow publication and either a valid rerun/reopen or a new repair Task.
4. verify persisted Workflow/Task IDs and inspect actual Column transitions, Agent Sessions, command receipts, artifacts, and terminal outcome.
5. compare incremental LLM calls/Tokens and Mailbox cost against the baseline.

## Pass criteria

- No false claim that the original immutable `done` Task was silently changed.
- A new active Workflow revision contains machine-enforced repair/QA/delivery/acceptance conditions.
- A real Task enters the repair Workflow and modifies/tests existing artifacts.
- Origin and browser/integration evidence are executable, not merely described in Markdown.
- A failed check routes to the responsible implementation stage with durable evidence; success is allowed only after retest.
- Conversation Job status and user-visible response match the actual persisted effect.

## Initial environment observation

Port 8000 already had a healthy DevWerk instance with zero pending Conversation Jobs. A second startup attempt correctly failed on the occupied port, but also exposed a Windows log-rotation file-lock error because both processes attempted to own `data/logs/devwerk.log`. The retest continues against the original healthy service; no code was changed.

## Execution record

### Discussion turn 1

- Conversation Job: `cjob_842b7347fb0a4bfeac4e5f82e2041b79`
- Result: correctly preserved the original `done` Task as immutable and proposed a new Workflow revision plus a new repair Task.
- Defect in the proposal: it assumed repaired backend code already existed, suggested starting at `integration_review`, and treated E2E placement as an unresolved user choice.

### Discussion turn 2

- Conversation Job: `cjob_e4eaf4c5c69248a2915f5cbd4cd90852`
- Agent Run: `arun_427b328b8b2b4069aaf9903744b3dff5`
- Result: confirmed the missing Origin behavior and missing E2E execution, but still proposed unsupported `task.create.entry_column` and `rerun_of_task_id` arguments.
- The proposal also weakened executable acceptance: `playwright --list` was used as the frozen check while full E2E was left as a manual Agent action; `delivery` and `accept` kept empty `acceptance_checks`.
- Direct code inspection confirmed that `TaskCreate` accepts only `task_plan_id` and `proposed_task_ref`; Tasks start at the published Workflow entry.

### Authorized repair turn

- Conversation Job: `cjob_e7fc72c794c646a4b226d0ef27a72655`
- Conversation Agent Run: `arun_ee5fedbb42694469b32b8b04577c63a9`
- Published Workflow revision 3: `wfrev_298759d657a04b8d89892cf7dbe9572f`
- Saved Task Plan: `tplan_c473aff0b68d442eab2c51acd8f004cc`
- Created repair Task: `tsk_36be9b4387c5479686ec432ec9724b29`
- Original Task `tsk_88ca035520db421980b295b2f8e374ec` remained `done` and unchanged.
- The repair relationship was persisted in the new Task brief/objective rather than a fabricated Runtime field. The new Task correctly entered the real Workflow entry, `requirements`, and reused the existing Project workspace.

The execution turn required multiple schema-error corrections before saving the Task Plan. It eventually created and dispatched a real Task. This proves the supported recovery model is **new revision + new Task over the existing workspace**, not mutation of the completed Task.

### Persisted revision 3 review

Revision 3 is only a partial acceptance improvement:

| Column | Frozen check | Assessment |
|---|---|---|
| `backend_development` | `mvn.cmd -f backend/pom.xml test` | Executable, but Origin behavior depends on the Worker adding a real negative test. |
| `frontend_development` | `npm.cmd --prefix frontend run build` | Build-only; does not prove browser behavior. |
| `integration_review` | Windows `findstr` for `getHeader.*Origin` | Deterministic but behaviorally weak and implementation-specific. |
| `system_test` | Maven test + frontend build + `playwright --list` | Does not execute browser E2E; fails the requested hard boundary. |
| `delivery` | none | Still permits prose-only delivery claims. |
| `accept` | none | Still permits prose-only final acceptance. |

Therefore Workflow revision publication and Task restart passed, but the stricter machine-enforced acceptance contract did **not** fully pass. Runtime execution remains under observation.

### Runtime execution and recovery

The revision 3 Task executed real work:

1. `requirements` succeeded and wrote `docs/requirements-traceability.md`.
2. `domain_design` succeeded and updated the architecture, API, and test-plan documents.
3. `backend_development` succeeded and wrote:
   - `backend/src/main/java/com/teamauth/filter/OriginCheckFilter.java`
   - `backend/src/main/java/com/teamauth/config/SecurityConfig.java`
   - `backend/src/test/java/com/teamauth/filter/OriginCheckFilterTest.java`
   - `docs/backend-development.md`
4. Maven acceptance passed, including the new Origin filter tests.

`frontend_development` then failed repeatedly with `RuntimeError: Column Agent ended without calling column.complete`. The exact model responses showed that the reused persistent Worker Session believed the old delivery's frontend Column had already completed (“Previously Completed / Generation 1”). It returned prose without acting on the new repair Assignment. Four consecutive runs repeated the same stale conclusion with zero tool calls.

An authorized recovery turn used `task.retry`. A second authorized turn used `task.retry(clear_context=true)`. After clearing the logical context, the Column completed and the Task advanced to `integration_review`. This proves project artifacts can be preserved while a stale Worker context is cleared, but also exposes that a new repair Task can inherit an earlier Task's completion narrative strongly enough to make it non-functional.

The persisted frontend source did not satisfy the requested repair: `frontend/tests/e2e/register-login.spec.js` still tests form validation and navigation only. It does not run the accepted register/login/duplicate-user/rate-limit backend behavior.

### Revision 3 deterministic-check failure

`integration_review` failed before the Worker could complete:

```text
ExecutionReplayUncertain: project.command.run outcome is unknown after
ValueError: path escapes project base_dir
```

The failing frozen check used `cmd /c for /r ... findstr`. The shell construct was incompatible with the command/path boundary. Because a Task remains pinned to its Workflow revision, retrying the Task could not adopt a corrected active revision.

### Unauthorized mailbox mutation and user-job starvation

After the Runtime failure, automatic mailbox Jobs did not remain notification-only. One mailbox Job, `cjob_d0a751e2704b4822befec41d961f8e5a`, performed control mutations without a new user execution grant:

- retried the blocked Task;
- changed Worker lifecycle state;
- published Workflow revision 4, `wfrev_454e865b32054de6923b8b4ab7f5f354`;
- marked `tsk_36be9b4387c5479686ec432ec9724b29` failed;
- created rerun Task `tsk_f2342984fbc3464981bbeb51f72b8aca`, which remained pinned to revision 3;
- marked that rerun failed;
- continued attempting further planning and mutation for more than 22 model iterations.

During that mailbox loop, the explicit user revision-4 request `cjob_2f412d37cb854e2aa61d47aed2dce46b` remained queued and received zero model calls. The local service was stopped with `shutdown.bat` to halt further token consumption. Database and workspace evidence were preserved.

The mailbox-generated revision 4 also failed the requested acceptance design: it retained `playwright --list`, empty `delivery`/`accept` checks, and replaced the recursive source scan with another `findstr` check rather than a behavioral Maven test.

## Final task states at test stop

| Task | Workflow | Final observed state | Meaning |
|---|---|---|---|
| `tsk_88ca035520db421980b295b2f8e374ec` | revision 2 | `done` | Original immutable delivery; correctly preserved. |
| `tsk_36be9b4387c5479686ec432ec9724b29` | revision 3 | `failed` | Performed real backend repair, then failed at the invalid integration check. |
| `tsk_f2342984fbc3464981bbeb51f72b8aca` | revision 3 | `failed` | Mailbox-created rerun; could not migrate to revision 4. |

## Framework findings

### P0 — Mailbox can mutate delivery state and starve user instructions

The mailbox acted as an autonomous planner/executor rather than a bounded communication channel. It published a Workflow, retried/cancelled Tasks, managed Workers, and created a rerun while an explicit user Job waited. This violates user authority, makes recovery non-deterministic, and caused an unbounded token loop.

### P0 — New repair Assignment can inherit a stale completed-Column narrative

The new Task reused a persistent frontend Worker context that treated the old Task's completion as authoritative for the new Assignment. Repeated Runs returned prose with no tools and no `column.complete`. A new Assignment must always place its current Task/Column/Assignment identity above prior completion memories; previous Task terminal state must not satisfy or short-circuit current work.

### P1 — Workflow revision correction has no direct migration path for an active repair Task

The Task correctly remains immutable with respect to its Workflow revision, but recovery UX lacks a single safe operation to create a successor against the corrected active revision while preserving repair provenance. The mailbox attempted `task.rerun`, which necessarily inherited the bad revision, then discovered the mismatch only afterward.

## Delivery findings (not DevWerk bug severity)

- Backend Origin behavior was materially implemented and unit-tested.
- Frontend “E2E” remained a UI/form/navigation test and did not prove the integrated product behavior.
- No real browser + backend E2E receipt was produced.
- Delivery and final acceptance still lacked deterministic frozen checks.
- Therefore the repaired delivery was not accepted.

## Incremental usage

Window: from the first repair discussion at `2026-09-20 23:29:43 +08:00` until the service was stopped.

| Scope | API calls | Input tokens | Output tokens | Total tokens | Cached input tokens |
|---|---:|---:|---:|---:|---:|
| Conversation Agent | 87 | 810,697 | 79,282 | 889,979 | 3,860,487 |
| Column Agents | 33 | 333,067 | 29,976 | 363,043 | 533,951 |
| **Total** | **120** | **1,143,764** | **109,258** | **1,253,022** | **4,394,438** |

Conversation cost by trigger:

| Trigger | Jobs | API calls | Total tokens | Cached input tokens |
|---|---:|---:|---:|---:|
| User | 6 | 34 | 453,249 | 1,074,723 |
| Mailbox | 10 | 53 | 436,730 | 2,785,764 |

The single runaway mailbox Job used 22 API calls, 141,213 total tokens, and 1,508,047 cached input tokens before shutdown. The user Job queued behind it used zero calls.

## Verdict

The supported architectural recovery path was proven in part: a completed Task remained immutable, a revised Workflow was published, a new Task reused the existing workspace, and real backend repair executed. The overall test still **fails** because the stronger acceptance contract was weakened during publication, stale Worker memory broke a new Assignment, the deterministic integration check was invalid, and mailbox supervision took unauthorized control while starving the user's corrective instruction.
