#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
RUNTIME_RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
PIPELINE_DIR="$SCRIPT_DIR"; source "${SCRIPT_DIR}/00.pipeline_config.env" 2>/dev/null || true
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
[[ -n "$RUNTIME_RAW_INPUT_DIR" ]] && RAW_INPUT_DIR="$RUNTIME_RAW_INPUT_DIR"
BIN="${SCIGBLAST_PANDASEQ_BIN:-${PANDASEQ_BIN:-pandaseq}}"; THREADS="${SCIGBLAST_PANDASEQ_THREADS:-${PANDASEQ_THREADS:-8}}"
MAX_PARALLEL="${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-${PANDASEQ_MAX_PARALLEL_SAMPLES:-2}}"; THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-${PARALLEL_THREAD_BUDGET:-128}}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-${TOTAL_MEMORY_GB:-300}}"; MEMORY_PER_SAMPLE_GB="${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-${PANDASEQ_MEMORY_GB:-16}}"
PANDASEQ_VERSION="3"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-${SCRIPT_DIR}/../output}}"; RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "$RAW_INPUT_DIR" ]]; then DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"; fi
stage_root(){ local stage="$1"; if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi; }
STAGE_MATCH_ROOT="$(stage_root 01.match)"; MANIFEST="${STAGE_MATCH_ROOT}/sample_manifest.csv"; IN="$(stage_root 03.clean_data)"; OUT="$(stage_root 04.pandaseq)"; SUMMARY="${OUT}/pandaseq_summary.csv"
safe(){ printf '%s' "$1" | tr -cs 'A-Za-z0-9._-' '_'; }
pair_fingerprint(){
  local r1="$1" r2="$2"
  local payload
  payload="${r1}|${r2}|$(stat -c '%s:%Y' "$r1")|$(stat -c '%s:%Y' "$r2")|${THREADS}|pandaseq_R1_R2_v1"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$payload" | sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "$payload" | shasum -a 256 | awk '{print $1}'
  else
    printf '%s' "$payload"
  fi
}
log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
[[ -f "$MANIFEST" ]] || { log 'ERROR missing manifest'; exit 1; }
command -v "$BIN" >/dev/null 2>&1 || { log "ERROR pandaseq not found: $BIN"; exit 1; }
[[ "$MAX_PARALLEL" =~ ^[1-9][0-9]*$ ]] || { log "ERROR invalid pandaseq parallel sample count"; exit 1; }
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]] || { log "ERROR invalid parallel thread budget"; exit 1; }
(( MAX_PARALLEL * THREADS <= THREAD_BUDGET )) || { log "ERROR pandaseq concurrency exceeds thread budget"; exit 1; }
(( MAX_PARALLEL * MEMORY_PER_SAMPLE_GB <= MEMORY_BUDGET_GB )) || { log "ERROR pandaseq concurrency exceeds memory budget"; exit 1; }
mkdir -p "$OUT"; printf 'sample_id,pair_id,input_r1,input_r2,input_pairs,merged_sequences,merge_percent,status,error\n' > "$SUMMARY"
mapfile -t JOBS < <(python3 - "$MANIFEST" <<'PY'
import csv,sys
seen=set()
for row in csv.DictReader(open(sys.argv[1],encoding='utf-8')):
 k=(row['sample_id'],row['pair_id'],row.get('r1_path',''))
 if row.get('status')=='OK' and k not in seen: seen.add(k); print('\t'.join((row['sample_id'],row['pair_id'],row.get('r1_path',''))))
PY
)
total=${#JOBS[@]}; ok=0; fail=0; completed=0; declare -a PIDS=(); declare -A PID_LABEL=(); declare -A PID_INDEX=()
log "[PIG][pandaseq] stage_start total=${total} max_parallel=${MAX_PARALLEL} threads=${THREADS}"

run_pandaseq_one() {
  local sample="$1" pair="$2" raw_r1="$3" idx="$4"
  ss=$(safe "$sample"); pp=$(safe "$pair"); raw_parent=""; if [[ -n "$RAW_INPUT_DIR" && "$raw_r1" == "${RAW_INPUT_DIR%/}/"* ]]; then raw_rel="${raw_r1#${RAW_INPUT_DIR%/}/}"; raw_parent="${raw_rel%/*}"; [[ "$raw_parent" == "$raw_rel" ]] && raw_parent=""; fi; safe_parent=$(printf '%s' "$raw_parent" | sed 's#[^A-Za-z0-9._/-]#_#g')
  idir="${IN}"; odir="${OUT}"; [[ -n "$safe_parent" ]] && idir="${idir}/${safe_parent}" && odir="${odir}/${safe_parent}"; idir="${idir}/${pp}/${ss}"; odir="${odir}/${pp}/${ss}"; mkdir -p "$odir"
  r1="${idir}/${pp}_R1.fq.gz"; r2="${idir}/${pp}_R2.fq.gz"; fa="${odir}/${pp}_merged.fasta"; marker="${odir}/.DONE"; logf="${odir}/pandaseq.log"
  [[ -s "$r1" && -s "$r2" ]] || { printf '%s,%s,%s,%s,0,0,0,ERROR,missing_clean_FASTQ\n' "$sample" "$pair" "$r1" "$r2" > "${SUMMARY}.row.${idx}"; return 1; }
  fingerprint="$(pair_fingerprint "$r1" "$r2")"
  if [[ -s "$fa" && -f "$marker" ]] && grep -qx "version=${PANDASEQ_VERSION}" "$marker" 2>/dev/null && grep -qx "input_fingerprint=${fingerprint}" "$marker" 2>/dev/null; then
    input=$(awk -F= '$1=="input_pairs"{print $2;exit}' "$marker"); merged=$(awk -F= '$1=="merged_sequences"{print $2;exit}' "$marker"); input=${input:-0}; merged=${merged:-0}; pct=$(awk -v a="$merged" -v b="$input" 'BEGIN{printf "%.2f", b?a*100/b:0}')
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' "$sample" "$pair" "$r1" "$r2" "$input" "$merged" "$pct" OK '' > "${SUMMARY}.row.${idx}"; return 0
  fi
  log "[pandaseq] ${sample}/${pair}"
  tmp="${fa}.tmp.${BASHPID:-$$}"
  if "$BIN" -f "$r1" -r "$r2" -B -w "$tmp" -T "$THREADS" >"$logf" 2>&1; then
    mv -f "$tmp" "$fa"; merged=$(grep -c '^>' "$fa" || true); input=$(gzip -cd "$r1" | awk 'END{print NR/4}'); pct=$(awk -v a="$merged" -v b="$input" 'BEGIN{printf "%.2f", b?a*100/b:0}')
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' "$sample" "$pair" "$r1" "$r2" "$input" "$merged" "$pct" OK '' > "${SUMMARY}.row.${idx}"
    marker_tmp="${marker}.tmp.${BASHPID:-$$}"
    printf 'stage=pandaseq\nversion=%s\ninput_fingerprint=%s\ninput_pairs=%s\nmerged_sequences=%s\n' "$PANDASEQ_VERSION" "$fingerprint" "$input" "$merged" > "$marker_tmp"
    mv -f "$marker_tmp" "$marker"
    return 0
  else
    rm -f "$tmp"; printf '%s,%s,%s,%s,0,0,0,ERROR,pandaseq_failed\n' "$sample" "$pair" "$r1" "$r2" > "${SUMMARY}.row.${idx}"; return 1
  fi
}

record_result(){
  local pid="$1" label="${PID_LABEL[$1]}" idx="${PID_INDEX[$1]}" status
  if wait "$pid"; then ok=$((ok+1)); cat "${SUMMARY}.row.${idx}" >> "$SUMMARY"; rm -f "${SUMMARY}.row.${idx}"; status=OK
  else status=$?; fail=$((fail+1)); [[ -f "${SUMMARY}.row.${idx}" ]] && cat "${SUMMARY}.row.${idx}" >> "$SUMMARY"; rm -f "${SUMMARY}.row.${idx}"; status="FAILED(${status})"; fi
  completed=$((completed+1)); log "[PIG][pandaseq] progress=${completed}/${total} percent=$((completed*100/(total>0?total:1))) sample=${label} status=${status}"
}
job_index=0
for job in "${JOBS[@]}"; do
  IFS=$'\t' read -r sample pair raw_r1 <<< "$job"
  while (( ${#PIDS[@]} >= MAX_PARALLEL )); do pid="${PIDS[0]}"; PIDS=("${PIDS[@]:1}"); record_result "$pid"; unset 'PID_LABEL[$pid]' 'PID_INDEX[$pid]'; done
  run_pandaseq_one "$sample" "$pair" "$raw_r1" "$job_index" &
  pid=$!; PIDS+=("$pid"); PID_LABEL["$pid"]="${sample}/${pair}"; PID_INDEX["$pid"]="$job_index"
  job_index=$((job_index+1))
done
while (( ${#PIDS[@]} > 0 )); do pid="${PIDS[0]}"; PIDS=("${PIDS[@]:1}"); record_result "$pid"; unset 'PID_LABEL[$pid]' 'PID_INDEX[$pid]'; done
log "[PIG][pandaseq] completed=${ok} failed=${fail} total=${total} summary=${SUMMARY}"
(( fail == 0 ))
