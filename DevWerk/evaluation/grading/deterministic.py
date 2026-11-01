"""Evaluate only properties observable outside an Agent's own claims."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from typing import Any

from evaluation.contracts import CaseCheck, CheckKind, CheckResult, EvaluationStatus


def check_one(check: CaseCheck, project_dir: Path, facts: dict[str, Any]) -> CheckResult:
    path = project_dir / check.path if check.path else None
    if check.kind in (CheckKind.FILE_EXISTS, CheckKind.FILE_CONTAINS, CheckKind.FILE_EQUALS):
        if path is None:
            return CheckResult(kind=check.kind, status=EvaluationStatus.UNSCORED, evidence="check path omitted")
        if not path.is_file():
            return CheckResult(kind=check.kind, status=EvaluationStatus.FAILED, evidence=f"missing: {check.path}")
        if check.kind == CheckKind.FILE_EXISTS:
            return CheckResult(kind=check.kind, status=EvaluationStatus.PASSED, evidence=f"file: {check.path}")
        if check.text is None:
            return CheckResult(kind=check.kind, status=EvaluationStatus.UNSCORED, evidence="expected text omitted")
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return CheckResult(kind=check.kind, status=EvaluationStatus.UNSCORED,
                               evidence=f"could not read {check.path}: {type(exc).__name__}")
        found = (actual.strip() == check.text.strip() if check.kind == CheckKind.FILE_EQUALS
                 else check.text in actual)
        return CheckResult(kind=check.kind, status=EvaluationStatus.PASSED if found else EvaluationStatus.FAILED,
                           evidence=f"text {'matched' if found else 'did not match'}: {check.path}")
    if check.kind == CheckKind.WORKFLOW_CREATED:
        found = facts["board"].get("workflow") is not None
        return CheckResult(kind=check.kind, status=EvaluationStatus.PASSED if found else EvaluationStatus.FAILED,
                           evidence="published Workflow in board snapshot")
    if check.kind == CheckKind.TASK_DONE:
        found = bool(facts["tasks"]) and all(item["status"] == "done" for item in facts["tasks"])
        return CheckResult(kind=check.kind, status=EvaluationStatus.PASSED if found else EvaluationStatus.FAILED,
                           evidence="all committed Tasks reached done")
    if check.kind == CheckKind.PYTEST_PASS:
        test_target = project_dir / "tests"
        if check.grader:
            test_target = Path(__file__).resolve().parents[1] / "rubrics" / check.grader
            if not test_target.is_file():
                return CheckResult(kind=check.kind, status=EvaluationStatus.UNSCORED,
                                   evidence=f"grader unavailable: {check.grader}")
        process_env = {key: value for key, value in os.environ.items()
                       if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "VIRTUAL_ENV"}}
        process_env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                        str(test_target)],
                                       cwd=project_dir, capture_output=True, text=True, timeout=120,
                                       env=process_env, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CheckResult(kind=check.kind, status=EvaluationStatus.UNSCORED,
                               evidence=f"test process unavailable: {type(exc).__name__}")
        return CheckResult(kind=check.kind,
                           status=EvaluationStatus.PASSED if completed.returncode == 0 else EvaluationStatus.FAILED,
                           evidence=f"pytest exit={completed.returncode}; "
                                    f"output={completed.stdout[-500:].replace(chr(10), ' ')}")
    if check.kind == CheckKind.MANUAL_REVIEW:
        return CheckResult(kind=check.kind, status=EvaluationStatus.UNSCORED,
                           evidence="requires independent human or rubric review")
    raise ValueError(f"unknown check kind: {check.kind}")


def overall(checks: list[CheckResult], complete: bool) -> EvaluationStatus:
    if not complete:
        return EvaluationStatus.INCONCLUSIVE
    if any(item.status == EvaluationStatus.FAILED for item in checks):
        return EvaluationStatus.FAILED
    if not checks or any(item.status == EvaluationStatus.UNSCORED for item in checks):
        return EvaluationStatus.UNSCORED
    return EvaluationStatus.PASSED

