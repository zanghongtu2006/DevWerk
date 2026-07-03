#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
IMAGE="${DEVWERK_DOCKER_IMAGE:-devwerk:local}"
DOCKERFILE="$ROOT/packaging/Dockerfile"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to build the DevWerk image." >&2
  exit 1
fi

docker build -f "$DOCKERFILE" -t "$IMAGE" "$ROOT"
echo "DevWerk Docker image: $IMAGE"
