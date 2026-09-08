#!/usr/bin/env bash
set -euo pipefail

# Complete 10X branch.  Edit the globals below; no command-line arguments are
# required.  The branch is deliberately independent from IR_split.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
CONFIG_FILE="${SCRIPT_DIR}/00.pipeline_config.env"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_RAW_INPUT_OVERRIDE="${SCIGBLAST_RUN_RAW_INPUT_DIR:-}"
RUN_SUBMISSION_OVERRIDE="${SCIGBLAST_RUN_SUBMISSION_XLSX:-}"
RUN_SUBMISSION_PATHS_OVERRIDE="${SCIGBLAST_RUN_SUBMISSION_PATHS:-}"
RUN_BARCODE_OVERRIDE="${SCIGBLAST_RUN_BARCODE_CSV:-}"
RUN_OUTPUT_OVERRIDE="${SCIGBLAST_OUTPUT_ROOT:-}"
RUN_MATCH_ONLY_OVERRIDE="${SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN:-}"
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
OUTPUT_ROOT="$(resolve_output_root "${SCIGBLAST_OUTPUT_ROOT:-}")"
if [[ "${SCIGBLAST_MULTI_CHILD:-0}" == "1" ]]; then
    SCIGBLAST_DATA_INPUTS=()
    SCIGBLAST_SUBMISSIONS=()
    SCIGBLAST_BARCODE_CSVS=()
fi
[[ -n "$RUN_RAW_INPUT_OVERRIDE" ]] && SCIGBLAST_RAW_INPUT_DIR="$RUN_RAW_INPUT_OVERRIDE"
if [[ -n "$RUN_SUBMISSION_PATHS_OVERRIDE" ]]; then
  SCIGBLAST_SUBMISSION_PATHS="$RUN_SUBMISSION_PATHS_OVERRIDE"
  SCIGBLAST_SUBMISSION_XLSX=""
elif [[ -n "$RUN_SUBMISSION_OVERRIDE" ]]; then
  SCIGBLAST_SUBMISSION_XLSX="$RUN_SUBMISSION_OVERRIDE"
fi
[[ -n "$RUN_BARCODE_OVERRIDE" ]] && SCIGBLAST_BARCODE_CSV="$RUN_BARCODE_OVERRIDE"
if [[ -n "$RUN_OUTPUT_OVERRIDE" ]]; then
    OUTPUT_ROOT="$(resolve_output_root "$RUN_OUTPUT_OVERRIDE")"
    SCIGBLAST_OUTPUT_ROOT="$OUTPUT_ROOT"
fi
[[ -n "$RUN_MATCH_ONLY_OVERRIDE" ]] && SCIGBLAST_MATCH_ONLY_FIRST_RUN="$RUN_MATCH_ONLY_OVERRIDE"
SCIGBLAST_SUBMISSION_PATHS="${SCIGBLAST_SUBMISSION_PATHS:-}"
export SCIGBLAST_RAW_INPUT_DIR SCIGBLAST_SUBMISSION_XLSX SCIGBLAST_SUBMISSION_PATHS SCIGBLAST_BARCODE_CSV SCIGBLAST_MATCH_ONLY_FIRST_RUN
export SCIGBLAST_CONFIG="$CONFIG_FILE"
export SCIGBLAST_OUTPUT_ROOT="$OUTPUT_ROOT"

