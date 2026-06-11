#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
image="${IMAGE:-vllm-skill-test:local}"
build_only=0

usage() {
  printf 'Usage: %s [--build-only]\n' "$0"
  printf 'Builds a Linux test image and runs ./scripts/test.sh inside it.\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --build-only)
      build_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

cd "$repo_dir"

docker build -t "$image" .
if [ "$build_only" -eq 1 ]; then
  printf 'Built %s\n' "$image"
  exit 0
fi

docker run --rm "$image" ./scripts/test.sh
