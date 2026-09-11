# Conversation Agent Generic Tool Loop v0.1.0

## Problem

The Conversation Agent currently contains a second, implicit Workflow engine. It
searches user and assistant prose for phrases such as `派发` or `create task`, maps
those phrases to `task.create`, exposes a hard-coded prerequisite list, forces a
specific next tool, and rejects a final response when the inferred receipt is
missing.

That mechanism is not a general Agent protocol:

- natural language is ambiguous and multilingual;
- `task.create` and its prerequisites are DevWerk business operations, not model
  protocol rules;
- the hidden sequence can disagree with the active Loop and durable Project state;
- adding a new capability requires editing Agent Core;
- a model can be forced into repeated invalid calls instead of being allowed to
  inspect an error and choose another action.

## Reference model

The useful common boundary in Codex and Hermes is a generic tool loop:

1. provide the stable conversation/session context and currently available tool
   schemas;
2. accept either a model tool-call batch or a tool-free final response;
3. validate and execute tool calls in the tool/runtime layer;
4. append exact tool results to the same turn;
5. continue until the model returns a tool-free response, waits, or encounters an
   explicit protocol/runtime failure.

Claude Code's public extension examples follow the same separation: reusable
methods and deterministic operations live in Skills, scripts, and hooks rather
than in a business-specific main-agent state machine.

## Authority boundaries

### Conversation Agent kernel

Owns the Project Session, Provider messages, tool-call/result pairing, audit
records, and generic no-progress detection. It exposes the full capability set
granted to the turn. It does not infer a required capability from prose, filter
tools according to a built-in business sequence, or force a named capability.

A tool-free assistant response ends a Conversation turn. Such prose never mutates
Project state and is never treated as proof that a mutation happened.

### Capability and persistence boundary

Owns input-schema validation, authorization, idempotency, immutable receipts, and
domain invariants. For example, `task.create` may reject a missing Task Plan and
initial Workflow creation may reject any path other than `loop.apply`. The model
receives that exact rejection and may inspect, repair its arguments, choose a
different tool, or report the blocker.

### Loop and Workflow Runtime

Own reusable method selection, Task contracts, Column graph transitions,
dependency/WIP admission, retry/recovery, and terminal state. Neither the
Conversation Agent kernel nor natural-language keywords define those rules.

### Loop library

Loops remain filesystem assets under `loops/<directory>/` with `loop.meta`,
`loop.json`, and optional assets. Reading or authoring files is a capability of the
Conversation Agent's environment; applying an initial Workflow remains the
validated `loop.apply` operation. Asset lifecycle support may be added as a
separate generic Loop-library capability, but must not become an ordered
Conversation state machine.

## Generic safety invariant

The kernel retains one protocol-level progress rule: an identical failed
state-changing tool call with identical arguments cannot be submitted repeatedly
in the same logical ledger. This rule is capability-agnostic and prevents an
unbounded Provider loop without deciding which business operation should happen
next. A changed operation, a successful receipt, a read/inspection, or a final
explanation remains available to the model.

## Implementation scope

This change:

1. removes natural-language mutation-claim parsing;
2. removes hard-coded capability prerequisites and forced tool selection;
3. removes `execution_obligation` from the user-turn envelope;
4. keeps the complete granted tool surface stable during a Conversation run;
5. keeps durable tool receipts and the generic identical-failure guard;
6. updates contract tests to assert the generic boundary.

It does not change Workflow/Task state machines, Loop definitions, database
schemas, or Column completion admission.

## Acceptance criteria

- Agent Core contains no `task.create`, `loop.apply`, Chinese action phrase, or
  capability-order table.
- A Conversation response may freely inspect, mutate through granted tools, or
  finish with text; only successful tool receipts change durable state.
- A failed tool result is returned to the model for repair.
- Repeating an identical failed mutation is stopped deterministically.
- Capability and Store tests continue to enforce Loop and Task admission rules.
