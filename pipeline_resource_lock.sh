#!/usr/bin/env bash

# Two shared pipeline slots; the gate remains exclusive for Treemap batches.
# Both descriptors are inherited by children and released by the kernel.
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
