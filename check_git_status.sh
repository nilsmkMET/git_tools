#!/usr/bin/env bash
set -euo pipefail

# Determine script root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="python3"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python interpreter not found in PATH." >&2
    exit 1
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/check_git_status.py" "$@"
