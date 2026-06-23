from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from types import SimpleNamespace

from app.core.logging import configure_logging


def test_configure_logging_writes_utf8_daily_rotating_file(tmp_path):
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    config = SimpleNamespace(
        log_level="debug",
        log_format="%(levelname)s [%(name)s] %(message)s",
        log_file_enabled=True,
        log_dir=str(tmp_path),
        log_file_name="backend.log",
        log_retention_days=7,
    )

    try:
        configure_logging(config)
        logging.getLogger("devwerk.test").info("daily file logging works: 租户")
        for handler in root.handlers:
            handler.flush()

        file_handlers = [handler for handler in root.handlers if isinstance(handler, TimedRotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].when == "MIDNIGHT"
        assert file_handlers[0].backupCount == 7
        assert file_handlers[0].suffix == "%Y-%m-%d"
        assert "daily file logging works: 租户" in (tmp_path / "backend.log").read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            root.addHandler(handler)
        root.setLevel(previous_level)
