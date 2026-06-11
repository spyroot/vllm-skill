#!/usr/bin/env bash
set -euo pipefail

# Local helper for Daedalus runtime checks. Defaults to focused unit tests and uses
# the active project conda Python when available so pytest is found consistently.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="${PYTHON}"
elif [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
  PYTHON_BIN="${CONDA_PREFIX}/bin/python"
else
  PYTHON_BIN="python3"
fi

usage() {
  cat <<EOF
Usage: $0 [test|pytest] [pytest args...]

Commands:
  test, pytest   Run Daedalus runtime focused pytest coverage.
  probe          Run OpenAI-compatible positive/negative endpoint interactions.

Environment:
  PYTHON         Override Python interpreter.

Default:
  $0 test
EOF
}

command="${1:-test}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "${command}" in
  test|tests|pytest)
    echo "Running Daedalus runtime tests with ${PYTHON_BIN}"
    exec "${PYTHON_BIN}" -m pytest "${SCRIPT_DIR}/tests" "$@"
    ;;
  probe)
    echo "Running OpenAI-compatible endpoint probe with ${PYTHON_BIN}"
    exec "${PYTHON_BIN}" "${SCRIPT_DIR}/openai_compat_probe.py" "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "ERROR: unknown command: ${command}" >&2
    usage >&2
    exit 1
    ;;
esac
