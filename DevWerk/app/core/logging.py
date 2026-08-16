from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(config: Any) -> None:
    """Configure console and daily rotating file logging for the backend."""
    global _CONFIGURED

    level_name = str(getattr(config, "log_level", "debug") or "debug").strip().upper()
    level = getattr(logging, level_name)
    fmt = str(getattr(config, "log_format", _DEFAULT_FORMAT) or _DEFAULT_FORMAT)
    if fmt.strip().lower() == "json":
        fmt = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
    logging.Formatter(fmt).format(logging.LogRecord("devwerk.logging", logging.DEBUG, "", 0, "probe", (), None))

    formatter = logging.Formatter(fmt)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [console_handler]

    log_path: Path | None = None
    if _as_bool(getattr(config, "log_file_enabled", True), default=True):
        log_dir = Path(str(getattr(config, "log_dir", "./data/logs") or "./data/logs")).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_name = Path(str(getattr(config, "log_file_name", "devwerk.log") or "devwerk.log")).name
        if file_name != "devwerk.log":
            raise ValueError("DevWerk log_file_name must be devwerk.log")
        log_path = (log_dir / file_name).resolve()
        retention_days = _positive_int(getattr(config, "log_retention_days", 30))
        file_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8",
            delay=True,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.namer = _daily_archive_name
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    for name in (
        "",
        "devwerk",
        "devwerk.workflows",
        "devwerk.kanban",
        "devwerk.usage",
        "devwerk.code_context",
        "devwerk.settings",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ):
        logging.getLogger(name).setLevel(level)

    logging.getLogger("devwerk.logging").debug(
        "logging configured level=%s format=%s file=%s retention_days=%s configured_before=%s",
        level_name,
        fmt,
        str(log_path) if log_path else "disabled",
        _positive_int(getattr(config, "log_retention_days", 30)),
        _CONFIGURED,
    )
    _CONFIGURED = True


def configure_logging_from_env() -> None:
    class _EnvLogConfig:
        log_level = os.environ.get("LOG_LEVEL") or "debug"
        log_format = os.environ.get("LOG_FORMAT") or _DEFAULT_FORMAT
        log_file_enabled = os.environ.get("LOG_FILE_ENABLED") or "true"
        log_dir = os.environ.get("LOG_DIR") or "./data/logs"
        log_file_name = os.environ.get("LOG_FILE_NAME") or "devwerk.log"
        log_retention_days = os.environ.get("LOG_RETENTION_DAYS") or "30"

    configure_logging(_EnvLogConfig())


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _positive_int(value: Any) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("log_retention_days must be positive")
    return parsed


def _daily_archive_name(default_name: str) -> str:
    path = Path(default_name)
    match = re.fullmatch(r"devwerk\.log\.(\d{4})-(\d{2})-(\d{2})", path.name)
    if match is None:
        raise ValueError(f"Unexpected DevWerk log archive name: {path.name}")
    return str(path.with_name(f"devwerk.{''.join(match.groups())}.log"))