resolve_submission() {
    local configured="$1" dataset="$2" branch="$3" f stem key token
    [[ -f "$configured" ]] && { printf '%s\n' "$configured"; return 0; }
    [[ -d "$configured" ]] || { echo "[$branch] submission file/directory does not exist: $configured" >&2; return 1; }
    local -a files=()
    while IFS= read -r -d '' f; do files+=("$f"); done < <(find "$configured" -type f -iname '*.xlsx' -print0 | sort -z)
    ((${#files[@]})) || { echo "[$branch] no .xlsx submission found under: $configured" >&2; return 1; }
    # Keep the directory intact: the matcher merges all workbooks and uses
    # each row's Note path to select the correct batch.
    printf '%s\n' "$configured"
}

# Batch mode uses Bash arrays in 00.pipeline_config.env.
_inputs=(); _subs=(); _barcodes=()
[[ "$(declare -p SCIGBLAST_DATA_INPUTS 2>/dev/null || true)" == "declare -a"* ]] && _inputs=("${SCIGBLAST_DATA_INPUTS[@]}")
[[ "$(declare -p SCIGBLAST_SUBMISSIONS 2>/dev/null || true)" == "declare -a"* ]] && _subs=("${SCIGBLAST_SUBMISSIONS[@]}")
[[ "$(declare -p SCIGBLAST_BARCODE_CSVS 2>/dev/null || true)" == "declare -a"* ]] && _barcodes=("${SCIGBLAST_BARCODE_CSVS[@]}")
if (( ! ${#_inputs[@]} && ${#_subs[@]} )); then echo "[10X] SCIGBLAST_SUBMISSIONS was set but SCIGBLAST_DATA_INPUTS is empty" >&2; exit 2; fi
if (( ${#_inputs[@]} )); then
    if (( ${#_subs[@]} == 0 )) && [[ -n "${SCIGBLAST_SUBMISSION_XLSX:-}" ]]; then _subs=("${SCIGBLAST_SUBMISSION_XLSX}"); fi
    if (( ${#_subs[@]} == 0 )); then echo "[10X] at least one submission workbook/directory is required" >&2; exit 2; fi
    _resolved_subs=()
    for _sub in "${_subs[@]}"; do _resolved_subs+=("$(resolve_submission "$_sub" "" 10X)"); done
    if (( ${#_barcodes[@]} )); then
        if (( ${#_barcodes[@]} == 1 && ${#_inputs[@]} > 1 )); then _v="${_barcodes[0]}"; _barcodes=(); for _x in "${_inputs[@]}"; do _barcodes+=("$_v"); done
        elif (( ${#_barcodes[@]} != ${#_inputs[@]} )); then echo "[10X] SCIGBLAST_BARCODE_CSVS must have one entry per dataset (or one shared entry)" >&2; exit 2; fi
    else
        _barcodes=()
    fi
    for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
        [[ -d "${_inputs[$_i]}" ]] || { echo "[10X] input directory does not exist: ${_inputs[$_i]}" >&2; exit 2; }
        if (( ${#_barcodes[@]} )); then [[ -f "${_barcodes[$_i]}" ]] || { echo "[10X] barcode CSV does not exist: ${_barcodes[$_i]}" >&2; exit 2; }; fi
    done
    _batch_status=0
    declare -A _label_counts=()
    for _input in "${_inputs[@]}"; do _label="$(basename "${_input%/}")"; _label="${_label//[^A-Za-z0-9._-]/_}"; ((_label_counts[$_label]+=1)); done
    for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
        _input="${_inputs[$_i]}"
        _label="$(basename "${_input%/}")"; _label="${_label//[^A-Za-z0-9._-]/_}"
        [[ -n "$_label" ]] || _label="dataset"
        if (( _label_counts[$_label] > 1 )); then _suffix="$(printf '%s' "$_input" | sha256sum | cut -c1-8)"; _label="${_label}__${_suffix}"; fi
        _bc="${SCIGBLAST_BARCODE_CSV:-}"; (( ${#_barcodes[@]} )) && _bc="${_barcodes[$_i]}"
        echo "[10X][batch $((_i+1))/${#_inputs[@]}] input=$_input submissions=${#_resolved_subs[@]} output=${OUTPUT_ROOT}/<stage>/${_label}"
        _submission_payload="$(printf '%s\n' "${_resolved_subs[@]}")"
        if SCIGBLAST_MULTI_CHILD=1 SCIGBLAST_DATASET_LABEL="$_label" SCIGBLAST_RUN_RAW_INPUT_DIR="$_input" SCIGBLAST_RUN_SUBMISSION_PATHS="$_submission_payload" SCIGBLAST_RUN_SUBMISSION_XLSX="" SCIGBLAST_RUN_BARCODE_CSV="$_bc" SCIGBLAST_OUTPUT_ROOT="${OUTPUT_ROOT}" SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-1}" bash "$0"; then
            echo "[10X][batch $((_i+1))] completed"
        else
            _rc=$?; _batch_status=1; echo "[10X][batch $((_i+1))] failed (exit=$_rc)" >&2
        fi
    done
    exit "$_batch_status"
fi
mkdir -p "$OUTPUT_ROOT"
RAW_INPUT_DIR="${SCIGBLAST_RAW_INPUT_DIR:-/colddata/zqy/XYFY_HZJ1}"
if [[ -n "${SCIGBLAST_SUBMISSION_XLSX:-}" && -d "${SCIGBLAST_SUBMISSION_XLSX}" ]]; then
    SCIGBLAST_SUBMISSION_XLSX="$(resolve_submission "$SCIGBLAST_SUBMISSION_XLSX" "$RAW_INPUT_DIR" 10X)"
    export SCIGBLAST_SUBMISSION_XLSX
fi
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-$(basename "${RAW_INPUT_DIR%/}")}" 
DATASET_LABEL="${DATASET_LABEL//[^A-Za-z0-9._-]/_}"
[[ -n "$DATASET_LABEL" ]] || DATASET_LABEL="dataset"
stage_root() { printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$1" "$DATASET_LABEL"; }
MAPPING_SUMMARY="$(stage_root 01.match)/sample_barcode_summary.csv"
FASTP_DIR="$(stage_root 02.fastp)/data"
FASTP_REPORT_DIR="$(stage_root 02.fastp)/report"
PREFILTER_DIR="$(stage_root 03.prefilter_data)"
CLEAN_DIR="$(stage_root 04.clean_data)"
PANDASEQ_DIR="$(stage_root 05.pandaseq)"
SPLIT_OUTPUT_DIR="$(stage_root 06.split_output)"
IGBLAST_OUTPUT_DIR="$(stage_root 07.igblastn_out)"
CLUSTER_OUTPUT_DIR="$(stage_root 08.cluster)"
LOG_FILE="${OUTPUT_ROOT}/logs/${DATASET_LABEL}/pipeline.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1
PID_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}/pids"
mkdir -p "$PID_DIR"
PID_FILE="${PID_DIR}/$$.pid"
printf '%s\n' "$$" > "$PID_FILE"
cleanup_pid() { rm -f "$PID_FILE"; }
trap cleanup_pid EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
CLEANUP_INTERMEDIATE="${SCIGBLAST_CLEANUP_INTERMEDIATE:-0}"
MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-1}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
MIN_FREE_MEMORY_GB="${SCIGBLAST_MIN_FREE_MEMORY_GB:-16}"
MEMORY_STOP_THRESHOLD_GB="${SCIGBLAST_MEMORY_STOP_THRESHOLD_GB:-150}"
MEMORY_POLL_SECONDS="${SCIGBLAST_MEMORY_POLL_SECONDS:-5}"
FORCE_RERUN="${SCIGBLAST_FORCE_RERUN:-0}"
export SCIGBLAST_IGBLAST_OUTPUT_DIR="${IGBLAST_OUTPUT_DIR}"
[[ "$MEMORY_BUDGET_GB" =~ ^[0-9]+$ && "$MEMORY_BUDGET_GB" -le 300 ]] || { echo "[10X] memory budget must be <=300GB" >&2; exit 1; }
[[ "$MIN_FREE_MEMORY_GB" =~ ^[0-9]+$ ]] || { echo "[10X] MIN_FREE_MEMORY_GB must be a non-negative integer" >&2; exit 1; }
[[ "$MEMORY_STOP_THRESHOLD_GB" =~ ^[0-9]+$ ]] || { echo "[10X] MEMORY_STOP_THRESHOLD_GB must be a non-negative integer" >&2; exit 1; }
[[ "$MEMORY_POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "[10X] MEMORY_POLL_SECONDS must be a positive integer" >&2; exit 1; }
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$MAX_THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$THREAD_BUDGET" -le "$MAX_THREAD_BUDGET" ]] || { echo "[10X] thread budget must be <=${MAX_THREAD_BUDGET}" >&2; exit 1; }
ALLOW_MAPPING_ERRORS=1
PIPELINE_STATUS=0
STATE_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}"
mkdir -p "$STATE_DIR"
PIPELINE_DONE_MARKER="${STATE_DIR}/.pipeline.DONE"
MATCH_REVIEW_MARKER="${STATE_DIR}/.match_review.done"
MAPPING_SHA256=""

available_memory_gb() {
    awk '/^MemAvailable:/ {printf "%d", $2 / 1024 / 1024; found=1; exit} END {if (!found) exit 1}' /proc/meminfo 2>/dev/null
}
memory_guard() {
    local label="$1" requested_gb="$2" available_gb
    available_gb="$(available_memory_gb 2>/dev/null || true)"
    [[ "$available_gb" =~ ^[0-9]+$ ]] || {
        echo "[10X] cannot read server available memory; refusing to start ${label}" >&2
        return 1
    }
    local start_limit="$(( requested_gb + MIN_FREE_MEMORY_GB ))"
    (( start_limit < MEMORY_STOP_THRESHOLD_GB )) && start_limit="$MEMORY_STOP_THRESHOLD_GB"
    if (( available_gb < start_limit )); then
        echo "[10X] insufficient available memory for ${label}: available=${available_gb}GB start_limit=${start_limit}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB" >&2
        return 1
    fi
    echo "[10X] memory check ${label}: available=${available_gb}GB required=${requested_gb}GB reserve=${MIN_FREE_MEMORY_GB}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB"
}
FASTP_REQUIRED_GB="$(( ${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-2} * ${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-16} ))"
PREFILTER_REQUIRED_GB="${SCIGBLAST_10X_PREFILTER_MEMORY_GB:-16}"
CLEAN_REQUIRED_GB="$(( ${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-2} * ${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-8} ))"
PANDASEQ_REQUIRED_GB="$(( ${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-2} * ${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-64} ))"
SPLIT_WORKERS="${SCIGBLAST_10X_WORKERS:-4}"
SPLIT_MEMORY_PER_WORKER_GB="${SCIGBLAST_10X_PHASE2_MEMORY_LIMIT_GB:-48}"
SPLIT_REQUIRED_GB="$(( SPLIT_WORKERS * SPLIT_MEMORY_PER_WORKER_GB ))"
IGBLAST_REQUIRED_GB="$(( ${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-2} * ${SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB:-96} ))"
CLUSTER_REQUIRED_GB="${SCIGBLAST_10X_CLUSTER_MEMORY_GB:-16}"
CONFIG_SHA256=""

children_of() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -P "$1" 2>/dev/null || true
    else
        ps -eo pid=,ppid= | awk -v parent="$1" '$2 == parent {print $1}'
    fi
}
collect_process_tree() {
    local root="$1" current child
    local -a queue=("$root")
    local -A seen=()
    while ((${#queue[@]})); do
        current="${queue[0]}"; queue=("${queue[@]:1}")
        [[ "$current" =~ ^[0-9]+$ ]] || continue
        [[ -n "${seen[$current]+yes}" ]] && continue
        seen["$current"]=1
        printf '%s\n' "$current"
        while read -r child; do
            [[ "$child" =~ ^[0-9]+$ ]] && queue+=("$child")
        done < <(children_of "$current")
    done
}
stop_process_tree() {
    local root="$1" i pid
    local -a pids=()
    mapfile -t pids < <(collect_process_tree "$root")
    for ((i=${#pids[@]}-1; i>=0; i--)); do
        pid="${pids[$i]}"; kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 2
    for ((i=${#pids[@]}-1; i>=0; i--)); do
        pid="${pids[$i]}"; kill -KILL "$pid" 2>/dev/null || true
    done
}
run_monitored_stage() {
    local label="$1" requested_gb="$2" event_file stage_pid watchdog_pid stage_status available_gb
    shift 2
    memory_guard "$label" "$requested_gb" || return 75
    event_file="${OUTPUT_ROOT}/memory_stop.$$.log"
    rm -f "$event_file"
    "$@" &
    stage_pid=$!
    (
        while kill -0 "$stage_pid" 2>/dev/null; do
            available_gb="$(available_memory_gb 2>/dev/null || true)"
            if ! [[ "$available_gb" =~ ^[0-9]+$ ]]; then
                printf 'stage=%s\nreason=MemAvailable_unreadable\ntimestamp=%s\n' "$label" "$(date -Iseconds)" > "$event_file"
                echo "[10X][memory] STOP stage=${label}: cannot read MemAvailable; terminating process tree PID=${stage_pid}" >&2
                stop_process_tree "$stage_pid"
                exit 75
            fi
            if (( available_gb < MEMORY_STOP_THRESHOLD_GB )); then
                printf 'stage=%s\navailable_gb=%s\nthreshold_gb=%s\ntimestamp=%s\n' "$label" "$available_gb" "$MEMORY_STOP_THRESHOLD_GB" "$(date -Iseconds)" > "$event_file"
                echo "[10X][memory] STOP stage=${label}: MemAvailable=${available_gb}GB below threshold=${MEMORY_STOP_THRESHOLD_GB}GB; terminating process tree PID=${stage_pid}" >&2
                stop_process_tree "$stage_pid"
                exit 75
            fi
            sleep "$MEMORY_POLL_SECONDS"
        done
    ) &
    watchdog_pid=$!
    if wait "$stage_pid"; then stage_status=0; else stage_status=$?; fi
    kill "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
    if [[ -s "$event_file" ]]; then
        echo "[10X][memory] ${label} stopped by memory watchdog; details: ${event_file}" >&2
        return 75
    fi
    rm -f "$event_file"
    return "$stage_status"
}

hash_file() {
    local path="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$path" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$path" | awk '{print $1}'
    else
        return 1
    fi
}

stage_script_hash() {
    case "$1" in
        01.match) { hash_file "${SCRIPT_DIR}/01.match_sample.py"; hash_file "${SCRIPT_DIR}/tools/pipeline_config.py"; } | sha256sum | awk '{print $1}' ;;
        02.fastp) hash_file "${SCRIPT_DIR}/02.run_fastp.sh" ;;
        03.prefilter) { hash_file "${SCRIPT_DIR}/03.prefilter_r1_r2.py"; hash_file "${SCRIPT_DIR}/tools/pipeline_config.py"; } | sha256sum | awk '{print $1}' ;;
        04.clean) hash_file "${SCRIPT_DIR}/04.clean_header.sh" ;;
        05.pandaseq) hash_file "${SCRIPT_DIR}/05.work_pandaseq.sh" ;;
        06.split) { hash_file "${SCRIPT_DIR}/06.split_and_represent.py"; hash_file "${SCRIPT_DIR}/tools/pipeline_config.py"; } | sha256sum | awk '{print $1}' ;;
        07.igblast) hash_file "${SCRIPT_DIR}/07.work_igblastn.sh" ;;
        08.cluster) hash_file "${SCRIPT_DIR}/08.chain_cluster.py" ;;
        *) return 0 ;;
    esac
}

write_stage_marker() {
    local stage="$1"
    local marker="${STATE_DIR}/.pipeline_stage_${stage}.DONE"
    local marker_tmp="${marker}.tmp.${BASHPID:-$$}"
    local stage_hash=""
    stage_hash="$(stage_script_hash "$stage" || true)"
    printf 'status=DONE\n' > "$marker_tmp"
    printf 'stage=%s\n' "$stage" >> "$marker_tmp"
    printf 'mapping_sha256=%s\n' "$MAPPING_SHA256" >> "$marker_tmp"
    printf 'config_sha256=%s\n' "$CONFIG_SHA256" >> "$marker_tmp"
    [[ -z "$stage_hash" ]] || printf 'stage_script_sha256=%s\n' "$stage_hash" >> "$marker_tmp"
    if [[ "$stage" == "08.cluster" ]]; then
        printf 'input_dir=%s\n' "$IGBLAST_OUTPUT_DIR" >> "$marker_tmp"
        printf 'output_dir=%s\n' "$CLUSTER_OUTPUT_DIR" >> "$marker_tmp"
        printf 'cluster_workers=%s\n' "${SCIGBLAST_10X_CLUSTER_WORKERS:-1}" >> "$marker_tmp"
    fi
    printf 'timestamp=%s\n' "$(date -Iseconds)" >> "$marker_tmp"
    mv -f "$marker_tmp" "$marker"
}

final_outputs_ready() {
    [[ -s "${IGBLAST_OUTPUT_DIR}/chain_summary.csv" ]] || return 1
    "${PYTHON_BIN}" - "$MAPPING_SUMMARY" "$IGBLAST_OUTPUT_DIR" <<'PY'
import csv, sys
from pathlib import Path
mapping = Path(sys.argv[1]); out = Path(sys.argv[2])
need_bcr = need_tcr = False
try:
    with mapping.open(newline='', encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            if row.get('status', '').upper() != 'OK':
                continue
            raw = (row.get('igblast_chains') or row.get('chain_raw') or '').upper()
            if raw in {'B', 'BCR'} or raw.startswith('IG'):
                need_bcr = True
            if raw in {'T', 'TCR', '7C', 'BOTH'} or raw.startswith('TR'):
                need_tcr = True
except (OSError, csv.Error):
    sys.exit(1)
def has_sample_output(name):
    return any(p.is_file() and p.stat().st_size > 0 for p in list(out.rglob('TCR.tsv')) + list(out.rglob('BCR.tsv'))) or any(p.is_file() for p in out.rglob('.NO_RESULTS'))
if (need_bcr or need_tcr) and not has_sample_output('batch_*.tsv'):
    sys.exit(1)
sys.exit(0)
PY
}

stage_done() {
    local stage="$1" marker="${STATE_DIR}/.pipeline_stage_${1}.DONE"
    local stage_hash=""
    [[ "$FORCE_RERUN" != "1" && -s "$marker" ]] || return 1
    grep -qx "status=DONE" "$marker" || return 1
    grep -qx "mapping_sha256=${MAPPING_SHA256}" "$marker" || return 1
    grep -qx "config_sha256=${CONFIG_SHA256}" "$marker" || return 1
    stage_hash="$(stage_script_hash "$stage" || true)"
    if [[ -n "$stage_hash" ]]; then
        grep -qx "stage_script_sha256=${stage_hash}" "$marker" || return 1
    fi
    case "$stage" in
        01.match) [[ -s "$MAPPING_SUMMARY" ]] || return 1 ;;
        02.fastp) [[ -d "$FASTP_DIR" && -d "$FASTP_REPORT_DIR" ]] || return 1; [[ -n "$(find "$FASTP_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        03.prefilter) [[ -d "$PREFILTER_DIR" ]] || return 1; [[ -n "$(find "$PREFILTER_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        04.clean) [[ -d "$CLEAN_DIR" ]] || return 1; [[ -n "$(find "$CLEAN_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        05.pandaseq) [[ -d "$PANDASEQ_DIR" ]] || return 1; [[ -n "$(find "$PANDASEQ_DIR" -type f \( -name '*_merged.fastq' -o -name '*_merged.fq' -o -name '*_merged.fasta' -o -name '*_merged.fa' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        06.split) [[ -d "$SPLIT_OUTPUT_DIR" ]] || return 1; [[ -n "$(find "$SPLIT_OUTPUT_DIR" -type f \( -name '*.fa' -o -name '*.fasta' -o -name '*.csv' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        07.igblast) [[ -s "${IGBLAST_OUTPUT_DIR}/chain_summary.csv" ]] || return 1; [[ -n "$(find "$IGBLAST_OUTPUT_DIR" -type f \( -name 'TCR.tsv' -o -name 'BCR.tsv' -o -name '.NO_RESULTS' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        08.cluster) [[ -s "${CLUSTER_OUTPUT_DIR}/stage8_summary.csv" ]] || return 1; [[ -n "$(find "$CLUSTER_OUTPUT_DIR" -type f \( -name '*.tsv' -o -name '*.csv' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        *) return 1 ;;
    esac
}

cleanup_stage() {
    local path="$1"
    [[ "$CLEANUP_INTERMEDIATE" == "1" ]] || return 0
    case "$path" in
        "${OUTPUT_ROOT}"/*)
            [[ -d "$path" ]] || return 0
            # Remove only large sequence payloads. Keep fastp JSON/HTML,
            # prefilter reports, PANDAseq logs and other audit metadata.
            find "$path" -type f \( \
                -iname '*.fq' -o -iname '*.fastq' -o \
                -iname '*.fq.gz' -o -iname '*.fastq.gz' -o \
                -iname '*.fa' -o -iname '*.fasta' -o \
                -iname '*.fa.gz' -o -iname '*.fasta.gz' \
            \) -delete
            find "$path" -depth -type d -empty -delete
            ;;
        *) echo "refusing to remove unexpected path: $path" >&2; return 1 ;;
    esac
}

echo "[10X 1/8] sample/barcode matching"
echo "[10X] memory budget=${MEMORY_BUDGET_GB}GB"
MATCH_STATUS=0
MATCH_RUN_SUMMARY="${MAPPING_SUMMARY}.run.$$"
MATCH_SUMMARY_READY=0
rm -f "$MATCH_RUN_SUMMARY"
SCIGBLAST_MATCH_OUTPUT="${MATCH_RUN_SUMMARY}" \
"${PYTHON_BIN}" "${SCRIPT_DIR}/01.match_sample.py" || MATCH_STATUS=$?
if [[ -s "$MATCH_RUN_SUMMARY" ]]; then
    mv -f "$MATCH_RUN_SUMMARY" "$MAPPING_SUMMARY"
    MATCH_SUMMARY_READY=1
fi
if [[ "$MATCH_STATUS" -ne 0 ]]; then
    PIPELINE_STATUS=1
    if [[ "$ALLOW_MAPPING_ERRORS" != "1" || "$MATCH_SUMMARY_READY" -ne 1 ]]; then
        echo "[10X] match failed and no usable summary was produced; stopping" >&2
        exit "$MATCH_STATUS"
    fi
    echo "[10X] warning: match summary contains errors; valid rows will continue" >&2
fi
if [[ "$MATCH_SUMMARY_READY" -ne 1 ]]; then
    echo "[10X] match did not produce a fresh summary; stopping" >&2
    exit 1
fi
MAPPING_SHA256="$(hash_file "$MAPPING_SUMMARY")" || {
    echo "[10X] cannot calculate mapping summary checksum" >&2
    exit 1
}
CONFIG_SHA256="$(hash_file "$CONFIG_FILE")" || { echo "[10X] cannot calculate config checksum" >&2; exit 1; }
if [[ "$MATCH_STATUS" -eq 0 ]]; then write_stage_marker "01.match"; fi

if [[ "${SCIGBLAST_WEB_MATCH_ONLY:-0}" == "1" || ( "$MATCH_ONLY_FIRST_RUN" == "1" && ! -f "$MATCH_REVIEW_MARKER" ) ]]; then
    marker_tmp="${MATCH_REVIEW_MARKER}.tmp.${BASHPID:-$$}"
    {
        printf 'status=READY_FOR_REVIEW\n'
        printf 'mapping_sha256=%s\n' "$MAPPING_SHA256"
        printf 'config_sha256=%s\n' "$CONFIG_SHA256"
        printf 'timestamp=%s\n' "$(date -Iseconds)"
    } > "$marker_tmp"
    mv -f "$marker_tmp" "$MATCH_REVIEW_MARKER"
    echo "[10X] first invocation completed matching only. Review: ${MAPPING_SUMMARY}" >&2
    echo "[10X] run this pipeline again to continue with fastp and downstream stages."
    exit "$MATCH_STATUS"
fi

# A completed run is resumable even when large intermediate FASTQ/FASTA files
# were cleaned.  Re-run matching first so fixing an error row changes the hash
# and invalidates this shortcut.
if [[ "$FORCE_RERUN" != "1" ]] && [[ -f "$PIPELINE_DONE_MARKER" ]] && final_outputs_ready && \
   grep -qx "mapping_sha256=${MAPPING_SHA256}" "$PIPELINE_DONE_MARKER" 2>/dev/null && \
   grep -qx "config_sha256=${CONFIG_SHA256}" "$PIPELINE_DONE_MARKER" 2>/dev/null; then
    echo "[10X] pipeline already complete for this mapping summary; skipping"
    exit 0
fi
rm -f "$PIPELINE_DONE_MARKER"

echo "[10X 2/8] fastp"
if ! stage_done "02.fastp"; then
    run_monitored_stage "fastp" "$FASTP_REQUIRED_GB" env \
    SCIGBLAST_FASTP_MAPPING_SUMMARY="${MAPPING_SUMMARY}" \
    bash "${SCRIPT_DIR}/02.run_fastp.sh" \
    --input-dir "${RAW_INPUT_DIR}" \
    --output-dir "${FASTP_DIR}" \
    --report-dir "${FASTP_REPORT_DIR}"
    write_stage_marker "02.fastp"
else echo "[10X] reusing completed fastp stage"; fi

echo "[10X 3/8] R1 TSO + R2 primer prefilter"
PREFILTER_STATUS=0
if ! stage_done "03.prefilter"; then
run_monitored_stage "R1/R2 prefilter" "$PREFILTER_REQUIRED_GB" env \
SCIGBLAST_10X_MAPPING_SUMMARY="${MAPPING_SUMMARY}" \
SCIGBLAST_10X_PREFILTER_INPUT="${FASTP_DIR}" \
SCIGBLAST_10X_PREFILTER_OUTPUT="${PREFILTER_DIR}" \
SCIGBLAST_10X_PREFILTER_REPORT="${PREFILTER_DIR}/r1_r2_prefilter_summary.csv" \
"${PYTHON_BIN}" "${SCRIPT_DIR}/03.prefilter_r1_r2.py" || PREFILTER_STATUS=$?
if [[ "$PREFILTER_STATUS" -ne 0 ]]; then
    PIPELINE_STATUS=1
    if [[ "$ALLOW_MAPPING_ERRORS" != "1" || ! -d "$PREFILTER_DIR" ]]; then
        echo "[10X] prefilter failed and no output directory was produced; stopping" >&2
        exit "$PREFILTER_STATUS"
    fi
    echo "[10X] warning: prefilter reported errors; valid sample outputs will continue" >&2
fi
else echo "[10X] reusing completed prefilter stage"; fi
if [[ "$PREFILTER_STATUS" -eq 0 ]]; then write_stage_marker "03.prefilter"; fi
if [[ "$PREFILTER_STATUS" -ne 0 ]]; then
    echo "[10X] preserving fastp FASTQ because prefilter failed" >&2
    echo "[10X] prefilter stage failed; downstream stages will not start" >&2
    exit "$PREFILTER_STATUS"
fi

echo "[10X 4/8] clean header"
if ! stage_done "04.clean"; then
    run_monitored_stage "clean header" "$CLEAN_REQUIRED_GB" bash "${SCRIPT_DIR}/04.clean_header.sh" \
    --input-dir "${PREFILTER_DIR}" \
    --output-dir "${CLEAN_DIR}"
    write_stage_marker "04.clean"
else echo "[10X] reusing completed clean stage"; fi

echo "[10X 5/8] PANDAseq"
if ! stage_done "05.pandaseq"; then
    run_monitored_stage "PANDAseq" "$PANDASEQ_REQUIRED_GB" bash "${SCRIPT_DIR}/05.work_pandaseq.sh" \
    --data-dir "${CLEAN_DIR}" \
    --output-dir "${PANDASEQ_DIR}"
    write_stage_marker "05.pandaseq"
else echo "[10X] reusing completed PANDAseq stage"; fi

echo "[10X 6/8] cell barcode/UMI split"
if ! stage_done "06.split"; then
run_monitored_stage "10X split/representative" "$SPLIT_REQUIRED_GB" env \
SCIGBLAST_10X_SPLIT_INPUT="${PANDASEQ_DIR}" \
SCIGBLAST_10X_SPLIT_OUTPUT="${SPLIT_OUTPUT_DIR}" \
"${PYTHON_BIN}" "${SCRIPT_DIR}/06.split_and_represent.py"
write_stage_marker "06.split"; else echo "[10X] reusing completed split stage"; fi

echo "[10X 7/8] IgBLAST"
    if ! stage_done "07.igblast"; then
        run_monitored_stage "IgBLAST" "$IGBLAST_REQUIRED_GB" env \
        SCIGBLAST_10X_CHAIN_SUMMARY="${MAPPING_SUMMARY}" \
        bash "${SCRIPT_DIR}/07.work_igblastn.sh" \
        --data-dir "${SPLIT_OUTPUT_DIR}/representative_fasta" \
        --output-dir "${IGBLAST_OUTPUT_DIR}"
        write_stage_marker "07.igblast"
    else echo "[10X] reusing completed IgBLAST stage"; fi
    echo "[10X 8/8] chain cluster"
    if ! stage_done "08.cluster"; then
        run_monitored_stage "10X chain cluster" "$CLUSTER_REQUIRED_GB" env \
        SCIGBLAST_10X_CLUSTER_INPUT="${IGBLAST_OUTPUT_DIR}" \
        SCIGBLAST_10X_CLUSTER_OUTPUT="${CLUSTER_OUTPUT_DIR}" \
        "${PYTHON_BIN}" "${SCRIPT_DIR}/08.chain_cluster.py"
        write_stage_marker "08.cluster"
    else echo "[10X] reusing completed chain cluster stage"; fi
    if [[ "$PIPELINE_STATUS" -eq 0 ]]; then
    final_outputs_ready || { echo "[10X] final IgBLAST output validation failed; refusing to write DONE" >&2; exit 1; }
    stage_done "08.cluster" || { echo "[10X] chain cluster output validation failed; refusing to write DONE" >&2; exit 1; }
    marker_tmp="${PIPELINE_DONE_MARKER}.tmp.${BASHPID:-$$}"
    {
        printf 'status=DONE\n'
        printf 'mapping_sha256=%s\n' "$MAPPING_SHA256"
        printf 'config_sha256=%s\n' "$CONFIG_SHA256"
        printf 'timestamp=%s\n' "$(date -Iseconds)"
    } > "$marker_tmp"
    mv -f "$marker_tmp" "$PIPELINE_DONE_MARKER"
    # Keep every intermediate until IgBLAST succeeds so a failed downstream
    # stage can be rerun without recomputing raw FASTQ processing.
    cleanup_stage "${FASTP_DIR}"
    cleanup_stage "${PREFILTER_DIR}"
    cleanup_stage "${CLEAN_DIR}"
    cleanup_stage "${PANDASEQ_DIR}"
else
    echo "[10X] pipeline had errors; preserving all intermediate data for rerun" >&2
fi

echo "[10X] completed"
exit "$PIPELINE_STATUS"
