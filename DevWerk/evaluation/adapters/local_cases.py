"""Versioned local cases independent of DevWerk product prompts."""

from __future__ import annotations

from pathlib import Path

from evaluation.contracts import EvaluationCase


CASE_DIR = Path(__file__).resolve().parents[1] / "cases"


def local_cases() -> list[str]:
    return [item.stem for item in sorted(CASE_DIR.glob("*.json"))]


def load_local_case(case_id: str) -> EvaluationCase:
    if case_id not in local_cases():
        raise KeyError(f"unknown local case {case_id!r}")
    return EvaluationCase.model_validate_json((CASE_DIR / f"{case_id}.json").read_text(encoding="utf-8"))

