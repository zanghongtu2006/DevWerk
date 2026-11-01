"""Execute versioned natural-language cases through DevWerk's public API."""

from __future__ import annotations

import hashlib
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from evaluation.adapters.devwerk_http import DevWerkHTTP
from evaluation.contracts import EvaluationCase, EvaluationResult, EvaluationStatus, RunState, TurnEvidence
from evaluation.evidence.collector import collect, write_json
from evaluation.evidence.metrics import role_metrics
from evaluation.grading.deterministic import check_one, overall


TERMINAL_JOBS = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
TERMINAL_TASKS = frozenset({"done", "failed", "cancelled"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_case(path: Path) -> EvaluationCase:
    return EvaluationCase.model_validate_json(path.read_text(encoding="utf-8"))


def _save_state(output: Path, state: RunState) -> None:
    write_json(output / "state.json", state.model_dump(mode="json"))


def prepare(client: DevWerkHTTP, case: EvaluationCase, output: Path,
            *, service_revision: str | None = None, model_profile: str | None = None) -> RunState:
    if output.exists():
        raise FileExistsError(f"evaluation run already exists: {output}")
    project_dir = output / "project"
    project_dir.mkdir(parents=True)
    for name, content in case.files.items():
        target = project_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if case.source_files:
        if not case.source_root:
            raise ValueError("source_files require source_root")
        source_root = Path(case.source_root).resolve()
        for item in case.source_files:
            if item.relative_path in case.files:
                raise ValueError(f"source file conflicts with inline case file: {item.relative_path}")
            source = (source_root / item.relative_path).resolve()
            if not source.is_relative_to(source_root) or not source.is_file():
                raise ValueError(f"source file missing or outside task folder: {item.relative_path}")
            if source.stat().st_size != item.size_bytes or hashlib.sha256(source.read_bytes()).hexdigest() != item.sha256:
                raise ValueError(f"pinned source file changed: {item.relative_path}")
            target = project_dir / item.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    project = client.create_project(f"Benchmark {case.id}", case.title, str(project_dir.resolve()))
    if Path(project["base_dir"]).resolve() != project_dir.resolve():
        raise RuntimeError("server registered a different Project workspace; evaluation cannot continue")
    state = RunState(case=case, project_id=project["id"], service_revision=service_revision,
                     model_profile=model_profile,
                     started_at=utc_now())
    _save_state(output, state)
    write_json(output / "input_manifest.json", {
        "case_id": case.id, "source": case.source, "source_revision": case.source_revision,
        "case_sha256": hashlib.sha256(case.model_dump_json().encode("utf-8")).hexdigest(),
        "files": ({name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                   for name, content in case.files.items()} |
                  {item.relative_path: item.sha256 for item in case.source_files}),
        "service_revision": service_revision or "unknown",
        "model_profile": model_profile or "unknown",
    })
    return state


def _observe_until(observe: Callable[[], bool], deadline: float, *,
                   clock: Callable[[], float], sleep: Callable[[float], None],
                   interval: float) -> bool:
    first = True
    while True:
        if not first and clock() >= deadline:
            return False
        first = False
        if observe():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleep(min(interval, remaining))


def run(client: DevWerkHTTP, output: Path, *, limit_seconds: float = 3600,
        poll_seconds: float = 300, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep) -> RunState:
    """Resume a pending job before sending another user message."""
    state = RunState.model_validate_json((output / "state.json").read_text(encoding="utf-8"))
    deadline = clock() + limit_seconds
    if state.stop_reason:
        return state

    def tasks_finished() -> bool:
        tasks = client.tasks(state.project_id)
        if not tasks:
            return False
        if any(item["status"] in {"failed", "cancelled"} for item in tasks):
            state.stop_reason = "Task failed or was cancelled before the next scripted turn"
            _save_state(output, state)
            return True
        if not all(item["status"] in TERMINAL_TASKS for item in tasks):
            return False
        return bool(client.quiescence(state.project_id)["quiescent"])

    if state.waiting_for_tasks:
        if not _observe_until(tasks_finished, deadline, clock=clock, sleep=sleep, interval=poll_seconds):
            return state
        if state.stop_reason:
            return state
        state.waiting_for_tasks = False
        _save_state(output, state)
    for index in range(state.next_turn, len(state.case.turns)):
        turn = state.case.turns[index]
        if state.pending_job_id is None:
            receipt = client.submit(state.project_id, turn.message)
            state.pending_job_id = receipt["job"]["id"]
            _save_state(output, state)
        job: dict = {}

        def job_finished() -> bool:
            nonlocal job
            job = client.job(state.project_id, state.pending_job_id or "")
            return job["status"] in TERMINAL_JOBS

        if not _observe_until(job_finished, deadline, clock=clock, sleep=sleep, interval=poll_seconds):
            return state
        state.turns.append(TurnEvidence(job_id=job["id"], status=job["status"],
                                        error=job.get("error"), agent_run_id=job.get("agent_run_id"),
                                        user_message_id=job["user_message_id"],
                                        created_at=job.get("created_at"), finished_at=job.get("finished_at")))
        state.pending_job_id = None
        state.next_turn = index + 1
        _save_state(output, state)
        if job["status"] != "succeeded":
            return state
        if turn.wait_for_tasks or (turn.wait_for_tasks_if_created and client.tasks(state.project_id)):
            state.waiting_for_tasks = True
            _save_state(output, state)
            if not _observe_until(tasks_finished, deadline, clock=clock, sleep=sleep, interval=poll_seconds):
                return state
            if state.stop_reason:
                return state
            state.waiting_for_tasks = False
            _save_state(output, state)
    return state


def summarize(client: DevWerkHTTP, output: Path) -> EvaluationResult:
    state = RunState.model_validate_json((output / "state.json").read_text(encoding="utf-8"))
    facts = collect(client, state.project_id, output)
    complete = (state.next_turn == len(state.case.turns) and state.pending_job_id is None
                and not state.waiting_for_tasks
                and state.stop_reason is None
                and all(item.status == "succeeded" for item in state.turns))
    if any(turn.wait_for_tasks or turn.wait_for_tasks_if_created for turn in state.case.turns):
        complete = complete and bool(facts["quiescence"]["quiescent"])
    checks = [check_one(item, output / "project", facts) for item in state.case.checks]
    result = EvaluationResult(
        case_id=state.case.id, case_revision=state.case.source_revision,
        case_sha256=hashlib.sha256(state.case.model_dump_json().encode("utf-8")).hexdigest(),
        project_id=state.project_id, started_at=state.started_at, finished_at=utc_now(),
        service_revision=state.service_revision, model_profile=state.model_profile,
        turns=state.turns, checks=checks,
        status=overall(checks, complete),
        conversation=role_metrics(facts["agent_runs"], output / "evidence", "conversation"),
        column=role_metrics(facts["agent_runs"], output / "evidence", "column"),
        workflow_created=facts["board"].get("workflow") is not None,
        task_count=len(facts["tasks"]),
        done_count=sum(item["status"] == "done" for item in facts["tasks"]),
        error="observation incomplete" if not complete else None,
    )
    if state.stop_reason or any(item.status != "succeeded" for item in state.turns):
        result.status = EvaluationStatus.FAILED
        result.error = state.stop_reason or "Conversation Job failed; see state.json and evidence"
    write_json(output / "result.json", result.model_dump(mode="json"))
    return result

