"""python -m evaluation: isolated Conversation Agent benchmark commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import httpx
from pathlib import Path

from evaluation.adapters.devwerk_http import DevWerkHTTP
from evaluation.adapters.jobbench import discover, load_task
from evaluation.adapters.local_cases import load_local_case, local_cases
from evaluation.contracts import EvaluationResult
from evaluation.grading.jobbench import (HARNESS2_JUDGE_REVISION, JobBenchPreflight,
                                         JobBenchScore, JudgeDependencies, JudgeStatus, score_run)
from evaluation.reporting.jobbench import (compare as compare_jobbench, render as render_jobbench,
                                           summarize as summarize_jobbench)
from evaluation.reporting.comparison import compare
from evaluation.reporting.markdown import render
from evaluation.runner import load_case, prepare, run, summarize


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="DevWerk external Conversation Agent benchmark")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check the already running service on port 8000")
    commands.add_parser("list", help="List bundled local cases")
    execute = commands.add_parser("run", help="Create a fresh Project and run one case")
    source = execute.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="Bundled local case ID")
    source.add_argument("--case-file", type=Path, help="Versioned EvaluationCase JSON")
    source.add_argument("--jobbench-task", help="Task ID from a pinned JobBench dataset")
    execute.add_argument("--jobbench-root", type=Path, help="dataset/main for --jobbench-task")
    execute.add_argument("--jobbench-revision", help="Pinned dataset revision")
    execute.add_argument("--output", type=Path, required=True, help="New run directory under data/evals")
    execute.add_argument("--service-revision", help="Confirmed running service commit; omit if unknown")
    execute.add_argument("--model-profile", help="Confirmed Provider/model/config label; omit if unknown")
    execute.add_argument("--limit-seconds", type=int, default=3600)
    resume = commands.add_parser("resume", help="Continue a persisted run without resubmitting a job")
    resume.add_argument("output", type=Path)
    resume.add_argument("--limit-seconds", type=int, default=3600)
    collect = commands.add_parser("collect", help="Read-only evidence refresh and score")
    collect.add_argument("output", type=Path)
    comparison = commands.add_parser("compare", help="Compare two sets of result.json paths")
    comparison.add_argument("--before", type=Path, nargs="+", required=True)
    comparison.add_argument("--after", type=Path, nargs="+", required=True)
    comparison.add_argument("--output", type=Path, required=True)
    listing = commands.add_parser("jobbench-list", help="List tasks in an installed pinned dataset")
    listing.add_argument("--root", type=Path, required=True)
    check = commands.add_parser("jobbench-check", help="Check pinned dataset, Judge and dependencies before scoring")
    check.add_argument("--root", type=Path, required=True)
    check.add_argument("--judge-repo", type=Path, required=True)
    check.add_argument("--judge-python", type=Path)
    judge = commands.add_parser("jobbench-judge", help="Score a completed JobBench run with the pinned upstream Judge")
    judge.add_argument("output", type=Path)
    judge.add_argument("--root", type=Path, required=True, help="Pinned dataset/main")
    judge.add_argument("--judge-repo", type=Path, required=True)
    judge.add_argument("--judge-model", required=True)
    judge.add_argument("--api-base", required=True)
    judge.add_argument("--judge-python", type=Path, help="Interpreter with upstream Judge dependencies")
    report = commands.add_parser("jobbench-report", help="Summarize JobBench score files using Harness2 aggregation")
    report.add_argument("scores", type=Path, nargs="+")
    report.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("jobbench-compare", help="Compare paired JobBench scores across two versions")
    comparison.add_argument("--before", type=Path, nargs="+", required=True)
    comparison.add_argument("--after", type=Path, nargs="+", required=True)
    comparison.add_argument("--output", type=Path, required=True)
    return cli


def _collect(client: DevWerkHTTP, output: Path) -> EvaluationResult:
    result = summarize(client, output)
    (output / "report.md").write_text(render(result), encoding="utf-8")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "list":
        print("\n".join(local_cases()))
        return 0
    if args.command == "jobbench-list":
        print("\n".join(discover(args.root)))
        return 0
    if args.command == "jobbench-check":
        try:
            revision = subprocess.run(["git", "-C", str(args.judge_repo), "rev-parse", "HEAD"],
                                      capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            revision = None
        judge_file = args.judge_repo / "eval" / "judge.py"
        patched = judge_file.is_file() and "JUDGE_MAX_COMPLETION_TOKENS" in judge_file.read_text(encoding="utf-8")
        official_record = args.root / ".harness2-download.json"
        dataset_file = official_record if official_record.is_file() else args.root.parent / "dataset-revision.json"
        dataset = json.loads(dataset_file.read_text(encoding="utf-8")) if dataset_file.is_file() else None
        interpreter = args.judge_python or Path(sys.executable)
        try:
            modules = subprocess.run([str(interpreter), "-c",
                "import importlib.util,json;print(json.dumps({x:importlib.util.find_spec(x) is not None for x in ('openai','pandas','openpyxl','mammoth','pdfplumber')}))"],
                capture_output=True, text=True, check=True)
            dependencies = json.loads(modules.stdout)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            dependencies = {}
        availability = JudgeDependencies.model_validate(dependencies)
        dataset_revision = dataset.get("revision") if dataset else None
        task_count = len(discover(args.root))
        key_present = bool(os.environ.get("JUDGE_API_KEY"))
        status = JobBenchPreflight(
            dataset_revision=dataset_revision, dataset_tasks=task_count,
            judge_revision=revision, judge_pin_matches=revision == HARNESS2_JUDGE_REVISION,
            harness2_patch_applied=patched, judge_python=str(interpreter),
            dependencies=availability, judge_api_key_present=key_present,
            windows_fcntl_shim=os.name == "nt",
            ready_to_score=bool(dataset_revision and task_count and revision == HARNESS2_JUDGE_REVISION
                                and patched and availability.openai and key_present),
        )
        print(status.model_dump_json(indent=2))
        return 0 if status.ready_to_score else 1
    if args.command == "jobbench-judge":
        with DevWerkHTTP() as client:
            summarize(client, args.output.resolve())
        kwargs = {"python": args.judge_python} if args.judge_python else {}
        result = score_run(args.output.resolve(), args.root.resolve(), args.judge_repo.resolve(),
                           judge_model=args.judge_model, api_base=args.api_base, **kwargs)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        return 0 if result.status == JudgeStatus.SCORED else 1
    if args.command == "jobbench-report":
        scores = [JobBenchScore.model_validate_json(path.read_text(encoding="utf-8")) for path in args.scores]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_jobbench(scores), encoding="utf-8")
        args.output.with_suffix(".json").write_text(
            summarize_jobbench(scores).model_dump_json(indent=2), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "jobbench-compare":
        before = [JobBenchScore.model_validate_json(path.read_text(encoding="utf-8")) for path in args.before]
        after = [JobBenchScore.model_validate_json(path.read_text(encoding="utf-8")) for path in args.after]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(compare_jobbench(before, after), encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "compare":
        before = [EvaluationResult.model_validate_json(path.read_text(encoding="utf-8")) for path in args.before]
        after = [EvaluationResult.model_validate_json(path.read_text(encoding="utf-8")) for path in args.after]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(compare(before, after), encoding="utf-8")
        print(args.output)
        return 0
    with DevWerkHTTP() as client:
        if args.command == "doctor":
            try:
                health = client.health()
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                print(json.dumps({"status": "unavailable", "reason": str(exc)}, ensure_ascii=False))
                return 2
            print(json.dumps({"status": health["status"], "supervisor": health["supervisor"],
                              "conversation_gateway": health["conversation_gateway"]["status"]}))
            return 0 if health["status"] == "ok" else 2
        if args.command == "run":
            if args.limit_seconds <= 0:
                raise ValueError("limit-seconds must be positive")
            if args.jobbench_task:
                if not args.jobbench_root or not args.jobbench_revision:
                    raise ValueError("JobBench requires --jobbench-root and --jobbench-revision")
                case = load_task(args.jobbench_root, args.jobbench_task, revision=args.jobbench_revision)
            else:
                case = load_local_case(args.case) if args.case else load_case(args.case_file)
            output = args.output.resolve()
            prepare(client, case, output, service_revision=args.service_revision,
                    model_profile=args.model_profile)
            run(client, output, limit_seconds=args.limit_seconds)
        elif args.command == "resume":
            output = args.output.resolve()
            run(client, output, limit_seconds=args.limit_seconds)
        else:
            output = args.output.resolve()
        result = _collect(client, output)
        print(json.dumps({"project_id": result.project_id, "status": result.status.value,
                          "report": str(output / "report.md")}, ensure_ascii=False))
        return 0 if result.status.value == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

