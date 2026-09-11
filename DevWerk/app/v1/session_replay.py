from __future__ import annotations

from typing import Any


def replayable_session_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay complete Turns while removing an interrupted tool-call tail."""
    replay: list[dict[str, Any]] = []
    index = 0
    while index < len(history):
        item = history[index]
        role = str(item.get("role") or "")
        if role != "assistant" or not item.get("tool_calls"):
            # Orphan tool results are invalid at a bounded context boundary.
            if role in {"user", "assistant"}:
                projected = {
                    key: item[key]
                    for key in ("role", "content", "tool_calls", "tool_call_id")
                    if key in item and item[key] not in (None, [])
                }
                if (
                    role in {"user", "assistant"}
                    and replay
                    and replay[-1].get("role") == role
                    and not replay[-1].get("tool_calls")
                ):
                    replay[-1]["content"] = (
                        str(replay[-1].get("content") or "").rstrip()
                        + "\n\n"
                        + str(projected.get("content") or "").lstrip()
                    )
                else:
                    replay.append(projected)
            index += 1
            continue

        calls = list(item.get("tool_calls") or [])
        expected_ids = {str(call.get("id") or "") for call in calls}
        tool_messages: list[dict[str, Any]] = []
        cursor = index + 1
        while cursor < len(history) and history[cursor].get("role") == "tool":
            tool_messages.append(history[cursor])
            cursor += 1
        returned_ids = {str(tool.get("tool_call_id") or "") for tool in tool_messages}
        if expected_ids and expected_ids.issubset(returned_ids):
            replay.append({
                "role": "assistant",
                "content": str(item.get("content") or ""),
                "tool_calls": calls,
            })
            replay.extend({
                "role": "tool",
                "content": str(tool.get("content") or ""),
                "tool_call_id": str(tool.get("tool_call_id") or ""),
            } for tool in tool_messages)
        elif str(item.get("content") or "").strip():
            replay.append({
                "role": "assistant",
                "content": str(item.get("content") or ""),
            })
        index = cursor
    return replay
