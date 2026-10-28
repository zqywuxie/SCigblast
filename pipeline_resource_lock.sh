#!/usr/bin/env bash

# Two shared pipeline slots; the gate remains exclusive for Treemap batches.
# Both descriptors are inherited by children and released by the kernel.

# Resource estimates are admission controls, not kernel-enforced RSS limits.
# Stages fitting half of both budgets may coexist; larger/unknown stages take
# the budget gate exclusively. All runners must use the same lock file.
scigblast_acquire_stage_budget() {
    local memory_gb="$1" label="$2" lock_file wait_seconds total_gb current cpus threads mode=exclusive threshold shared_gb root python_bin resources resource_ok=0
    lock_file="${SCIGBLAST_RESOURCE_LOCK_FILE:-/colddata/SCigblast/results/.scigblast-resource.lock}"
    wait_seconds="${SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS:-86400}"
    [[ "$memory_gb" =~ ^[0-9]+$ && "$wait_seconds" =~ ^[0-9]+$ ]] || { echo "[SCigblast] invalid stage resource estimate" >&2; return 2; }
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    python_bin="${PYTHON_BIN:-python3}"
    [[ -n "${SCIGBLAST_RUNTIME_BIN_DIR:-}" ]] && python_bin="${SCIGBLAST_RUNTIME_BIN_DIR}/python3"
    resources="$("$python_bin" - "$root/mapping/pipeline" <<'PY'
import math, sys
sys.path.insert(0, sys.argv[1])
from resources import detect_resources
resource = detect_resources()
if resource['memory_limit_bytes'] is None:
    raise SystemExit(1)
print(resource['memory_limit_bytes'] // (1024 ** 3), max(1, math.floor(resource['cpu_count'])))
PY
)" || resources=''
    if [[ "$resources" =~ ^([0-9]+)[[:space:]]+([1-9][0-9]*)$ ]]; then
        total_gb="${BASH_REMATCH[1]}"; cpus="${BASH_REMATCH[2]}"; resource_ok=1
    else
        # A failed probe is never evidence that two heavy stages can overlap.
        total_gb=0; cpus=1
    fi
    current="${SCIGBLAST_SHARED_MEMORY_BUDGET_GB:-300}"
    threshold="${SCIGBLAST_SHARED_MEMORY_RESERVE_GB:-150}"
    [[ "$current" =~ ^[1-9][0-9]*$ && "$threshold" =~ ^[0-9]+$ ]] || { echo "[SCigblast] invalid shared memory budget" >&2; return 2; }
    (( current < total_gb )) && total_gb=$current
    shared_gb=$((total_gb > threshold ? total_gb - threshold : 0))
    case "$label" in
        fastp) threads=$(( ${SCIGBLAST_FASTP_THREADS:-${FASTP_THREADS:-16}} * ${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-${MAX_JOBS:-2}} )) ;;
        # Cleaning may launch both decompression and compression workers per
        # sample; until their CPU use is measured, keep it exclusive.
        clean|"clean header") threads=0 ;;
        pandaseq|PANDAseq) threads=$(( ${SCIGBLAST_PANDASEQ_THREADS:-${PANDASEQ_THREADS:-16}} * ${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-${PANDASEQ_MAX_PARALLEL_SAMPLES:-2}} )) ;;
        igblast|IgBLAST) threads=$(( ${SCIGBLAST_IGBLAST_THREADS:-${IGBLAST_THREADS:-16}} * ${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-4} )) ;;
        *) threads=0 ;;
    esac
    # Different per-pipeline watchdog thresholds must not overlap: another
    # stage could otherwise cause the stricter watchdog to terminate work.
    current="${MEMORY_STOP_THRESHOLD_GB:-${SCIGBLAST_MEMORY_STOP_THRESHOLD_GB:-$threshold}}"
    if [[ "$current" =~ ^[0-9]+$ ]] && (( resource_ok == 1 && ${SCIGBLAST_SHARED_MEMORY_BUDGET_GB:-300} == 300 && threshold == 150 && current <= threshold && threads > 0 && threads * 2 <= cpus && memory_gb * 2 <= shared_gb )); then
        mode=shared
    fi
    exec {SCIGBLAST_RESOURCE_BUDGET_FD}>>"${lock_file}.budget" || { echo "[SCigblast] cannot open stage resource gate" >&2; return 2; }
    export SCIGBLAST_RESOURCE_BUDGET_FD
    echo "[SCigblast] waiting stage resources: stage=${label} mode=${mode} memory_estimate=${memory_gb}GB threads_estimate=${threads} shared_memory=${shared_gb}GB cpus=${cpus}"
    if [[ "$mode" == shared ]]; then
        flock -s -w "$wait_seconds" "$SCIGBLAST_RESOURCE_BUDGET_FD" || { exec {SCIGBLAST_RESOURCE_BUDGET_FD}>&-; return 75; }
    else
        flock -x -w "$wait_seconds" "$SCIGBLAST_RESOURCE_BUDGET_FD" || { exec {SCIGBLAST_RESOURCE_BUDGET_FD}>&-; return 75; }
    fi
    echo "[SCigblast] acquired stage resources: stage=${label} mode=${mode}"
}

