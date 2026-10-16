# Ordinary-user Software Delivery Live Regression

**Date:** 2026-09-21 (Asia/Shanghai)  
**Plan:** [Ordinary-user Software Delivery Black-box Test Plan](2026-09-21-ordinary-user-software-black-box-test-plan.md)  
**Project:** `prj_b3b364ba5d97483691a9fdcef6bf0c6c`  
**Workspace:** `D:\workspace\codex-devwerk-project-files\ordinary-user-software-test-20260921-183009`

## Scope

This is the valid clean-room black-box regression. No prior Project, conversation, Workflow, Task, or workspace is reused. User messages use ordinary product language and do not disclose DevWerk's internal orchestration terminology.

## Conversation record before start

| Turn | Job | User intent | Result | Premature work |
|---:|---|---|---|---|
| 1 | `cjob_eca58a4a86f64816a810d41ed3f53acb` | Describe Vue/Spring Boot in-memory login product and ask for important questions. | Asked about login state, errors, validation, welcome page, security, and API/CORS. | none |
| 2 | `cjob_f49b034695ec4802af2d9dd90145b8eb` | Decide Session-Cookie, validation, password hashing, welcome page; ask for rate-limit recommendation. | Recommended registration and login rate limits and BCrypt. | none |
| 3 | `cjob_68e2cee263fb471fa8f2b1f522f496e2` | Require Origin protection and ask for API behavior and browser journeys. | Proposed API outcomes and browser coverage. | none |
| 4 | `cjob_837ceef721e144f5be5f79e32ab64b3e` | Correct login failure to 401; describe one-command start/test/cleanup expectation. | Recorded correction and identified real port/test/tool/cleanup gaps. | none |
| 5 | `cjob_7750a4cbd00b442998109b3a9cee1111` | Resolve ports, test types, Playwright, Origin probe, and process cleanup; ask for final summary. | Produced a human-readable delivery/verification summary and declared no blocker. | none |

All five turns used discussion mode. API inspection after every turn showed zero Workflow and Task IDs. DevWerk did not begin work based on its own suggestion to start.

## Execution authorization

The only start instruction was ordinary user language:

> 这个方案符合预期。现在开始这个项目，并持续推进，直到产品成功交付；如果确实遇到必须由我决定的阻塞，再明确告诉我。

Conversation Job `cjob_e03733c4279946c9be5dc085969c6e60` succeeded and created real execution state without architecture coaching:

- Task: `tsk_275c1ad9215c42ddb0b532c0d78d40d1`;
- Task Plan: `tplan_ff3dc702c3424790890604f436f88838`;
- Workflow Plan: `wfplan_4f282c4529b045e087ba2788cdee9202`;
- Task-bound Workflow revision: `wfrev_55d00e8aa2e643378a9e3e29f495d5cb`, revision 4;
- initial status: `running`;
- initial stage: requirements;
- Workflow stages exposed by the public API: requirements, domain design, backend development, frontend development, integration review, system test, delivery, final acceptance;
- seven persisted acceptance obligations.

The start response was concise and factually backed by the Task ID. No stage-sized duplicate Tasks were created.

## Runtime execution

### Requirements activation

The Task entered requirements with Column Run `run_6f27bf93b68d47d5a3c6400be626d33a` and Agent Run `arun_6c0eb7ac60e44431bb46880454fa2c8a`. The current Task input references the accepted `docs/requirements.md` baseline and the same Task is expected to traverse the full lifecycle.

_Further runtime, feedback, artifact, terminal, and usage evidence is appended as execution proceeds._

### Monitoring handoff

At the user's request, active observation stopped while DevWerk continues independently. No pause, retry, conversation message, or Task state mutation was issued.

Last confirmed state before handoff:

- Task `tsk_275c1ad9215c42ddb0b532c0d78d40d1`: `running`;
- requirements: succeeded on attempt 1;
- domain design: succeeded on attempt 1;
- backend development: running on attempt 1;
- no duplicate stage Tasks;
- no observed rework cycle yet.

Final delivery, feedback lifecycle, acceptance evidence, and usage remain pending for the later audit.
