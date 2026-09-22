# Human-Team Software Workflow Retest Plan — 2026-09-16

## Scope

Run a fresh, isolated Vue/Spring Boot client-server delivery test without deleting the previous project's database or files. Treat each Workflow Column Agent as an accountable team member: product clarification, DDD/API design, backend implementation, frontend implementation, independent integration review, independent QA, reproducible delivery, and acceptance. Use the current `software.ddd_delivery` Loop as a candidate only after discussing its fit with the persistent Conversation Agent. Do not edit Runtime or Loop code during the observational phase; record any issue before deciding on repair.

## Human discussion before authorization

Use multiple `mode=discuss`, `start_task=false` turns as a non-specialist product owner. Agree on user behaviors, registration/login/anti-brute-force rules, success and failure UX, technology and local delivery target, non-goals, and acceptance checks. Explicitly discuss who receives review/test feedback, whether an implementation Column retains its logical Agent Session on return, and what real build/test command must pass before it can advance. Inspect after each turn to ensure no Workflow/Task starts before the agreed go-ahead. Only then authorize a Task, and verify the actual Workflow and Task IDs.

## Execution and evidence gate

For every Column transition, capture Task/Column Runs, Agent Runs and Session IDs, tool receipts, feedback artifacts, files, tests, and time. A source write or an Agent statement is not success. Require a compiled backend, a buildable frontend, passing relevant development tests, integration review, QA, run/stop evidence, and final acceptance. Exercise genuine cross-Column feedback and at least one directed rework/recheck cycle; for a complex-task success claim, verify multiple real interactions rather than merely forward flow. Observe whether a failed test reaches the responsible implementation Agent and whether that Agent performs a targeted modification, then reruns checks. Do not substitute Conversation Agent `task.resume/retry` for rework.

## Stop/report rule

Publish a success report only after all lifecycle and interaction gates in [the mandatory test contract](../column-agent-lifecycle-interaction-test-gate-2026-09-16.md) have actual evidence. If provider quota/network or a P0 Runtime fault prevents completion, stop that test branch, record exact IDs, calls, status and reproduction, and label the result incomplete or P0 rather than successful. Keep a timestamped step log and final delivery/consumption audit in a separate result document.
