from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.adapters.jobbench import load_task
from evaluation.contracts import RunState, TurnEvidence
from evaluation.grading.jobbench import JudgeStatus, changed_files, parse_details, score_run
from evaluation.reporting.jobbench import compare, render, summarize
from evaluation.runner import prepare


def fixture_run(tmp_path: Path) -> tuple[Path, Path, RunState]:
    dataset = tmp_path / "dataset" / "main"
    task = dataset / "finance" / "task1"
    folder = task / "task_folder"
    folder.mkdir(parents=True)
    (dataset.parent / "dataset-revision.json").write_text('{"revision": "dataset-sha"}', encoding="utf-8")
    (folder / "TASK_INSTRUCTIONS.txt").write_text("Write a summary.", encoding="utf-8")
    (folder / "source.txt").write_text("Input", encoding="utf-8")
    (task / "RUBRICS.json").write_text(json.dumps({"rubrics": [
        {"weight": 2, "criterion": ["A", "B"]}, {"weight": 3, "criterion": "C"}]}), encoding="utf-8")
    case = load_task(dataset, "finance/task1", revision="dataset-sha")
    output = tmp_path / "run"
    project = output / "project"
    project.mkdir(parents=True)
    (project / "source.txt").write_text("Input", encoding="utf-8")
    (project / "TASK_INSTRUCTIONS.txt").write_text("Write a summary.", encoding="utf-8")
    (output / "input_manifest.json").write_text(json.dumps({"files": {
        item.relative_path: item.sha256 for item in case.source_files}}), encoding="utf-8")
    state = RunState(case=case, project_id="prj1", next_turn=1,
                     turns=[TurnEvidence(job_id="job1", status="succeeded", user_message_id=1)],
                     started_at="now")
    (output / "state.json").write_text(state.model_dump_json(), encoding="utf-8")
    evidence = output / "evidence"
    evidence.mkdir()
    (evidence / "tasks.json").write_text("[]", encoding="utf-8")
    (evidence / "quiescence.json").write_text('{"quiescent": true}', encoding="utf-8")
    return dataset, output, state


def test_prepare_copies_complete_binary_source_and_checks_hash(tmp_path: Path):
    dataset, _, _ = fixture_run(tmp_path)
    folder = dataset / "finance" / "task1" / "task_folder"
    (folder / "evidence.pdf").write_bytes(b"\x00\xffPDF")
    case = load_task(dataset, "finance/task1", revision="dataset-sha")

    class ProjectClient:
        def create_project(self, _name, _description, base_dir):
            return {"id": "prj2", "base_dir": base_dir}

    output = tmp_path / "binary-run"
    prepare(ProjectClient(), case, output)
    assert (output / "project" / "evidence.pdf").read_bytes() == b"\x00\xffPDF"
    assert changed_files(output, RunState.model_validate_json(
        (output / "state.json").read_text(encoding="utf-8"))) == []


def test_no_output_is_weighted_zero_and_inputs_are_not_deliverables(tmp_path: Path, monkeypatch):
    dataset, output, state = fixture_run(tmp_path)
    assert changed_files(output, state) == []
    from evaluation.grading import jobbench
    monkeypatch.setattr(jobbench.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "fa03a17f9ff662f136798a274e10011c3aa00fd0\n"})())
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "judge.py").write_text("JUDGE_MAX_COMPLETION_TOKENS", encoding="utf-8")
    score = score_run(output, dataset, tmp_path, judge_model="judge", api_base="https://example.test")
    assert score.status == JudgeStatus.SCORED
    assert score.n_passed == 0 and score.n_criteria == 5 and score.no_output


def test_changed_output_is_isolated_and_missing_judge_key_is_unscored(tmp_path: Path, monkeypatch):
    dataset, output, state = fixture_run(tmp_path)
    (output / "project" / "answer.md").write_text("Done", encoding="utf-8")
    internal = output / "project" / ".devwerk" / "memory"
    internal.mkdir(parents=True)
    (internal / "CURRENT.md").write_text("Private runtime memory", encoding="utf-8")
    from evaluation.grading import jobbench
    monkeypatch.setattr(jobbench.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "fa03a17f9ff662f136798a274e10011c3aa00fd0\n"})())
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "judge.py").write_text("JUDGE_MAX_COMPLETION_TOKENS", encoding="utf-8")
    monkeypatch.delenv("JUDGE_API_KEY", raising=False)
    score = score_run(output, dataset, tmp_path, judge_model="judge", api_base="https://example.test")
    assert score.status == JudgeStatus.UNSCORED
    assert score.deliverable_files == ["answer.md"]
    assert not (output / "deliverable" / "source.txt").exists()


def test_upstream_weighted_details_and_harness_mean(tmp_path: Path):
    _, _, state = fixture_run(tmp_path)
    details = {"total_score": 2, "max_score": 5, "judge_model": "judge", "rubrics": [
        {"index": 0, "weight": 2, "result": {"score": 2, "criteria_count": 2, "criteria_passed": 2}},
        {"index": 1, "weight": 3, "result": {"score": 0, "criteria_count": 1, "criteria_passed": 0}}]}
    score = parse_details(state, details, [2, 3], files=["answer.md"], revision="pinned")
    assert score.status == JudgeStatus.SCORED
    assert score.n_passed == 2 and score.n_criteria == 5 and not score.all_pass
    assert summarize([score, score]).score == 40
    mixed = [score, score.model_copy(update={"task_id": "finance/task2",
                                      "status": JudgeStatus.UNSCORED,
                                      "n_passed": None, "n_criteria": None})]
    totals = summarize(mixed)
    assert totals.n_attempted == 2 and totals.n_ungraded == 1
    assert totals.attempted_score == 20 and totals.score == 40
    assert totals.attempted_all_pass == 0
    assert "| finance/task2 | 0.0% | unscored |" in render(mixed)
    details["rubrics"].pop()
    assert parse_details(state, details, [2, 3], files=[], revision="pinned").status == JudgeStatus.UNSCORED


