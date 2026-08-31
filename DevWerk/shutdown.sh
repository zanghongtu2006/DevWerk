#!/usr/bin/env sh
set -u

# DevWerk Service - Linux/macOS/container shutdown script
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PID_FILE="$ROOT/data/devwerk.pid"
RESTART_MARKER="$ROOT/data/restart.request"

rm -f "$RESTART_MARKER"

if [ ! -f "$PID_FILE" ]; then
  echo "[DevWerk] No running DevWerk service found."
  exit 0
fi

PID="$(sed -n '1p' "$PID_FILE")"
case "$PID" in
  ''|*[!0-9]*)
    echo "[DevWerk] Invalid PID file: $PID_FILE" >&2
    rm -f "$PID_FILE"
    exit 1
    ;;
esac

if ! kill -0 "$PID" 2>/dev/null; then
  echo "[DevWerk] DevWerk process $PID is no longer running."
  rm -f "$PID_FILE"
  exit 0
fi

COMMAND="$(ps -p "$PID" -o args= 2>/dev/null || true)"
case "$COMMAND" in
  *"$ROOT/venv/bin/python"*"-m uvicorn app.main:app"*) ;;
  *)
    echo "[DevWerk] PID $PID does not belong to this DevWerk installation." >&2
    exit 1
    ;;
esac

echo "[DevWerk] Stopping process $PID..."
kill "$PID"

COUNT=0
while kill -0 "$PID" 2>/dev/null && [ "$COUNT" -lt 50 ]; do
  sleep 0.1
  COUNT=$((COUNT + 1))
done

if kill -0 "$PID" 2>/dev/null; then
  echo "[DevWerk] Process $PID did not stop; forcing shutdown."
  kill -9 "$PID"
fi

rm -f "$PID_FILE"
echo "[DevWerk] DevWerk service stopped."
