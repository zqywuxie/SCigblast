#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PYTHON="${PYTHON_BIN:-python3}"
if [[ -n "${SCIGBLAST_RUNTIME_BIN_DIR:-}" ]]; then
  PYTHON="${SCIGBLAST_RUNTIME_BIN_DIR}/python3"
fi
exec "$PYTHON" "$SCRIPT_DIR/run_mapping.py" "$@"
