from __future__ import annotations

import json
from typing import Any

from app.kanban.store import compress_conversation_messages, get_conversation, get_project_settings


DEFAULT_CONTEXT_BUDGET = 24_000
DEFAULT_RECENT_MESSAGES = 10


def prepare_conversation_context(task_id: str, *, fallback_messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a resumable prompt transcript and compact it before it exceeds the configured budget."""
    conversation = get_conversation(task_id)
    if not conversation:
        return {"messages": list(fallback_messages or []), "summary": "", "token_estimate": 0, "compressed": False}

    settings_payload = get_project_settings(conversation["project_id"])
    project_settings = settings_payload.get("settings") if isinstance(settings_payload, dict) else {}
    parameters = project_settings.get("parameters") if isinstance(project_settings, dict) else {}
    budget = _positive_int((parameters or {}).get("context_budget_tokens"), DEFAULT_CONTEXT_BUDGET)
    keep_recent = _positive_int((parameters or {}).get("context_recent_messages"), DEFAULT_RECENT_MESSAGES)
    messages = conversation.get("messages") or []
    active = [item for item in messages if not item.get("compressed")]
    estimate = _estimate_messages(active) + _estimate_text(conversation.get("summary") or "")
    compressed = False

    if estimate > budget and len(active) > keep_recent:
        old = active[:-keep_recent]
        summary = _merge_summary(conversation.get("summary") or "", old)
        compress_conversation_messages(
            task_id,
            through_sequence=int(old[-1]["sequence"]),
            summary=summary,
            token_estimate=_estimate_text(summary) + _estimate_messages(active[-keep_recent:]),
        )
        conversation = get_conversation(task_id) or conversation
        active = [item for item in conversation.get("messages") or [] if not item.get("compressed")]
        estimate = _estimate_text(conversation.get("summary") or "") + _estimate_messages(active)
        compressed = True

    prompt_messages: list[dict[str, str]] = []
    summary = str(conversation.get("summary") or "").strip()
    if summary:
        prompt_messages.append(
            {
                "role": "system",
                "content": "conversation_memory:\n" + summary,
            }
        )
    prompt_messages.extend(
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in active
        if str(item.get("content") or "").strip()
    )
    if not prompt_messages:
        prompt_messages = list(fallback_messages or [])
    return {
        "messages": prompt_messages,
        "summary": summary,
        "token_estimate": estimate,
        "budget": budget,
        "compressed": compressed,
    }


def _merge_summary(existing: str, messages: list[dict[str, Any]]) -> str:
    """Loss-minimizing deterministic fallback; a future compressor agent can replace this function."""
    lines: list[str] = []
    if existing.strip():
        lines.append("Previous summary:\n" + existing.strip())
    lines.append("Conversation decisions and evidence:")
    for item in messages:
        role = str(item.get("role") or "unknown")
        message_type = str(item.get("message_type") or "message")
        content = " ".join(str(item.get("content") or "").split())
        if len(content) > 1200:
            content = content[:1200] + "..."
        lines.append(f"- [{role}/{message_type}] {content}")
    return "\n".join(lines)[-16_000:]


def _estimate_messages(messages: list[dict[str, Any]]) -> int:
    return sum(_estimate_text(str(item.get("content") or "")) + 8 for item in messages)


def _estimate_text(value: str) -> int:
    if not value:
        return 0
    ascii_chars = sum(1 for char in value if ord(char) < 128)
    non_ascii = len(value) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def context_debug_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_count": len(context.get("messages") or []),
        "summary_chars": len(str(context.get("summary") or "")),
        "token_estimate": context.get("token_estimate"),
        "budget": context.get("budget"),
        "compressed": bool(context.get("compressed")),
    }


def serialize_context(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
