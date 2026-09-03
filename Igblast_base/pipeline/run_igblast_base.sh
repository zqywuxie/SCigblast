#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
PID_FILE="${SCRIPT_DIR}/run_igblast_base.$$.pid"
printf '%s\n' "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
CONFIG_FILE="${SCRIPT_DIR}/00.pipeline_config.env"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_RAW_INPUT_OVERRIDE="${SCIGBLAST_RUN_RAW_INPUT_DIR:-}"
RUN_SUBMISSION_OVERRIDE="${SCIGBLAST_RUN_SUBMISSION:-}"
RUN_SUBMISSION_PATHS_OVERRIDE="${SCIGBLAST_RUN_SUBMISSION_PATHS:-}"
RUN_OUTPUT_OVERRIDE="${SCIGBLAST_RUN_OUTPUT_ROOT:-${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-}}}"
RUN_DATASET_LABEL_OVERRIDE="${SCIGBLAST_RUN_DATASET_LABEL:-${SCIGBLAST_DATASET_LABEL:-}}"
RUN_MATCH_ONLY_OVERRIDE="${SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN:-${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-${MATCH_ONLY_FIRST_RUN:-}}}"
[[ -f "$CONFIG_FILE" ]] && { set -a; . "$CONFIG_FILE"; set +a; }
if [[ "${SCIGBLAST_MULTI_CHILD:-0}" == "1" ]]; then
  SCIGBLAST_DATA_INPUTS=(); SCIGBLAST_SUBMISSIONS=()
fi
[[ -n "$RUN_RAW_INPUT_OVERRIDE" ]] && RAW_INPUT_DIR="$RUN_RAW_INPUT_OVERRIDE"
SUBMISSION_PATH="${RUN_SUBMISSION_OVERRIDE:-}"
SUBMISSION_PATHS="${RUN_SUBMISSION_PATHS_OVERRIDE:-}"
if [[ -n "$SUBMISSION_PATHS" ]]; then SUBMISSION_PATH=""; fi
[[ -n "$RUN_OUTPUT_OVERRIDE" ]] && SCIGBLAST_OUTPUT_ROOT="$RUN_OUTPUT_OVERRIDE"
[[ -n "$RUN_MATCH_ONLY_OVERRIDE" ]] && SCIGBLAST_MATCH_ONLY_FIRST_RUN="$RUN_MATCH_ONLY_OVERRIDE"
[[ -n "$RUN_DATASET_LABEL_OVERRIDE" ]] && SCIGBLAST_DATASET_LABEL="$RUN_DATASET_LABEL_OVERRIDE"
SCIGBLAST_SUBMISSION_PATHS="${SCIGBLAST_SUBMISSION_PATHS:-${SUBMISSION_PATHS:-}}"
export RAW_INPUT_DIR SCIGBLAST_OUTPUT_ROOT SCIGBLAST_MATCH_ONLY_FIRST_RUN SCIGBLAST_SUBMISSION_PATHS
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
OUTPUT_ROOT="$(resolve_output_root "${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-}}")"
SCIGBLAST_OUTPUT_ROOT="$OUTPUT_ROOT"
export SCIGBLAST_OUTPUT_ROOT
RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"

