#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
if [[ -z "${SCIGBLAST_RESOURCE_LOCK_FILE:-}" && -f "${SCRIPT_DIR}/00.pipeline_config.env" ]]; then
  SCIGBLAST_RESOURCE_LOCK_FILE="$(bash -c '. "$1"; printf "%s" "${SCIGBLAST_RESOURCE_LOCK_FILE:-}"' _ "${SCRIPT_DIR}/00.pipeline_config.env")"
fi
source "${BRANCH_ROOT}/../pipeline_resource_lock.sh"
scigblast_acquire_resource_lock || exit $?
PYTHON="${PYTHON_BIN:-python3}"
if [[ -n "${SCIGBLAST_RUNTIME_BIN_DIR:-}" ]]; then
  PYTHON="${SCIGBLAST_RUNTIME_BIN_DIR}/python3"
fi
exec "$PYTHON" "$SCRIPT_DIR/run_mapping.py" "$@"
