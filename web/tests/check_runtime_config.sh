#!/usr/bin/env bash
# Config-only contract test: never launches analysis or reads sample files.
set -euo pipefail
cd "$(dirname "$0")/../.."
export SCIGBLAST_RUNTIME_BIN_DIR=/opt/conda/bin
for branch in IR_split 10X_split Igblast_base PigIgblast; do
    source "$branch/pipeline/00.pipeline_config.env"
    test "$PYTHON_BIN" = /opt/conda/bin/python3
    test "$SCIGBLAST_FASTP_BIN" = /opt/conda/bin/fastp
    test "$SCIGBLAST_PANDASEQ_BIN" = /opt/conda/bin/pandaseq
    test "$SCIGBLAST_IGBLAST_BIN" = /opt/conda/bin/igblastn
    test "$IGBLAST_BIN" = /opt/conda/bin/igblastn
    echo "$branch: runtime config OK"
done
