"""Keep outcomes, Agent roles and provenance visible in version comparisons."""

from __future__ import annotations

from evaluation.contracts import EvaluationResult, EvaluationStatus


def _count(items: list[EvaluationResult], status: EvaluationStatus) -> int:
    return sum(item.status == status for item in items)


def compare(before: list[EvaluationResult], after: list[EvaluationResult]) -> str:
    if {item.case_id for item in before} != {item.case_id for item in after}:
        raise ValueError("comparison requires the same case IDs")
    if {item.case_revision for item in before} != {item.case_revision for item in after}:
        raise ValueError("comparison requires the same case revisions")
    if {item.case_sha256 for item in before} != {item.case_sha256 for item in after}:
        raise ValueError("comparison requires identical case inputs and rubrics")
    profiles = {item.model_profile for item in before + after}
    known_revisions = all(item.service_revision for item in before + after)
    lines = ["# Conversation Agent benchmark comparison", "",
             "All repeated runs are shown. " + (
                 "Model profile differs or is unknown; attribution is confounded."
                 if len(profiles) != 1 or None in profiles else "Model profile label is consistent."),
             "Service revision is confirmed for every run." if known_revisions else
             "At least one running service revision is unknown; do not infer an update effect.", "",
             "| Group | Runs | Passed | Failed | Unscored | Inconclusive | Conversation rejects | Column rejects |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for label, items in (("before", before), ("after", after)):
        lines.append(f"| {label} | {len(items)} | {_count(items, EvaluationStatus.PASSED)} | "
                     f"{_count(items, EvaluationStatus.FAILED)} | "
                     f"{_count(items, EvaluationStatus.UNSCORED)} | "
                     f"{_count(items, EvaluationStatus.INCONCLUSIVE)} | "
                     f"{sum(item.conversation.rejected_tools for item in items)} | "
                     f"{sum(item.column.rejected_tools for item in items)} |")
    lines.extend(["", "Individual runs:", "",
             "| Version | Case | Outcome | Conversation rejected | Column rejected | Tasks done |",
             "| --- | --- | --- | ---: | ---: | ---: |"])
    for label, items in (("before", before), ("after", after)):
        for item in items:
            lines.append(f"| {label} (`{item.service_revision or 'unknown'}`) | {item.case_id} | "
                         f"{item.status.value} | {item.conversation.rejected_tools} | "
                         f"{item.column.rejected_tools} | {item.done_count if item.done_count is not None else '?'} |")
    return "\n".join(lines) + "\n"

