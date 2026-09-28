#!/usr/bin/env bash

# Serialize independent SCigblast pipeline processes that share one memory
# cgroup or host. The kernel lock is released automatically on process exit.
scigblast_acquire_resource_lock() {
    local lock_file="${SCIGBLAST_RESOURCE_LOCK_FILE:-/colddata/SCigblast/results/.scigblast-resource.lock}"
    local wait_seconds="${SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS:-86400}"

    [[ "$wait_seconds" =~ ^[0-9]+$ ]] || {
        echo "[SCigblast] SCIGBLAST_RESOURCE_LOCK_WAIT_SECONDS must be a non-negative integer" >&2
        return 2
    }
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
    if ! flock -x -w "$wait_seconds" "$SCIGBLAST_RESOURCE_LOCK_FD"; then
        echo "[SCigblast] timed out waiting for shared pipeline resource lock after ${wait_seconds}s" >&2
        return 75
    fi
    echo "[SCigblast] acquired shared pipeline resource lock"
}