def test_upstream_unweighted_fallback_when_all_weights_are_zero(tmp_path: Path):
    _, _, state = fixture_run(tmp_path)
    payload = {"total_score": 0, "max_score": 0, "judge_model": "judge", "rubrics": [
        {"index": 0, "weight": 0, "result": {"score": 0, "criteria_count": 2, "criteria_passed": 1}}]}
    score = parse_details(state, payload, [0], files=["answer.md"], revision="pinned")
    assert score.metric == "unweighted-fallback"
    assert score.n_passed == 1 and score.n_criteria == 2


def test_comparison_pairs_only_same_task_and_judge(tmp_path: Path):
    _, _, state = fixture_run(tmp_path)
    payload = {"total_score": 2, "max_score": 5, "judge_model": "judge", "rubrics": [
        {"index": 0, "weight": 2, "result": {"score": 2, "criteria_count": 1, "criteria_passed": 1}},
        {"index": 1, "weight": 3, "result": {"score": 0, "criteria_count": 1, "criteria_passed": 0}}]}
    first = parse_details(state, payload, [2, 3], files=["answer.md"], revision="pinned")
    second = first.model_copy(update={"n_passed": 5.0})
    assert "60.0 points" in compare([first], [second])
    changed_judge = second.model_copy(update={"judge_model": "other"})
    import pytest
    with pytest.raises(ValueError, match="same dataset, rubric and Judge"):
        compare([first], [changed_judge])


def test_running_task_must_not_be_scored(tmp_path: Path, monkeypatch):
    dataset, output, _ = fixture_run(tmp_path)
    (output / "evidence" / "tasks.json").write_text('[{"status": "running"}]', encoding="utf-8")
    from evaluation.grading import jobbench
    monkeypatch.setattr(jobbench.subprocess, "run", lambda *a, **k: type("P", (), {"stdout": "fa03a17f9ff662f136798a274e10011c3aa00fd0\n"})())
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "judge.py").write_text("JUDGE_MAX_COMPLETION_TOKENS", encoding="utf-8")
    score = score_run(output, dataset, tmp_path, judge_model="judge", api_base="https://example.test")
    assert score.status == JudgeStatus.INCOMPLETE


@pytest.mark.parametrize("turn_status,task_status,stop_reason", [
    ("failed", None, None),
    ("succeeded", "failed", None),
    ("succeeded", None, "Task failed before completion"),
])
def test_failed_run_cannot_send_intermediate_files_to_judge(
        tmp_path: Path, monkeypatch, turn_status: str, task_status: str | None,
        stop_reason: str | None):
    dataset, output, state = fixture_run(tmp_path)
    state.turns[0].status = turn_status
    state.stop_reason = stop_reason
    (output / "state.json").write_text(state.model_dump_json(), encoding="utf-8")
    (output / "project" / "answer.md").write_text("Unfinished draft", encoding="utf-8")
    if task_status:
        (output / "evidence" / "tasks.json").write_text(
            json.dumps([{"status": task_status}]), encoding="utf-8")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "judge.py").write_text("JUDGE_MAX_COMPLETION_TOKENS", encoding="utf-8")
    monkeypatch.setenv("JUDGE_API_KEY", "test-only")

    def git_only(command, **_kwargs):
        assert command[0] == "git", "the Judge must not run for failed work"
        return type("Process", (), {"stdout": "fa03a17f9ff662f136798a274e10011c3aa00fd0\n"})()

    from evaluation.grading import jobbench
    monkeypatch.setattr(jobbench.subprocess, "run", git_only)
    score = score_run(output, dataset, tmp_path, judge_model="judge", api_base="https://example.test")
    assert score.status == JudgeStatus.INCOMPLETE
    assert score.n_passed is None
    assert not (output / "deliverable").exists()


def test_completed_output_calls_pinned_judge_and_requires_complete_receipt(tmp_path: Path, monkeypatch):
    dataset, output, _ = fixture_run(tmp_path)
    (output / "project" / "answer.md").write_text("Summary", encoding="utf-8")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "judge.py").write_text("JUDGE_MAX_COMPLETION_TOKENS", encoding="utf-8")
    monkeypatch.setenv("JUDGE_API_KEY", "test-only")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[0] == "git":
            return type("Process", (), {"stdout": "fa03a17f9ff662f136798a274e10011c3aa00fd0\n"})()
        details = Path(command[command.index("--details-file") + 1])
        details.write_text(json.dumps({"judge_model": "judge", "total_score": 2,
            "max_score": 5, "rubrics": [
                {"index": 0, "weight": 2, "result": {"score": 2, "criteria_count": 2, "criteria_passed": 2}},
                {"index": 1, "weight": 3, "result": {"score": 0, "criteria_count": 1, "criteria_passed": 0}}]}), encoding="utf-8")
        return type("Process", (), {"returncode": 0, "stderr": ""})()

    from evaluation.grading import jobbench
    monkeypatch.setattr(jobbench.subprocess, "run", fake_run)
    score = score_run(output, dataset, tmp_path, judge_model="judge", api_base="https://example.test")
    assert score.status == JudgeStatus.SCORED and score.n_passed == 2 and score.n_criteria == 5
    assert len(calls) == 2 and "--output-dir" in calls[1]
    assert (output / "deliverable" / "answer.md").read_text(encoding="utf-8") == "Summary"
