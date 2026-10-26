# MiniMax timeout request streaming replay

## Purpose and boundary

This is an independent transport experiment. It replays exact `llm.provider_request` payloads from the six recorded MiniMax-M2.7 read timeouts in Project `prj_b7e19c05f54f4177bd330037ea68daa4`, adding only `stream: true`. It does not change DevWerk's Provider adapter, workflow, Task, or running service. Returned tool calls are recorded as data and never executed.

## Method

Use selected trace IDs from distinct chapters and request sizes. Extract the payload directly from `data/logs/devwerk.log` by trace ID without copying prompt text into this document or result files. Use DevWerk's configured MiniMax endpoint and credential. Parse complete SSE events, including `message_start`, `content_block_delta`, `message_delta`, `message_stop`, `ping`, and `error`. Record HTTP headers/status, time to headers, first event and first content, largest event gap, total time, token usage, terminal state, and event counts. Enforce a bounded read-idle timeout and a separate overall deadline. Persist metrics only.

Compare the replay measurements with the original non-streaming calls, each of which failed at about 600 seconds. A stream with progress and a terminal `message_stop` establishes that streaming prevents this particular full-response wait from timing out. A stream that starts but does not finish only improves diagnosis, not delivery. A replay is not a controlled performance benchmark because Provider load and generation are nondeterministic.

## Results

Three historical `LLM_TIMEOUT` requests were replayed against MiniMax-M2.7 on 2026-10-23. The original log records a 600-second read timeout for each. The replay kept the logged model, messages, tools, and generation parameters; it changed only `stream` to `true`. A complete response means an HTTP 200 stream ended with `message_stop`.

| Original trace / chapter | UTF-8 JSON / HTTP body | First event | First content delta | Largest event gap | Complete response | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `llm_844fc3729eb744a9816740734c5db34f` / ch02 | 105,408 / 169,766 B | 1.735 s | 2.000 s | 0.985 s | 56.688 s | `tool_use`, 27,252 input / 4,749 output tokens |
| `llm_7ceeeb5f61e14cf390ca1f8a892ea4d2` / ch05 | 44,877 / 64,638 B | 1.687 s | 1.687 s | 1.313 s | 160.797 s | `tool_use`, 11,622 input / 9,744 output tokens |
| `llm_758f2edd43e149abae6680cef2355c97` / ch06 | 48,053 / 72,401 B | 1.156 s | 1.484 s | 0.657 s | 233.156 s | `tool_use`, 12,359 input / 16,688 output tokens |

All three streams completed well before the original 600-second read timeout, with continuous event progress. This is a clear improvement for these replayed requests and supports replacing the full-response read wait with streaming in the Provider adapter. It does **not** prove streaming makes generation faster or that all future timeouts disappear: these are new, nondeterministic generations rather than simultaneous non-streaming controls. A first event alone is insufficient; DevWerk must wait for `message_stop`, assemble and validate the full response, and treat a mid-stream error or missing terminal event as incomplete. Returned tool calls were not executed, so this experiment does not establish Task completion.

The independent script is `tools/minimax_stream_timeout_demo.py`; metrics are in `.tmp/minimax-stream-timeout-demo.json`. The script does not persist prompt text, model output, or credentials. Validation: dry-run extraction of all three logged payloads, successful live completion of all three, and Python compilation of the script. DevWerk's running service and Provider adapter were not changed.

## Input-size audit

The first number in the table is an unescaped UTF-8 JSON measurement. `requests.post(json=...)` escapes non-ASCII text when preparing the actual HTTP body, so its byte length is larger. These byte counts are **not token counts**. The three original logged payloads were 105,392 / 44,861 / 48,037 UTF-8 JSON bytes; adding `stream: true` accounts for the 16-byte difference in the replay table. The actual non-streaming HTTP bodies would be 169,750 / 64,622 / 72,385 bytes under the current `requests` serializer.

The 2× difference is predominantly a **different Column context selection**, not a larger ch02 recap or accumulated model chat. Each request had only three provider messages: one assignment frame, a pair of `project.files.measure` calls, and their two results. The tool results were about 2 KB total.

| Context component (unescaped UTF-8 JSON, approximate) | ch02 `review` | ch05 `authoring` | ch06 `authoring` |
| --- | ---: | ---: | ---: |
| Preloaded accepted artifacts | 39.7 KB: six baseline files and ch01正文 | none | none |
| Preloaded current-Task artifacts | 18.3 KB: ch02提要、正文、审查报告 | 10.1 KB: ch05提要 | 13.7 KB: ch06提要 |
| Project Loop envelope | 7.1 KB, including all ten method assets | 7.1 KB | 7.1 KB |
| Task context | 6.1 KB | 5.1 KB | 4.2 KB |
| Context manifest | 6.9 KB | 3.4 KB | 3.4 KB |
| Tool schemas / system prompt | 6.5 / 3.8 KB | 5.4 / 3.5 KB | 5.4 / 3.5 KB |

These rows are nested components rather than an additive accounting of the whole request. In ch02 `review`, `upstream_outputs` also repeated the same `authoring`, `foundation`, and `recap` objects already in `task.context` (another 3.7 KB). That duplication follows from `runtime.py`: each completed Column output is saved into Task context, then `_build_input` independently includes both Task context and the selected upstream outputs. The `review` selection in the novel Loop also requests `baseline/*.md` and `chapters/*.md` from **transitive** accepted dependencies. This loads prior chapter正文 even though the recap mechanism exists, and can grow with chapter count. The review may need historical evidence for a particular continuity question, but preloading all prior chapters is broader than that need.

In `authoring`, the absence of prior-Task artifacts confirms the earlier boundary change is operating for these two requests. The current-Task recap file itself is oversized: ch05 has 3,409 characters and ch06 has 4,649, with one expanded section of about 2,906 and 3,895 characters respectively. The Loop instruction asks for roughly 100 Chinese characters but the generated files are not bounded by that instruction. The Column also receives foundation output through `task.context`, the full Loop asset set through the `ContextSelection.include_loop_assets=True` default, and other Project/Task planning data. Some method and task facts are legitimate; they are not all recap text. The largest directly avoidable input here is the unbounded recap growth, followed by selective removal of context that a Column does not use. Neither input size alone nor this demo proves the cause of the original timeout: the largest input completed fastest in replay, while ch06 spent 233 seconds producing 16,688 output tokens, including 15,212 thinking tokens.

## Timeout-setting provenance

`--read-idle 90` and `--overall 720` are **experimenter-chosen demo limits**, not MiniMax or Anthropic API requirements. In `requests`, `timeout=(10, 90)` means a 10-second connection timeout and a 90-second socket-read idle timeout, not a 90-second total generation limit or a guarantee of an SSE event every 90 seconds. The demo's overall deadline is separately checked while consuming events. [Requests documents the between-bytes behavior](https://requests.readthedocs.io/en/stable/user/advanced/#timeouts). [MiniMax documents `stream` support](https://platform.minimax.io/docs/api-reference/text-anthropic-api), and [Anthropic recommends streaming for long requests](https://platform.claude.com/docs/en/api/errors#long-requests); neither document prescribes 90 seconds. Production values should be selected from observed first-byte and inter-event distributions, with a separate whole-request deadline and explicit handling of incomplete streams.
