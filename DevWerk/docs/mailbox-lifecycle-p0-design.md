# DevWerk Mailbox Lifecycle — P0 核心设计

**状态**：Normative / P0 Implementation Source of Truth  
**日期**：2026-08-29  
**适用范围**：Project Mailbox、Conversation Session Gateway、Workflow Scheduler

## 1. 目标

Project Mailbox 是 Project 内部组件向长期 Conversation Agent Session 传递已持久化事实的通信设施。它保存消息身份、来源、投递、接收、处理结果和显式重投记录；它不执行 Workflow，不决定 Task 路由，也不自行调用 LLM。

Mailbox 消息可以唤醒一个订阅它的 Conversation Session Gateway，但一次自动投递最多对应一个 Conversation Job。消费失败是该次投递的明确终态，不得通过轮询或 lease 过期自动形成新的 LLM 调用。

## 2. 模块边界

```text
Workflow Runtime / Recovery / Scheduler
        |
        | append durable fact
        v
MailboxService
  - message identity
  - delivery lifecycle
  - receipt and acknowledgement
  - explicit redelivery
        |
        | one pending batch / one durable delivery
        v
Conversation Session Gateway
  - serialize one Project Session
  - run at most one Conversation Job for the delivery
  - commit acknowledgement or delivery failure
        |
        v
Conversation Agent
  - explain facts
  - make semantic governance decisions when needed
  - never implement transport retry
```

### 2.1 MailboxService

MailboxService exclusively owns message creation/deduplication, pending selection, delivery/receipt/acknowledgement/failure transitions, immutable delivery-attempt history, explicit redelivery and lifecycle queries. It does not import or call AgentCore, Workflow Runtime or Scheduler.

### 2.2 Scheduler and Workflow Runtime

Scheduler and Workflow Runtime exclusively own dependency resolution, WIP/resource eligibility, Task admission, Column transition, terminal settlement and automatic downstream Task start. They may append a Mailbox fact after committing a state change, but never read Mailbox acknowledgement as a condition for Workflow progress and never ask Conversation Agent to perform deterministic scheduling work.

### 2.3 Conversation Session Gateway

Gateway is a Mailbox consumer, not part of Mailbox storage. It records delivery before a Job becomes runnable, records receipt when the Project Session begins processing, acknowledges only after the Conversation result is committed, and records a terminal delivery failure when the Job fails or the process is interrupted. It never changes a failed delivery back to pending implicitly.

### 2.4 Conversation Agent

Conversation Agent receives the current delivery as structured Project facts. It may report, diagnose or intervene. It does not control transport retry. Task terminal notifications are asynchronous: `done` or `failed` is already committed before Conversation Agent sees it.

## 3. Message lifecycle

Canonical states:

```text
pending      durable and eligible for first or explicitly requested delivery
delivered    bound to one durable Conversation Job
received     the bound Project Session started processing it
acknowledged consumer result/audited no-op committed successfully
failed       delivery ended unsuccessfully; no automatic redelivery
attention    explicit operator or user direction required; no automatic redelivery
```

Canonical transitions:

```text
pending   -> delivered
delivered -> received
delivered -> failed
received  -> acknowledged
received  -> failed
received  -> attention
failed    -> pending       explicit redelivery only
attention -> pending       explicit redelivery only
```

`acknowledged` is terminal. LLM usage limits, rate limits, provider timeouts, model protocol failures, tool failures and process interruptions change the active delivery to `failed` or `attention`; none creates a new Conversation Job automatically.

## 4. Delivery attempt record

`v1_project_mailbox` stores the current message aggregate. `v1_mailbox_deliveries` stores one immutable attempt per explicit delivery:

```text
v1_project_mailbox
  id / project_id / event_id / event_type / task_id / run_id
  payload_json / state / created_at / acknowledged_at
  last_error / delivery_count

v1_mailbox_deliveries
  id / project_id / mailbox_id / attempt_no / conversation_job_id
  state / delivered_at / received_at / finished_at / error
```

Invariants:

- `(mailbox_id, attempt_no)` is unique.
- One message has at most one active `delivered` or `received` attempt.
- One delivery is bound to exactly one Conversation Job.
- A Job may batch messages, but every message has its own delivery record.
- Failure preserves message and evidence; it does not erase or acknowledge them.
- Explicit redelivery creates a new attempt number after returning the message to `pending`.

## 5. Conversation Job integration

```text
select pending messages
-> create Conversation Job
-> atomically mark messages delivered and create delivery rows
-> claim Project Session
-> atomically mark deliveries received
-> run Conversation Agent once
-> acknowledge or fail the bound deliveries
```

A user message creates its own Job. If pending Mailbox messages exist when that Job is claimed, Gateway may bind them to the same Job once. This coalesces communication without creating another Job.

On Job failure, active deliveries become `failed` or `attention`, the Session lease is released, and no replacement Job is created. A later user turn can inspect current Project facts without silently reprocessing failed deliveries. Redelivery requires an explicit audited operation naming the message.

## 6. Scheduled review boundary

Scheduled review is a time trigger, not a Mailbox message. It follows the same single-activation rule:

```text
pending -> delivered -> observed
                     -> failed/attention
```

A failed due review is not left `pending`; otherwise the dispatcher would reproduce the same unbounded Conversation loop.

## 7. Startup behavior

- A queued Conversation Job and its `delivered` messages remain queued.
- A Job that was `running` when the process ended becomes failed.
- Its `received` deliveries become failed with `runtime_interrupted` evidence.
- No interrupted delivery is automatically returned to pending.
- Explicit redelivery remains possible after inspection.

## 8. Observability

Mailbox lifecycle is operational state and does not appear as conversation bubbles. API/audit projections expose current state, event/task/run correlation, delivery count, Job identity, delivered/received/finished timestamps and acknowledgement/failure reason. LLM usage remains linked through Conversation Job and Agent Run IDs.

## 9. P0 guard tests

1. A failed Mailbox-triggered Job leaves messages `failed`, not `pending`.
2. Repeated dispatcher ticks after failure create zero replacement Jobs and zero model calls.
3. An LLM usage-limit failure cannot form a retry storm.
4. Startup fails an interrupted delivery without redelivery.
5. Explicit redelivery is the only `failed/attention -> pending` path and increments history.
6. Success records delivered, received and acknowledged timestamps plus one delivery attempt.
7. Scheduler releases dependency-ready Tasks without reading Mailbox state or invoking Conversation Agent.
8. A later independent message still creates one new Job when an earlier message is failed.

## 10. V1 acceptance invariants

- Mailbox is a durable communication mechanism, not an LLM retry mechanism.
- Every automatic delivery has a finite, auditable lifecycle.
- Consumer failure is visible and terminal for that delivery.
- Workflow progress is independent of Mailbox consumption.
- Conversation Agent remains informed through one controlled delivery.
- No polling interval, claim expiry or restart can multiply one message into unbounded LLM calls.
