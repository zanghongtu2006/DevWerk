from __future__ import annotations

class ConversationProtocolStalled(RuntimeError):
    """The Conversation Agent repeated an operation without protocol progress."""

    error_code = "conversation_protocol_stalled"
    error_category = "protocol_permanent"
