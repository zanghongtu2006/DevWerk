#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VERSION="${DEVWERK_VERSION:-0.1.0}"
SKIP_INSTALLERS="${DEVWERK_SKIP_INSTALLERS:-0}"
SKIP_DOCKER="${DEVWERK_SKIP_DOCKER:-0}"

sh "$ROOT/scripts/package-devwerk.sh"
sh "$ROOT/scripts/package-idea-plugin.sh"

if [ "$SKIP_INSTALLERS" != "1" ]; then
  sh "$ROOT/scripts/package-installers.sh"
fi

if [ "$SKIP_DOCKER" != "1" ]; then
  if command -v docker >/dev/null 2>&1; then
    DEVWERK_DOCKER_IMAGE="devwerk:$VERSION" sh "$ROOT/scripts/build-docker.sh"
  else
    echo "Skipping Docker image: docker was not found." >&2
  fi
fi

echo "All packages are under $ROOT/dist"
