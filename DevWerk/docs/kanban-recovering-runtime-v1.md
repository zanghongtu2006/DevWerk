# Kanban Recovering Runtime V1

## 1. Purpose

Kanban Runtime is responsible for keeping a Task moving when a Column execution is interrupted by a temporary infrastructure failure. Recovery is part of workflow execution and does not depend on a Conversation Agent turn.

The Task keeps its identity, current Column, context, artifacts, dependencies, scheduling entry and workflow revision throughout recovery.

## 2. State transition

```text
running -- recoverable infrastructure error --> recovering
recovering -- retry time reached + scheduler claim --> running
running -- successful Column result ----------> next Column / done
running -- non-recoverable error -------------> failed
```

`recovering` is an active, non-terminal Task state. A recovering Task remains visible and counts as unfinished work. Runtime records `next_retry_at`; the Task becomes eligible for normal Kanban scheduling only after that time, under the same dependency, WIP and resource rules as a pending Task. V1 uses the runtime policy's fixed recovery delay. There is no retry-count ceiling.

## 3. Error classification

The Runtime uses structured provider error codes instead of message matching.

Recoverable provider errors are:

- provider read timeout;
- rate limiting;
- provider overload;
- provider 5xx/internal error;
- provider concurrency saturation.

Authentication, authorization, billing, request-contract, usage-limit and token-plan-limit errors are terminal because repeating the same execution cannot correct them.

Workflow outcomes such as reviewer rejection are not infrastructure recovery. They continue to use the Workflow's declared directed transitions.

## 4. Recovery record

When a recoverable failure occurs, the current Column Run and Attempt are closed as failed audit records. The Task moves to `recovering` without creating terminal evidence or a `task.failed` notification. Runtime emits:

- `task.recovering`: failure code, category, failed Column, failed Run and `next_retry_at`;
- `task.recovery_started`: the scheduler reclaimed the same Task and Column;
- `task.recovered`: the retried Column completed successfully and workflow execution continued.

The next claim creates a new Column Run/Attempt for the same Task and Column. Existing project files and Task context remain available to the executor.

## 5. Conversation Agent boundary

Conversation Agent observes recovery through events and may explain it to the user. It is not required to reopen, rerun or rebuild the Task for a mechanical provider failure. Ambiguous or terminal failures remain available for user-facing diagnosis and intervention.

## 6. V1 acceptance

- A provider timeout causes `running -> recovering`, not `failed`.
- A recovering Task is not reclaimed before `next_retry_at`, preventing a tight provider retry loop.
- The scheduler automatically reclaims the recovering Task.
- A later successful execution continues the original workflow using the same Task ID and Column.
- Token-plan-limit and contract errors still produce a clear terminal failure.
- Project quiescence never reports completion while a Task is recovering.
