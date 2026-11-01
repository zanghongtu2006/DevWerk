"""Render a concise result with links back to unabridged local evidence."""

from __future__ import annotations

from datetime import datetime

from evaluation.contracts import EvaluationResult


def render(result: EvaluationResult) -> str:
    lines = [f"# {result.case_id}: {result.status.value}", "",
             f"Project: `{result.project_id}`; case revision: `{result.case_revision}`; "
             f"service revision: `{result.service_revision or 'unknown'}`; "
             f"model profile: `{result.model_profile or 'unknown'}`.", "",
             f"Turns: {len(result.turns)}; tasks: {result.task_count}; "
             f"done: {result.done_count}; Workflow created: {result.workflow_created}.", "",
             "| Role | Runs | Tool calls | Rejected calls | First-call rejects |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for label, metric in (("Conversation", result.conversation), ("Column", result.column)):
        lines.append(f"| {label} | {metric.runs} | {metric.tool_calls} | "
                     f"{metric.rejected_tools} | {metric.first_tool_rejected} |")
    lines.extend(["", "| Check | Result | Evidence |", "| --- | --- | --- |"])
    for check in result.checks:
        lines.append(f"| {check.kind.value} | {check.status.value} | {check.evidence.replace('|', '/')} |")
    lines.extend(["", "Raw evidence: `evidence/`; run state: `state.json`; machine result: `result.json`."])
    job_durations: list[float] = []
    for turn in result.turns:
        if turn.created_at and turn.finished_at:
            try:
                job_durations.append((datetime.fromisoformat(turn.finished_at) -
                                      datetime.fromisoformat(turn.created_at)).total_seconds())
            except ValueError:
                pass
    if job_durations:
        lines.extend(["", f"Conversation Job server durations (seconds): {job_durations}."])
    if result.error:
        lines.extend(["", f"Observation: {result.error}"])
    return "\n".join(lines) + "\n"