# Batch mode uses SCIGBLAST_DATA_INPUTS and optional SCIGBLAST_SUBMISSIONS arrays in
# 00.pipeline_config.env. No barcode file is used by the baseline pipeline.
_inputs=(); _subs=()
[[ "$(declare -p SCIGBLAST_DATA_INPUTS 2>/dev/null || true)" == "declare -a"* ]] && _inputs=("${SCIGBLAST_DATA_INPUTS[@]}")
[[ "$(declare -p SCIGBLAST_SUBMISSIONS 2>/dev/null || true)" == "declare -a"* ]] && _subs=("${SCIGBLAST_SUBMISSIONS[@]}")
if (( ! ${#_inputs[@]} && ${#_subs[@]} )); then echo "[BASE] SCIGBLAST_SUBMISSIONS was set but SCIGBLAST_DATA_INPUTS is empty" >&2; exit 2; fi
# A recursive child is always a single-dataset run.  Only DATA_INPUTS can
# activate batch mode; a carried submission path must never re-enter it.
if (( ${#_inputs[@]} )); then
  if (( ${#_subs[@]} == 0 )) && [[ -n "${SUBMISSION_XLSX:-}" ]]; then _subs=("${SUBMISSION_XLSX}"); fi
  if (( ${#_subs[@]} == 0 )); then echo "[BASE] at least one submission workbook/directory is required" >&2; exit 2; fi
  _resolved_subs=()
  for _sub in "${_subs[@]}"; do
    if [[ -d "$_sub" ]]; then find "$_sub" -type f -iname '*.xlsx' -print -quit | grep -q . || { echo "[BASE] no .xlsx submission under: $_sub" >&2; exit 2; }
    else [[ -f "$_sub" ]] || { echo "[BASE] submission file/directory does not exist: $_sub" >&2; exit 2; }; fi
    _resolved_subs+=("$_sub")
  done
  for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
    [[ -d "${_inputs[$_i]}" ]] || { echo "[BASE] input directory does not exist: ${_inputs[$_i]}" >&2; exit 2; }
  done
  _batch_status=0
  declare -A _label_counts=()
  for _input in "${_inputs[@]}"; do _label="$(basename "${_input%/}")"; _label="${_label//[^A-Za-z0-9._-]/_}"; ((_label_counts[$_label]+=1)); done
  for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
    _input="${_inputs[$_i]}"
    _label="$(basename "${_input%/}")"; _label="${_label//[^A-Za-z0-9._-]/_}"
    [[ -n "$_label" ]] || _label="dataset"
    if (( _label_counts[$_label] > 1 )); then _suffix="$(printf '%s' "$_input" | sha256sum | cut -c1-8)"; _label="${_label}__${_suffix}"; fi
    _submission_payload="$(printf '%s\n' "${_resolved_subs[@]}")"
    echo "[BASE][batch $((_i+1))/${#_inputs[@]}] input=$_input submissions=${#_resolved_subs[@]} output=${OUTPUT_ROOT}/<stage>/$_label"
    if SCIGBLAST_MULTI_CHILD=1 SCIGBLAST_RUN_RAW_INPUT_DIR="$_input" SCIGBLAST_RUN_SUBMISSION_PATHS="$_submission_payload" SCIGBLAST_RUN_SUBMISSION="" SCIGBLAST_RUN_OUTPUT_ROOT="${OUTPUT_ROOT}" SCIGBLAST_RUN_DATASET_LABEL="$_label" SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-1}" bash "$0"; then
      echo "[BASE][batch $((_i+1))] completed"
    else
      _rc=$?; _batch_status=1; echo "[BASE][batch $((_i+1))] failed (exit=$_rc)" >&2
    fi
  done
  exit "$_batch_status"
fi
[[ -n "$RAW_INPUT_DIR" && -d "$RAW_INPUT_DIR" ]] || { echo "[BASE] set RAW_INPUT_DIR or SCIGBLAST_DATA_INPUTS in 00.pipeline_config.env" >&2; exit 2; }

DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" ]]; then
  DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"
fi
[[ -n "$DATASET_LABEL" ]] || DATASET_LABEL="dataset"
export SCIGBLAST_DATASET_LABEL="$DATASET_LABEL"
stage_root() {
  local stage="$1"
  if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi
}

FASTP_ROOT="$(stage_root 02.fastp)"; FASTP_DIR="${FASTP_ROOT}/data"; FASTP_REPORT="${FASTP_ROOT}/report"
CLEAN_DIR="$(stage_root 03.clean_data)"; PANDASEQ_DIR="$(stage_root 04.pandaseq)"; IGBLAST_DIR="$(stage_root 05.igblastn_out)"
SUMMARY="$(stage_root 01.match)/sample_manifest.csv"
export SCIGBLAST_OUTPUT_ROOT="$OUTPUT_ROOT"
export SCIGBLAST_MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"
export SCIGBLAST_FASTP_THREADS="${SCIGBLAST_FASTP_THREADS:-${FASTP_THREADS:-8}}"
export SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES="${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-${FASTP_MAX_PARALLEL_SAMPLES:-4}}"
export SCIGBLAST_FASTP_MEMORY_LIMIT_GB="${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-${SCIGBLAST_FASTP_MEMORY_GB:-${FASTP_MEMORY_GB:-8}}}"
export SCIGBLAST_CLEAN_THREADS="${SCIGBLAST_CLEAN_THREADS:-${CLEAN_THREADS:-4}}"
export SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES="${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-${CLEAN_MAX_PARALLEL_SAMPLES:-4}}"
export SCIGBLAST_CLEAN_MEMORY_LIMIT_GB="${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-${SCIGBLAST_CLEAN_MEMORY_GB:-${CLEAN_MEMORY_GB:-8}}}"
export SCIGBLAST_PANDASEQ_THREADS="${SCIGBLAST_PANDASEQ_THREADS:-${PANDASEQ_THREADS:-16}}"
export SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES="${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-${PANDASEQ_MAX_PARALLEL_SAMPLES:-2}}"
export SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB="${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-${SCIGBLAST_PANDASEQ_MEMORY_GB:-${PANDASEQ_MEMORY_GB:-32}}}"
export SCIGBLAST_PARALLEL_THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-${PARALLEL_THREAD_BUDGET:-128}}"
export SCIGBLAST_MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
export SCIGBLAST_IGBLAST_THREADS="${SCIGBLAST_IGBLAST_THREADS:-8}"
export SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS="${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-2}"
export SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB="${SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB:-96}"
export SCIGBLAST_IGBLAST_BATCH_FASTA="${SCIGBLAST_IGBLAST_BATCH_FASTA:-500}"
export SCIGBLAST_IGBLAST_OUTPUT_DIR="$IGBLAST_DIR"
[[ -n "${SCIGBLAST_IGBLAST_DB_DIR:-}" ]] && export SCIGBLAST_IGBLAST_DB_DIR="$SCIGBLAST_IGBLAST_DB_DIR"
export SCIGBLAST_IGBLAST_SPECIES="${SCIGBLAST_IGBLAST_SPECIES:-human}"
export SCIGBLAST_MIN_FREE_MEMORY_GB="${SCIGBLAST_MIN_FREE_MEMORY_GB:-16}"
export SCIGBLAST_MEMORY_STOP_THRESHOLD_GB="${SCIGBLAST_MEMORY_STOP_THRESHOLD_GB:-150}"
export SCIGBLAST_MEMORY_POLL_SECONDS="${SCIGBLAST_MEMORY_POLL_SECONDS:-5}"
STATE_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}"; mkdir -p "$STATE_DIR"
LOG_FILE="${OUTPUT_ROOT}/logs/${DATASET_LABEL}/pipeline.log"; mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$OUTPUT_ROOT"; exec > >(tee -a "$LOG_FILE") 2>&1

THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET}"
MIN_FREE_MEMORY_GB="${SCIGBLAST_MIN_FREE_MEMORY_GB}"
MEMORY_STOP_THRESHOLD_GB="${SCIGBLAST_MEMORY_STOP_THRESHOLD_GB}"
MEMORY_POLL_SECONDS="${SCIGBLAST_MEMORY_POLL_SECONDS}"
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$MAX_THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$THREAD_BUDGET" -le "$MAX_THREAD_BUDGET" ]] || { echo "[BASE] thread budget must be <=${MAX_THREAD_BUDGET}" >&2; exit 2; }
[[ "${SCIGBLAST_MEMORY_BUDGET_GB}" =~ ^[0-9]+$ && "${SCIGBLAST_MEMORY_BUDGET_GB}" -le 300 ]] || { echo "[BASE] memory budget must be <=300GB" >&2; exit 2; }
[[ "$MIN_FREE_MEMORY_GB" =~ ^[0-9]+$ && "$MEMORY_STOP_THRESHOLD_GB" =~ ^[0-9]+$ && "$MEMORY_POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "[BASE] invalid memory watchdog configuration" >&2; exit 2; }
FASTP_REQUIRED_GB="$(( ${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES} * ${SCIGBLAST_FASTP_MEMORY_LIMIT_GB} ))"
CLEAN_REQUIRED_GB="$(( ${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES} * ${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB} ))"
PANDASEQ_REQUIRED_GB="$(( ${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES} * ${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB} ))"
IGBLAST_REQUIRED_GB="$(( ${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS} * ${SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB} ))"

available_memory_gb() {
  awk '/^MemAvailable:/ {printf "%d", $2 / 1024 / 1024; found=1; exit} END {if (!found) exit 1}' /proc/meminfo 2>/dev/null
}
children_of() {
  if command -v pgrep >/dev/null 2>&1; then pgrep -P "$1" 2>/dev/null || true
  else ps -eo pid=,ppid= | awk -v parent="$1" '$2 == parent {print $1}'; fi
}
collect_process_tree() {
  local root="$1" current child
  local -a queue=("$root")
  local -A seen=()
  while ((${#queue[@]})); do
    current="${queue[0]}"; queue=("${queue[@]:1}")
    [[ "$current" =~ ^[0-9]+$ && -z "${seen[$current]+yes}" ]] || continue
    seen["$current"]=1; printf '%s\n' "$current"
    while read -r child; do [[ "$child" =~ ^[0-9]+$ ]] && queue+=("$child"); done < <(children_of "$current")
  done
}
stop_process_tree() {
  local root="$1" i pid
  local -a pids=()
  mapfile -t pids < <(collect_process_tree "$root")
  for ((i=${#pids[@]}-1; i>=0; i--)); do kill -TERM "${pids[$i]}" 2>/dev/null || true; done
  sleep 2
  for ((i=${#pids[@]}-1; i>=0; i--)); do kill -KILL "${pids[$i]}" 2>/dev/null || true; done
}
run_monitored_stage() {
  local label="$1" requested_gb="$2" event_file stage_pid watchdog_pid stage_status available_gb
  shift 2
  available_gb="$(available_memory_gb 2>/dev/null || true)"
  [[ "$available_gb" =~ ^[0-9]+$ ]] || { echo "[BASE][memory] cannot read MemAvailable; refusing ${label}" >&2; return 75; }
  local start_limit="$(( requested_gb + MIN_FREE_MEMORY_GB ))"
  (( start_limit < MEMORY_STOP_THRESHOLD_GB )) && start_limit="$MEMORY_STOP_THRESHOLD_GB"
  (( available_gb >= start_limit )) || { echo "[BASE][memory] insufficient for ${label}: available=${available_gb}GB start_limit=${start_limit}GB" >&2; return 75; }
  echo "[BASE][memory] ${label}: available=${available_gb}GB required=${requested_gb}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB"
  event_file="${OUTPUT_ROOT}/memory_stop.$$.log"
  rm -f "$event_file"
  "$@" &
  stage_pid=$!
  (
    while kill -0 "$stage_pid" 2>/dev/null; do
      available_gb="$(available_memory_gb 2>/dev/null || true)"
      if ! [[ "$available_gb" =~ ^[0-9]+$ ]] || (( available_gb < MEMORY_STOP_THRESHOLD_GB )); then
        printf 'stage=%s\navailable_gb=%s\nthreshold_gb=%s\ntimestamp=%s\n' "$label" "${available_gb:-unknown}" "$MEMORY_STOP_THRESHOLD_GB" "$(date -Iseconds)" > "$event_file"
        echo "[BASE][memory] STOP ${label}: MemAvailable=${available_gb:-unknown}GB threshold=${MEMORY_STOP_THRESHOLD_GB}GB" >&2
        stop_process_tree "$stage_pid"
        exit 75
      fi
      sleep "$MEMORY_POLL_SECONDS"
    done
  ) & watchdog_pid=$!
  if wait "$stage_pid"; then stage_status=0; else stage_status=$?; fi
  kill "$watchdog_pid" 2>/dev/null || true; wait "$watchdog_pid" 2>/dev/null || true
  if [[ -s "$event_file" ]]; then echo "[BASE][memory] ${label} stopped; details=${event_file}" >&2; return 75; fi
  rm -f "$event_file"
  return "$stage_status"
}

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else return 1; fi
}
CONFIG_SHA256="$(hash_file "$CONFIG_FILE" 2>/dev/null || printf unknown)"
MAPPING_SHA256="unknown"
stage_script(){ case "$1" in 02.fastp) printf '%s/02.run_fastp.sh\n' "$SCRIPT_DIR";; 03.clean) printf '%s/03.clean_header.sh\n' "$SCRIPT_DIR";; 04.pandaseq) printf '%s/04.work_pandaseq.sh\n' "$SCRIPT_DIR";; 05.igblast) printf '%s/05.work_igblastn.sh\n' "$SCRIPT_DIR";; esac; }
stage_fingerprint(){ local script; script="$(stage_script "$1")"; { hash_file "$script"; hash_file "${SCRIPT_DIR}/tools/pipeline_config.py"; } | sha256sum | awk '{print $1}'; }
stage_done() {
  local stage="$1" marker="${STATE_DIR}/.pipeline_stage_${1}.DONE"
  [[ "${SCIGBLAST_FORCE_RERUN:-0}" != "1" && -s "$marker" ]] || return 1
  grep -qx 'status=DONE' "$marker" || return 1
  grep -qx "config_sha256=${CONFIG_SHA256}" "$marker" || return 1
  # A corrected match row must invalidate every downstream stage.  Without
  # this check, a previous all-good marker could cause fastp/clean/PANDAseq/
  # IgBLAST to be skipped even though sample_manifest.csv now contains newly
  # usable samples.
  grep -qx "mapping_sha256=${MAPPING_SHA256}" "$marker" || return 1
  grep -qx "stage_sha256=$(stage_fingerprint "$stage")" "$marker" || return 1
  case "$stage" in
    01.match) [[ -s "$SUMMARY" ]] || return 1 ;;
    02.fastp) grep -qx 'layout_version=baseline_pair_sample_v2' "$marker" || return 1; [[ -d "$FASTP_DIR" && -d "$FASTP_REPORT" ]] || return 1; [[ -n "$(find "$FASTP_DIR" -type f -name '*.fq.gz' -print -quit 2>/dev/null)" ]] || return 1 ;;
    03.clean) [[ -n "$(find "$CLEAN_DIR" -type f -name '*.fq.gz' -print -quit 2>/dev/null)" ]] || return 1 ;;
    04.pandaseq) [[ -n "$(find "$PANDASEQ_DIR" -type f \( -name '*.fa' -o -name '*.fasta' \) -print -quit 2>/dev/null)" ]] || return 1 ;;
    05.igblast) [[ -s "$IGBLAST_DIR/chain_summary.csv" || -s "$IGBLAST_DIR/igblastn_run_summary.tsv" ]] || return 1 ;;
    *) return 1 ;;
  esac
}
mark_stage() {
  local stage="$1" marker="${STATE_DIR}/.pipeline_stage_${1}.DONE"
  local marker_tmp="${marker}.tmp.${BASHPID:-$$}"
  {
    printf 'status=DONE\n'; printf 'stage=%s\n' "$stage";
    printf 'layout_version=baseline_pair_sample_v2\n';
    printf 'config_sha256=%s\n' "$CONFIG_SHA256";
    printf 'mapping_sha256=%s\n' "$MAPPING_SHA256";
    printf 'stage_sha256=%s\n' "$(stage_fingerprint "$stage")";
    printf 'timestamp=%s\n' "$(date -Iseconds)";
  } > "$marker_tmp"
  mv -f "$marker_tmp" "$marker"
}

echo "[BASE 1/5] match FASTQ pairs"
MATCH_STATUS=0
SCIGBLAST_BASE_RAW_INPUT="$RAW_INPUT_DIR" \
SCIGBLAST_BASE_OUTPUT_ROOT="$OUTPUT_ROOT" \
SCIGBLAST_BASE_SUBMISSION="${SUBMISSION_PATH:-}" \
SCIGBLAST_SUBMISSION_PATHS="${SCIGBLAST_SUBMISSION_PATHS:-}" \
SCIGBLAST_MATCH_INPUT="$RAW_INPUT_DIR" \
SCIGBLAST_MATCH_OUTPUT="$SUMMARY" \
SCIGBLAST_MATCH_SUBMISSION="${SUBMISSION_PATH:-}" \
SCIGBLAST_MATCH_NO_BARCODE="${SCIGBLAST_MATCH_NO_BARCODE:-1}" \
SCIGBLAST_BASE_DEFAULT_CHAINS="${SCIGBLAST_BASE_DEFAULT_CHAINS:-}" \
"${PYTHON_BIN}" "${SCRIPT_DIR}/01.match_sample.py" || MATCH_STATUS=$?
[[ -s "$SUMMARY" ]] || { echo "[BASE] match did not produce summary" >&2; exit "${MATCH_STATUS:-1}"; }
MAPPING_SHA256="$(hash_file "$SUMMARY" 2>/dev/null || printf unknown)"
if [[ "${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-${MATCH_ONLY_FIRST_RUN:-1}}" == "1" && ! -f "${STATE_DIR}/.match_review.done" ]]; then
  match_review_marker="${STATE_DIR}/.match_review.done"
  match_review_tmp="${match_review_marker}.tmp.${BASHPID:-$$}"
  printf 'status=READY_FOR_REVIEW\ntimestamp=%s\n' "$(date -Iseconds)" > "$match_review_tmp"
  mv -f "$match_review_tmp" "$match_review_marker"
  echo "[BASE] matching finished; review ${SUMMARY}, then rerun"
  exit "$MATCH_STATUS"
fi
(( MATCH_STATUS == 0 || "${SCIGBLAST_ALLOW_MAPPING_ERRORS:-${ALLOW_MAPPING_ERRORS:-1}}" == "1" )) || exit "$MATCH_STATUS"

echo "[BASE 2/5] fastp"
FASTP_RERAN=0
if ! stage_done 02.fastp; then
  run_monitored_stage "fastp" "$FASTP_REQUIRED_GB" bash "${SCRIPT_DIR}/02.run_fastp.sh"
  mark_stage 02.fastp
  FASTP_RERAN=1
else echo "[BASE] reusing fastp"; fi

if (( FASTP_RERAN )); then
  rm -f "${STATE_DIR}/.pipeline_stage_03.clean.DONE" \
        "${STATE_DIR}/.pipeline_stage_04.pandaseq.DONE" \
        "${STATE_DIR}/.pipeline_stage_05.igblast.DONE"
fi

echo "[BASE 3/5] clean header"
CLEAN_RERAN=0
if ! stage_done 03.clean; then
  run_monitored_stage "clean header" "$CLEAN_REQUIRED_GB" bash "${SCRIPT_DIR}/03.clean_header.sh"
  mark_stage 03.clean
  CLEAN_RERAN=1
else echo "[BASE] reusing clean"; fi

if (( CLEAN_RERAN )); then
  rm -f "${STATE_DIR}/.pipeline_stage_04.pandaseq.DONE" \
        "${STATE_DIR}/.pipeline_stage_05.igblast.DONE"
fi

echo "[BASE 4/5] PANDAseq"
PANDASEQ_RERAN=0
if ! stage_done 04.pandaseq; then
  run_monitored_stage "PANDAseq" "$PANDASEQ_REQUIRED_GB" bash "${SCRIPT_DIR}/04.work_pandaseq.sh"
  mark_stage 04.pandaseq
  PANDASEQ_RERAN=1
else echo "[BASE] reusing PANDAseq"; fi

if (( PANDASEQ_RERAN )); then
  rm -f "${STATE_DIR}/.pipeline_stage_05.igblast.DONE"
fi

echo "[BASE 5/5] IgBLAST"
if ! stage_done 05.igblast; then
  run_monitored_stage "IgBLAST" "$IGBLAST_REQUIRED_GB" bash "${SCRIPT_DIR}/05.work_igblastn.sh" --data-dir "$PANDASEQ_DIR" --output-dir "$IGBLAST_DIR" \
    --input-mode pandaseq --db-dir "${SCIGBLAST_IGBLAST_DB_DIR:-/data/scAnalyis/Scigblast/igblast}" \
    --species "${SCIGBLAST_IGBLAST_SPECIES:-human}" \
    --threads "${SCIGBLAST_IGBLAST_THREADS:-8}" \
    --parallel "${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-2}"
  mark_stage 05.igblast
else echo "[BASE] reusing IgBLAST"; fi
echo "[BASE] completed: ${IGBLAST_DIR}"
