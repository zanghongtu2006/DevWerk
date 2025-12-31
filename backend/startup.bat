$env:OLLAMA_BASE_URL="http://82.157.232.122:12434"
$env:OLLAMA_MODEL="deepseek-r1:32b"
$env:OLLAMA_TIMEOUT="180"
uvicorn app.main:app --reload --port 8000
