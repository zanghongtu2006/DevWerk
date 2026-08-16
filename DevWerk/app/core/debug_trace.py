"""Unredacted V1 pre-release debug tracing.

The Version 1 release baseline intentionally records complete local inputs and
outputs so Conversation Agent and Kanban failures can be reconstructed from the
log file. Production redaction and log minimization are post-V1 work.
"""

from __future__ import annotations

import json
import logging
from typing import Any


def trace_json(logger: logging.Logger, event: str, **payload: Any) -> None:
    """Write one complete, single-line JSON trace record at DEBUG level."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug(
        "%s %s",
        event,
        json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")),
    )