scigblast_release_stage_budget() {
    if [[ -n "${SCIGBLAST_RESOURCE_BUDGET_FD:-}" ]]; then
        exec {SCIGBLAST_RESOURCE_BUDGET_FD}>&-
        unset SCIGBLAST_RESOURCE_BUDGET_FD
    fi
}

scigblast_igblast_workers() {
    local requested="$1" threads="$2" thread_budget="$3" memory_budget="$4" memory_per_job="$5" value
    for value in "$requested" "$threads" "$thread_budget" "$memory_budget" "$memory_per_job"; do
        [[ "$value" =~ ^[1-9][0-9]*$ ]] || { echo "[SCigblast] IgBLAST budgets must be positive integers" >&2; return 2; }
    done
    SCIGBLAST_EFFECTIVE_IGBLAST_JOBS=$requested
    (( SCIGBLAST_EFFECTIVE_IGBLAST_JOBS > 4 )) && SCIGBLAST_EFFECTIVE_IGBLAST_JOBS=4
    value=$((thread_budget / threads))
    (( SCIGBLAST_EFFECTIVE_IGBLAST_JOBS > value )) && SCIGBLAST_EFFECTIVE_IGBLAST_JOBS=$value
    value=$((memory_budget / memory_per_job))
    (( SCIGBLAST_EFFECTIVE_IGBLAST_JOBS > value )) && SCIGBLAST_EFFECTIVE_IGBLAST_JOBS=$value
    (( SCIGBLAST_EFFECTIVE_IGBLAST_JOBS > 0 )) || { echo "[SCigblast] IgBLAST thread or memory budget cannot accommodate one worker" >&2; return 2; }
    return 0
}

scigblast_acquire_resource_lock() {
    local lock_file="${SCIGBLAST_RESOURCE_LOCK_FILE:-/colddata/SCigblast/results/.scigblast-resource.lock}"
    local wait_seconds="${SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS:-86400}"
    local started_seconds=$SECONDS slot wait_limit

    [[ "$wait_seconds" =~ ^[0-9]+$ ]] || {
        echo "[SCigblast] SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS must be a non-negative integer" >&2
        return 2
    }
    wait_limit=$((10#$wait_seconds))
    command -v flock >/dev/null 2>&1 || {
        echo "[SCigblast] flock is required for shared pipeline resource scheduling" >&2
        return 2
    }
    mkdir -p "$(dirname "$lock_file")" || {
        echo "[SCigblast] cannot create resource lock directory: $(dirname "$lock_file")" >&2
        return 2
    }

    exec {SCIGBLAST_RESOURCE_LOCK_FD}>"$lock_file" || {
        echo "[SCigblast] cannot open shared resource lock: $lock_file" >&2
        return 2
    }
    echo "[SCigblast] waiting for shared pipeline resource lock: $lock_file"
    if ! flock -s -w "$wait_seconds" "$SCIGBLAST_RESOURCE_LOCK_FD"; then
        echo "[SCigblast] timed out waiting for shared pipeline resource lock after ${wait_seconds}s" >&2
        exec {SCIGBLAST_RESOURCE_LOCK_FD}>&-
        return 75
    fi
    while true; do
        for slot in 1 2; do
            if ! exec {SCIGBLAST_RESOURCE_SLOT_FD}>"${lock_file}.slot-${slot}"; then
                exec {SCIGBLAST_RESOURCE_LOCK_FD}>&-
                echo "[SCigblast] cannot open pipeline resource slot: ${lock_file}.slot-${slot}" >&2
                return 2
            fi
            if flock -x -n "$SCIGBLAST_RESOURCE_SLOT_FD"; then
                export SCIGBLAST_RESOURCE_LOCK_FD SCIGBLAST_RESOURCE_SLOT_FD
                echo "[SCigblast] acquired shared pipeline resource lock"
                echo "[SCigblast] pipeline resource slot: ${slot}/2"
                return 0
            fi
            exec {SCIGBLAST_RESOURCE_SLOT_FD}>&-
        done
        if (( SECONDS - started_seconds >= wait_limit )); then
            exec {SCIGBLAST_RESOURCE_LOCK_FD}>&-
            echo "[SCigblast] timed out waiting for shared pipeline resource lock after ${wait_seconds}s" >&2
            return 75
        fi
        sleep 0.2
    done
}
