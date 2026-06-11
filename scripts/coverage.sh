#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python_bin="${PYTHON_BIN:-python3}"
fail_under="${COVERAGE_FAIL_UNDER:-80}"

if ! command -v "$python_bin" >/dev/null 2>&1; then
  printf 'BLOCKER: Python is required but was not found. Set PYTHON_BIN or activate the project env.\n' >&2
  exit 78
fi

if ! "$python_bin" -c 'import coverage, pytest' >/dev/null 2>&1; then
  printf 'BLOCKER: coverage and pytest are required in the selected Python environment.\n' >&2
  exit 78
fi

cd "$repo_dir"

"$python_bin" -m coverage erase
PYTEST_DISABLE_PLUGIN_AUTOLOAD="${PYTEST_DISABLE_PLUGIN_AUTOLOAD:-1}" \
  "$python_bin" -m coverage run -m pytest tests runtime/daedalus-vllm/tests
"$python_bin" -m coverage report --fail-under="$fail_under"
"$python_bin" -m coverage xml
"$python_bin" -m coverage html
