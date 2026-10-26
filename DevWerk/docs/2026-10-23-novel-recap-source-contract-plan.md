# Novel recap source contract and chapter 6 diagnosis

## Scope and ownership

The external `novel.production` Loop owns which story sources each Column needs. The generic Runtime owns faithful selection and validation of the frozen Column input. The current `recap` selector preloads all baseline versions registered by the current Task, but later chapters revise only some baseline files. Chapter 3 therefore received two of six baseline files without an input-contract failure. That is an incomplete source set, not a shorter valid baseline.

The required recap sources are different at the root and at a successor Task: the root uses the six baseline files; a successor uses the immediately preceding done Task's accepted recap and final chapter. Loading baseline revisions into every successor is unnecessary and can silently produce a partial baseline. No scenario-specific logic belongs in Runtime.

## Small change

1. Add one generic, opt-in `ContextSelection` field for working artifact globs used only when a Task has no direct dependencies. Keep existing `working_artifact_globs` behavior unchanged for every other Workflow. In Runtime, select these current-Task registered versions only for a root Task, with the existing hash, size and file-count checks.
2. In the Loop's `recap` Column, use that root-only selector for `baseline/*.md`; use the existing direct accepted-dependency selector for `summaries/*.md` and `chapters/*.md`. Successor recaps then receive only the immediately preceding accepted recap and final body as historical content.
3. Declare a JSON Schema `input_contract` in the Loop: with no direct dependency, all six specific baseline paths must be present in `working_artifacts`; with a direct dependency, both an accepted summary and an accepted chapter body must be present. Runtime validates this before calling the Column Agent. A missing or budget-excluded source is therefore a visible input failure rather than a silent partial prompt. This contract belongs to the Loop and introduces no novel-specific Runtime checks.
4. Align the Loop's plan and Column instructions with the two source cases. The rolling recap keeps durable facts from the prior recap and adds changes from the prior final chapter. It does not load every earlier chapter by default. Existing review/rework and accepted-content checks stay unchanged.

## Verification

- Focused domain and Runtime tests for root-only selection, successor exclusion, and default selector compatibility.
- Loop contract tests for all six root sources, both successor sources, and rejection when any required source is absent. Check that later recap input has no baseline content.
- Run focused tests only. Existing published Project Workflows are frozen; this Loop change applies to a newly published revision, and no live Task or service is restarted.

## Chapter 6: diagnosis only

Task `tsk_affab36349e840eea76b7af420e1379a` stopped in `review` with `RuntimeError: Column Agent ended without calling column.complete`. AgentRun `arun_309cd7182661406c93dfee8b21c2421a` successfully read/measured inputs and wrote a review file. The next three MiniMax-M2.7 HTTP responses were 200 with `stop_reason=tool_use`, but their content had no `tool_use` block (the first two had only `thinking`, the third had `content=null`). The Anthropic adapter legitimately normalized them to no text and no tool calls. The Column runner sent two completion reminders, then raised after the third empty response; recovery classified this as `blocked_runtime`. No repair, retry, or state change is in scope for this Task.

The database confirms `status=recovering`, `control_state=paused`, `current_column=review`, and `failure_disposition=blocked_runtime` at 2026-10-23 13:19:54 UTC. The final AgentRun had four successful tool calls followed by three empty assistant turns. This is a Provider response/protocol failure at completion time, not a context-window rejection, a 600-second request timeout, or a reviewer route decision. The review file write did not itself advance the Workflow; only a committed `column.complete` could do so.

### Why the reminders did not complete the Column

The raw logged requests for the three empty responses did expose `column.complete` among eight tools. The first followed the successful `project.files.write` receipt; the next two included the Runtime's 246-character instruction to call `column.complete`. All three requests had no `tool_choice`, because the Runner's `require_tool` switch applies only to certain Conversation Agent replies. Thus the reminders were text instructions, not a forced tool selection. The Provider returned HTTP 200 and `stop_reason=tool_use` three times, but the first two `content` arrays contained only a `thinking` block and the third had `content=null`. A `tool_use` stop reason without a `tool_use` block is internally inconsistent. The adapter extracts only text and actual `tool_use` blocks; each response became empty. The Runner drops empty assistant messages when it reconstructs Provider history, so the third request contained the two reminders merged into one user message after the same tool result. It did not have a new tool action to react to.

There is also a confirmed DevWerk protocol defect that may contribute to this class of failure: after a MiniMax tool-use response, `AnthropicClient.complete` discards `thinking` blocks and their signatures, and `_to_provider_messages` reconstructs only text and tool calls. The immediately preceding write response included a 28,877-character `thinking` block; the next request's assistant message contained only its `tool_use` block. MiniMax's official interleaved-thinking guidance says to return the complete assistant output, including thinking blocks, in subsequent Anthropic-compatible requests. This loss can weaken multi-step reasoning. The available log cannot prove it alone caused MiniMax to omit the three tool blocks; the Provider's internal generation/serialization is not observable. The adapter also fails to reject a `tool_use` stop reason without a tool block as a Provider protocol error, so the Runner treats it as an ordinary empty Column response and exhausts its three-attempt limit. Two similar malformed responses occurred earlier in this Project's chapter-2 work and were followed by valid tool calls, confirming that the anomaly is intermittent.

Sources: [MiniMax interleaved-thinking guidance](https://www.minimax.io/news/why-is-interleaved-thinking-important-for-m2), [Anthropic tool-use thinking round trip](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows).

An isolated follow-up on 2026-10-24 is recorded in [the thinking round-trip replay note](2026-10-24-minimax-thinking-roundtrip-replay.md). Two harmless synthetic A/B pairs both returned valid tool calls with and without preserved thinking. This does not establish that the thinking omission caused the chapter-6 failure; the exact logged payload was not sent because automatic approval review rejected that sensitive replay.

## Implemented and checked

- `ContextSelection.root_working_artifact_globs` is optional and defaults to empty; existing selectors retain their behavior. Runtime applies it only when `dependencies` is empty.
- Loop version `1.5.2` selects all six root baseline files or the direct predecessor's accepted summary and final body. Its input contract rejects missing or excluded required source content before the Agent runs. The successor context excludes a partially revised baseline.
- Focused Loop tests: 23 passed. Runtime, domain, and Task-entry tests: 59 passed. `git diff --check` passed. No live Project was restarted or altered; this revision cannot retroactively change the already frozen chapter-6 Workflow.
