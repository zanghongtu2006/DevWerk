# Contributing to DevWerk

## Setup

```bash
cd DevWerk
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
copy config\llm.example.json config\llm.json
.\startup.bat
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/llm.example.json config/llm.json
uvicorn app.main:app --reload --port 8000
```

Open:

```text
http://localhost:8000/dashboard
http://localhost:8000/workbench
http://localhost:8000/docs
```

## Configuration

Keep `.env` small. Structured model routing belongs in
`DevWerk/config/llm.json`, which is ignored by git.

Do not commit real API keys. Use local ignored config files or real environment
variables.

## Project Structure

```text
DevWerk/
  app/
    core/        configuration, prompt contracts, schema
    models/      protocol and planning models
    routes/      workflows, Kanban, settings, Web UIs
    services/    workflow engine, agents, LLM clients, storage
  config/
    agents/
    workflows/
    llm.example.json
  tests/
  startup.bat

idea-plugin/
  IntelliJ capability provider and snapshot-protected apply path

docs/
  runtime notes and smoke tests
```

## Checks

```powershell
cd DevWerk
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m pytest tests

cd ..\idea-plugin
.\gradlew.bat test verifyPlugin --no-daemon
```

## Principles

1. Kanban is the source of task truth.
2. Columns and agents are independent; workflow must stay configurable.
3. DevWerk asks for capabilities, not IDE-specific APIs.
4. Source writes are performed by capability providers through snapshots.
5. Events, artifacts, and phase outputs must make workflow movement auditable.
6. API keys and local runtime data never belong in committed files.
