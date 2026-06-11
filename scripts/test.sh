#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
bats_bin="${BATS_BIN:-}"
shellcheck_bin="${SHELLCHECK_BIN:-shellcheck}"
rg_bin="${RG_BIN:-rg}"
if [ -n "${PYTHON_BIN:-}" ]; then
  python_bin="$PYTHON_BIN"
elif [ -n "${PYTHON:-}" ]; then
  python_bin="$PYTHON"
elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "${CONDA_PREFIX}/bin/python" ]; then
  python_bin="${CONDA_PREFIX}/bin/python"
else
  python_bin="python3"
fi

if [ -z "$bats_bin" ]; then
  if [ -x /opt/homebrew/bin/bats ]; then
    bats_bin="/opt/homebrew/bin/bats"
  else
    bats_bin="bats"
  fi
fi

if ! command -v "$shellcheck_bin" >/dev/null 2>&1; then
  printf 'BLOCKER: shellcheck is required but was not found on PATH.\n' >&2
  exit 78
fi

if ! command -v "$rg_bin" >/dev/null 2>&1; then
  printf 'BLOCKER: ripgrep is required but rg was not found on PATH.\n' >&2
  exit 78
fi

if ! command -v "$bats_bin" >/dev/null 2>&1; then
  printf 'BLOCKER: bats is required but was not found. Set BATS_BIN or install bats-core.\n' >&2
  exit 78
fi

if ! command -v "$python_bin" >/dev/null 2>&1; then
  printf 'BLOCKER: Python is required but was not found. Set PYTHON_BIN or activate the project conda env.\n' >&2
  exit 78
fi

if ! "$python_bin" -c 'import pytest' >/dev/null 2>&1; then
  printf 'BLOCKER: pytest is required in the selected Python environment. Use the project conda env or install pytest.\n' >&2
  exit 78
fi

cd "$repo_dir"

tmp_shell_scripts="$(mktemp "${TMPDIR:-/tmp}/vllm-skill-shell-scripts.XXXXXX")"
tmp_bats_tests="$(mktemp "${TMPDIR:-/tmp}/vllm-skill-bats-tests.XXXXXX")"
trap 'rm -f "$tmp_shell_scripts" "$tmp_bats_tests"' EXIT

find scripts skills runtime -type f -name '*.sh' | sort > "$tmp_shell_scripts"
if [ -s "$tmp_shell_scripts" ]; then
  xargs "$shellcheck_bin" < "$tmp_shell_scripts"
  while IFS= read -r script_path; do
    bash -n "$script_path"
  done < "$tmp_shell_scripts"
fi

find tests skills runtime -type f -name '*.bats' 2>/dev/null | sort > "$tmp_bats_tests"
if [ -s "$tmp_bats_tests" ]; then
  xargs "$bats_bin" < "$tmp_bats_tests"
fi

PYTEST_DISABLE_PLUGIN_AUTOLOAD="${PYTEST_DISABLE_PLUGIN_AUTOLOAD:-1}" \
  "$python_bin" -m pytest \
  "${repo_dir}/tests" \
  "${repo_dir}/runtime/daedalus-vllm/tests"

printf 'All checks passed.\n'
