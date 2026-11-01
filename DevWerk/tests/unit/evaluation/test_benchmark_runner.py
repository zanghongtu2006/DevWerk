from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from evaluation.adapters.devwerk_http import DevWerkHTTP
from evaluation.adapters.jobbench import discover, load_task
from evaluation.adapters.local_cases import load_local_case, local_cases
from evaluation.contracts import CaseCheck, CaseTurn, EvaluationCase, EvaluationResult, EvaluationStatus, RunState
from evaluation.grading.deterministic import check_one, overall
from evaluation.reporting.comparison import compare
from evaluation.runner import prepare, run


def test_bundled_cases_are_valid_and_isolated():
    assert set(local_cases()) == {"G01", "G02", "W01", "W02"}
    for name in local_cases():
        case = load_local_case(name)
        assert case.turns
        assert all("RUBRICS" not in path for path in case.files)
    with pytest.raises(ValueError):
        EvaluationCase(id="bad", title="bad", focus="independent", turns=[CaseTurn(message="x")],
                       files={"C:\\private.txt": "leak"})


def test_jobbench_importer_keeps_rubric_out_and_preserves_binary(tmp_path: Path):
    folder = tmp_path / "finance" / "task1" / "task_folder"
    folder.mkdir(parents=True)
    (tmp_path / ".harness2-download.json").write_text('{"revision": "pinned"}', encoding="utf-8")
    (folder / "TASK_INSTRUCTIONS.txt").write_text("Summarize the attached data.", encoding="utf-8")
    (folder / "data.txt").write_text("revenue=2", encoding="utf-8")
    (folder.parent / "RUBRICS.json").write_text('{"rubrics": [{"weight": 1, "criterion": "quality"}]}', encoding="utf-8")
    (folder / "RUBRICS.json").write_text('{"answer": "private"}', encoding="utf-8")
    assert discover(tmp_path) == ["finance/task1"]
    case = load_task(tmp_path, "finance/task1", revision="pinned")
    assert case.files == {}
    assert {item.relative_path for item in case.source_files} == {"TASK_INSTRUCTIONS.txt", "data.txt"}
    assert "private" not in case.model_dump_json()
    (folder / "binary.pdf").write_bytes(b"\xff\xfe")
    case = load_task(tmp_path, "finance/task1", revision="pinned")
    assert {item.relative_path for item in case.source_files} == {
        "TASK_INSTRUCTIONS.txt", "data.txt", "binary.pdf"}


