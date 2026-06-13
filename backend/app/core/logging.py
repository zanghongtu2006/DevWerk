from __future__ import annotations

import logging
import os
import sys
from typing import Any


_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(config: Any) -> None:
    """Configure console logging early enough for uvicorn imports and app startup."""
    global _CONFIGURED

    level_name = str(getattr(config, "log_level", "debug") or "debug").strip().upper()
    level = getattr(logging, level_name, logging.DEBUG)
    fmt = str(getattr(config, "log_format", _DEFAULT_FORMAT) or _DEFAULT_FORMAT)
    try:
        logging.Formatter(fmt).format(logging.LogRecord("devwerk.logging", logging.DEBUG, "", 0, "probe", (), None))
    except Exception:
        fmt = _DEFAULT_FORMAT

    logging.basicConfig(
        level=level,
        format=fmt,
        stream=sys.stdout,
        force=True,
    )

    for name in (
        "",
        "devwerk",
        "devwerk.ide",
        "devwerk.kanban",
        "devwerk.usage",
        "devwerk.planner",
        "devwerk.coder_harness",
        "devwerk.prompt_builder",
        "devwerk.settings",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ):
        logging.getLogger(name).setLevel(level)

    logging.getLogger("devwerk.logging").debug(
        "logging configured level=%s format=%s configured_before=%s",
        level_name,
        fmt,
        _CONFIGURED,
    )
    _CONFIGURED = True


def configure_logging_from_env() -> None:
    class _EnvLogConfig:
        log_level = os.environ.get("LOG_LEVEL") or "debug"
        log_format = os.environ.get("LOG_FORMAT") or _DEFAULT_FORMAT

    configure_logging(_EnvLogConfig())
