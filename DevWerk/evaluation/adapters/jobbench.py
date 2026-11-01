"""Import complete JobBench task folders without exposing grader rubrics."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json

from evaluation.contracts import CaseTurn, EvaluationCase, SourceFile


def discover(dataset_main: Path) -> list[str]:
    return [path.parent.parent.relative_to(dataset_main).as_posix()
            for path in sorted(dataset_main.glob("*/*/task_folder/TASK_INSTRUCTIONS.txt"))]


def load_task(dataset_main: Path, task_id: str, *, revision: str) -> EvaluationCase:
    if task_id not in discover(dataset_main):
        raise KeyError(f"JobBench task absent from pinned dataset: {task_id}")
    official_record = dataset_main / ".harness2-download.json"
    local_record = dataset_main.parent / "dataset-revision.json"
    record = official_record if official_record.is_file() else local_record
    if not record.is_file():
        raise ValueError("JobBench dataset has no revision manifest")
    recorded_revision = json.loads(record.read_text(encoding="utf-8")).get("revision")
    if recorded_revision != revision:
        raise ValueError("requested JobBench revision differs from dataset manifest")
    folder = dataset_main / task_id / "task_folder"
    rubric = dataset_main / task_id / "RUBRICS.json"
    if not rubric.is_file():
        raise FileNotFoundError(f"JobBench rubric absent: {rubric}")
    prompt = (folder / "TASK_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    sources: list[SourceFile] = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink in JobBench source folder: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        if path.name == "RUBRICS.json" or any(part in {"eval_result", "expected", "reference"} for part in relative.parts):
            continue
        sources.append(SourceFile(relative_path=relative.as_posix(),
                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                  size_bytes=path.stat().st_size))
    return EvaluationCase(
        id="jb_" + task_id.replace("/", "_"), title=f"JobBench {task_id}",
        source="JobBench via Harness2 task_folder layout",
        source_revision=revision, focus="independent",
        turns=[CaseTurn(message=prompt, wait_for_tasks_if_created=True)],
        source_root=str(folder.resolve()), source_files=sources,
        jobbench_task_id=task_id,
        jobbench_rubric_sha256=hashlib.sha256(rubric.read_bytes()).hexdigest(),
    )

