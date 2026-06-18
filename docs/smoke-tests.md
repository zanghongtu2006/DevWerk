# DevWerk Smoke Tests

This document lists the smoke tests that should pass before frontend/backend
integration work continues. Smoke tests are intentionally small: they verify the
critical DevWerk loop without depending on a real external LLM unless stated.

## Backend Unit Smoke

Command:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests\test_smoke.py -q
```

Coverage:

- `IdeChatResponse` can represent backend errors without requiring a reply body.
- The default kanban lifecycle contains the required control states:
  `draft`, `context_indexed`, `planned`, `coding`, `ready_to_apply`, `applied`,
  `verified`, `done`, and `failed`.

Expected result:

```text
3 passed
```

## Backend Coding Workflow Smoke

Command:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests\test_backend_coding_workflow.py -q
```

Coverage:

- Uses an isolated SQLite database under pytest `tmp_path`.
- Uses stub LLM clients, so no real provider credentials or tokens are required.
- Calls `POST /v1/workflows` with a normal coding request and workspace summary.
- Verifies automatic workflow execution reaches `ready_to_apply` with file ops.
- Verifies interactive execution pauses at `planned`, then the same task resumes
  through `POST /v1/workflows/{task_id}/messages` after plan confirmation.
- Verifies durable conversation messages, column runs, candidate revisions,
  context compression, and result-cursor behavior.
- Verifies client-side `tool_requests` can accompany generated changes.
- Verifies a simulated plugin `apply_result` with passing verification moves the
  same kanban task to `done`.
- Verifies candidate plan paths are optional unless `required=true`, while
  unplanned writes and missing required changes still trigger rework.

Expected result:

```text
All coding workflow tests passed
```

## Backend Full Test Smoke

Command:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q
```

Coverage:

- Runs all backend smoke tests.
- This is the default backend-only safety check before IDE/plugin integration.

Expected result:

```text
8 passed
```

## Backend Debug Log Smoke

The backend defaults to debug logging. These settings can be overridden in
`backend/.env`:

```env
LOG_LEVEL=debug
LOG_FORMAT=%(asctime)s %(levelname)s [%(name)s] %(message)s
UVICORN_ACCESS_LOG=true
```

Command:

```powershell
cd backend
startup.bat
```

Coverage:

- Uvicorn starts with debug log level.
- `devwerk.*` loggers emit debug logs.
- Each `/v1/*` request logs start/end, method, path, project id, status, and duration.
- Usage, kanban, planner, coder harness, and IDE route debug logs are visible.

Expected result:

```text
[DevWerk] Log level:          debug
...
DEBUG [devwerk.request] request start method=...
DEBUG [devwerk.request] request end method=...
```

## Backend Syntax Smoke

Command:

```powershell
cd backend
.\.venv\Scripts\python.exe -m compileall app tests
```

Coverage:

- Compiles backend application and test modules.
- Catches syntax/import-level mistakes that pytest might not touch directly.

Expected result:

```text
Listing 'app'...
Listing 'tests'...
```

No compile errors should be printed.

## Plugin Kotlin Smoke

Command:

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin --offline --no-daemon
```

Coverage:

- Verifies IDE plugin Kotlin sources compile.
- Does not run a full Gradle build.
- Does not start IntelliJ sandbox.

Expected result:

```text
BUILD SUCCESSFUL
```

## Plugin runIde Startup Smoke

Command:

```powershell
cd idea-plugin
.\gradlew.bat runIde --no-daemon
```

Coverage:

- Runs the real IntelliJ sandbox with the DevWerk plugin installed.
- Verifies `ensureCoroutinesJavaAgent` produces
  `build/tmp/initializeIntelliJPlugin/coroutines-javaagent.jar`.
- Verifies `runIde` no longer fails with:
  `Error opening zip file or JAR manifest missing : ... coroutines-javaagent.jar`.

Expected result:

- IntelliJ starts successfully and stays alive.
- Stop the IDE manually after startup verification.

## Local End-to-End Developer Smoke

This helper is intentionally stored under `.devwerk/` and ignored by git.

Command:

```powershell
.\.devwerk\smoke\run-local-smoke.ps1 -IdeHoldSeconds 90
```

Coverage:

- Deletes and regenerates the coroutine javaagent jar.
- Runs plugin `compileKotlin`.
- Starts backend with `startup.bat`.
- Verifies `/docs`.
- Verifies backend API contract for `/v1/workflows`, workflow event/result
  endpoints, multi-turn messages, and `/v1/ide/attachments`.
- Starts real `runIde` and requires it to stay alive for the configured window.
- Cleans backend and IDE processes after the run.

Expected result:

```text
[DevWerk smoke] PASS
```

## Pre-Integration Checklist

Run this minimum set before frontend/backend debugging:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall app tests

cd ..\idea-plugin
.\gradlew.bat compileKotlin --offline --no-daemon
```

Then run `.\gradlew.bat runIde --no-daemon` when the IntelliJ sandbox is free.
