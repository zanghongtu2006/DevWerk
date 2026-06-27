#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
sh "$ROOT/scripts/package-devwerk.sh"
sh "$ROOT/scripts/package-idea-plugin.sh"
echo "All packages are under $ROOT/dist"
