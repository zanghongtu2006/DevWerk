#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP="$ROOT/DevWerk"
DIST="$ROOT/dist"
STAGE="$DIST/DevWerk"
PACKAGE="$DIST/devwerk-release.zip"

test -d "$APP"
rm -rf "$STAGE"
mkdir -p "$STAGE" "$DIST"

tar -C "$APP" \
  --exclude='.idea' \
  --exclude='.pytest_cache' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='data' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='.env.development' \
  --exclude='.env.production' \
  --exclude='.env.test' \
  --exclude='config/llm.json' \
  --exclude='tests' \
  --exclude='venv' \
  -cf - . | tar -C "$STAGE" -xf -

chmod +x "$STAGE/install.sh" "$STAGE/start.sh"
rm -f "$PACKAGE"
if command -v zip >/dev/null 2>&1; then
  (cd "$STAGE" && zip -qr "$PACKAGE" .)
else
  if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "zip or Python is required to create $PACKAGE" >&2
    exit 1
  fi
  "$PYTHON" - <<'PY' "$STAGE" "$PACKAGE"
from pathlib import Path
import sys, zipfile
stage = Path(sys.argv[1])
package = Path(sys.argv[2])
with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as z:
    for path in stage.rglob("*"):
        if path.is_file():
            z.write(path, path.relative_to(stage))
PY
fi
echo "DevWerk package: $PACKAGE"
