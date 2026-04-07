# Contributing to DevWerk

## Setup

```bash
# 1. Clone & enter backend
cd DevWerk/backend

# 2. Create virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set real API keys as REAL ENVIRONMENT VARIABLES, not in .env

# 5. Run
uvicorn app.main:app --reload --port 8000
# or on Windows:  startup.bat
```

## Environment Configuration

### APP_ENV — which mode?

| APP_ENV | Description |
|---------|-------------|
| `development` | Default. Hot-reload enabled. Ollama as LLM. |
| `test` | For CI. Uses test-specific overrides in `.env.test`. |
| `production` | No reload. Validates API keys are set. |

### LLM Provider

```bash
# Local Ollama (default — no API key needed)
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=deepseek-r1:32b

# OpenAI cloud
LLM_PROVIDER=openai
# Set the key as a REAL env var — NEVER in .env files:
#   Linux/macOS:  export OPENAI_API_KEY=sk-...
OPENAI_API_KEY=${OPENAI_API_KEY}   # reads from real env
OPENAI_MODEL=gpt-4o-mini
```

### Production Deployment

```bash
# Set real keys as environment variables — never in .env files
export OPENAI_API_KEY=sk-...
export APP_ENV=production
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Project Structure

```
backend/
├── app/
│   ├── core/
│   │   ├── config.py      # Pydantic Settings — env var definitions
│   │   ├── prompt.py       # System prompt templates
│   │   ├── prompt_factory.py
│   │   └── schema.py       # LLM response JSON schema
│   ├── models/
│   │   ├── ide.py          # Pydantic request/response models
│   │   └── plan.py         # Plan / ExecuteRequest models
│   ├── routes/
│   │   └── ide.py          # /v1/ide/* endpoints
│   ├── services/
│   │   ├── llm_factory.py  # Client factory
│   │   ├── openai_client.py
│   │   ├── ollama_client.py
│   │   ├── planner.py       # Plan phase: LLM research + plan extraction
│   │   ├── coerce.py
│   │   └── prompt_builder.py
│   └── main.py             # FastAPI app entry point
├── .env.example            # ← copy to .env and fill values
├── requirements.txt
└── startup.bat
```

## Running Tests

```bash
pytest -v
```

## Key Principles

1. **API keys never in committed files** — use real environment variables
2. **Settings as code** — Pydantic `Field()` docs explain every variable
3. **Fail fast** — `Settings.validate_provider()` raises on missing keys at startup
4. **Env-specific overrides** — `.env.development` / `.env.production` for local deviations
5. **Plan before Execute** — always show the user what will be changed before writing files
