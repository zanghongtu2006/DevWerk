"""JobBench deliverable isolation and upstream Judge score normalization."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from enum import StrEnum
from typing import Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.contracts import RunState
from evaluation.evidence.collector import write_json


HARNESS2_JUDGE_REVISION = "fa03a17f9ff662f136798a274e10011c3aa00fd0"


class JudgeStatus(StrEnum):
    SCORED = "scored"
    UNSCORED = "unscored"
    INCOMPLETE = "incomplete"


class RubricResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    criteria_count: int = Field(ge=0)
    criteria_passed: int = Field(ge=0)
    score: float = Field(ge=0)
    overall_reasoning: str = ""


class RubricDetail(BaseModel):
    model_config = ConfigDict(extra="allow")
    index: int = Field(ge=0)
    weight: float = Field(ge=0)
    result: RubricResult


class JudgeDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    total_score: float = Field(ge=0)
    max_score: float = Field(ge=0)
    judge_model: str = ""
    rubrics: list[RubricDetail] = Field(min_length=1)


class DeliverableManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[str]
    sha256: dict[str, str]


class JudgeDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")
    openai: bool = False
    pandas: bool = False
    openpyxl: bool = False
    mammoth: bool = False
    pdfplumber: bool = False


class JobBenchPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_revision: str | None
    dataset_tasks: int = Field(ge=0)
    judge_revision: str | None
    judge_pin_matches: bool
    harness2_patch_applied: bool
    judge_python: str
    dependencies: JudgeDependencies
    judge_api_key_present: bool
    windows_fcntl_shim: bool
    ready_to_score: bool


class JobBenchScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    project_id: str
    dataset_revision: str
    rubric_sha256: str
    service_revision: str | None = None
    model_profile: str | None = None
    judge_model: str | None = None
    judge_revision: str | None = None
    status: JudgeStatus
    reason: str | None = None
    n_passed: float | None = Field(default=None, ge=0)
    n_criteria: float | None = Field(default=None, gt=0)
    criteria_passed: int | None = Field(default=None, ge=0)
    criteria_count: int | None = Field(default=None, ge=0)
    all_pass: bool | None = None
    no_output: bool = False
    metric: Literal["weighted", "unweighted-fallback"] | None = None
    deliverable_files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_score(self) -> JobBenchScore:
        if self.status == JudgeStatus.SCORED and (self.n_passed is None or self.n_criteria is None):
            raise ValueError("scored result requires n_passed and n_criteria")
        if self.n_passed is not None and self.n_criteria is not None and self.n_passed > self.n_criteria:
            raise ValueError("score exceeds maximum")
        return self


def _load_run(output: Path) -> RunState:
    return RunState.model_validate_json((output / "state.json").read_text(encoding="utf-8"))


def _rubric_path(dataset_main: Path, state: RunState) -> Path:
    task_id = state.case.jobbench_task_id
    if not task_id:
        raise ValueError("run is not a JobBench task")
    rubric = dataset_main / task_id / "RUBRICS.json"
    if not rubric.is_file() or hashlib.sha256(rubric.read_bytes()).hexdigest() != state.case.jobbench_rubric_sha256:
        raise ValueError("pinned JobBench rubric missing or changed")
    return rubric


def _base(state: RunState, status: JudgeStatus, **kwargs: object) -> JobBenchScore:
    return JobBenchScore(task_id=state.case.jobbench_task_id or "", project_id=state.project_id,
                         dataset_revision=state.case.source_revision,
                         rubric_sha256=state.case.jobbench_rubric_sha256 or "",
                         service_revision=state.service_revision, model_profile=state.model_profile,
                         status=status, **kwargs)


def _finished(output: Path, state: RunState) -> bool:
    if (state.pending_job_id or state.waiting_for_tasks or state.stop_reason
            or state.next_turn != len(state.case.turns)):
        return False
    if not state.turns or any(turn.status != "succeeded" for turn in state.turns):
        return False
    evidence = output / "evidence"
    if not (evidence / "tasks.json").is_file() or not (evidence / "quiescence.json").is_file():
        return False
    tasks = json.loads((evidence / "tasks.json").read_text(encoding="utf-8"))
    quiet = json.loads((evidence / "quiescence.json").read_text(encoding="utf-8"))
    return all(item["status"] == "done" for item in tasks) and bool(quiet.get("quiescent"))


def changed_files(output: Path, state: RunState) -> list[Path]:
    project = output / "project"
    manifest = output / "input_manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError("input_manifest.json is required to separate inputs from deliverables")
    baseline = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    changed: list[Path] = []
    for path in sorted(project.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink in deliverable tree: {path}")
        if not path.is_file():
            continue
        relative_path = path.relative_to(project)
        if relative_path.parts[0] == ".devwerk":
            continue
        relative = relative_path.as_posix()
        if hashlib.sha256(path.read_bytes()).hexdigest() != baseline.get(relative):
            changed.append(path)
    return changed


def stage_deliverable(output: Path, state: RunState) -> list[str]:
    files = changed_files(output, state)
    deliverable = output / "deliverable"
    if deliverable.exists():
        shutil.rmtree(deliverable)
    deliverable.mkdir()
    names: list[str] = []
    for source in files:
        relative = source.relative_to(output / "project")
        target = deliverable / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        names.append(relative.as_posix())
    manifest = DeliverableManifest(files=names,
        sha256={name: hashlib.sha256((deliverable / name).read_bytes()).hexdigest() for name in names})
    write_json(output / "deliverable_manifest.json", manifest.model_dump(mode="json"))
    return names


def _no_output(state: RunState, rubric: Path, *, judge_model: str, revision: str) -> JobBenchScore:
    data = json.loads(rubric.read_text(encoding="utf-8"))
    rubrics = data.get("rubrics") or []
    maximum = sum(float(item.get("weight") or 0) for item in rubrics)
    count = sum(1 if isinstance(item.get("criterion", []), str) else len(item.get("criterion", []))
                for item in rubrics)
    if maximum <= 0:
        return _base(state, JudgeStatus.UNSCORED, reason="rubric has no positive weight",
                     judge_model=judge_model, judge_revision=revision)
    return _base(state, JudgeStatus.SCORED, n_passed=0, n_criteria=maximum,
                 criteria_passed=0, criteria_count=count, all_pass=False, no_output=True,
                 judge_model=judge_model, judge_revision=revision, metric="weighted")


def parse_details(state: RunState, payload: object, expected_weights: list[float],
                  *, files: list[str], revision: str | None,
                  expected_model: str | None = None) -> JobBenchScore:
    try:
        details = JudgeDetails.model_validate(payload)
        if expected_model and details.judge_model != expected_model:
            raise ValueError("Judge model differs from requested model")
        indices = [item.index for item in details.rubrics]
        if sorted(indices) != list(range(len(expected_weights))):
            raise ValueError("Judge returned incomplete rubric results")
        if any(abs(item.weight - expected_weights[item.index]) > 1e-6 for item in details.rubrics):
            raise ValueError("Judge rubric weights differ from pinned dataset")
        if any(item.result.criteria_passed > item.result.criteria_count or item.result.score > item.weight
               for item in details.rubrics):
            raise ValueError("Judge rubric result exceeds its maximum")
        if any(item.result.overall_reasoning.startswith(("Failed after", "Judge raised an exception"))
               for item in details.rubrics):
            raise ValueError("at least one rubric Judge request failed")
        if abs(sum(item.weight for item in details.rubrics) - details.max_score) > 1e-6:
            raise ValueError("Judge max_score differs from rubric weights")
        if abs(sum(item.result.score for item in details.rubrics) - details.total_score) > 1e-6:
            raise ValueError("Judge total_score differs from rubric results")
    except (ValueError, TypeError) as exc:
        return _base(state, JudgeStatus.UNSCORED, reason=f"invalid Judge details: {exc}",
                     deliverable_files=files, judge_revision=revision, judge_model=expected_model)
    count = sum(item.result.criteria_count for item in details.rubrics)
    passed = sum(item.result.criteria_passed for item in details.rubrics)
    if details.max_score > 0:
        earned, maximum, metric = details.total_score, details.max_score, "weighted"
    elif count > 0:
        earned, maximum, metric = float(passed), float(count), "unweighted-fallback"
    else:
        return _base(state, JudgeStatus.UNSCORED, reason="Judge returned no score or criteria",
                     deliverable_files=files, judge_revision=revision, judge_model=expected_model)
    return _base(state, JudgeStatus.SCORED, n_passed=earned,
                 n_criteria=maximum, criteria_passed=passed, criteria_count=count,
                 all_pass=bool(count > 0 and passed == count), judge_model=details.judge_model,
                 judge_revision=revision, deliverable_files=files, metric=metric)


def score_run(output: Path, dataset_main: Path, judge_repo: Path, *, judge_model: str,
              api_base: str, python: Path = Path(sys.executable), timeout: int = 1800) -> JobBenchScore:
    state = _load_run(output)
    rubric = _rubric_path(dataset_main, state)
    revision = subprocess.run(["git", "-C", str(judge_repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    if revision != HARNESS2_JUDGE_REVISION:
        raise ValueError(f"Judge revision differs from Harness2 pin: {revision}")
    judge_source = judge_repo / "eval" / "judge.py"
    if not judge_source.is_file():
        raise FileNotFoundError(judge_source)
    if "JUDGE_MAX_COMPLETION_TOKENS" not in judge_source.read_text(encoding="utf-8"):
        raise ValueError("Harness2 Judge patch not applied to pinned upstream checkout")
    if not _finished(output, state):
        result = _base(state, JudgeStatus.INCOMPLETE, reason="run or task not completed in collected evidence",
                       judge_model=judge_model, judge_revision=revision)
    else:
        files = stage_deliverable(output, state)
        if not files:
            result = _no_output(state, rubric, judge_model=judge_model, revision=revision)
        elif not os.environ.get("JUDGE_API_KEY"):
            result = _base(state, JudgeStatus.UNSCORED, reason="JUDGE_API_KEY absent",
                           deliverable_files=files, judge_model=judge_model, judge_revision=revision)
        else:
            details_path = output / "judge_details.json"
            if details_path.exists():
                details_path.unlink()
            command = [str(python), str(judge_source), "--output-dir", str(output / "deliverable"),
                       "--rubrics-file", str(rubric), "--details-file", str(details_path),
                       "--judge-model", judge_model, "--api-base", api_base,
                       "--max-workers", "8", "--max-retries", "3", "--timeout-per-rubric", "240",
                       "--evaluated-model", state.model_profile or "DevWerk"]
            env = os.environ.copy()
            env["JUDGE_REASONING_EFFORT"] = "low"
            if os.name == "nt":
                compatibility = str(Path(__file__).resolve().parents[1] / "judge_compat")
                env["PYTHONPATH"] = compatibility + os.pathsep + env.get("PYTHONPATH", "")
            try:
                process = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
                if process.returncode != 0 or not details_path.is_file():
                    result = _base(state, JudgeStatus.UNSCORED,
                                   reason=f"Judge process failed (exit {process.returncode}): {process.stderr[-500:]}",
                                   deliverable_files=files, judge_model=judge_model, judge_revision=revision)
                else:
                    payload = json.loads(details_path.read_text(encoding="utf-8"))
                    expected = [float(item.get("weight") or 0)
                                for item in json.loads(rubric.read_text(encoding="utf-8")).get("rubrics") or []]
                    result = parse_details(state, payload, expected, files=files,
                                           revision=revision, expected_model=judge_model)
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                result = _base(state, JudgeStatus.UNSCORED, reason=f"Judge could not complete: {exc}",
                               deliverable_files=files, judge_model=judge_model, judge_revision=revision)
    write_json(output / "jobbench_score.json", result.model_dump(mode="json"))
    return result
