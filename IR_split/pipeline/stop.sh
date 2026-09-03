#!/usr/bin/env bash
set -u

# Stop the complete IR pipeline, including fastp/PANDAseq/IgBLAST children.
# Each run has its own PID file; no pipeline-wide lock is used.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
CONFIG_FILE="${SCRIPT_DIR}/00.pipeline_config.env"
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    . "$CONFIG_FILE"
    set +a
fi
resolve_output_root() {
    local value="${1:-}"
    if [[ -z "$value" ]]; then
        printf '%s/output\n' "$BRANCH_ROOT"
    elif [[ "$value" == /* ]]; then
        printf '%s\n' "$value"
    else
        printf '%s/%s\n' "$BRANCH_ROOT" "$value"
    fi
}
OUTPUT_ROOT="$(resolve_output_root "${RUNTIME_OUTPUT_ROOT:-${SCIGBLAST_OUTPUT_ROOT:-}}")"
PID_DIR="${SCRIPT_DIR}/.pids"  # legacy registry
STATE_ROOT="${OUTPUT_ROOT}/.pipeline_state"
SPLIT_PID_FILE="${SCRIPT_DIR}/.split_barcode.pid"
PROCESS_MARKERS=(
"run_ir_pipeline.sh" "01.match_sample.py" "02.run_fastp.sh"
    "03.split_barcode.py" "04.clean_header.sh" "05.work_pandaseq.sh"
    "06.representative.py" "07.work_igblastn.sh"
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
    # A zombie answers kill -0 but no longer owns a running worker.
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
# worker remains alive. Also discover orphaned workers by script path/name,
# restricted to this branch's pipeline directory.
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
    # Tool workers (fastp/pandaseq/igblastn and python3 -) may not retain a
    # pipeline script in argv.  They inherit the output root from the runner.
    for root in "${!TARGET_ROOTS[@]}"; do
        [[ "$command" == *"$root"* || "$cwd" == "$root" || "$cwd" == "$root"/* ]] && return 0
        [[ "$environment" == *"=$root"* || "$environment" == *"=$root"$'\n'* ]] && return 0
    done
    return 1
}

discover_pipeline_processes() {
    # Do not inspect /proc for every process on the machine.  On the shared
    # server this used to fork three readers per PID (cmdline/cwd/environ),
    # making an otherwise idle stop take minutes.  First make a cheap one-pass
    # candidate list from ps; only the small candidate set is validated with
    # the detailed matcher below.
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
        # Stage workers can be embedded as ``python3 -`` or run as a native
        # tool after their parent shell exits.  Keep this small executable
        # filter so environment-only workers can still be validated, without
        # opening /proc for unrelated processes.
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

matches_job() {
    local command
    command=$(pid_command "$1")
    [[ "$command" == *"$2"* ]]
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
    # Signal descendants first so the parent cannot leave active workers behind.
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
    echo "[IR] graceful stop timed out; forcing saved process tree..." >&2
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
pipeline_pid_files=("${PID_DIR}"/*.pid)
if [[ -d "$STATE_ROOT" ]]; then
    while IFS= read -r -d '' pipeline_pid_file; do
        pipeline_pid_files+=("$pipeline_pid_file")
    done < <(find "$STATE_ROOT" -type f \( -path '*/pids/*.pid' -o -path '*/.pids/*.pid' \) -print0 2>/dev/null || true)
fi
for pipeline_pid_file in "${pipeline_pid_files[@]}"; do
    pipeline_pid="$(tr -d '[:space:]' < "$pipeline_pid_file" 2>/dev/null || true)"
    if [[ "$pipeline_pid" =~ ^[0-9]+$ ]] && pid_alive "$pipeline_pid"; then
        if matches_pipeline_process "$pipeline_pid"; then
            TARGETS["$pipeline_pid"]=1
            remember_roots "$pipeline_pid"
        else
            echo "[IR] ignoring PID file for an unrelated process: ${pipeline_pid}" >&2
        fi
    else
        rm -f "$pipeline_pid_file"
    fi
done
split_pid=""
[[ -f "$SPLIT_PID_FILE" ]] && split_pid=$(tr -d '[:space:]' < "$SPLIT_PID_FILE" 2>/dev/null || true)
if [[ "$split_pid" =~ ^[0-9]+$ ]] && pid_alive "$split_pid" && matches_pipeline_process "$split_pid"; then
    TARGETS["$split_pid"]=1
    remember_roots "$split_pid"
fi
# PID files are the only source of targets.  We intentionally do not scan the
# server process table: an orphan without a pipeline PID file is not ours to
# terminate and a full /proc walk is too expensive on this shared host.
if (( ${#TARGETS[@]} == 0 )); then
    echo "[IR] no registered pipeline process found" >&2
fi

for pass in 1 2 3; do
    for pid in "${!TARGETS[@]}"; do
        if pid_alive "$pid"; then
            echo "[IR] stopping pipeline/worker process tree (PID ${pid}, pass ${pass})..."
            stop_tree "$pid" || STOP_FAILED=1
        fi
    done
    # Descendant trees are complete; no process-table scan is needed between
    # passes. The final fallback scan below handles orphaned workers.
    remaining=""
    for tracked_pid in "${!TARGETS[@]}"; do
        if pid_alive "$tracked_pid"; then remaining+="${tracked_pid}"$'\n'; fi
    done
    [[ -z "$remaining" ]] && break
done

remaining="$(discover_pipeline_processes | sort -n -u || true)"
if [[ -n "$remaining" ]]; then
    echo "[IR] unable to stop all branch processes:${remaining}" >&2
    STOP_FAILED=1
fi
if (( STOP_FAILED )); then
    echo "[IR] stop incomplete; active PID files were preserved" >&2
    exit 1
fi
rm -f "$SPLIT_PID_FILE" "${PID_DIR}"/*.pid 2>/dev/null || true
for pipeline_pid_file in "${pipeline_pid_files[@]}"; do
    rm -f "$pipeline_pid_file" 2>/dev/null || true
done
rmdir "$PID_DIR" 2>/dev/null || true
echo "[IR] stop complete"
