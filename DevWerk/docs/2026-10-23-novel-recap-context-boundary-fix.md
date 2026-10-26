# Novel Loop: recap-to-authoring context boundary

## Problem and ownership

The `novel.production` Loop currently creates a recap from accepted chapters and then preloads those same chapter bodies into `authoring`. This defeats the recap's context-reduction purpose. The choice belongs to the external Loop's Column context and instruction, not to DevWerk's generic artifact-provenance mechanism. Existing published Workflow revisions remain frozen; the updated Loop applies to newly published revisions.

## Small change

1. Keep `accepted_artifacts` and `working_artifacts` in the generic Runtime. They distinguish completed dependency versions from mutable current-Task work. Add a typed `accepted_dependency_scope` selector with backward-compatible `transitive` default and optional `direct` selection.
2. Make `recap` use bounded rolling history after chapter 1: its `direct` selector preloads only the immediately preceding done Task's registered summary and body; chapter 1 still uses its current-Task story baseline. Older chapters may be read only to resolve a specific contradiction, not loaded by default. This deterministic selection avoids an expanding `task.inspect` response and does not depend on model-chosen file paths.
3. Make `authoring` receive only current-Task summary, current body and review feedback as file contents. Remove accepted-dependency artifact globs, raw `recap` upstream output, and the file-list/read tools from this Column, so it cannot silently reread previous chapters or baselines. Remove the cross-Task `agent_session_key`: default Task-local Worker reuse still preserves context across review/rework visits within one chapter, without carrying a prior chapter's Session handoff into the next. Retain fixed Loop method assets and Project bindings, which are not generated chapter artifacts.
4. Keep independent `review` access to accepted historical evidence and existing routing. Adjust Loop plan prose so it no longer promises full prior history to the writer.

## Verification

- Assert the Loop Column configuration and capabilities match these boundaries.
- Exercise Runtime context assembly with completed prior chapters: `recap` receives only the direct previous chapter's accepted recap and body, while `authoring` has the current recap but neither the previous chapters nor generated baseline.
- Run the focused Loop and Runtime context tests. No live Project or service restart is required; existing Project Workflow revision is unchanged.

## Result

- The updated external Loop is version `1.5.1`. The generic Runtime and artifact repository gained only the optional direct-dependency selector; their default transitive selection is unchanged.
- `recap` preloads only the direct done dependency's registered summary and body. `authoring` receives no accepted-dependency artifacts or raw recap output and has no file-list/read capability. Its Worker Session is Task-local while review/rework visits in the same Task can still reuse it.
- Workflow and context regression suite: 214 passed. After the final `authoring` input restriction, focused Loop and Task-local Worker tests: 5 passed. No live model run was performed; the already published novel Project retains its frozen old Workflow revision.
