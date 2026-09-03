#!/usr/bin/env bash
set -u

# Stop the complete 10X pipeline and all child processes.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
CONFIG_FILE="${SCRIPT_DIR}/00.pipeline_config.env"
[[ -f "$CONFIG_FILE" ]] && source "$CONFIG_FILE" 2>/dev/null || true
resolve_output_root(){ local value="${1:-}"; if [[ -z "$value" ]]; then printf '%s/output\n' "$BRANCH_ROOT"; elif [[ "$value" == /* ]]; then printf '%s\n' "$value"; else printf '%s/%s\n' "$BRANCH_ROOT" "$value"; fi; }
OUTPUT_ROOT="$(resolve_output_root "${RUNTIME_OUTPUT_ROOT:-${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-}}}")"
STATE_ROOT="${OUTPUT_ROOT}/.pipeline_state"
PID_DIR="${SCRIPT_DIR}/.pids"  # legacy registry
PROCESS_MARKERS=(
"run_10x_pipeline.sh" "01.match_sample.py" "02.run_fastp.sh"
    "03.prefilter_r1_r2.py" "04.clean_header.sh" "05.work_pandaseq.sh"
    "06.split_and_represent.py" "07.work_igblastn.sh" "08.chain_cluster.py"
)

children_of() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -P "$1" 2>/dev/null || true
    else
        ps -eo pid=,ppid= | awk -v parent="$1" '$2 == parent {print $1}'
    fi
}

pid_alive() {
    local pid="$1" state
    [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    [[ -z "$state" || "$state" != Z* ]]
}

pid_command() {
    local pid="$1"
    if [[ -r "/proc/${pid}/cmdline" ]]; then
        tr '\0' ' ' < "/proc/${pid}/cmdline"
    else
        ps -p "$pid" -o args= 2>/dev/null || true
    fi
}

pid_cwd() {
    local pid="$1"
    if [[ -e "/proc/${pid}/cwd" ]]; then
        readlink -f "/proc/${pid}/cwd" 2>/dev/null || true
    fi
}

pid_environment() {
    local pid="$1"
    [[ -r "/proc/${pid}/environ" ]] || return 0
    tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null || true
}

declare -A TARGET_ROOTS=()
[[ -n "$OUTPUT_ROOT" && "$OUTPUT_ROOT" != "/" ]] && TARGET_ROOTS["$OUTPUT_ROOT"]=1
remember_roots() {
    local pid="$1" line value
    while IFS= read -r line; do
        case "$line" in
            SCIGBLAST_OUTPUT_ROOT=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
            OUTPUT_ROOT=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
            SCIGBLAST_*_OUTPUT_DIR=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
            SCIGBLAST_*_OUTPUT=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
        esac
    done < <(pid_environment "$pid")
}

# A PID file can disappear when the parent shell is interrupted while a stage
# worker remains alive.  Also discover orphaned workers by script path/name,
# while restricting matches to this branch's pipeline directory.
matches_pipeline_process() {
    local pid="$1" command cwd environment marker root branch_in_command=0
    [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || return 1
    command="$(pid_command "$pid")"
    cwd="$(pid_cwd "$pid")"
    environment="$(pid_environment "$pid")"
    [[ "$command" == *"${SCRIPT_DIR}"* ]] && branch_in_command=1
    for marker in "${PROCESS_MARKERS[@]}"; do
        if [[ "$command" == *"$marker"* ]] && (( branch_in_command )); then
            return 0
        fi
        if [[ "$command" == *"$marker"* ]] && [[ "$cwd" == "$SCRIPT_DIR" ]]; then
            return 0
        fi
    done
    for root in "${!TARGET_ROOTS[@]}"; do
        [[ "$command" == *"$root"* || "$cwd" == "$root" || "$cwd" == "$root"/* ]] && return 0
        [[ "$environment" == *"=$root"* || "$environment" == *"=$root"$'\n'* ]] && return 0
    done
    return 1
}

discover_pipeline_processes() {
    # One cheap ps pass, then validate only likely candidates.  The previous
    # implementation opened three /proc files for every server process.
    local line pid command marker root candidate
    local -A seen=()
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        pid="${line%%[[:space:]]*}"
        command="${line#*[[:space:]]}"
        [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || continue
        candidate=0
        [[ "$command" == *"$SCRIPT_DIR"* ]] && candidate=1
        if (( candidate == 0 )); then
            for marker in "${PROCESS_MARKERS[@]}"; do
                [[ "$command" == *"$marker"* ]] && { candidate=1; break; }
            done
        fi
        if (( candidate == 0 )) && [[ "$command" == *"python3 -"* || "$command" == *"python -"* || "$command" == *"fastp"* || "$command" == *"pandaseq"* || "$command" == *"igblastn"* ]]; then
            candidate=1
        fi
        if (( candidate == 0 )); then
            for root in "${!TARGET_ROOTS[@]}"; do
                [[ "$command" == *"$root"* ]] && { candidate=1; break; }
            done
        fi
        (( candidate )) || continue
        matches_pipeline_process "$pid" || continue
        [[ -n "${seen[$pid]+yes}" ]] && continue
        seen["$pid"]=1
        printf '%s\n' "$pid"
    done < <(ps -eo pid=,args= 2>/dev/null || true)
}

collect_tree() {
    local root="$1" current child
    local -a queue=("$root")
    local -A seen=()
    while ((${#queue[@]})); do
        current="${queue[0]}"
        queue=("${queue[@]:1}")
        [[ "$current" =~ ^[0-9]+$ ]] || continue
        [[ -n "${seen[$current]+yes}" ]] && continue
        seen["$current"]=1
        printf '%s\n' "$current"
        while read -r child; do
            [[ "$child" =~ ^[0-9]+$ ]] && queue+=("$child")
        done < <(children_of "$current")
    done
}

stop_tree() {
    local root="$1" i pid alive
    local -a pids=()
    mapfile -t pids < <(collect_tree "$root")
    for ((i=${#pids[@]}-1; i>=0; i--)); do
        pid="${pids[$i]}"
        pid_alive "$pid" && kill -TERM "$pid" 2>/dev/null || true
    done
    for i in $(seq 1 20); do
        alive=0
        for pid in "${pids[@]}"; do
            pid_alive "$pid" && alive=1
        done
        ((alive == 0)) && return 0
        sleep 1
    done
    echo "[10X] graceful stop timed out; forcing saved process tree..." >&2
    for ((i=${#pids[@]}-1; i>=0; i--)); do
        pid="${pids[$i]}"
        pid_alive "$pid" && kill -KILL "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in "${pids[@]}"; do pid_alive "$pid" && return 1; done
    return 0
}

STOP_FAILED=0
declare -A TARGETS=()
shopt -s nullglob
pid_files=("${PID_DIR}"/*.pid)
if [[ -d "$STATE_ROOT" ]]; then
    while IFS= read -r -d '' pid_file; do pid_files+=("$pid_file"); done < <(find "$STATE_ROOT" -type f -path '*/pids/*.pid' -print0 2>/dev/null || true)
fi
for pid_file in "${pid_files[@]}"; do
    pid="$(tr -d '[:space:]' < "$pid_file" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && pid_alive "$pid"; then
        if matches_pipeline_process "$pid"; then
            TARGETS["$pid"]=1
            remember_roots "$pid"
        else
            echo "[10X] ignoring PID file for an unrelated process: ${pid}" >&2
        fi
    else
        rm -f "$pid_file"
    fi
done
# PID files are authoritative; do not scan unrelated server processes.
if (( ${#TARGETS[@]} == 0 )); then echo "[10X] no registered pipeline process found" >&2; fi
for pid in "${!TARGETS[@]}"; do
    if pid_alive "$pid"; then
        echo "[10X] stopping pipeline/worker process tree (PID ${pid})..."
        stop_tree "$pid" || STOP_FAILED=1
    fi
done
remaining=""
for pid in "${!TARGETS[@]}"; do pid_alive "$pid" && remaining+="${pid}"$'\n'; done
if [[ -n "$remaining" ]]; then
    echo "[10X] unable to stop all branch processes:${remaining}" >&2
    STOP_FAILED=1
fi
if (( STOP_FAILED )); then
    echo "[10X] stop incomplete; active PID files were preserved" >&2
    exit 1
fi
rm -f "${PID_DIR}"/*.pid 2>/dev/null || true
rmdir "$PID_DIR" 2>/dev/null || true
echo "[10X] stop complete"
