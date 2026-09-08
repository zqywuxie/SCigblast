#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PIPELINE_DIR="$SCRIPT_DIR"
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
RUN_RAW_INPUT_OVERRIDE="${SCIGBLAST_RUN_RAW_INPUT_DIR:-}"
RUN_SUBMISSION_OVERRIDE="${SCIGBLAST_RUN_SUBMISSION_XLSX:-}"
RUN_SUBMISSION_PATHS_OVERRIDE="${SCIGBLAST_RUN_SUBMISSION_PATHS:-}"
RUN_BARCODE_OVERRIDE="${SCIGBLAST_RUN_BARCODE_CSV:-}"
RUN_OUTPUT_OVERRIDE="${SCIGBLAST_RUN_OUTPUT_ROOT:-${SCIGBLAST_OUTPUT_ROOT:-}}"
RUN_DATASET_LABEL_OVERRIDE="${SCIGBLAST_RUN_DATASET_LABEL:-${SCIGBLAST_DATASET_LABEL:-}}"
RUN_MATCH_ONLY_OVERRIDE="${SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN:-${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-${MATCH_ONLY_FIRST_RUN:-}}}"
source "${SCRIPT_DIR}/00.pipeline_config.env" 2>/dev/null || true
PYTHON_BIN="${PYTHON_BIN:-python3}"
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
if [[ -n "$RUN_OUTPUT_OVERRIDE" ]]; then
  OUTPUT_ROOT="$(resolve_output_root "$RUN_OUTPUT_OVERRIDE")"
  SCIGBLAST_OUTPUT_ROOT="$OUTPUT_ROOT"
fi
export SCIGBLAST_OUTPUT_ROOT
if [[ "${SCIGBLAST_MULTI_CHILD:-0}" == "1" ]]; then
  SCIGBLAST_DATA_INPUTS=(); SCIGBLAST_SUBMISSIONS=(); SCIGBLAST_BARCODE_CSVS=()
fi
[[ -n "$RUN_RAW_INPUT_OVERRIDE" ]] && RAW_INPUT_DIR="$RUN_RAW_INPUT_OVERRIDE"
if [[ -n "$RUN_SUBMISSION_PATHS_OVERRIDE" ]]; then
  SCIGBLAST_SUBMISSION_PATHS="$RUN_SUBMISSION_PATHS_OVERRIDE"
  SUBMISSION_XLSX=""
elif [[ -n "$RUN_SUBMISSION_OVERRIDE" ]]; then
  SUBMISSION_XLSX="$RUN_SUBMISSION_OVERRIDE"
fi
[[ -n "$RUN_BARCODE_OVERRIDE" ]] && BARCODE_CSV="$RUN_BARCODE_OVERRIDE"
[[ -n "$RUN_MATCH_ONLY_OVERRIDE" ]] && SCIGBLAST_MATCH_ONLY_FIRST_RUN="$RUN_MATCH_ONLY_OVERRIDE"
[[ -n "$RUN_DATASET_LABEL_OVERRIDE" ]] && SCIGBLAST_DATASET_LABEL="$RUN_DATASET_LABEL_OVERRIDE"
SCIGBLAST_SUBMISSION_PATHS="${SCIGBLAST_SUBMISSION_PATHS:-}"
export RAW_INPUT_DIR SUBMISSION_XLSX SCIGBLAST_SUBMISSION_PATHS BARCODE_CSV

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

