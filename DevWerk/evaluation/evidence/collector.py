"""Collect the persisted facts exposed by DevWerk's read-only API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.adapters.devwerk_http import DevWerkHTTP


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    staging.replace(path)


def collect(client: DevWerkHTTP, project_id: str, output: Path) -> dict[str, Any]:
    """Never infer success from an Agent's text; preserve tool receipts separately."""
    evidence = output / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    project = client.project(project_id)
    messages = client.conversation(project_id)
    tasks = client.tasks(project_id)
    board = client.board(project_id)
    quiescence = client.quiescence(project_id)
    runs = client.agent_runs(project_id)
    for name, data in (("project", project), ("conversation", messages), ("tasks", tasks),
                       ("board", board), ("quiescence", quiescence), ("agent_runs", runs)):
        write_json(evidence / f"{name}.json", data)
    for run in runs:
        detail = client.agent_run_pages(project_id, run["id"])
        write_json(evidence / "runs" / f"{run['id']}.json", detail)
    return {"project": project, "conversation": messages, "tasks": tasks,
            "board": board, "quiescence": quiescence, "agent_runs": runs}

