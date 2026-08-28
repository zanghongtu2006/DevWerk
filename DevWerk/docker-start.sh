#!/usr/bin/env sh
set -eu

cd /opt/devwerk
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
