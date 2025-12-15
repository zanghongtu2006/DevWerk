# README.md

## Run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# optional: export env or use .env with your runner
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export OLLAMA_MODEL="deepseek-r1:32b"
export OLLAMA_TIMEOUT="180"

uvicorn app.main:app --host 0.0.0.0 --port 8000
