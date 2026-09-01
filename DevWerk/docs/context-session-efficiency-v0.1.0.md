# DevWerk Context and Session Efficiency — v0.1.0

Status: **Normative / Release Optimization**
Version: **0.1.0**
Date: **2026-08-27**

## 1. Purpose

This design reduces provider calls and active-context size without changing domain workflows. It applies equally to novel production, software delivery, research, operations, and future Loops. Runtime remains aware only of Projects, Tasks, Columns, capabilities, Artifacts, Memory, Sessions, Workcells, and evidence.

Full conversation, Agent, tool, event, and Artifact evidence remains durable and queryable. Optimization changes the active context projection, not the audit record.

## 2. Active Context Projection

An Agent receives the smallest authoritative projection needed for its current activation:

1. immutable runtime and Loop definition;
2. current Project bindings and Task input;
3. declared upstream outputs;
4. Loop-selected preloaded Artifacts and Memory records;
5. the current Workcell node and subscribed Handoffs;
6. a compact logical Session checkpoint when the participant is reactivated.

Preloaded text carries path, content hash, character count, and content. The context manifest explicitly identifies it as authoritative for the frozen activation. An Agent does not list or read the same path again unless it is missing, was modified after the snapshot, or an independent post-write verification is required.

The consumption rule is part of the Runtime protocol, not an optional hint. A preloaded Loop asset is read from `project.loop.assets`; it is not a Project filesystem path. A preloaded Project Artifact is read from the activation projection. File capabilities are reserved for paths absent from the projection, paths known to have changed, or explicit independent verification.

Workcell context is projected again before every participant activation so that newly written Artifacts and their hashes are visible to the next participant. Runtime does not reuse the stale Column-entry projection for the entire Workcell.

Loops continue to choose Artifact globs and Memory selectors. Runtime does not choose files based on a domain, Column name, or prompt text.

## 3. Capability Receipts

A successful text write returns a deterministic receipt containing:

- path;
- byte size;
- UTF-8 character count;
- non-whitespace character count;
- line count;
- content hash.

The receipt is valid completion evidence. An Agent should not call a separate measure capability merely to rediscover facts already established by the write receipt. Independent verification remains available when a Loop requires it or an external actor may have changed the file.

## 4. Conversation Session Projection

SQLite retains complete internal Agent messages and tool results. The next Conversation turn replays the human conversation projection only:

- user messages before the current request;
- user-visible Conversation Agent replies.

Internal assistant tool-call messages, raw tool results, silent mailbox processing, failed status bubbles, and notification duplicates are not replayed as dialogue. Current Workflow, Task, mailbox, Memory, and governance state is rebuilt from authoritative stores on every turn.

This preserves a complete long-lived Project conversation while preventing internal execution evidence from becoming conversational context.

The provider-visible Session has a stable prefix: Conversation identity, instruction revision, platform policy, and capability schemas. Mutable Project metadata and rebuilt authoritative state are appended in the current Turn input rather than embedded in the system message. Capabilities are not hidden when Project state changes; a discussion-only Turn and other authority rules are enforced at dispatch and returned as structured tool results. This preserves the same main-Agent identity and improves provider prompt-prefix reuse.

The structure follows the common boundary verified in the reference agents: Codex separates a durable Session from a Turn and continues a Turn whenever tool output requires follow-up; Hermes keeps a byte-stable conversation prefix, persists complete tool-call/result history, and treats gateway input as new Turns; Claude Code's public repository does not expose its production loop source, but its published behavior confirms resumable sessions, streamed tool activity, and recovery that removes malformed tool output from retry context. DevWerk adopts these boundaries without importing their product-specific safety, UI, or autonomous-agent policies.

## 5. Participant Session Checkpoint

When a Column or Workcell participant completes through a tool call, Runtime persists a compact checkpoint containing its declared outcome, summary, and structured output even if the provider returned no natural-language text. A Workcell participant reports semantic completion through `workcell.complete`; it does not emit a routing signal or assemble evidence identifiers. Runtime collects successful action evidence from the current Agent Run, maps the declared outcome to the Workcell transition, persists the directed Handoff, and advances the Workcell state machine internally.

On reactivation, active context includes:

- the latest Agent Run checkpoint;
- the latest successful checkpoint when the most recent run failed;
- current Task, Artifact manifests, Memory, Workcell, and directed Handoff state;
- current content-addressed manifests for Loop-selected inputs.

The first activation receives the participant's Loop-declared full context projection. A persistent Session reactivation receives a compact resume projection: Project and Loop identity, bindings, Task identity and inputs, current manifests, Memory selection, and directed Handoffs. It does not receive Loop asset bodies or Artifact bodies a second time. The participant fetches only a specific missing or changed Project path when the checkpoint, manifest, and directed Handoff are insufficient.

All older Agent Runs remain in SQLite for audit but are not replayed into active context. This keeps repeated review/repair cycles bounded while preserving the same logical Session identity.

## 6. Memory Role

File Memory remains the semantic source of durable Project knowledge. Workcell snapshots remain compact, content-addressed recovery state. Session checkpoints are execution continuity, not a replacement for semantic Memory.

The Runtime records which Memory records and preloaded Artifacts entered each activation. Empty core Memory files are not injected. A Loop may declare Memory selectors; the architecture does not invent domain memories or make a separate LLM call for memory capture.

## 7. Acceptance Invariants

1. Text write receipts make redundant measure calls unnecessary.
2. Preloaded Artifact manifests are hash-addressed and visible to every executor kind.
3. Conversation replay preserves human dialogue and excludes raw tool traces.
4. Participant reactivation receives a non-empty compact checkpoint for tool-based completion.
5. Active participant history is bounded by semantic role rather than an arbitrary turn count.
6. Complete execution evidence remains queryable in SQLite.
7. No Runtime branch refers to novels, code, GitLab, Writer, Reviewer, or another domain role.
8. Existing Loop graphs and terminal semantics remain unchanged.
9. Every Workcell participant sees an activation-time Artifact projection rather than a stale Column-entry snapshot.
10. A persistent participant reactivation does not re-inject unchanged static content.
11. Workcell routing signals and action-evidence assembly are Runtime responsibilities, not model-generated protocol data.
12. Conversation identity, instruction, platform policy, and capability schemas form a stable Session prefix; current Project state is supplied as the authoritative Turn input.
13. An unsupported Conversation mutation claim may continue only after a new successful state-changing tool receipt. Repeated prose or read-only calls at the same execution progress fail explicitly without an arbitrary retry count.
14. The first unsupported mutation claim changes the next Provider request to a required-tool continuation and exposes only the claimed capabilities. Ordinary discussion remains tool-optional. A Provider that ignores the required tool choice, or repeats the same failed operation without materially different arguments, terminates with an explicit protocol failure and a human-readable Conversation reply.
