#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="${WESTCOTT_PYTHON:-python3}"
if [[ "$PYTHON" == */* ]]; then
  [[ -x "$PYTHON" ]] || { echo "configured Python interpreter is unavailable" >&2; exit 69; }
else
  command -v "$PYTHON" >/dev/null 2>&1 || { echo "configured Python interpreter is unavailable" >&2; exit 69; }
fi
exec "$PYTHON" "$SCRIPT_DIR/claude_code_runner.py" "$@"
