# Conversation Agent benchmark

This directory is a development-only benchmark. It calls the same HTTP conversation endpoint as the Web UI and lets DevWerk supervise its own Tasks. It does not import the production Agent or Runtime.

Run from `DevWerk/` with the repository virtual environment. DevWerk must already be healthy on port 8000.

```powershell
.\venv\Scripts\python.exe -m evaluation doctor
.\venv\Scripts\python.exe -m evaluation list
.\venv\Scripts\python.exe -m evaluation run --case G01 --output data/evals/baseline/G01-01 --limit-seconds 1800
.\venv\Scripts\python.exe -m evaluation collect data/evals/baseline/G01-01
.\venv\Scripts\python.exe -m evaluation resume data/evals/baseline/G01-01
```

Each `run` creates a new Project and writes its case materials under the new Project workspace. Do not reuse an output path. On interruption, `resume` loads `state.json`, checks an accepted job before sending another message, and preserves the same Project. `collect` reads evidence without sending messages. A timeout is recorded as incomplete; it does not cancel an active Task. The default progress observation interval is five minutes.

`result.json` has separate Conversation and Column metrics. `report.md` links to the persisted evidence directory. `passed` means only the declared checks passed. `unscored` means the outcome needs a judge or human review; a missing grade is never zero or success. Provider token usage remains unknown until a stable usage endpoint is available. The `--service-revision` argument is a **confirmed running service revision**, not the current checkout's HEAD; leave it unset when uncertain.

Compare repeated runs with the same case versions:

```powershell
.\venv\Scripts\python.exe -m evaluation compare --before data/evals/base/G01-01/result.json --after data/evals/new/G01-01/result.json --output data/evals/G01-comparison.md
```

## Harness² relationship

The interface separation follows `D:/workspace/github/harness2/harness2/core/spec.py` at commit `1cfe254`: task input, execution, trajectory, delivery and grade are distinct. The local cases are DevWerk development diagnostics. They are not Harness² leaderboard scores.

`evaluation/adapters/jobbench.py` imports the complete original `task_folder`, including binary documents, from a pinned JobBench `dataset/main` layout. It verifies every input hash and keeps `RUBRICS.json` outside the Project. `jobbench-judge` refreshes read-only Project evidence, stages only new or changed Project files, invokes the Harness²-pinned upstream JobBench Judge, and writes `jobbench_score.json` plus raw `judge_details.json`. `jobbench-report` writes Markdown and a JSON summary. Its primary attempted-task mean counts every incomplete or unscored attempted task as zero; it also retains the scored-task conditional mean for diagnosis. Raw receipts keep their actual statuses, while a completed run with no deliverables receives a Judge score of zero.

For a repeatable comparison, pin the [JobBench dataset](https://huggingface.co/datasets/JobBench/job-bench) to a revision, materialize its `dataset/<profession>/<task>` tree under a local `dataset/main`, and pin the [upstream Judge](https://github.com/Job-Bench/job-bench-eval) to `fa03a17f9ff662f136798a274e10011c3aa00fd0`. Apply Harness²'s `third_party/patches/job-bench-eval-judge.diff` to that checkout. Install its Python dependencies, set `JUDGE_API_KEY`, and choose a fixed Judge model and API endpoint. The evaluator checks the Judge pin and patch; it never prints the key.

```powershell
.\venv\Scripts\python.exe -m evaluation jobbench-list --root D:\path\to\job-bench-eval\dataset\main
.\venv\Scripts\python.exe -m evaluation jobbench-check --root D:\path\to\dataset\main --judge-repo D:\path\to\job-bench-eval --judge-python D:\path\to\judge-venv\Scripts\python.exe
.\venv\Scripts\python.exe -m evaluation run --jobbench-task occupation/task_id --jobbench-root D:\path\to\job-bench-eval\dataset\main --jobbench-revision pinned-revision --output data/evals/jb/task-01
.\venv\Scripts\python.exe -m evaluation jobbench-judge data/evals/jb/task-01 --root D:\path\to\dataset\main --judge-repo D:\path\to\job-bench-eval --judge-python D:\path\to\judge-venv\Scripts\python.exe --judge-model grok-4-1-fast --api-base https://api.x.ai/v1
.\venv\Scripts\python.exe -m evaluation jobbench-report data/evals/jb/task-01/jobbench_score.json --output data/evals/jb/scoreboard.md
.\venv\Scripts\python.exe -m evaluation jobbench-compare --before data/evals/base/task-01/jobbench_score.json --after data/evals/new/task-01/jobbench_score.json --output data/evals/jb/comparison.md
```

After a `run` that times out while a Task remains active, use `resume` before invoking the Judge. `jobbench-judge` refreshes read-only evidence itself. If the Project has no Task, the Conversation Job must finish and the Project must be quiescent. The current machine has one official task cached at dataset revision `d43a66575635da0d3f6cb9a20976dc1a60f3ffc9` (`technical_writers/task1`). The upstream Judge and its dependencies are installed in an isolated environment. A no-deliverable offline smoke returned 0/66; a later live one-task run using the configured MiniMax model as both executor and Judge returned 66/66. See `docs/2026-10-27-jobbench-online-smoke-result.md` for evidence and limitations. The normal CLI still requires `JUDGE_API_KEY` in its process environment.

On Windows, `evaluation/judge_compat/fcntl.py` provides the upstream Judge's per-process writer lock; run one Judge process per output directory. This preserves its scoring logic but differs from its POSIX runtime. A small task subset, DevWerk's tools/runtime, and a different Judge model are **not** full Harness² leaderboard equivalence. The scoreboard reports the same metric with these substrate limits.

`jobbench-compare` requires identical task IDs, rubric hashes, dataset revision and Judge across both sets. It reports the paired mean change only for tasks scored on both sides; ungraded tasks stay visible in the table.

The benchmark uses only `http://127.0.0.1:8000`. It never starts, stops or replaces that service. Generated raw evidence and Project workspaces live in ignored `data/evals/`; they can contain user or model output. Keep graders under `evaluation/rubrics/`, outside the Project workspace. A shared filesystem alone does not give formal answer isolation, so these runs are development diagnostics.