# Batch mode: configure Bash arrays in 00.pipeline_config.env.
_inputs=(); _subs=(); _barcodes=()
[[ "$(declare -p SCIGBLAST_DATA_INPUTS 2>/dev/null || true)" == "declare -a"* ]] && _inputs=("${SCIGBLAST_DATA_INPUTS[@]}")
[[ "$(declare -p SCIGBLAST_SUBMISSIONS 2>/dev/null || true)" == "declare -a"* ]] && _subs=("${SCIGBLAST_SUBMISSIONS[@]}")
[[ "$(declare -p SCIGBLAST_BARCODE_CSVS 2>/dev/null || true)" == "declare -a"* ]] && _barcodes=("${SCIGBLAST_BARCODE_CSVS[@]}")
if (( ! ${#_inputs[@]} && ${#_subs[@]} )); then echo "[PIG] SCIGBLAST_SUBMISSIONS was set but SCIGBLAST_DATA_INPUTS is empty" >&2; exit 2; fi
if (( ${#_inputs[@]} )); then
  if (( ${#_subs[@]} == 0 )) && [[ -n "${SUBMISSION_XLSX:-}" ]]; then _subs=("${SUBMISSION_XLSX}"); fi
  if (( ${#_subs[@]} == 0 )); then echo "[PIG] at least one submission workbook/directory is required" >&2; exit 2; fi
  _resolved_subs=()
  for _sub in "${_subs[@]}"; do _resolved_subs+=("$(resolve_submission "$_sub" "" PIG)"); done
  if (( ${#_barcodes[@]} )); then
    if (( ${#_barcodes[@]} == 1 && ${#_inputs[@]} > 1 )); then _v="${_barcodes[0]}"; _barcodes=(); for _x in "${_inputs[@]}"; do _barcodes+=("$_v"); done
    elif (( ${#_barcodes[@]} != ${#_inputs[@]} )); then echo "[PIG] SCIGBLAST_BARCODE_CSVS must have one entry per dataset (or one shared entry)" >&2; exit 2; fi
  else
    _barcodes=()
  fi
  for (( _i=0; _i<${#_inputs[@]}; _i++ )); do
    [[ -d "${_inputs[$_i]}" ]] || { echo "[PIG] input directory does not exist: ${_inputs[$_i]}" >&2; exit 2; }
    if (( ${#_barcodes[@]} )); then [[ -f "${_barcodes[$_i]}" ]] || { echo "[PIG] barcode CSV does not exist: ${_barcodes[$_i]}" >&2; exit 2; }; fi
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
    echo "[PIG][batch $((_i+1))/${#_inputs[@]}] input=$_input submissions=${#_resolved_subs[@]} output=${OUTPUT_ROOT}/<stage>/$_label"
    _submission_payload="$(printf '%s\n' "${_resolved_subs[@]}")"
    if SCIGBLAST_MULTI_CHILD=1 SCIGBLAST_RUN_RAW_INPUT_DIR="$_input" SCIGBLAST_RUN_SUBMISSION_PATHS="$_submission_payload" SCIGBLAST_RUN_SUBMISSION_XLSX="" SCIGBLAST_RUN_BARCODE_CSV="$_bc" SCIGBLAST_RUN_OUTPUT_ROOT="${OUTPUT_ROOT}" SCIGBLAST_RUN_DATASET_LABEL="$_label" SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-1}" bash "$0"; then
      echo "[PIG][batch $((_i+1))] completed"
    else
      _rc=$?; _batch_status=1; echo "[PIG][batch $((_i+1))] failed (exit=$_rc)" >&2
    fi
  done
  exit "$_batch_status"
fi
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "${RAW_INPUT_DIR:-}" ]]; then
  DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"
fi
[[ -n "$DATASET_LABEL" ]] || DATASET_LABEL="dataset"
export SCIGBLAST_DATASET_LABEL="$DATASET_LABEL"
OUTPUT_ROOT="$(resolve_output_root "${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-}}")"; export OUTPUT_ROOT; mkdir -p "$OUTPUT_ROOT"
if [[ -n "${SUBMISSION_XLSX:-}" && -d "${SUBMISSION_XLSX}" ]]; then
  SUBMISSION_XLSX="$(resolve_submission "$SUBMISSION_XLSX" "${RAW_INPUT_DIR:-}" PIG)"
  export SUBMISSION_XLSX
fi
PID_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}/pids"; mkdir -p "$PID_DIR"
PID_FILE="${PID_DIR}/$$.pid"; LOG_FILE="${OUTPUT_ROOT}/logs/${DATASET_LABEL}/pipeline.log"; mkdir -p "$(dirname "$LOG_FILE")"
MATCH_ONLY_FIRST_RUN="${SCIGBLAST_MATCH_ONLY_FIRST_RUN:-${MATCH_ONLY_FIRST_RUN:-1}}"
MATCH_REVIEW_MARKER="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}/.match_review.done"
exec > >(tee -a "$LOG_FILE") 2>&1
TOTAL_MEMORY_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-${TOTAL_MEMORY_GB:-300}}"
PARALLEL_THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-${PARALLEL_THREAD_BUDGET:-128}}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
MIN_FREE_MEMORY_GB="${SCIGBLAST_MIN_FREE_MEMORY_GB:-${MIN_FREE_MEMORY_GB:-16}}"
MEMORY_STOP_THRESHOLD_GB="${SCIGBLAST_MEMORY_STOP_THRESHOLD_GB:-${MEMORY_STOP_THRESHOLD_GB:-150}}"
MEMORY_POLL_SECONDS="${SCIGBLAST_MEMORY_POLL_SECONDS:-${MEMORY_POLL_SECONDS:-5}}"
[[ "$TOTAL_MEMORY_GB" =~ ^[0-9]+$ && "$TOTAL_MEMORY_GB" -le 300 ]] || { echo "[PIG] ERROR memory budget must be <=300GB" >&2; exit 2; }
[[ "$PARALLEL_THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$MAX_THREAD_BUDGET" =~ ^[1-9][0-9]*$ && "$PARALLEL_THREAD_BUDGET" -le "$MAX_THREAD_BUDGET" ]] || { echo "[PIG] ERROR thread budget must be <=${MAX_THREAD_BUDGET}" >&2; exit 2; }
[[ "$MIN_FREE_MEMORY_GB" =~ ^[0-9]+$ ]] || { echo "[PIG] ERROR MIN_FREE_MEMORY_GB must be a non-negative integer" >&2; exit 2; }
[[ "$MEMORY_STOP_THRESHOLD_GB" =~ ^[0-9]+$ ]] || { echo "[PIG] ERROR MEMORY_STOP_THRESHOLD_GB must be a non-negative integer" >&2; exit 2; }
[[ "$MEMORY_POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || { echo "[PIG] ERROR MEMORY_POLL_SECONDS must be a positive integer" >&2; exit 2; }
available_memory_gb(){
  awk '/^MemAvailable:/ {printf "%d", $2 / 1024 / 1024; found=1; exit} END {if (!found) exit 1}' /proc/meminfo 2>/dev/null
}
memory_guard(){
  local label="$1" requested_gb="$2" available_gb
  available_gb="$(available_memory_gb 2>/dev/null || true)"
  [[ "$available_gb" =~ ^[0-9]+$ ]] || {
    echo "[PIG] cannot read server available memory; refusing to start ${label}" >&2
    return 1
  }
  local start_limit="$(( requested_gb + MIN_FREE_MEMORY_GB ))"
  (( start_limit < MEMORY_STOP_THRESHOLD_GB )) && start_limit="$MEMORY_STOP_THRESHOLD_GB"
  if (( available_gb < start_limit )); then
    echo "[PIG] insufficient available memory for ${label}: available=${available_gb}GB start_limit=${start_limit}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB" >&2
    return 1
  fi
  echo "[PIG] memory check ${label}: available=${available_gb}GB required=${requested_gb}GB reserve=${MIN_FREE_MEMORY_GB}GB stop_threshold=${MEMORY_STOP_THRESHOLD_GB}GB"
}
FASTP_REQUIRED_GB="$(( ${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-${MAX_JOBS:-2}} * ${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-${FASTP_MEMORY_GB:-8}} ))"
CLEAN_REQUIRED_GB="$(( ${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-${CLEAN_MAX_PARALLEL_SAMPLES:-2}} * ${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-${CLEAN_MEMORY_GB:-8}} ))"
PANDASEQ_REQUIRED_GB="$(( ${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-${PANDASEQ_MAX_PARALLEL_SAMPLES:-2}} * ${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-${PANDASEQ_MEMORY_GB:-64}} ))"
IGBLAST_REQUIRED_GB="$(( ${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-${MAX_JOBS:-2}} * ${SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB:-${IGBLAST_MEMORY_GB:-48}} ))"
children_of(){
  if command -v pgrep >/dev/null 2>&1; then pgrep -P "$1" 2>/dev/null || true
  else ps -eo pid=,ppid= | awk -v parent="$1" '$2 == parent {print $1}'; fi
}
collect_process_tree(){
  local root="$1" current child
  local -a queue=("$root")
  local -A seen=()
  while ((${#queue[@]})); do
    current="${queue[0]}"; queue=("${queue[@]:1}")
    [[ "$current" =~ ^[0-9]+$ ]] || continue
    [[ -n "${seen[$current]+yes}" ]] && continue
    seen["$current"]=1; printf '%s\n' "$current"
    while read -r child; do [[ "$child" =~ ^[0-9]+$ ]] && queue+=("$child"); done < <(children_of "$current")
  done
}
stop_process_tree(){
  local root="$1" i pid
  local -a pids=()
  mapfile -t pids < <(collect_process_tree "$root")
  for ((i=${#pids[@]}-1; i>=0; i--)); do pid="${pids[$i]}"; kill -TERM "$pid" 2>/dev/null || true; done
  sleep 2
  for ((i=${#pids[@]}-1; i>=0; i--)); do pid="${pids[$i]}"; kill -KILL "$pid" 2>/dev/null || true; done
}
run_monitored_stage(){
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
        echo "[PIG][memory] STOP stage=${label}: cannot read MemAvailable; terminating process tree PID=${stage_pid}" >&2
        stop_process_tree "$stage_pid"
        exit 75
      fi
      if (( available_gb < MEMORY_STOP_THRESHOLD_GB )); then
        printf 'stage=%s\navailable_gb=%s\nthreshold_gb=%s\ntimestamp=%s\n' "$label" "$available_gb" "$MEMORY_STOP_THRESHOLD_GB" "$(date -Iseconds)" > "$event_file"
        echo "[PIG][memory] STOP stage=${label}: MemAvailable=${available_gb}GB below threshold=${MEMORY_STOP_THRESHOLD_GB}GB; terminating process tree PID=${stage_pid}" >&2
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
    echo "[PIG][memory] ${label} stopped by memory watchdog; details: ${event_file}" >&2
    return 75
  fi
  rm -f "$event_file"
  return "$stage_status"
}
preflight_db(){
  local root="${DB_ROOT:-}" version="${PIG_DB_VERSION:-pig_new}" base aux chain part
  if [[ "$IGBLAST_BIN" == */* || "$IGBLAST_BIN" == *\\* ]]; then
    [[ -x "$IGBLAST_BIN" ]] || { echo "[PIG] ERROR IGBLAST_BIN is not executable: $IGBLAST_BIN" >&2; exit 2; }
  else
    command -v "$IGBLAST_BIN" >/dev/null 2>&1 || { echo "[PIG] ERROR IGBLAST_BIN not found: $IGBLAST_BIN" >&2; exit 2; }
  fi
  [[ -n "$root" && -d "$root" ]] || { echo "[PIG] ERROR DB_ROOT does not exist: $root" >&2; exit 2; }
  base="${root}/database_251117/${version}"
  [[ -d "$base" ]] || { echo "[PIG] ERROR pig database directory does not exist: $base" >&2; exit 2; }
  aux="${root}/optional_file/pig.aux"
  [[ -s "$aux" ]] || { echo "[PIG] ERROR missing/empty pig auxiliary file: $aux" >&2; exit 2; }
  for chain in TRA TRB; do
    for part in V J; do
      [[ -e "${base}/${chain}/pig_gl_${chain}_${part}.nhr" || -e "${base}/${chain}/pig_gl_${chain}_${part}.nin" ]] || {
        echo "[PIG] ERROR missing pig ${chain} ${part} BLAST prefix under ${base}/${chain}" >&2; exit 2;
      }
    done
    if [[ "$chain" == "TRB" ]]; then
      [[ -e "${base}/${chain}/pig_gl_${chain}_D.nhr" || -e "${base}/${chain}/pig_gl_${chain}_D.nin" ]] || {
        echo "[PIG] ERROR missing pig TRB D BLAST prefix under ${base}/${chain}" >&2; exit 2;
      }
    fi
  done
  echo "[PIG] database preflight OK: root=${root} version=${version}"
}
echo "$$" > "$PID_FILE"; trap 'rm -f "$PID_FILE"' EXIT
terminate_children(){
  # `jobs -pr` only lists background jobs.  The pipeline runs each stage in
  # the foreground, so enumerate the complete descendant tree instead.
  children_of(){
    if command -v pgrep >/dev/null 2>&1; then
      pgrep -P "$1" 2>/dev/null || true
    else
      ps -eo pid=,ppid= | awk -v parent="$1" '$2 == parent {print $1}'
    fi
  }
  local current child p i
  local -a queue=("$$") pids=()
  local -A seen=()
  while ((${#queue[@]})); do
    current="${queue[0]}"; queue=("${queue[@]:1}")
    [[ "$current" =~ ^[0-9]+$ ]] || continue
    [[ -n "${seen[$current]+yes}" ]] && continue
    seen["$current"]=1; pids+=("$current")
    while read -r child; do [[ "$child" =~ ^[0-9]+$ ]] && queue+=("$child"); done < <(children_of "$current")
  done
  for ((i=${#pids[@]}-1; i>=0; i--)); do
    p="${pids[$i]}"; [[ "$p" == "$$" ]] || kill -TERM "$p" 2>/dev/null || true
  done
  sleep 2
  for ((i=${#pids[@]}-1; i>=0; i--)); do
    p="${pids[$i]}"; [[ "$p" == "$$" ]] || kill -KILL "$p" 2>/dev/null || true
  done
  exit 143
}
trap terminate_children INT TERM
export IGDATA="${DB_ROOT}"
export BLASTDB="${DB_ROOT}/database_251117/${PIG_DB_VERSION}"
STATE_DIR="${OUTPUT_ROOT}/.pipeline_state/${DATASET_LABEL}"
stage_root(){ printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$1" "$DATASET_LABEL"; }
hash_file(){ if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'; else printf unknown; fi; }
CONFIG_SHA256="$(hash_file "${SCRIPT_DIR}/00.pipeline_config.env")"
stage_script(){ case "$1" in fastp) printf '%s/02.run_fastp.sh\n' "$SCRIPT_DIR";; clean) printf '%s/03.clean_header.py\n' "$SCRIPT_DIR";; pandaseq) printf '%s/04.work_pandaseq.sh\n' "$SCRIPT_DIR";; igblast) printf '%s/05.work_igblastn.py\n' "$SCRIPT_DIR";; esac; }
stage_fingerprint(){ local script; script="$(stage_script "$1")"; { hash_file "$script"; hash_file "${SCRIPT_DIR}/tools/pipeline_config.py"; } | sha256sum | awk '{print $1}'; }
stage_output_ready(){ case "$1" in
  fastp) [[ -d "$(stage_root 02.fastp)/data" && -d "$(stage_root 02.fastp)/report" ]] && [[ -n "$(find "$(stage_root 02.fastp)/data" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]];;
  clean) [[ -n "$(find "$(stage_root 03.clean_data)" -type f \( -name '*.fq.gz' -o -name '*.fastq.gz' \) -print -quit 2>/dev/null)" ]];;
  pandaseq) [[ -n "$(find "$(stage_root 04.pandaseq)" -type f \( -name '*.fa' -o -name '*.fasta' \) -print -quit 2>/dev/null)" ]];;
  igblast) [[ -s "$(stage_root 05.igblastn_out)/chain_summary.csv" || -s "$(stage_root 05.igblastn_out)/igblastn_run_summary.tsv" ]];;
  *) return 1;; esac; }
MAPPING_SHA256="unknown"
stage_done(){ local label="$1" marker="${STATE_DIR}/.pipeline_stage_${label}.DONE"; [[ "${SCIGBLAST_FORCE_RERUN:-0}" != 1 && -s "$marker" ]] || return 1; grep -qx 'status=DONE' "$marker" || return 1; grep -qx "config_sha256=${CONFIG_SHA256}" "$marker" || return 1; grep -qx "mapping_sha256=${MAPPING_SHA256}" "$marker" || return 1; grep -qx "stage_sha256=$(stage_fingerprint "$label")" "$marker" || return 1; stage_output_ready "$label"; }
mark_stage(){ local label="$1" marker="${STATE_DIR}/.pipeline_stage_${label}.DONE" tmp="${marker}.tmp.${BASHPID:-$$}"; { printf 'status=DONE\nstage=%s\nconfig_sha256=%s\nmapping_sha256=%s\nstage_sha256=%s\ntimestamp=%s\n' "$label" "$CONFIG_SHA256" "$MAPPING_SHA256" "$(stage_fingerprint "$label")" "$(date -Iseconds)"; } > "$tmp"; mv -f "$tmp" "$marker"; }
run_stage(){ local label="$1" required_gb="$2"; shift 2; if stage_done "$label"; then echo "[PIG] reusing completed ${label} stage"; return 0; fi; echo "[PIG] ${label} (sample-level resume enabled)"; run_monitored_stage "$label" "$required_gb" "$@" || return $?; stage_output_ready "$label" || { echo "[PIG] ${label} completed but required output is missing" >&2; return 1; }; mark_stage "$label"; }
# Matching is cheap and must always be rerun so corrected submission rows are
# visible on the next invocation.  Every downstream stage skips only complete
# sample-level outputs, never an entire stage based on a stale marker.
MATCH_STATUS=0
echo "[PIG] match (sample-level resume enabled)"
SCIGBLAST_MATCH_INPUT="${RAW_INPUT_DIR}" \
SCIGBLAST_MATCH_SUBMISSION="${SUBMISSION_XLSX:-}" \
SCIGBLAST_SUBMISSION_PATHS="${SCIGBLAST_SUBMISSION_PATHS:-}" \
SCIGBLAST_MATCH_OUTPUT="${OUTPUT_ROOT}/01.match/${DATASET_LABEL}/sample_manifest.csv" \
"${PYTHON_BIN}" "${SCRIPT_DIR}/01.match_sample.py" || MATCH_STATUS=$?
MAPPING_SHA256="$(hash_file "${OUTPUT_ROOT}/01.match/${DATASET_LABEL}/sample_manifest.csv" 2>/dev/null || printf unknown)"
if [[ "${SCIGBLAST_WEB_MATCH_ONLY:-0}" == "1" || ( "$MATCH_ONLY_FIRST_RUN" == "1" && ! -f "$MATCH_REVIEW_MARKER" ) ]]; then
  marker_tmp="${MATCH_REVIEW_MARKER}.tmp.${BASHPID:-$$}"
  {
    printf 'status=READY_FOR_REVIEW\n'
    printf 'timestamp=%s\n' "$(date -Iseconds)"
  } > "$marker_tmp"
  mv -f "$marker_tmp" "$MATCH_REVIEW_MARKER"
  echo "[PIG] first invocation completed matching only. Review: ${OUTPUT_ROOT}/01.match/${DATASET_LABEL}/sample_manifest.csv"
  echo "[PIG] run this pipeline again to continue with fastp and downstream stages."
  exit "$MATCH_STATUS"
fi
if (( MATCH_STATUS != 0 )); then
  OK_MATCHES="$(awk -F, 'NR>1 && toupper($0) ~ /,OK,/ {n++} END{print n+0}' "${OUTPUT_ROOT}/01.match/${DATASET_LABEL}/sample_manifest.csv" 2>/dev/null || true)"
  if [[ ! "$OK_MATCHES" =~ ^[1-9][0-9]*$ ]]; then
    echo "[PIG] match produced no usable samples; downstream stages will not start" >&2
    exit "$MATCH_STATUS"
  fi
  echo "[PIG] match has errors for some samples; continuing with ${OK_MATCHES} usable samples" >&2
fi
# Database validation is deliberately after the first match-only invocation.
# This lets the user review/fix sample mappings without requiring a working
# Pig IgBLAST installation, while still failing before any heavy stage starts.
preflight_db
if ! stage_done fastp; then
  run_stage fastp "$FASTP_REQUIRED_GB" bash "${SCRIPT_DIR}/02.run_fastp.sh"
  rm -f "${STATE_DIR}/.pipeline_stage_clean.DONE" "${STATE_DIR}/.pipeline_stage_pandaseq.DONE" "${STATE_DIR}/.pipeline_stage_igblast.DONE"
else echo "[PIG] reusing completed fastp stage"; fi
if ! stage_done clean; then
  run_stage clean "$CLEAN_REQUIRED_GB" "${PYTHON_BIN}" "${SCRIPT_DIR}/03.clean_header.py"
  rm -f "${STATE_DIR}/.pipeline_stage_pandaseq.DONE" "${STATE_DIR}/.pipeline_stage_igblast.DONE"
else echo "[PIG] reusing completed clean stage"; fi
if ! stage_done pandaseq; then
  run_stage pandaseq "$PANDASEQ_REQUIRED_GB" bash "${SCRIPT_DIR}/04.work_pandaseq.sh"
  rm -f "${STATE_DIR}/.pipeline_stage_igblast.DONE"
else echo "[PIG] reusing completed pandaseq stage"; fi
run_stage igblast "$IGBLAST_REQUIRED_GB" "${PYTHON_BIN}" "${SCRIPT_DIR}/05.work_igblastn.py"
echo "[PIG] pipeline completed"
