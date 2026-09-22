# Ordinary-user Software Delivery Black-box Test Plan

**Date:** 2026-09-21  
**Status:** approved test shape; execution pending on a new Project

## Purpose

Test DevWerk as a normal user would use it. The tester describes a product, answers understandable product questions, agrees on quality expectations, and asks DevWerk to begin. The tester does not name or teach DevWerk's internal architecture.

## Tester persona

The tester is a product owner who understands the desired user experience but does not know DevWerk's internal concepts. The tester may specify technologies, security behavior, test expectations, and delivery form because those are product or engineering requirements. The tester must not prescribe DevWerk's internal orchestration method.

## Product request

Build a small registration and login product:

- Vue frontend with registration, login, and welcome pages;
- Java Spring Boot backend;
- in-memory data only;
- registration and login abuse protection;
- requests from unexpected origins rejected;
- automated backend and frontend tests;
- real browser verification of the complete user journey;
- one Windows command/script that starts the product, verifies it, and cleans up its processes.

## Forbidden coaching language

The tester will not mention or prescribe:

- any installed method/template identifier;
- internal board or execution graph terminology;
- how many internal work items or execution agents to create;
- internal participant lifecycle, routing, state machine, evidence schema, or tool names;
- exact internal recovery APIs;
- database IDs or internal record types.

The tester may say “please fix defects and retest before delivery,” because that is a normal human expectation, but cannot explain how DevWerk must implement that behavior internally.

## Conversation script

The wording may adapt naturally to the previous answer, but must stay within the following ordinary-user intent.

### Turn 1 — Describe the idea

> I want a small registration and login product. The frontend should use Vue and show a welcome page after login. The backend should use Java Spring Boot, and data can stay in memory. Let us clarify the important requirements before starting.

Expected: DevWerk asks material product/acceptance questions and does not start implementation.

### Turn 2 — Security expectations

> Registration and failed login attempts should be rate-limited. Requests from unexpected website origins must be rejected. Please recommend practical thresholds and a suitable login mechanism for this small local product.

Expected: understandable recommendation with trade-offs; no implementation starts.

### Turn 3 — API and user behavior

> Use ordinary registration, login, logout, and current-user APIs. Duplicate users, wrong passwords, invalid inputs, blocked origins, and rate limits should return stable and distinguishable errors. What user journeys must be tested in a real browser?

Expected: discusses end-user journeys and failure paths.

### Turn 4 — Delivery expectations

> I want the finished frontend build and backend to be started locally by one Windows script. It should wait until both are ready, run all tests including browser tests, and clean up processes and ports even when a test fails. Is anything important still unresolved?

Expected: identifies only genuine remaining product or environment decisions.

### Turn 5 — Final confirmation

> Use the practical defaults you recommended. Summarize exactly what will be delivered and how I will know it works. Do not start yet.

Expected: concise human-readable proposal, no premature implementation.

### Turn 6 — Start

> The proposal is approved. Please start the project now and keep working until it is either delivered successfully or you have a clear blocker that requires me.

Expected: DevWerk independently selects its delivery method, organizes the work, starts real execution, and truthfully reports the actual state.

## Black-box observations

The tester observes only externally visible behavior while conversing:

- Did implementation begin before approval?
- After approval, did the project board and execution history appear without the tester teaching internal mechanics?
- Are progress, failure, rework, and delivery understandable?
- When a test finds a defect, is code actually changed and the failed behavior retested?
- Does the product run and satisfy the agreed human requirement?
- Are success claims supported by real executable results?

## Audit observations kept out of prompts

After each turn, the tester may inspect API records, logs, artifacts, and usage data to verify truthfulness. These checks are never inserted into the Conversation Agent prompt. They include premature entity creation, execution topology, feedback consumption, verification receipts, repeated failed operations, Mailbox behavior, API calls, and Tokens.

## Pass criteria

1. Discussion remains discussion until the ordinary “start” request.
2. DevWerk independently creates a coherent, executable project process after approval.
3. Work is observable at human-meaningful stages and reaches a truthful terminal state.
4. Defects cause real repair and retest rather than prose-only acknowledgement.
5. The delivered product matches the agreed behavior and can be started and verified as promised.
6. Internal coordination does not require the user to know DevWerk architecture.
7. Model/API usage is measurable and does not contain uncontrolled notification or correction loops.

## Stop conditions

- Stop and record a framework failure if the system cannot start work without internal coaching.
- Stop and record a framework failure if it repeatedly submits the same rejected operation.
- Stop and record a delivery failure if the process finishes but the product does not meet the agreed requirement.
- Long-running work may continue unattended only after real execution has started and no framework P0 is present.
