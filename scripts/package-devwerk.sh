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
  --exclude='.pytest_cache' \
  --exclude='__pycache__' \
  --exclude='data' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='.env.development' \
  --exclude='.env.production' \
  --exclude='.env.test' \
  --exclude='config/llm.json' \
  --exclude='tests' \
  -cf - . | tar -C "$STAGE" -xf -

cat > "$STAGE/install.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo "DevWerk installed. Copy config/llm.example.json to config/llm.json and set credentials before starting."
EOF

cat > "$STAGE/start.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  sh ./install.sh
fi
. .venv/bin/activate
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
EOF

cat > "$STAGE/install.bat" <<'EOF'
@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv || python -m venv .venv
call .venv\Scripts\python.exe -m pip install --upgrade pip
call .venv\Scripts\pip.exe install -r requirements.txt
echo DevWerk installed. Copy config\llm.example.json to config\llm.json and set credentials before starting.
EOF

cat > "$STAGE/start.bat" <<'EOF'
@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe call install.bat
call .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
EOF

chmod +x "$STAGE/install.sh" "$STAGE/start.sh"
rm -f "$PACKAGE"
if command -v zip >/dev/null 2>&1; then
  (cd "$STAGE" && zip -qr "$PACKAGE" .)
else
  python3 - <<'PY' "$STAGE" "$PACKAGE"
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
