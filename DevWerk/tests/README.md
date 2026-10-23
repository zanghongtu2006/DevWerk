# Tests

`unit/` contains deterministic tests grouped by the behavior they protect. Keep a new regression beside the owning module; use `helpers.py` for shared construction, `support/` for stable test paths, and `conftest.py` for isolated fixtures. Do not import a production behavior from another test module merely to exercise it.

| Package | What it covers | Modules |
| --- | --- | ---: |
| `unit/agent` | Agent assignments, context and memory | 4 |
| `unit/api` | HTTP/Web contracts | 1 |
| `unit/config` | Settings and architecture boundaries | 2 |
| `unit/conversation` | Conversation, intent, scope and supervision | 4 |
| `unit/evaluation` | Evaluation harness behavior | 1 |
| `unit/storage` | Project persistence | 2 |
| `unit/tooling` | Tool Registry, provider and workspace files | 4 |
| `unit/workflow` | Definitions, scheduling, completion and recovery | 8 |

The migration preserved all **572** previously collected cases and added one default Loop-path regression. Collected case counts, including parameterization, are authoritative; the number of `test_` functions is smaller.

From the repository root:

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit -q
```

Run the owning package or module first while fixing a failure, then run the full deterministic suite. Live Provider and Runtime delivery checks belong to separate integration records; they must not be replaced with mocked unit results.
