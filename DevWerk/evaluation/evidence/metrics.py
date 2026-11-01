"""Facts attributable to Conversation and Column Runs."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.contracts import RoleMetrics


def role_metrics(runs: list[dict], evidence: Path, kind: str) -> RoleMetrics:
    result = RoleMetrics()
    for run in runs:
        if run["kind"] != kind:
            continue
        result.runs += 1
        result.iterations += int(run.get("iterations") or 0)
        result.tool_calls += int(run.get("tool_calls") or 0)
        path = evidence / "runs" / f"{run['id']}.json"
        detail = json.loads(path.read_text(encoding="utf-8"))
        calls = detail["tool_invocations"]
        result.rejected_tools += sum(not call["ok"] for call in calls)
        if calls:
            if calls[0]["ok"]:
                result.first_tool_accepted += 1
            else:
                result.first_tool_rejected += 1
    # Usage is omitted until a stable Provider usage endpoint exists. JSON size is not tokens.
    return result

