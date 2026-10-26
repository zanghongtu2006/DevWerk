# Anthropic-compatible thinking round-trip fix

## Boundary and cause

The Anthropic adapter owns provider wire content. It currently reads `text` and `tool_use` from a response but drops `thinking` and `redacted_thinking`. The next tool turn is reconstructed from normalized text and tool calls, so MiniMax never receives the complete assistant output it required the client to replay. The Agent runner owns the working transcript; the message store owns durable transcript records; context replay and compaction own complete tool batches. None should interpret the thinking text or expose it as conversation progress.

## Small change

1. Define typed Anthropic assistant content blocks, including signed thinking and redacted thinking. Validate provider output at the adapter boundary. Preserve block order and signatures as an opaque `provider_content` on normalized model responses.
2. Carry that content into the runner's assistant message and durable message record. The Anthropic adapter reuses the validated content for a tool turn; other adapters continue using normalized text and calls. If the runner must normalize a tool-call ID, align the corresponding replay block ID with the executed call.
3. Keep `provider_content` when replaying a complete assistant/tool batch. A compacted batch is replaced as a whole, including its thinking; it is never included in a user-facing report or summary text.
4. Test response parsing, ordered round-trip, persisted replay, malformed blocks, and unchanged behavior for responses without thinking. Run focused tests, then the relevant agent/context tests. No service restart or black-box project test in this change.

This addresses protocol fidelity, not a proven causal explanation for every empty `tool_use` response. The prior synthetic A/B replay was not an exact production replay.

## Verification

The Anthropic provider tests and Agent context/session tests passed together: `77 passed`. They cover ordered signed-thinking replay, redacted thinking, tool-call ID normalization, durable storage, public-message exclusion, and existing context behavior. No live MiniMax replay or running-service restart was performed.
