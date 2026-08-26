from __future__ import annotations

import os
import threading
import time
from pathlib import Path


class ManagedRestart:
    """Request one restart from the `startup.bat` process that owns Uvicorn."""

    def __init__(self, marker_path: Path, *, enabled: bool):
        self.marker_path = marker_path.expanduser().resolve()
        self.enabled = enabled
        self._scheduled = False
        self._lock = threading.Lock()

    def schedule(self, delay_seconds: float = 0.75) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._scheduled:
                return True
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            self.marker_path.write_text("restart\n", encoding="utf-8")
            self._scheduled = True
            threading.Thread(
                target=self._terminate_after,
                args=(delay_seconds,),
                name="devwerk-managed-restart",
                daemon=True,
            ).start()
        return True

    @staticmethod
    def _terminate_after(delay_seconds: float) -> None:
        time.sleep(delay_seconds)
        os._exit(0)
