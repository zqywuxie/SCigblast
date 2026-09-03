#!/usr/bin/env bash
set -euo pipefail

# Complete IR branch.  This branch does not use the 10X TSO/cell-barcode
# splitter.  Edit the globals below; no command-line arguments are required.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
CONFIG_FILE="${SCRIPT_DIR}/00.pipeline_config.env"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PIPELINE_VARIANT="${SCIGBLAST_IR_PIPELINE_VARIANT:-representative}"
INPUT_MODE="${SCIGBLAST_IR_INPUT_MODE:-raw}"
RUN_PREPROCESSING="${SCIGBLAST_IR_RUN_PREPROCESSING:-auto}"
RUN_PREPROCESSING_ENV_OVERRIDE="${SCIGBLAST_IR_RUN_PREPROCESSING:-}"
RUN_PREPROCESSING_OVERRIDE="${SCIGBLAST_RUN_PREPROCESSING:-}"
RUN_INPUT_MODE_OVERRIDE="${SCIGBLAST_RUN_INPUT_MODE:-}"
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
PIPELINE_VARIANT="${SCIGBLAST_IR_PIPELINE_VARIANT:-$PIPELINE_VARIANT}"
if [[ -n "$RUN_PREPROCESSING_OVERRIDE" ]]; then
    RUN_PREPROCESSING="$RUN_PREPROCESSING_OVERRIDE"
elif [[ -n "$RUN_PREPROCESSING_ENV_OVERRIDE" ]]; then
    RUN_PREPROCESSING="$RUN_PREPROCESSING_ENV_OVERRIDE"
fi
if [[ -n "$RUN_INPUT_MODE_OVERRIDE" ]]; then
    INPUT_MODE="$RUN_INPUT_MODE_OVERRIDE"
else
    INPUT_MODE="${SCIGBLAST_IR_INPUT_MODE:-$INPUT_MODE}"
fi
case "$PIPELINE_VARIANT" in
    representative|merged) ;;
    *)
        echo "[IR] unsupported pipeline variant: $PIPELINE_VARIANT (expected representative or merged)" >&2
        exit 2
        ;;
esac
case "$INPUT_MODE" in
    raw|presplit) ;;
    *) echo "[IR] unsupported input mode: $INPUT_MODE (expected raw or presplit)" >&2; exit 2 ;;
esac
case "$RUN_PREPROCESSING" in
    auto|0|1) ;;
    *) echo "[IR] unsupported SCIGBLAST_IR_RUN_PREPROCESSING: $RUN_PREPROCESSING (expected auto, 0 or 1)" >&2; exit 2 ;;
esac
if [[ "$PIPELINE_VARIANT" == "representative" ]]; then
    _default_pandaseq_format="fastq"
else
    _default_pandaseq_format="fasta"
fi
PANDASEQ_OUTPUT_FORMAT="${SCIGBLAST_IR_PANDASEQ_OUTPUT_FORMAT:-${_default_pandaseq_format}}"
case "$PANDASEQ_OUTPUT_FORMAT" in
    fastq|fasta) ;;
    *) echo "[IR] unsupported PANDAseq output format: $PANDASEQ_OUTPUT_FORMAT (expected fastq or fasta)" >&2; exit 2 ;;
esac
if [[ "$PIPELINE_VARIANT" == "merged" && "$PANDASEQ_OUTPUT_FORMAT" == "fastq" ]]; then
    echo "[IR] merged variant requires PANDAseq FASTA output; use representative variant for quality-aware FASTQ" >&2
    exit 2
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

sanitize_dataset_label() {
    local value="${1:-dataset}"
    value="${value//[^A-Za-z0-9._-]/_}"
    [[ -n "$value" ]] || value="dataset"
    printf '%s\n' "$value"
}

csv_quote() {
    local value="${1:-}"
    value="${value//\"/\"\"}"
    printf '"%s"' "$value"
}

if [[ "${SCIGBLAST_MULTI_CHILD:-0}" == "1" ]]; then
    SCIGBLAST_DATA_INPUTS=()
    SCIGBLAST_SUBMISSIONS=()
    SCIGBLAST_BARCODE_CSVS=()
fi
[[ -n "$RUN_RAW_INPUT_OVERRIDE" ]] && SCIGBLAST_RAW_INPUT_DIR="$RUN_RAW_INPUT_OVERRIDE"
if [[ -n "$RUN_SUBMISSION_PATHS_OVERRIDE" ]]; then
    # A child receives the complete submission collection.  Selection is
    # performed by Note in the matcher, never by array index.
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
SCIGBLAST_RAW_INPUT_DIR="${SCIGBLAST_RAW_INPUT_DIR:-}"
SCIGBLAST_SUBMISSION_XLSX="${SCIGBLAST_SUBMISSION_XLSX:-}"
SCIGBLAST_SUBMISSION_PATHS="${SCIGBLAST_SUBMISSION_PATHS:-}"
SCIGBLAST_BARCODE_CSV="${SCIGBLAST_BARCODE_CSV:-}"
SCIGBLAST_MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-1}"
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

