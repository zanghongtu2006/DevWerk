from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import logging
import pytest

from app.core.logging import _daily_archive_name, configure_logging


def test_daily_log_archive_has_one_human_readable_name():
    archived = _daily_archive_name(str(Path("data/logs/devwerk.log.2026-08-14")))

    assert Path(archived).name == "devwerk.20260814.log"


def test_logging_rejects_additional_business_log_names(tmp_path):
    log_dir = tmp_path / "logs"
    config = SimpleNamespace(
        log_level="info",
        log_format="%(message)s",
        log_file_enabled=True,
        log_dir=str(log_dir),
        log_file_name="startup.stdout.log",
        log_retention_days=30,
    )

    with pytest.raises(ValueError, match="must be devwerk.log"):
        configure_logging(config)

    assert not log_dir.exists() or list(log_dir.iterdir()) == []


def test_current_business_log_is_devwerk_log(tmp_path):
    log_dir = tmp_path / "logs"
    config = SimpleNamespace(
        log_level="info",
        log_format="%(message)s",
        log_file_enabled=True,
        log_dir=str(log_dir),
        log_file_name="devwerk.log",
        log_retention_days=30,
    )

    configure_logging(config)
    logging.getLogger("devwerk.test").info("written to the single business log")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert (log_dir / "devwerk.log").read_text(encoding="utf-8").endswith(
        "written to the single business log\n"
    )
    assert [path.name for path in log_dir.iterdir()] == ["devwerk.log"]
