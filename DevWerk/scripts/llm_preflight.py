"""Minimal real-provider preflight for DevWerk V1."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.v1.llm import complete


if __name__ == "__main__":
    print(
        complete(
            [{"role": "user", "content": "Reply with the single word OK."}],
            [],
            project_id="preflight",
            agent="conversation",
            timeout_seconds=60,
        )
    )
