#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  sh ./install.sh
fi

. .venv/bin/activate
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
