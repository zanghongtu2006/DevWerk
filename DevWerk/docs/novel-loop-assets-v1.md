# Novel Loop Asset Boundary V1

## Purpose

The Novel Production Loop supplies a stable writing and review method while each Project and each chapter retain their own facts. A Column receives all three layers without copying the same fact into multiple owners.

## Three asset layers

### Loop method assets

Files under `loops/novel-production/assets/` are versioned with the Loop and included in its digest. They define reusable writing and review principles: how to build an outline, scene, dialogue, symbol, pacing, supporting-character, emotion, anti-AI-style, body-description, and era decisions. They never name a particular novel, character, chapter, or fixed scene sequence.

Runtime exposes these read-only files as `project.loop.assets` to every Column Agent.

### Project story baseline

The `foundation` Column derives one chapter-independent baseline from Loop bindings and method assets. It stores only facts true across the novel:

- overall outline and long arc;
- world and era rules;
- character canon and relationships;
- symbol system;
- project style decisions.

The baseline lives under `baseline/`. It must not prescribe a current chapter's scene beats, cast, emotional curve, or recap.

### Task chapter context

Each chapter Task owns its chapter number and artifact paths. The recap, current objective, scene choices, emotional movement, permitted characters, clues, draft, and review feedback remain chapter-local. Rework follows the Workflow graph and preserves the Writer session and Reviewer feedback.

## Scheduling meaning

`TaskPlanReadiness.queue` means planned automatic waiting. A Task is admitted immediately when dependencies are already satisfied, or receives `auto_admit` while dependencies remain. A deliberate human or operational stop uses the existing explicit `hold` scheduling state.

Starting a Task Plan materializes its complete Task graph exactly once. The requested item is returned to the caller; all other items immediately become visible Tasks and wait on their declared dependencies and WIP policy. Kanban, rather than Conversation Agent mailbox turns, owns their subsequent admission.

## Task Plan persistence

`task.plan.save` is an immutable write boundary, not a validation probe. Conversation Agent submits the complete user-owned plan. A rejected call has no effect and is corrected by resubmitting that same plan; placeholder plans are not persisted.

## V1 invariants

- Loop assets participate in the Loop digest.
- Runtime rejects assets whose current digest differs from the Project binding.
- Loop method assets are read-only runtime context.
- Project baseline is chapter-independent.
- Chapter decisions and feedback remain Task-owned.
- Dependency/WIP queueing progresses without Conversation Agent intervention.