def test_http_uses_user_entry_and_independent_run_pagination():
    calls: list[tuple[str, str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = request.url.params
        calls.append((request.method, path, dict(query)))
        if request.method == "POST":
            assert path.endswith("/conversation")
            assert json.loads(request.content) == {"message": "hello"}
            return httpx.Response(202, json={"job": {"id": "job1", "status": "queued",
                                                    "user_message_id": 1}})
        if path.endswith("/agent-runs/run1"):
            after = int(query.get("after_sequence", "0"))
            if after == 0:
                return httpx.Response(200, json={"id": "run1", "kind": "conversation",
                                                  "iterations": 1, "tool_calls": 101, "messages": [
                    {"sequence": i} for i in range(1, 101)], "tool_invocations": [
                        {"sequence": i, "capability": "x", "ok": True}
                        for i in range(1, 101)]})
            if after == 100:
                return httpx.Response(200, json={"id": "run1", "kind": "conversation",
                                                  "iterations": 1, "tool_calls": 101,
                                                  "messages": [{"sequence": 101}],
                                                  "tool_invocations": [
                                                      {"sequence": 101, "capability": "x", "ok": True}]})
        raise AssertionError(f"unexpected API request: {request.url}")

    with DevWerkHTTP(transport=httpx.MockTransport(answer)) as client:
        assert client.submit("prj1", "hello")["job"]["id"] == "job1"
        detail = client.agent_run_pages("prj1", "run1")
    assert len(detail["messages"]) == 101
    assert len(detail["tool_invocations"]) == 101
    assert len([call for call in calls if call[1].endswith("/agent-runs/run1")]) == 3
    with pytest.raises(ValueError):
        DevWerkHTTP("http://127.0.0.1:8001")


class FakeClient:
    def __init__(self):
        self.submitted = 0
        self.job_reads = 0
        self.task_reads = 0

    def create_project(self, name, description, base_dir):
        return {"id": "prj1", "base_dir": base_dir}

    def submit(self, project_id, message):
        self.submitted += 1
        return {"job": {"id": "job1"}}

    def job(self, project_id, job_id):
        self.job_reads += 1
        return {"id": job_id, "status": "succeeded", "error": None,
                "agent_run_id": "run1", "user_message_id": 1}

    def tasks(self, project_id):
        self.task_reads += 1
        return [{"status": "running" if self.task_reads == 1 else "done"}]

    def get(self, path):
        return {"quiescent": True}

    def quiescence(self, project_id):
        return {"quiescent": True}


def test_resume_waits_for_task_without_resubmitting(tmp_path: Path):
    case = EvaluationCase(id="W", title="work", focus="orchestration",
                          turns=[CaseTurn(message="do work", wait_for_tasks=True),
                                 CaseTurn(message="continue")])
    client = FakeClient()
    output = tmp_path / "run"
    prepare(client, case, output)
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    interrupted = run(client, output, limit_seconds=200, poll_seconds=300,
                      clock=lambda: now[0], sleep=sleep)
    assert interrupted.next_turn == 1
    assert interrupted.waiting_for_tasks
    assert client.submitted == 1
    resumed = run(client, output, limit_seconds=600, poll_seconds=300,
                  clock=lambda: now[0], sleep=sleep)
    assert resumed.next_turn == 2
    assert not resumed.waiting_for_tasks
    assert client.submitted == 2
    persisted = RunState.model_validate_json((output / "state.json").read_text(encoding="utf-8"))
    assert persisted.next_turn == 2


def test_failed_task_stops_scripted_followup(tmp_path: Path):
    case = EvaluationCase(id="W", title="work", focus="orchestration",
                          turns=[CaseTurn(message="do work", wait_for_tasks=True),
                                 CaseTurn(message="continue")])
    client = FakeClient()
    client.tasks = lambda _: [{"status": "failed"}]
    output = tmp_path / "run"
    prepare(client, case, output)
    state = run(client, output, limit_seconds=10, clock=lambda: 0.0, sleep=lambda _: None)
    assert state.next_turn == 1
    assert state.stop_reason
    assert client.submitted == 1


def test_unknown_or_incomplete_is_never_passed():
    assert overall([], complete=True) == EvaluationStatus.UNSCORED
    assert overall([], complete=False) == EvaluationStatus.INCONCLUSIVE


def test_delivery_checks_require_committed_tasks_and_external_review(tmp_path: Path):
    facts = {"tasks": [{"status": "done"}, {"status": "failed"}], "board": {"workflow": {"id": "wf"}}}
    done = check_one(CaseCheck(kind="task_done"), tmp_path, facts)
    manual = check_one(CaseCheck(kind="manual_review"), tmp_path, facts)
    assert done.status == EvaluationStatus.FAILED
    assert manual.status == EvaluationStatus.UNSCORED
    assert overall([manual], complete=True) == EvaluationStatus.UNSCORED


def test_external_code_grader_checks_real_behavior(tmp_path: Path):
    module = tmp_path / "finance" / "orders.py"
    module.parent.mkdir()
    module.write_text(
        "def total(lines, discount=0):\n"
        "    if discount < 0:\n        raise ValueError('negative discount')\n"
        "    return max(0, sum(x['price'] * x['quantity'] for x in lines) - discount)\n",
        encoding="utf-8",
    )
    check = CaseCheck(kind="pytest_pass", grader="W02_test.py")
    assert check_one(check, tmp_path, {}).status == EvaluationStatus.PASSED
    module.write_text("def total(lines, discount=0):\n    return 0\n", encoding="utf-8")
    assert check_one(check, tmp_path, {}).status == EvaluationStatus.FAILED


def test_comparison_rejects_changed_case_materials():
    base = EvaluationResult(case_id="G01", case_revision="1", case_sha256="a", project_id="p",
                            started_at="now")
    changed = base.model_copy(update={"case_sha256": "b"})
    with pytest.raises(ValueError, match="identical case inputs"):
        compare([base], [changed])

