# DevWerk Smoke Tests

These checks protect the current dynamic workflow architecture. Tests should
not assume default Kanban columns, fixed planner/coder/reviewer agents, or
legacy `/v1/plan` and `/v1/execute` APIs.

## Backend Full Smoke

```powershell
cd DevWerk
$env:LOG_FILE_ENABLED='false'
.\.venv\Scripts\python.exe -m pytest tests -q
```

Coverage:

- New projects start without workflow columns and cannot create tasks until a
  workflow is saved.
- Project conversation can save a non-coding workflow and can dispatch or
  continue tasks through `/v1/workflows`.
- Dynamic workflow columns spawn column agents from project workflow settings.
- Workflow definitions must explicitly declare success and failure semantic
  actions; no-transition columns are not terminal fallbacks.
- A repair-style coding workflow can produce file ops and reach its configured
  success terminal.
- A failing dynamic workflow reaches the project-defined failure terminal.
- Supervisor timeout, queued worker recovery, retry idempotency, and dispatch
  dedupe use project-defined actions and columns.
- Web UI routes use external template/CSS/JS files and all operational data is
  fetched from backend APIs.
- Usage summary supports global, project, task, and agent breakdowns.

Expected result:

```text
45 passed, 2 skipped
```

Skipped tests are opt-in real-browser and real-LLM tests.

## Syntax Smoke

```powershell
cd DevWerk
$env:LOG_FILE_ENABLED='false'
.\.venv\Scripts\python.exe -m compileall app tests
```

Expected result: no compile errors.

## Web UI Smoke

Unit coverage checks:

- `app/web/templates/dashboard.html` is the only HTML template.
- `/web/static/dashboard.css` owns layout and scrolling.
- `/web/static/dashboard.js` owns rendering and event handlers.
- Overview, Projects, Kanban, Tasks, Events, Memory, Analytics, and Settings
  have distinct renderers.
- No demo metrics or mock task data are used for operational views.

The optional browser smoke can be enabled with:

```powershell
$env:DEVWERK_RUN_BROWSER_SMOKE='1'
.\.venv\Scripts\python.exe -m pytest tests\test_web_ui_browser_smoke.py -q
```

## Dynamic Workflow Smoke

The backend coding smoke is intentionally model-free but workflow-real:

```powershell
cd DevWerk
$env:LOG_FILE_ENABLED='false'
.\.venv\Scripts\python.exe -m pytest tests\test_backend_coding_workflow.py -q
```

It creates an isolated project workflow with custom columns, stubs the LLM
client at the column-agent boundary, and verifies both success and failure
paths. The test must never depend on default columns.

## Real LLM Smoke

Real LLM smoke is opt-in because it spends provider quota:

```powershell
$env:DEVWERK_RUN_REAL_LLM_SMOKE='1'
.\.venv\Scripts\python.exe -m pytest tests\test_real_llm_smoke.py -q
```

Use this only after `config/llm.json` has a valid `routing.default` and API key.

## Plugin Kotlin Smoke

```powershell
cd idea-plugin
.\gradlew.bat compileKotlin
```

Coverage:

- Verifies IntelliJ-family plugin Kotlin sources compile.
- Does not start the IntelliJ sandbox.

## Safety Checks

Before pushing:

```powershell
cd DevWerk
$env:LOG_FILE_ENABLED='false'
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests -q

cd ..\idea-plugin
.\gradlew.bat compileKotlin
```

Also run a BOM scan and make sure ignored local files such as
`config/llm.json`, `.env`, and generated logs are not staged.
