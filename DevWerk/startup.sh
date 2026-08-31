#!/usr/bin/env sh
set -u

# DevWerk Service - Linux/macOS/container startup script
# Usage: ./startup.sh [development|production|test]

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

# Load .env as KEY=VALUE lines. Do not execute .env.
if [ -f .env ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      ''|'#'*) continue ;;
      *[!A-Za-z0-9_]*) continue ;;
    esac
    export "$key=$value"
  done < .env
fi

if [ "${1:-}" != "" ]; then
  APP_ENV="$1"
  export APP_ENV
fi

APP_ENV="${APP_ENV:-development}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-false}"
LOG_LEVEL="${LOG_LEVEL:-debug}"
UVICORN_ACCESS_LOG="${UVICORN_ACCESS_LOG:-true}"
export APP_ENV HOST PORT RELOAD LOG_LEVEL UVICORN_ACCESS_LOG

case "$APP_ENV" in
  development|production|test) ;;
  *)
    echo "[DevWerk] Invalid APP_ENV: $APP_ENV" >&2
    echo "[DevWerk] Valid values: development | production | test" >&2
    exit 1
    ;;
esac

PYTHON_EXE="$ROOT/venv/bin/python"
if [ ! -x "$PYTHON_EXE" ]; then
  echo "[DevWerk] Project virtual environment not found: $PYTHON_EXE" >&2
  echo "[DevWerk] Restore the existing DevWerk venv before starting the service." >&2
  exit 1
fi

if ! "$PYTHON_EXE" --version >/dev/null 2>&1; then
  echo "[DevWerk] Project virtual environment cannot be executed: $PYTHON_EXE" >&2
  echo "[DevWerk] DevWerk will not fall back to a system Python interpreter." >&2
  exit 1
fi

if [ ! -f requirements.txt ]; then
  echo "[DevWerk] requirements.txt not found. Are you in the DevWerk service directory?" >&2
  exit 1
fi

if ! "$PYTHON_EXE" -c "import fastapi, pydantic, uvicorn" >/dev/null 2>&1; then
  echo "[DevWerk] Service dependencies are missing or out of date."
  echo "[DevWerk] Installing requirements into $PYTHON_EXE ..."
  "$PYTHON_EXE" -m pip install --disable-pip-version-check -r requirements.txt || exit 1
fi

if ! "$PYTHON_EXE" -c "import fastapi, pydantic, uvicorn" >/dev/null 2>&1; then
  echo "[DevWerk] Dependency verification failed after installation." >&2
  exit 1
fi

DEVWERK_STARTUP_MANAGED=1
DEVWERK_RESTART_MARKER="$ROOT/data/restart.request"
DEVWERK_PID_FILE="$ROOT/data/devwerk.pid"
export DEVWERK_STARTUP_MANAGED DEVWERK_RESTART_MARKER
mkdir -p "$ROOT/data"

SERVICE_PID=""
stop_service() {
  if [ -n "$SERVICE_PID" ] && kill -0 "$SERVICE_PID" 2>/dev/null; then
    kill "$SERVICE_PID" 2>/dev/null || true
    wait "$SERVICE_PID" 2>/dev/null || true
  fi
  rm -f "$DEVWERK_PID_FILE"
}
trap 'stop_service; exit 130' INT
trap 'stop_service; exit 143' TERM
trap 'rm -f "$DEVWERK_PID_FILE"' EXIT

echo "[DevWerk] Starting in $APP_ENV mode..."
echo "[DevWerk] Python:             $PYTHON_EXE"
echo "[DevWerk] Starting uvicorn on http://$HOST:$PORT ..."
echo "[DevWerk] Log level:          $LOG_LEVEL"
echo "[DevWerk] API docs:           http://localhost:$PORT/docs"
echo "[DevWerk] Alternative docs:   http://localhost:$PORT/redoc"
echo "[DevWerk] Web workbench:      http://localhost:$PORT/"
echo "[DevWerk] Health endpoint:    http://localhost:$PORT/v1/health"
echo
echo "[DevWerk] Press Ctrl+C to stop."
echo

while :; do
  set -- -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level "$LOG_LEVEL"
  case "$RELOAD" in true|TRUE|1|yes|YES) set -- "$@" --reload ;; esac
  case "$UVICORN_ACCESS_LOG" in false|FALSE|0|no|NO) set -- "$@" --no-access-log ;; *) set -- "$@" --access-log ;; esac

  "$PYTHON_EXE" "$@" &
  SERVICE_PID=$!
  printf '%s\n' "$SERVICE_PID" > "$DEVWERK_PID_FILE"
  if wait "$SERVICE_PID"; then
    SERVICE_STATUS=0
  else
    SERVICE_STATUS=$?
  fi
  SERVICE_PID=""
  rm -f "$DEVWERK_PID_FILE"

  if [ -f "$DEVWERK_RESTART_MARKER" ]; then
    rm -f "$DEVWERK_RESTART_MARKER"
    echo "[DevWerk] Settings saved. Restarting with the Project virtual environment..."
    continue
  fi
  exit "$SERVICE_STATUS"
done
