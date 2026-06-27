#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PLUGIN="$ROOT/idea-plugin"
DIST="$ROOT/dist/idea-plugin"

test -d "$PLUGIN"
cd "$PLUGIN"
./gradlew buildPlugin
mkdir -p "$DIST"
find "$PLUGIN/build/distributions" -maxdepth 1 -name '*.zip' -type f -exec cp {} "$DIST/" \;
echo "IntelliJ-family plugin package directory: $DIST"