# Batch mode: configure Bash arrays in 00.pipeline_config.env.  The arrays are
# independent: every input is checked against the complete submission
# collection, and the matcher uses Note as the hard batch boundary.
_inputs=(); _subs=(); _barcodes=()
[[ "$(declare -p SCIGBLAST_DATA_INPUTS 2>/dev/null || true)" == "declare -a"* ]] && _inputs=("${SCIGBLAST_DATA_INPUTS[@]}")
[[ "$(declare -p SCIGBLAST_SUBMISSIONS 2>/dev/null || true)" == "declare -a"* ]] && _subs=("${SCIGBLAST_SUBMISSIONS[@]}")
[[ "$(declare -p SCIGBLAST_BARCODE_CSVS 2>/dev/null || true)" == "declare -a"* ]] && _barcodes=("${SCIGBLAST_BARCODE_CSVS[@]}")
if (( ! ${#_inputs[@]} && ${#_subs[@]} )); then echo "[IR] SCIGBLAST_SUBMISSIONS was set but SCIGBLAST_DATA_INPUTS is empty" >&2; exit 2; fi
if (( ${#_inputs[@]} )); then
    if (( ${#_subs[@]} == 0 )); then
        echo "[IR] SCIGBLAST_SUBMISSIONS must contain at least one workbook or directory" >&2
        exit 2
    fi
    _resolved_subs=()
    for _sub in "${_subs[@]}"; do
        _resolved_subs+=("$(resolve_submission "$_sub" "" IR)")
    done
    if (( ${#_barcodes[@]} )); then
        if (( ${#_barcodes[@]} == 1 && ${#_inputs[@]} > 1 )); then _v="${_barcodes[0]}"; _barcodes=(); for _x in "${_inputs[@]}"; do _barcodes+=("$_v"); done
        elif (( ${#_barcodes[@]} != ${#_inputs[@]} )); then echo "[IR] SCIGBLAST_BARCODE_CSVS must have one entry per dataset (or one shared entry)" >&2; exit 2; fi
    else
        _barcodes=()
    fi
    for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
        [[ -d "${_inputs[$_i]}" ]] || { echo "[IR] input directory does not exist: ${_inputs[$_i]}" >&2; exit 2; }
        if (( ${#_barcodes[@]} )); then [[ -f "${_barcodes[$_i]}" ]] || { echo "[IR] barcode CSV does not exist: ${_barcodes[$_i]}" >&2; exit 2; }; fi
    done
    _batch_status=0
    declare -A _label_counts=()
    for _input in "${_inputs[@]}"; do
        _label="$(sanitize_dataset_label "$(basename "${_input%/}")")"
        ((_label_counts[$_label]+=1))
    done

    mkdir -p "$OUTPUT_ROOT"
    _manifest_tmp="${OUTPUT_ROOT}/dataset_manifest.csv.tmp.${BASHPID:-$$}"
    {
        printf 'dataset,input_root,input_mode,submission_sources,barcode_csv,match_dir,fastp_dir,split_dir,clean_dir,pandaseq_dir,representative_dir,igblast_dir,preprocessing_dir,status,error\n'
        for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
            _input="${_inputs[$_i]}"
            _label="$(sanitize_dataset_label "$(basename "${_input%/}")")"
            if (( _label_counts[$_label] > 1 )); then
                _suffix="$(printf '%s' "$_input" | sha256sum | cut -c1-8)"
                _label="${_label}__${_suffix}"
            fi
            _bc="${SCIGBLAST_BARCODE_CSV:-}"
            (( ${#_barcodes[@]} )) && _bc="${_barcodes[$_i]}"
            _submission_payload="$(printf '%s\n' "${_resolved_subs[@]}")"
            _m="${OUTPUT_ROOT}/01.match/${_label}"
            _f="${OUTPUT_ROOT}/02.fastp/${_label}"
            _s="${OUTPUT_ROOT}/03.IR_split_output/${_label}"
            _c="${OUTPUT_ROOT}/04.clean_data/${_label}"
            _p="${OUTPUT_ROOT}/05.pandaseq/${_label}"
            _r="${OUTPUT_ROOT}/06.representative/${_label}"
            if [[ "$PIPELINE_VARIANT" == "merged" ]]; then
                _g="${OUTPUT_ROOT}/06.igblastn_out/${_label}"
            else
                _g="${OUTPUT_ROOT}/07.igblastn_out/${_label}"
            fi
            _a="${OUTPUT_ROOT}/08.preprocessing/${_label}"
            csv_quote "$_label"; printf ','
            csv_quote "$_input"; printf ','
            csv_quote "$INPUT_MODE"; printf ','
            csv_quote "$_submission_payload"; printf ','
            csv_quote "$_bc"; printf ','
            csv_quote "$_m"; printf ','; csv_quote "$_f"; printf ','
            csv_quote "$_s"; printf ','; csv_quote "$_c"; printf ','
            csv_quote "$_p"; printf ','; csv_quote "$_r"; printf ','
            csv_quote "$_g"; printf ','; csv_quote "$_a"; printf ',CONFIGURED,\n'
        done
    } > "$_manifest_tmp"
    mv -f "$_manifest_tmp" "${OUTPUT_ROOT}/dataset_manifest.csv"

    update_dataset_manifest() {
        local label="$1" status="$2" error="${3:-}"
        "${PYTHON_BIN}" - "${OUTPUT_ROOT}/dataset_manifest.csv" "$label" "$status" "$error" <<'PY'
import csv
import os
import sys
import tempfile

path, label, status, error = sys.argv[1:]
with open(path, encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle))
if not rows:
    raise SystemExit(0)
fields = list(rows[0])
for row in rows:
    if row.get("dataset", "") == label:
        row["status"] = status
        row["error"] = error
fd, tmp = tempfile.mkstemp(prefix=".dataset_manifest.", suffix=".tmp", dir=os.path.dirname(path) or ".")
os.close(fd)
try:
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
    }

    for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
        _input="${_inputs[$_i]}"
        _label="$(sanitize_dataset_label "$(basename "${_input%/}")")"
        if (( _label_counts[$_label] > 1 )); then _suffix="$(printf '%s' "$_input" | sha256sum | cut -c1-8)"; _out="${OUTPUT_ROOT}/${_label}__${_suffix}"; else _out="${OUTPUT_ROOT}/${_label}"; fi
        _dataset_label="$(basename "$_out")"
        _bc="${SCIGBLAST_BARCODE_CSV:-}"; (( ${#_barcodes[@]} )) && _bc="${_barcodes[$_i]}"
        _submission_payload="$(printf '%s\n' "${_resolved_subs[@]}")"
        echo "[IR][batch $((_i+1))/${#_inputs[@]}] input=$_input submissions=${#_resolved_subs[@]} dataset=$_dataset_label output_root=$OUTPUT_ROOT"
        if SCIGBLAST_MULTI_CHILD=1 SCIGBLAST_DATASET_LABEL="$_dataset_label" SCIGBLAST_RUN_INPUT_MODE="$INPUT_MODE" SCIGBLAST_RUN_PREPROCESSING="$RUN_PREPROCESSING" SCIGBLAST_RUN_RAW_INPUT_DIR="$_input" SCIGBLAST_RUN_SUBMISSION_PATHS="$_submission_payload" SCIGBLAST_RUN_SUBMISSION_XLSX="" SCIGBLAST_RUN_BARCODE_CSV="$_bc" SCIGBLAST_OUTPUT_ROOT="$OUTPUT_ROOT" SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-1}" bash "$0"; then
            if [[ -f "${OUTPUT_ROOT}/.pipeline_state/${_dataset_label}/.pipeline.DONE" ]]; then
                update_dataset_manifest "$_dataset_label" "DONE" ""
            else
                update_dataset_manifest "$_dataset_label" "MATCH_READY" ""
            fi
            echo "[IR][batch $((_i+1))] completed"
        else
            _rc=$?; _batch_status=1; update_dataset_manifest "$_dataset_label" "FAILED" "exit=${_rc}"; echo "[IR][batch $((_i+1))] failed (exit=$_rc)" >&2
        fi
    done
    exit "$_batch_status"
fi
# Only match and fastp consume raw FASTQ.  All later stages consume the
# explicit output directory from the preceding stage.  Edit this hardcoded
# input path when processing another batch.
RAW_INPUT_DIR="${SCIGBLAST_RAW_INPUT_DIR:-/colddata/zqy/XYFY_HZJ1}"
if [[ -n "${SCIGBLAST_SUBMISSION_XLSX:-}" && -d "${SCIGBLAST_SUBMISSION_XLSX}" ]]; then
    SCIGBLAST_SUBMISSION_XLSX="$(resolve_submission "$SCIGBLAST_SUBMISSION_XLSX" "$RAW_INPUT_DIR" IR)"
    export SCIGBLAST_SUBMISSION_XLSX
fi

dataset_label="${SCIGBLAST_DATASET_LABEL:-$(sanitize_dataset_label "$(basename "${RAW_INPUT_DIR%/}")")}" 
DATASET_LABEL="$(sanitize_dataset_label "$dataset_label")"
stage_root() { printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$1" "$DATASET_LABEL"; }

mkdir -p "$OUTPUT_ROOT" "${OUTPUT_ROOT}/logs/${DATASET_LABEL}" "${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}"
LOG_FILE="${OUTPUT_ROOT}/logs/${DATASET_LABEL}/pipeline.log"
exec > >(tee -a "$LOG_FILE") 2>&1

PID_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}/pids"
mkdir -p "$PID_DIR"
PID_FILE="${PID_DIR}/run.$$.pid"
printf '%s\n' "$$" > "$PID_FILE"
cleanup_pid() {
    local rc=$? status="FAILED" error="exit=${rc}"
    rm -f "$PID_FILE"
    if [[ -n "${PIPELINE_DONE_MARKER:-}" && -f "$PIPELINE_DONE_MARKER" ]]; then
        status="DONE"; error=""
    elif [[ "$rc" -eq 0 ]]; then
        status="MATCH_READY"; error=""
    fi
    update_dataset_manifest_status "$status" "$error"
    return "$rc"
}
trap cleanup_pid EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

MAPPING_SUMMARY="${SCIGBLAST_MATCH_OUTPUT:-$(stage_root 01.match)/sample_barcode_summary.csv}"
FASTP_DIR="${SCIGBLAST_FASTP_OUTPUT_DIR:-$(stage_root 02.fastp)/data}"
FASTP_REPORT_DIR="${SCIGBLAST_FASTP_REPORT_DIR:-$(stage_root 02.fastp)/report}"
SPLIT_OUTPUT_DIR="${SCIGBLAST_IR_OUTPUT:-$(stage_root 03.IR_split_output)}"
CLEAN_DIR="${SCIGBLAST_CLEAN_OUTPUT_DIR:-$(stage_root 04.clean_data)}"
PANDASEQ_DIR="${SCIGBLAST_PANDASEQ_OUTPUT_DIR:-$(stage_root 05.pandaseq)}"
REPRESENTATIVE_DIR="${SCIGBLAST_IR_REP_OUTPUT:-$(stage_root 06.representative)}"
PREPROCESSING_DIR="${SCIGBLAST_IR_PREPROCESSING_OUTPUT:-$(stage_root 08.preprocessing)}"
if [[ "$PIPELINE_VARIANT" == "merged" ]]; then
    IGBLAST_STAGE_KEY="06.igblast"
    IGBLAST_OUTPUT_DIR="${SCIGBLAST_IGBLAST_OUTPUT_DIR:-$(stage_root 06.igblastn_out)}"
else
    IGBLAST_STAGE_KEY="07.igblast"
    IGBLAST_OUTPUT_DIR="${SCIGBLAST_IGBLAST_OUTPUT_DIR:-$(stage_root 07.igblastn_out)}"
fi

# A direct single-dataset invocation has no parent batch loop.  Create the
# same manifest contract so downstream preprocessing can always discover the
# dataset without knowing how the runner was started.
DATASET_MANIFEST="${OUTPUT_ROOT}/dataset_manifest.csv"
if [[ ! -s "$DATASET_MANIFEST" ]]; then
    _manifest_tmp="${DATASET_MANIFEST}.tmp.${BASHPID:-$$}"
    {
        printf 'dataset,input_root,input_mode,submission_sources,barcode_csv,match_dir,fastp_dir,split_dir,clean_dir,pandaseq_dir,representative_dir,igblast_dir,preprocessing_dir,status,error\n'
        csv_quote "$DATASET_LABEL"; printf ','; csv_quote "$RAW_INPUT_DIR"; printf ','; csv_quote "$INPUT_MODE"; printf ','
        csv_quote "${SCIGBLAST_SUBMISSION_PATHS:-${SCIGBLAST_SUBMISSION_XLSX:-}}"; printf ','
        csv_quote "${SCIGBLAST_BARCODE_CSV:-}"; printf ','; csv_quote "$(dirname "$MAPPING_SUMMARY")"; printf ','
        csv_quote "$(dirname "$FASTP_DIR")"; printf ','; csv_quote "$SPLIT_OUTPUT_DIR"; printf ','
        csv_quote "$CLEAN_DIR"; printf ','; csv_quote "$PANDASEQ_DIR"; printf ','
        csv_quote "$REPRESENTATIVE_DIR"; printf ','; csv_quote "$IGBLAST_OUTPUT_DIR"; printf ','; csv_quote "$PREPROCESSING_DIR"; printf ',CONFIGURED,\n'
    } > "$_manifest_tmp"
    mv -f "$_manifest_tmp" "$DATASET_MANIFEST"
fi

update_dataset_manifest_status() {
    local status="$1" error="${2:-}"
    [[ -s "$DATASET_MANIFEST" ]] || return 0
    "${PYTHON_BIN}" - "$DATASET_MANIFEST" "$DATASET_LABEL" "$status" "$error" <<'PY' || true
import csv
import os
import sys
import tempfile

path, label, status, error = sys.argv[1:]
try:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
except OSError:
    raise SystemExit(0)
if not rows:
    raise SystemExit(0)
fields = list(rows[0])
for row in rows:
    if row.get("dataset", "") == label:
        row["status"] = status
        row["error"] = error
fd, tmp = tempfile.mkstemp(prefix=".dataset_manifest.", suffix=".tmp", dir=os.path.dirname(path) or ".")
os.close(fd)
try:
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
}

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
export SCIGBLAST_IR_INPUT_MODE="$INPUT_MODE"
export SCIGBLAST_IR_RUN_PREPROCESSING="$RUN_PREPROCESSING"
export SCIGBLAST_DATASET_LABEL="$DATASET_LABEL"
[[ "$MEMORY_BUDGET_GB" =~ ^[0-9]+$ && "$MEMORY_BUDGET_GB" -le 300 ]] || { echo "[IR] memory budget must be <=300GB" >&2; exit 1; }
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$MAX_THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$THREAD_BUDGET" -le "$MAX_THREAD_BUDGET" ]] || { echo "[IR] thread budget must be <=${MAX_THREAD_BUDGET}" >&2; exit 1; }
[[ "$MIN_FREE_MEMORY_GB" =~ ^[0-9]+$ ]] || { echo "[IR] MIN_FREE_MEMORY_GB must be a non-negative integer" >&2; exit 1; }
[[ "$MEMORY_STOP_THRESHOLD_GB" =~ ^[0-9]+$ ]] || { echo "[IR] MEMORY_STOP_THRESHOLD_GB must be a non-negative integer" >&2; exit 1; }
[[ "$MEMORY_POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "[IR] MEMORY_POLL_SECONDS must be a positive integer" >&2; exit 1; }
ALLOW_MAPPING_ERRORS=1
ALLOW_PARTIAL_STAGES="${SCIGBLAST_ALLOW_PARTIAL_STAGES:-1}"
PIPELINE_STATUS=0
STATE_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}"
mkdir -p "$STATE_DIR"
PIPELINE_DONE_MARKER="${STATE_DIR}/.pipeline.DONE"
MATCH_REVIEW_MARKER="${STATE_DIR}/.match_review.done"
MAPPING_SHA256=""
CONFIG_SHA256=""
SCRIPT_SHA256=""

available_memory_gb() {
    awk '/^MemAvailable:/ {printf "%d", $2 / 1024 / 1024; found=1; exit} END {if (!found) exit 1}' /proc/meminfo 2>/dev/null
}
memory_guard() {
    local label="$1" requested_gb="$2" available_gb
    available_gb="$(available_memory_gb 2>/dev/null || true)"
    [[ "$available_gb" =~ ^[0-9]+$ ]] || {
        echo "[IR] cannot read server available memory; refusing to start ${label}" >&2
        return 1
    }
    local start_limit="$(( requested_gb + MIN_FREE_MEMORY_GB ))"
    (( start_limit < MEMORY_STOP_THRESHOLD_GB )) && start_limit="$MEMORY_STOP_THRESHOLD_GB"
    if (( available_gb < start_limit )); then
        echo "[IR] insufficient available memory for ${label}: available=${available_gb}GB start_limit=${start_limit}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB" >&2
        return 1
    fi
    echo "[IR] memory check ${label}: available=${available_gb}GB required=${requested_gb}GB reserve=${MIN_FREE_MEMORY_GB}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB"
}
FASTP_REQUIRED_GB="$(( ${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-2} * ${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-8} ))"
CLEAN_REQUIRED_GB="$(( ${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-2} * ${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-8} ))"
PANDASEQ_REQUIRED_GB="$(( ${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-2} * ${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-8} ))"
IGBLAST_REQUIRED_GB="$(( ${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-2} * ${SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB:-96} ))"
SPLIT_REQUIRED_GB="${SCIGBLAST_IR_MEMORY_GB:-16}"
REPRESENTATIVE_REQUIRED_GB="${SCIGBLAST_IR_REP_MEMORY_GB:-96}"

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
                echo "[IR][memory] STOP stage=${label}: cannot read MemAvailable; terminating process tree PID=${stage_pid}" >&2
                stop_process_tree "$stage_pid"
                exit 75
            fi
            if (( available_gb < MEMORY_STOP_THRESHOLD_GB )); then
                printf 'stage=%s\navailable_gb=%s\nthreshold_gb=%s\ntimestamp=%s\n' "$label" "$available_gb" "$MEMORY_STOP_THRESHOLD_GB" "$(date -Iseconds)" > "$event_file"
                echo "[IR][memory] STOP stage=${label}: MemAvailable=${available_gb}GB below threshold=${MEMORY_STOP_THRESHOLD_GB}GB; terminating process tree PID=${stage_pid}" >&2
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
        echo "[IR][memory] ${label} stopped by memory watchdog; details: ${event_file}" >&2
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

stage_script_path() {
    case "$1" in
        01.match) printf '%s\n' "${SCRIPT_DIR}/01.match_sample.py" ;;
        02.fastp) printf '%s\n' "${SCRIPT_DIR}/02.run_fastp.sh" ;;
        03.split) printf '%s\n' "${SCRIPT_DIR}/03.split_barcode.py" ;;
        04.clean) printf '%s\n' "${SCRIPT_DIR}/04.clean_header.sh" ;;
        05.pandaseq) printf '%s\n' "${SCRIPT_DIR}/05.work_pandaseq.sh" ;;
        06.representative) printf '%s\n' "${SCRIPT_DIR}/06.representative.py" ;;
        06.igblast|07.igblast) printf '%s\n' "${SCRIPT_DIR}/07.work_igblastn.sh" ;;
        08.preprocessing) printf '%s\n' "${SCRIPT_DIR}/08.preprocessing.py" ;;
        *) return 1 ;;
    esac
}

stage_script_hash() {
    local stage="$1" path file digest payload=""
    path="$(stage_script_path "$stage")" || return 1
    if [[ "$stage" == "08.preprocessing" ]]; then
        [[ -f "$path" ]] || return 1
        digest="$(hash_file "$path")" || return 1
        payload+="${path}\t${digest}\n"
        while IFS= read -r -d '' file; do
            digest="$(hash_file "$file")" || return 1
            payload+="${file}\t${digest}\n"
        done < <(find "${SCRIPT_DIR}/models" -maxdepth 1 -type f -name '*.py' -print0 | sort -z)
        [[ -n "$payload" ]] || return 1
        if command -v sha256sum >/dev/null 2>&1; then
            printf '%b' "$payload" | sha256sum | awk '{print $1}'
        elif command -v shasum >/dev/null 2>&1; then
            printf '%b' "$payload" | shasum -a 256 | awk '{print $1}'
        else
            return 1
        fi
        return
    fi
    [[ -f "$path" ]] || return 1
    if [[ "$stage" == "01.match" || "$stage" == "03.split" ]]; then
        { hash_file "$path"; hash_file "${SCRIPT_DIR}/tools/pipeline_config.py"; } | sha256sum | awk '{print $1}'
        return
    fi
    hash_file "$path"
}

pipeline_script_hash() {
    local stage digest payload=""
    digest="$(hash_file "${SCRIPT_DIR}/run_ir_pipeline.sh")" || return 1
    payload+="run_ir_pipeline\t${digest}\n"
    for stage in 01.match 02.fastp 03.split 04.clean 05.pandaseq 06.representative 07.igblast; do
        digest="$(stage_script_hash "$stage")" || return 1
        payload+="${stage}\t${digest}\n"
    done
    if [[ "$PIPELINE_VARIANT" == "representative" && "$RUN_PREPROCESSING" != "0" ]]; then
        digest="$(stage_script_hash 08.preprocessing)" || return 1
        payload+="08.preprocessing\t${digest}\n"
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%b' "$payload" | sha256sum | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        printf '%b' "$payload" | shasum -a 256 | awk '{print $1}'
    else
        return 1
    fi
}

write_stage_marker() {
    local stage="$1" status="${2:-DONE}"
    local marker="${STATE_DIR}/.pipeline_stage_${stage}.DONE"
    local marker_tmp="${marker}.tmp.${BASHPID:-$$}"
    printf 'status=%s\n' "$status" > "$marker_tmp"
    printf 'stage=%s\n' "$stage" >> "$marker_tmp"
    printf 'mapping_sha256=%s\n' "$MAPPING_SHA256" >> "$marker_tmp"
    printf 'config_sha256=%s\n' "$CONFIG_SHA256" >> "$marker_tmp"
    printf 'script_sha256=%s\n' "$(stage_script_hash "$stage")" >> "$marker_tmp"
    printf 'timestamp=%s\n' "$(date -Iseconds)" >> "$marker_tmp"
    mv -f "$marker_tmp" "$marker"
}

legacy_flat_split_files() {
    [[ -d "$SPLIT_OUTPUT_DIR" && -s "$MAPPING_SUMMARY" ]] || return 0
    "${PYTHON_BIN}" - "$SPLIT_OUTPUT_DIR" "$MAPPING_SUMMARY" <<'PY'
import csv
import sys
from pathlib import Path

root = Path(sys.argv[1])
summary = Path(sys.argv[2])

known = {}
with summary.open(encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
        if str(row.get("status", "")).strip().upper() != "OK":
            continue
        pair_id = str(row.get("pair_id", "")).strip().replace("\\", "/")
        sample = str(row.get("sample_id", "")).strip()
        if pair_id and sample:
            pair = Path(pair_id)
            known.setdefault((pair.parent.name, pair.name), set()).add(sample)

for path in sorted(root.glob("*/*")):
    if not path.is_file() or not path.name.lower().endswith((".fq.gz", ".fastq.gz")):
        continue
    try:
        rel = path.relative_to(root)
    except ValueError:
        continue
    # Legacy form: <lane>/<pair>_R1/R2.fq.gz. New output has a sample leaf.
    name = path.name
    for suffix in ("_R1.fq.gz", "_R2.fq.gz", "_R1.fastq.gz", "_R2.fastq.gz"):
        if name.lower().endswith(suffix.lower()):
            name = name[:-len(suffix)]
            break
    if len(rel.parts) == 2 and len(known.get((path.parent.name, name), set())) == 1:
        print(path)
PY
}

has_legacy_flat_split_output() {
    [[ -n "$(legacy_flat_split_files | head -n 1)" ]]
}

remove_legacy_flat_split_output() {
    while IFS= read -r legacy_file; do
        [[ -n "$legacy_file" ]] && rm -f -- "$legacy_file"
    done < <(legacy_flat_split_files)
}

has_legacy_flat_clean_output() {
    [[ -d "$CLEAN_DIR" ]] || return 1
    [[ -n "$(find "$CLEAN_DIR" -mindepth 2 -maxdepth 2 -type f -name '*_forward_cleaned.fq.gz' -print -quit 2>/dev/null)" ]]
}

has_legacy_flat_pandaseq_output() {
    [[ -d "$PANDASEQ_DIR" ]] || return 1
    [[ -n "$(find "$PANDASEQ_DIR" -mindepth 2 -maxdepth 2 -type f \( -name '*_merged.fasta' -o -name '*_merged.fa' \) -print -quit 2>/dev/null)" ]]
}

remove_legacy_flat_pandaseq_output() {
    [[ -d "$PANDASEQ_DIR" ]] || return 0
    find "$PANDASEQ_DIR" -mindepth 2 -maxdepth 2 -type f \
        \( -name '*_merged.fasta' -o -name '*_merged.fa' -o -name '*_unaligned.fasta' -o -name '*_unaligned.fa' \) \
        -print -delete 2>/dev/null || true
}

stage_done() {
    local stage="$1" marker="${STATE_DIR}/.pipeline_stage_${1}.DONE"
    [[ "$FORCE_RERUN" != "1" && -s "$marker" ]] || return 1
    # A PARTIAL stage must rerun after a corrected match row is supplied.  The
    # stage workers have their own per-sample checkpoints, so completed samples
    # remain skipped while newly usable/error-repaired samples are added.
    grep -qx 'status=DONE' "$marker" || return 1
    # fastp is intentionally restricted to status=OK files from the match
    # summary, so a corrected mapping must invalidate this stage as well.
    grep -qx "mapping_sha256=${MAPPING_SHA256}" "$marker" || return 1
    grep -qx "config_sha256=${CONFIG_SHA256}" "$marker" || return 1
    grep -qx "script_sha256=$(stage_script_hash "$stage")" "$marker" || return 1
    case "$stage" in
        01.match) [[ -s "$MAPPING_SUMMARY" ]] || return 1 ;;
        02.fastp) [[ -d "$FASTP_DIR" && -d "$FASTP_REPORT_DIR" ]] || return 1; [[ -s "${FASTP_REPORT_DIR}/fastp_summary.csv" ]] || return 1; [[ -n "$(find "$FASTP_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        03.split) [[ -d "$SPLIT_OUTPUT_DIR" ]] || return 1; [[ -s "${SPLIT_OUTPUT_DIR}/ir_split_summary.csv" ]] || return 1; [[ -n "$(find "$SPLIT_OUTPUT_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]] || return 1; if has_legacy_flat_split_output; then return 1; fi ;;
        04.clean) [[ -d "$CLEAN_DIR" ]] || return 1; [[ -n "$(find "$CLEAN_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]] || return 1; if has_legacy_flat_clean_output; then return 1; fi ;;
        05.pandaseq) [[ -d "$PANDASEQ_DIR" ]] || return 1; [[ -s "${PANDASEQ_DIR}/pandaseq_summary.csv" ]] || return 1; [[ -n "$(find "$PANDASEQ_DIR" -type f \( -name '*_merged.fastq' -o -name '*_merged.fq' -o -name '*_merged.fasta' -o -name '*_merged.fa' \) -print -quit 2>/dev/null)" ]] || return 1; if has_legacy_flat_pandaseq_output; then return 1; fi ;;
        06.representative)
            [[ -s "${REPRESENTATIVE_DIR}/representative_map.tsv.gz" && -s "${REPRESENTATIVE_DIR}/representative_summary.csv" ]] || return 1
            [[ -n "$(gzip -cd "${REPRESENTATIVE_DIR}/representative_map.tsv.gz" 2>/dev/null | awk 'NR > 1 {print; exit}')" ]] || return 1
            [[ -n "$(awk 'NR > 1 {print; exit}' "${REPRESENTATIVE_DIR}/representative_summary.csv" 2>/dev/null)" ]] || return 1
            [[ -n "$(find "$REPRESENTATIVE_DIR" -type f -name '*.fasta' -size +0c -print -quit 2>/dev/null)" ]] || return 1
            ;;
        06.igblast)
            [[ -s "${IGBLAST_OUTPUT_DIR}/chain_summary.csv" ]] || return 1
            [[ -n "$(find "$IGBLAST_OUTPUT_DIR" -type f \( -name 'TCR.tsv' -o -name 'BCR.tsv' -o -name '.NO_RESULTS' \) -print -quit 2>/dev/null)" ]] || return 1
            ;;
        07.igblast) [[ -s "${IGBLAST_OUTPUT_DIR}/chain_summary.csv" ]] || return 1; [[ -n "$(find "$IGBLAST_OUTPUT_DIR" -type f \( -name 'TCR.tsv' -o -name 'BCR.tsv' -o -name '.NO_RESULTS' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
        08.preprocessing)
            [[ "$PIPELINE_VARIANT" == "representative" && "$RUN_PREPROCESSING" != "0" ]] || return 1
            [[ -s "${PREPROCESSING_DIR}/Datapoint.csv" && -s "${PREPROCESSING_DIR}/processing_manifest.csv" ]] || return 1
            [[ -s "${PREPROCESSING_DIR}/.preprocessing_state/run.json" ]] || return 1
            grep -q '"status": "DONE"' "${PREPROCESSING_DIR}/.preprocessing_state/run.json" || return 1
            ;;
        *) return 1 ;;
    esac
}

final_outputs_ready() {
    [[ -s "${IGBLAST_OUTPUT_DIR}/chain_summary.csv" ]] || return 1
    [[ -n "$(find "${IGBLAST_OUTPUT_DIR}" -type f \( -name 'TCR.tsv' -o -name 'BCR.tsv' -o -name '.NO_RESULTS' \) -print -quit 2>/dev/null)" ]] || return 1
    if [[ "$PIPELINE_VARIANT" == "representative" && "$RUN_PREPROCESSING" != "0" ]]; then
        stage_done 08.preprocessing
    else
        return 0
    fi
}

has_fastp_pair() {
    local r1 r2
    while IFS= read -r -d '' r1; do
        r2="${r1%_R1.fq.gz}_R2.fq.gz"
        [[ -s "$r2" ]] && return 0
    done < <(find "$FASTP_DIR" -type f -name '*_R1.fq.gz' -print0 2>/dev/null || true)
    return 1
}

cleanup_stage() {
    local path="$1"
    [[ "$CLEANUP_INTERMEDIATE" == "1" ]] || return 0
    case "$path" in
        "${OUTPUT_ROOT}"/*)
            [[ -d "$path" ]] || return 0
            # Keep fastp JSON/HTML and split/clean/PANDAseq reports; remove
            # only the large sequence payloads after the next stage succeeds.
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

archive_igblast_batches() {
    local batches="${IGBLAST_OUTPUT_DIR}/.batches"
    [[ -d "$batches" ]] || return 0
    if [[ "$CLEANUP_INTERMEDIATE" == "1" ]]; then
        rm -rf "$batches"
        echo "[IR] explicit cleanup removed stale IgBLAST batches: $batches"
        return 0
    fi
    local archive="${IGBLAST_OUTPUT_DIR}/.batches.previous.$(date +%Y%m%d%H%M%S).${BASHPID:-$$}"
    mv -- "$batches" "$archive"
    echo "[IR] archived stale IgBLAST batches (intermediates retained): $archive"
}

echo "[IR 1/8] sample/barcode matching"
echo "[IR] memory budget=${MEMORY_BUDGET_GB}GB"
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
    # Keep usable rows flowing through downstream stages, but preserve the
    # matching failure in the final pipeline status.
    PIPELINE_STATUS=1
    if [[ "$ALLOW_MAPPING_ERRORS" != "1" || "$MATCH_SUMMARY_READY" -ne 1 ]]; then
        echo "[IR] match failed and no usable summary was produced; stopping" >&2
        exit "$MATCH_STATUS"
    fi
    echo "[IR] warning: match summary contains errors; valid rows will continue" >&2
fi
if [[ "$MATCH_SUMMARY_READY" -ne 1 ]]; then
    echo "[IR] match did not produce a fresh summary; stopping" >&2
    exit 1
fi
MAPPING_SHA256="$(hash_file "$MAPPING_SUMMARY")" || {
    echo "[IR] cannot calculate mapping summary checksum" >&2
    exit 1
}
CONFIG_SHA256="$(hash_file "$CONFIG_FILE"):${INPUT_MODE}" || { echo "[IR] cannot calculate config checksum" >&2; exit 1; }
SCRIPT_SHA256="$(pipeline_script_hash)" || { echo "[IR] cannot calculate pipeline script checksum" >&2; exit 1; }
if [[ "$MATCH_STATUS" -eq 0 ]]; then write_stage_marker "01.match"; fi

MATCH_REVIEW_CURRENT=0
if [[ -s "$MATCH_REVIEW_MARKER" ]] \
   && grep -qx "mapping_sha256=${MAPPING_SHA256}" "$MATCH_REVIEW_MARKER" 2>/dev/null \
   && grep -qx "config_sha256=${CONFIG_SHA256}" "$MATCH_REVIEW_MARKER" 2>/dev/null; then
    MATCH_REVIEW_CURRENT=1
fi
if [[ "$MATCH_ONLY_FIRST_RUN" == "1" && "$MATCH_REVIEW_CURRENT" -ne 1 ]]; then
    marker_tmp="${MATCH_REVIEW_MARKER}.tmp.${BASHPID:-$$}"
    {
        printf 'status=READY_FOR_REVIEW\n'
        printf 'mapping_sha256=%s\n' "$MAPPING_SHA256"
        printf 'config_sha256=%s\n' "$CONFIG_SHA256"
        printf 'timestamp=%s\n' "$(date -Iseconds)"
    } > "$marker_tmp"
    mv -f "$marker_tmp" "$MATCH_REVIEW_MARKER"
    echo "[IR] first invocation completed matching only. Review: ${MAPPING_SUMMARY}" >&2
    echo "[IR] run this pipeline again to continue with fastp and downstream stages."
    exit "$MATCH_STATUS"
fi

# The final marker makes a fully completed run resumable even when large
# intermediate sequence files were cleaned.  Matching is intentionally run
# first; changing an error row changes this checksum and invalidates the skip.
if [[ "$FORCE_RERUN" != "1" ]] && [[ -f "$PIPELINE_DONE_MARKER" ]] && \
   ! has_legacy_flat_split_output && ! has_legacy_flat_clean_output && \
   ! has_legacy_flat_pandaseq_output && final_outputs_ready && \
   grep -qx "mapping_sha256=${MAPPING_SHA256}" "$PIPELINE_DONE_MARKER" 2>/dev/null && \
   grep -qx "config_sha256=${CONFIG_SHA256}" "$PIPELINE_DONE_MARKER" 2>/dev/null && \
   grep -qx "script_sha256=${SCRIPT_SHA256}" "$PIPELINE_DONE_MARKER" 2>/dev/null; then
    echo "[IR] pipeline already complete for this mapping summary; skipping"
    exit 0
fi
rm -f "$PIPELINE_DONE_MARKER"

echo "[IR 2/8] fastp"
if ! stage_done "02.fastp"; then
    FASTP_STATUS=0
    if run_monitored_stage "fastp" "$FASTP_REQUIRED_GB" env \
        SCIGBLAST_FASTP_MAPPING_SUMMARY="${MAPPING_SUMMARY}" \
        bash "${SCRIPT_DIR}/02.run_fastp.sh" \
        --input-dir "${RAW_INPUT_DIR}" \
        --output-dir "${FASTP_DIR}" \
        --report-dir "${FASTP_REPORT_DIR}"; then
        FASTP_STATUS=0
    else
        FASTP_STATUS=$?
    fi
    if (( FASTP_STATUS != 0 )); then
        PIPELINE_STATUS=1
        if [[ "$FASTP_STATUS" == "75" ]]; then
            echo "[IR] fastp stopped by memory guard; downstream stages will not start" >&2
            exit "$FASTP_STATUS"
        elif [[ "$ALLOW_PARTIAL_STAGES" == "1" ]] && has_fastp_pair; then
            echo "[IR] fastp had failed samples; valid FASTQ pairs will continue downstream" >&2
            write_stage_marker "02.fastp" "PARTIAL"
        else
            echo "[IR] fastp produced no usable FASTQ pair; stopping" >&2
            exit "$FASTP_STATUS"
        fi
    else
        write_stage_marker "02.fastp"
    fi
else echo "[IR] reusing completed fastp stage"; fi

if [[ "$INPUT_MODE" == "presplit" ]]; then
    echo "[IR 3/8] validate/adopt pre-split UMI-tagged FASTQ after fastp"
else
    echo "[IR 3/8] sample/barcode split on fastp output"
fi
SPLIT_INPUT_DIR="$FASTP_DIR"
SPLIT_STATUS=0
SPLIT_RERAN=0
if stage_done "03.split"; then
    echo "[IR] reusing completed split stage"
else
SPLIT_RERAN=1
remove_legacy_flat_split_output
run_monitored_stage "IR split" "$SPLIT_REQUIRED_GB" env \
SCIGBLAST_IR_INPUT="${SPLIT_INPUT_DIR}" \
SCIGBLAST_IR_OUTPUT="${SPLIT_OUTPUT_DIR}" \
SCIGBLAST_IR_MAPPING_SUMMARY="${MAPPING_SUMMARY}" \
SCIGBLAST_IR_INPUT_MODE="${INPUT_MODE}" \
"${PYTHON_BIN}" "${SCRIPT_DIR}/03.split_barcode.py" || SPLIT_STATUS=$?
if [[ "$SPLIT_STATUS" -ne 0 ]]; then
    # split_barcode may produce valid outputs while reporting unmatched files;
    # continue those outputs, but do not mark the whole run as complete.
    PIPELINE_STATUS=1
    if [[ "$ALLOW_MAPPING_ERRORS" != "1" || ! -d "$SPLIT_OUTPUT_DIR" ]]; then
        echo "[IR] split failed and no output directory was produced; stopping" >&2
        exit "$SPLIT_STATUS"
    fi
    echo "[IR] warning: split reported mapping errors; valid sample outputs will continue" >&2
fi
if [[ "$SPLIT_STATUS" -ne 0 ]]; then
    if [[ -z "$(find "$SPLIT_OUTPUT_DIR" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]]; then
        echo "[IR] split produced no valid sample FASTQ; downstream stages will not start" >&2
        exit "$SPLIT_STATUS"
    fi
    write_stage_marker "03.split" "PARTIAL"
else
    write_stage_marker "03.split"
fi
fi

echo "[IR 4/8] clean header"
if (( SPLIT_RERAN )); then
    rm -f "${STATE_DIR}/.pipeline_stage_04.clean.DONE" \
          "${STATE_DIR}/.pipeline_stage_05.pandaseq.DONE" \
          "${STATE_DIR}/.pipeline_stage_06.representative.DONE" \
          "${STATE_DIR}/.pipeline_stage_06.igblast.DONE" \
          "${STATE_DIR}/.pipeline_stage_07.igblast.DONE" \
          "${STATE_DIR}/.pipeline_stage_08.preprocessing.DONE"
fi
CLEAN_RERAN=0
if ! stage_done "04.clean"; then
    CLEAN_RERAN=1
    run_monitored_stage "clean header" "$CLEAN_REQUIRED_GB" env \
    SCIGBLAST_IR_MAPPING_SUMMARY="${MAPPING_SUMMARY}" \
    bash "${SCRIPT_DIR}/04.clean_header.sh" \
    --input-dir "${SPLIT_OUTPUT_DIR}" \
    --output-dir "${CLEAN_DIR}"
    write_stage_marker "04.clean"
else echo "[IR] reusing completed clean stage"; fi

echo "[IR 5/8] PANDAseq"
if (( CLEAN_RERAN )); then
    rm -f "${STATE_DIR}/.pipeline_stage_05.pandaseq.DONE" \
          "${STATE_DIR}/.pipeline_stage_06.representative.DONE" \
          "${STATE_DIR}/.pipeline_stage_06.igblast.DONE" \
          "${STATE_DIR}/.pipeline_stage_07.igblast.DONE" \
          "${STATE_DIR}/.pipeline_stage_08.preprocessing.DONE"
fi
PANDASEQ_RERAN=0
if ! stage_done "05.pandaseq"; then
    PANDASEQ_RERAN=1
    remove_legacy_flat_pandaseq_output
    run_monitored_stage "PANDAseq" "$PANDASEQ_REQUIRED_GB" env \
    SCIGBLAST_PANDASEQ_OUTPUT_FORMAT="${PANDASEQ_OUTPUT_FORMAT}" \
    bash "${SCRIPT_DIR}/05.work_pandaseq.sh" \
    --data-dir "${CLEAN_DIR}" \
    --output-dir "${PANDASEQ_DIR}"
    write_stage_marker "05.pandaseq"
else echo "[IR] reusing completed PANDAseq stage"; fi

if [[ "$PIPELINE_VARIANT" == "merged" ]]; then
    # Direct mode skips representative selection and sends every PANDAseq
    # merged FASTA to IgBLAST.  This is useful when UMI-level representatives
    # are not desired; 07.work_igblastn.sh handles the pandaseq input mode.
    echo "[IR 6/6] IgBLAST on merged PANDAseq FASTA"
    if (( PANDASEQ_RERAN )); then
        rm -f "${STATE_DIR}/.pipeline_stage_06.igblast.DONE"
        archive_igblast_batches
    fi
    if ! stage_done "$IGBLAST_STAGE_KEY"; then
        rm -f "${STATE_DIR}/.pipeline_stage_06.igblast.DONE"
        run_monitored_stage "IgBLAST (merged)" "$IGBLAST_REQUIRED_GB" env \
        SCIGBLAST_IGBLAST_CHAIN_SUMMARY="${MAPPING_SUMMARY}" \
        bash "${SCRIPT_DIR}/07.work_igblastn.sh" \
        --data-dir "${PANDASEQ_DIR}" \
        --input-mode pandaseq \
        --output-dir "${IGBLAST_OUTPUT_DIR}"
        write_stage_marker "$IGBLAST_STAGE_KEY"
    else echo "[IR] reusing completed merged-IgBLAST stage"; fi
else
    echo "[IR 6/8] representative sequences"
    if (( PANDASEQ_RERAN )); then
        rm -f "${STATE_DIR}/.pipeline_stage_06.representative.DONE" \
              "${STATE_DIR}/.pipeline_stage_07.igblast.DONE"
        # IgBLAST batches are derived from the PANDAseq inventory.  Reusing the
        # old manifest would silently omit newly restored sample directories.
        archive_igblast_batches
    fi
    REPRESENTATIVE_RERAN=0
    if ! stage_done "06.representative"; then
        REPRESENTATIVE_RERAN=1
        # Never leave an older DONE marker able to resurrect stale representative
        # files if this run fails before publishing a new result.
        rm -f "${STATE_DIR}/.pipeline_stage_06.representative.DONE"
        run_monitored_stage "IR representative" "$REPRESENTATIVE_REQUIRED_GB" env \
        SCIGBLAST_IR_REP_INPUT="${PANDASEQ_DIR}" \
        SCIGBLAST_IR_REP_OUTPUT="${REPRESENTATIVE_DIR}" \
        SCIGBLAST_IR_SPLIT_INPUT="${SPLIT_OUTPUT_DIR}" \
        "${PYTHON_BIN}" "${SCRIPT_DIR}/06.representative.py"
        write_stage_marker "06.representative"
    else echo "[IR] reusing completed representative stage"; fi

    echo "[IR 7/8] IgBLAST"
    if (( REPRESENTATIVE_RERAN )); then
        rm -f "${STATE_DIR}/.pipeline_stage_07.igblast.DONE"
        rm -f "${STATE_DIR}/.pipeline_stage_08.preprocessing.DONE"
        archive_igblast_batches
    fi
    IGBLAST_RERAN=0
    if ! stage_done "$IGBLAST_STAGE_KEY"; then
        IGBLAST_RERAN=1
        # Analysis consumes the final TSVs, so any IgBLAST rerun invalidates
        # its checkpoint even when representative FASTA did not change.
        rm -f "${STATE_DIR}/.pipeline_stage_08.preprocessing.DONE"
        run_monitored_stage "IgBLAST" "$IGBLAST_REQUIRED_GB" env \
        SCIGBLAST_IGBLAST_CHAIN_SUMMARY="${MAPPING_SUMMARY}" \
        bash "${SCRIPT_DIR}/07.work_igblastn.sh" \
        --data-dir "${REPRESENTATIVE_DIR}/representative_fasta" \
        --input-mode representative \
        --output-dir "${IGBLAST_OUTPUT_DIR}"
        write_stage_marker "$IGBLAST_STAGE_KEY"
    else echo "[IR] reusing completed IgBLAST stage"; fi

    if [[ "$RUN_PREPROCESSING" != "0" ]]; then
        echo "[IR 8/8] preprocessing representative IgBLAST output"
        if (( IGBLAST_RERAN )) || ! stage_done "08.preprocessing"; then
            rm -f "${STATE_DIR}/.pipeline_stage_08.preprocessing.DONE"
            run_monitored_stage "IR preprocessing" "$REPRESENTATIVE_REQUIRED_GB" env \
            SCIGBLAST_IR_PREPROCESSING_INPUTS_SERIALIZED="${IGBLAST_OUTPUT_DIR}" \
            SCIGBLAST_IR_PREPROCESSING_INPUT="${IGBLAST_OUTPUT_DIR}" \
            SCIGBLAST_IR_PREPROCESSING_OUTPUT="${PREPROCESSING_DIR}" \
            SCIGBLAST_IR_PREPROCESSING_REPRESENTATIVE_ROOT="${REPRESENTATIVE_DIR}" \
            SCIGBLAST_IR_PREPROCESSING_UMI_SOURCE="auto" \
            "${PYTHON_BIN}" "${SCRIPT_DIR}/08.preprocessing.py"
            write_stage_marker "08.preprocessing"
        else
            echo "[IR] reusing completed preprocessing stage"
        fi
    else
        echo "[IR] representative output preprocessing disabled (SCIGBLAST_IR_RUN_PREPROCESSING=0)"
    fi
fi

if [[ "$PIPELINE_STATUS" -eq 0 ]]; then
    final_outputs_ready || { echo "[IR] final output validation failed; refusing to write DONE" >&2; exit 1; }
    marker_tmp="${PIPELINE_DONE_MARKER}.tmp.${BASHPID:-$$}"
    {
        printf 'status=DONE\n'
        printf 'mapping_sha256=%s\n' "$MAPPING_SHA256"
        printf 'config_sha256=%s\n' "$CONFIG_SHA256"
        printf 'script_sha256=%s\n' "$SCRIPT_SHA256"
        printf 'timestamp=%s\n' "$(date -Iseconds)"
    } > "$marker_tmp"
    mv -f "$marker_tmp" "$PIPELINE_DONE_MARKER"
    # Do not delete any intermediate sequence payload until every downstream
    # stage has succeeded.  This keeps a failed run fully resumable.
    cleanup_stage "${FASTP_DIR}"
    cleanup_stage "${SPLIT_OUTPUT_DIR}"
    cleanup_stage "${CLEAN_DIR}"
    cleanup_stage "${PANDASEQ_DIR}"
    # Representative FASTA/map/summary are durable inputs for downstream
    # review and reruns; never delete them as intermediate payloads.
else
    echo "[IR] pipeline had errors; preserving all intermediate data for rerun" >&2
fi

echo "[IR] completed"
exit "$PIPELINE_STATUS"
