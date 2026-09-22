# Column Agent Lifecycle and Interaction Test Gate

**Status:** mandatory acceptance criterion for future DevWerk Workflow tests and audits.

## Purpose and minimal documentation change

This record establishes a test/review gate; it does not change Runtime or Loop code. Add it to the V1 test contract and document index. The current software-delivery incident demonstrates why a successful Agent reply, Task state change, or a single implementation pass is not proof of a working multi-Column Workflow.

## Required audit for every Workflow test

Before calling a test complete or publishing a success report, inspect the actual Workflow revision, Task/Column Run history, Agent Run and logical Session identities, tool receipts, feedback artifacts, and transition events. For each participating Column, state when its Agent was created, activated, suspended/resumed or terminated, what it received, what it produced, and which outcome moved the Task. Do not infer interaction from Loop instructions or a diagram alone.

When a later Column returns a review, test, delivery, or acceptance result, verify that the intended earlier Column actually receives that specific feedback, that its Agent executes a targeted change rather than only receiving a `resume/retry` control operation, and that the subsequent check runs again against the changed artifact. The Column must not be marked successful merely because source files were written; relevant build/compile and development-test commands must have successful, durable execution evidence before implementation advances. Independent review, system test, and delivery checks remain separate gates.

## Complex-task acceptance

A complex-task test must deliberately exercise multiple real cross-Column interactions, including forward progress, feedback, directed rework, and renewed verification. Seed or observe genuine issues rather than fabricating a passing report. Record the exact feedback-to-change-to-recheck chain and Agent/Session continuity for each cycle. If the test never exercises these interactions, its result is **not tested**, not successful. If the Workflow cannot deliver feedback, retain the intended Agent lifecycle, execute rework, or enforce the implementation gate, classify the finding as **P0**. An unresolved complex-task P0 prevents a success report even when some files exist or a Task appears `done`.

This requirement applies to software, novel, and future Loops alike; business roles and artifact types belong to the Loop, while lifecycle, evidence, and transition integrity belong to DevWerk.

## Current incident baseline

In the 2026-09-15 Vue/Spring project, requirements and domain design completed, but backend development did not. No downstream review/test/delivery Column ran, so no downstream feedback was delivered back to backend. Conversation Agent performed one test-configuration file change and independently reran Maven, but this was outside the directed Column rework path and did not produce passing tests. Repeated Task `resume/retry` operations did not constitute rework. See [delivery audit](DEVWERK_Vue_Spring_Delivery_Audit_2026-09-15.md) and [P0 incident](bugs/2026-09-15-software-delivery-replay-mailbox-scheduler-stall.md).
