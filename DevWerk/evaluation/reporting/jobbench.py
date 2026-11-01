"""Harness² JobBench weighted score and ungraded-count reporting."""

from __future__ import annotations

from statistics import mean
from pydantic import BaseModel, ConfigDict, Field

from evaluation.grading.jobbench import JobBenchScore, JudgeStatus


class JobBenchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempted_score: float | None = Field(default=None, ge=0, le=100)
    attempted_all_pass: float | None = Field(default=None, ge=0, le=100)
    n_attempted: int = Field(ge=0)
    score: float | None = Field(default=None, ge=0, le=100)
    all_pass: float | None = Field(default=None, ge=0, le=100)
    n: int = Field(ge=0)
    n_ungraded: int = Field(ge=0)
    n_no_output: int = Field(ge=0)
    dataset_revision: str | None = None
    judge_revision: str | None = None
    judge_model: str | None = None
    service_revision: str | None = None
    model_profile: str | None = None


def summarize(scores: list[JobBenchScore]) -> JobBenchSummary:
    provenance = {(item.dataset_revision, item.judge_revision, item.judge_model,
                   item.service_revision, item.model_profile) for item in scores}
    if len(provenance) > 1:
        raise ValueError("cannot aggregate different dataset, Judge, service or evaluated model revisions")
    graded = [item for item in scores if item.status == JudgeStatus.SCORED]
    fractions = [item.n_passed / item.n_criteria for item in graded]
    origin = scores[0] if scores else None
    return JobBenchSummary(
        attempted_score=round(100 * sum(fractions) / len(scores), 2) if scores else None,
        attempted_all_pass=round(100 * sum(bool(item.all_pass) for item in graded) / len(scores), 2)
        if scores else None,
        n_attempted=len(scores),
        score=round(100 * mean(fractions), 2) if fractions else None,
        all_pass=round(100 * sum(bool(item.all_pass) for item in graded) / len(graded), 2)
        if graded else None,
        n=len(graded), n_ungraded=len(scores) - len(graded),
        n_no_output=sum(item.no_output for item in graded),
        dataset_revision=origin.dataset_revision if origin else None,
        judge_revision=origin.judge_revision if origin else None,
        judge_model=origin.judge_model if origin else None,
        service_revision=origin.service_revision if origin else None,
        model_profile=origin.model_profile if origin else None,
    )


def render(scores: list[JobBenchScore]) -> str:
    totals = summarize(scores)
    lines = ["# JobBench attempted-task score", "",
             f"Attempted-task mean (incomplete/unscored = 0): {totals.attempted_score if totals.attempted_score is not None else 'no attempts'}%",
             f"Attempted-task all-pass rate: {totals.attempted_all_pass if totals.attempted_all_pass is not None else 'no attempts'}%",
             f"Scored-task conditional mean: {totals.score if totals.score is not None else 'unscored'}%",
             f"Scored-task conditional all-pass rate: {totals.all_pass if totals.all_pass is not None else 'unscored'}%",
             f"Attempted: {totals.n_attempted}",
             f"Graded: {totals.n}; ungraded: {totals.n_ungraded}; no-output zeros: {totals.n_no_output}",
             f"Dataset: {totals.dataset_revision or 'unknown'}; Judge: {totals.judge_model or 'unknown'} ({totals.judge_revision or 'unknown'})",
             f"Metric units: {', '.join(sorted({item.metric for item in scores if item.metric})) or 'unscored'}",
             "", "| Task | Score | Status | Project |", "| --- | ---: | --- | --- |"]
    for item in scores:
        percent = round(100 * item.n_passed / item.n_criteria, 2) if item.status == JudgeStatus.SCORED else 0.0
        lines.append(f"| {item.task_id} | {percent}% | "
                     f"{item.status.value} | {item.project_id} |")
    lines.extend(["", "Each attempted task contributes its weighted score, or zero when incomplete/unscored.",
                  "The scored-task conditional mean is diagnostic only.",
                  "The DevWerk execution substrate and any task subset differ from a full Harness² run."])
    return "\n".join(lines) + "\n"


def compare(before: list[JobBenchScore], after: list[JobBenchScore]) -> str:
    old = {item.task_id: item for item in before}
    new = {item.task_id: item for item in after}
    if len(old) != len(before) or len(new) != len(after) or old.keys() != new.keys():
        raise ValueError("JobBench comparison requires identical, unique task IDs")
    if any((old[key].dataset_revision, old[key].rubric_sha256, old[key].judge_revision,
            old[key].judge_model) !=
           (new[key].dataset_revision, new[key].rubric_sha256, new[key].judge_revision,
            new[key].judge_model) for key in old):
        raise ValueError("JobBench comparison requires the same dataset, rubric and Judge")
    old_total, new_total = summarize(before), summarize(after)
    paired = [(key, old[key], new[key]) for key in sorted(old)
              if old[key].status == new[key].status == JudgeStatus.SCORED]
    paired_delta = round(mean(100 * (latest.n_passed / latest.n_criteria -
                                      prior.n_passed / prior.n_criteria)
                              for _, prior, latest in paired), 2) if paired else None
    lines = ["# JobBench paired comparison", "",
             f"Before attempted-task mean: {old_total.attempted_score if old_total.attempted_score is not None else 'no attempts'}%; "
             f"after attempted-task mean: {new_total.attempted_score if new_total.attempted_score is not None else 'no attempts'}%",
             f"Before mean: {old_total.score if old_total.score is not None else 'unscored'}%; "
             f"after mean: {new_total.score if new_total.score is not None else 'unscored'}%",
             f"Paired scored tasks: {len(paired)}/{len(old)}; paired mean change: "
             f"{str(paired_delta) + ' points' if paired_delta is not None else 'unscored'}",
             "", "| Task | Before | After | Change |", "| --- | ---: | ---: | ---: |"]
    for key in sorted(old):
        prior, latest = old[key], new[key]
        if prior.status == latest.status == JudgeStatus.SCORED:
            p = round(100 * prior.n_passed / prior.n_criteria, 2)
            n = round(100 * latest.n_passed / latest.n_criteria, 2)
            lines.append(f"| {key} | {p}% | {n}% | {round(n - p, 2):+} |")
        else:
            lines.append(f"| {key} | {prior.status.value} | {latest.status.value} | — |")
    return "\n".join(lines) + "\n"
