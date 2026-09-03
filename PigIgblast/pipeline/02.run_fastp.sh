#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PIPELINE_DIR="$SCRIPT_DIR"
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
RUNTIME_RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
source "${SCRIPT_DIR}/00.pipeline_config.env" 2>/dev/null || true
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
[[ -n "$RUNTIME_RAW_INPUT_DIR" ]] && RAW_INPUT_DIR="$RUNTIME_RAW_INPUT_DIR"
export PIG_PIPELINE_CONFIG="${PIG_PIPELINE_CONFIG:-${SCRIPT_DIR}/00.pipeline_config.env}"
RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
FASTP_BIN="${SCIGBLAST_FASTP_BIN:-${FASTP_BIN:-fastp}}"
THREADS="${SCIGBLAST_FASTP_THREADS:-${FASTP_THREADS:-8}}"
MAX_PARALLEL="${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-${FASTP_MAX_PARALLEL_SAMPLES:-4}}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-${PARALLEL_THREAD_BUDGET:-128}}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-${TOTAL_MEMORY_GB:-300}}"
MEMORY_PER_SAMPLE_GB="${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-${FASTP_MEMORY_GB:-8}}"
Q="${SCIGBLAST_FASTP_QUAL_THRESHOLD:-${FASTP_QUALITY:-20}}"
N_LIMIT="${SCIGBLAST_FASTP_N_BASE_LIMIT:-${FASTP_N_LIMIT:-20}}"
FASTP_STAGE_VERSION="3"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-${SCRIPT_DIR}/../output}}"
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "${RAW_INPUT_DIR:-}" ]]; then
  DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"
fi
stage_root(){
  local stage="$1"
  if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi
}
STAGE_MATCH_ROOT="$(stage_root 01.match)"
MANIFEST="${STAGE_MATCH_ROOT}/sample_manifest.csv"
OUT="$(stage_root 02.fastp)"
DATA="${OUT}/data"
REPORT="${OUT}/report"
SUMMARY="${REPORT}/fastp_summary.csv"

log(){ printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die(){ log "ERROR: $*" >&2; exit 1; }
input_fingerprint(){
  local r1="$1" r2="$2"
  local payload
  payload="${r1}|${r2}|$(stat -c '%s:%Y' "$r1")|$(stat -c '%s:%Y' "$r2")|${Q}|${N_LIMIT}|${THREADS}|fastp_R1_R2_v1"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$payload" | sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "$payload" | shasum -a 256 | awk '{print $1}'
  else
    printf '%s' "$payload"
  fi
}
[[ -f "$MANIFEST" ]] || die "missing $MANIFEST; run 01.match_sample.py first"
command -v "$FASTP_BIN" >/dev/null 2>&1 || die "fastp not found: $FASTP_BIN"
mkdir -p "$DATA" "$REPORT"

if [[ ! -f "$SUMMARY" ]]; then
  printf 'sample_id,pair_id,r1_before,r1_after,r1_percent,r2_before,r2_after,r2_percent,status,error\n' > "$SUMMARY"
fi

[[ "$MAX_PARALLEL" =~ ^[1-9][0-9]*$ ]] || die "invalid fastp parallel sample count"
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]] || die "invalid parallel thread budget"
(( MAX_PARALLEL * THREADS <= THREAD_BUDGET )) || die "fastp concurrency exceeds thread budget"
(( MAX_PARALLEL * MEMORY_PER_SAMPLE_GB <= MEMORY_BUDGET_GB )) || die "fastp concurrency exceeds memory budget"
mapfile -t JOBS < <("${PYTHON_BIN:-python3}" - "$MANIFEST" <<'PY'
import csv,sys
with open(sys.argv[1], newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row.get('status','OK') == 'OK':
            print('\t'.join((row.get('sample_id',''), row.get('pair_id',''), row.get('r1_path',''), row.get('r2_path',''))))
PY
)
total=${#JOBS[@]}; done_count=0; fail_count=0; completed_count=0
log "[PIG][fastp] stage_start total=${total} max_parallel=${MAX_PARALLEL} threads=${THREADS}"
declare -a PIDS=(); declare -A PID_INDEX=(); declare -A PID_SAMPLE=()

run_fastp_one() {
  local sample="$1" pair="$2" r1="$3" r2="$4"
  safe_sample=$(printf '%s' "$sample" | tr -cs 'A-Za-z0-9._-' '_')
  safe_pair=$(printf '%s' "$pair" | tr -cs 'A-Za-z0-9._-' '_')
  raw_parent=""
  if [[ -n "$RAW_INPUT_DIR" && "$r1" == "${RAW_INPUT_DIR%/}/"* ]]; then
    raw_rel="${r1#${RAW_INPUT_DIR%/}/}"
    raw_parent="${raw_rel%/*}"
    [[ "$raw_parent" == "$raw_rel" ]] && raw_parent=""
  fi
  safe_parent=$(printf '%s' "$raw_parent" | sed 's#[^A-Za-z0-9._/-]#_#g')
  odir="${DATA}"
  rdir="${REPORT}"
  [[ -n "$safe_parent" ]] && odir="${odir}/${safe_parent}" && rdir="${rdir}/${safe_parent}"
  odir="${odir}/${safe_pair}/${safe_sample}"
  rdir="${rdir}/${safe_pair}/${safe_sample}"
  mkdir -p "$odir" "$rdir"
  marker="${odir}/.DONE"
  out1="${odir}/${safe_pair}_R1.fq.gz"; out2="${odir}/${safe_pair}_R2.fq.gz"
  json="${rdir}/${safe_pair}.json"; html="${rdir}/${safe_pair}.html"
  fingerprint="$(input_fingerprint "$r1" "$r2")"
  if [[ -f "$marker" && -s "$out1" && -s "$out2" && -s "$json" ]] && grep -qx "version=${FASTP_STAGE_VERSION}" "$marker" 2>/dev/null && grep -qx "input_fingerprint=${fingerprint}" "$marker" 2>/dev/null; then
    log "[fastp] skip ${sample}/${pair} (marker)"; return 0
  fi
  if [[ -s "$out1" && -s "$out2" && -s "$json" ]] && gzip -t "$out1" "$out2" 2>/dev/null; then
    marker_tmp="${marker}.tmp.${BASHPID:-$$}"
    printf 'stage=fastp\nversion=%s\nsample=%s\npair=%s\ninput_fingerprint=%s\nrecovered_existing_output=1\n' "$FASTP_STAGE_VERSION" "$sample" "$pair" "$fingerprint" > "$marker_tmp"
    mv -f "$marker_tmp" "$marker"
    log "[fastp] skip ${sample}/${pair} (validated existing output)"; return 0
  fi
  # Keep .gz as the final suffix so fastp writes compressed output even for
  # the temporary file; renaming an uncompressed .tmp file to .gz is invalid.
  tmp1="${out1%.gz}.tmp.${BASHPID:-$$}.gz"; tmp2="${out2%.gz}.tmp.${BASHPID:-$$}.gz"; tmpj="${json}.tmp.${BASHPID:-$$}"; tmph="${html}.tmp.${BASHPID:-$$}"
  log "[fastp] ${sample}/${pair}"
  if "$FASTP_BIN" -i "$r1" -I "$r2" -o "$tmp1" -O "$tmp2" -j "$tmpj" -h "$tmph" -q "$Q" -u "$N_LIMIT" -w "$THREADS" -A -G; then
    mv -f "$tmp1" "$out1"; mv -f "$tmp2" "$out2"; mv -f "$tmpj" "$json"; mv -f "$tmph" "$html"
    marker_tmp="${marker}.tmp.${BASHPID:-$$}"
    printf 'stage=fastp\nversion=%s\nsample=%s\npair=%s\ninput_fingerprint=%s\n' "$FASTP_STAGE_VERSION" "$sample" "$pair" "$fingerprint" > "$marker_tmp"
    mv -f "$marker_tmp" "$marker"
    return 0
  else
    rm -f "$tmp1" "$tmp2" "$tmpj" "$tmph"
    log "[fastp] FAILED ${sample}/${pair}"; return 1
  fi
}

record_result() {
  local pid="$1" label="${PID_SAMPLE[$1]}" status
  if wait "$pid"; then
    done_count=$((done_count+1)); completed_count=$((completed_count+1))
    pct=$((completed_count * 100 / (total > 0 ? total : 1)))
    log "[PIG][fastp] progress=${completed_count}/${total} percent=${pct} sample=${label} status=OK"
  else
    status=$?; fail_count=$((fail_count+1)); completed_count=$((completed_count+1))
    pct=$((completed_count * 100 / (total > 0 ? total : 1)))
    log "[PIG][fastp] progress=${completed_count}/${total} percent=${pct} sample=${label} status=FAILED exit=${status}"
  fi
}

for job in "${JOBS[@]}"; do
  IFS=$'\t' read -r sample pair r1 r2 <<< "$job"
  while (( ${#PIDS[@]} >= MAX_PARALLEL )); do
    pid="${PIDS[0]}"; PIDS=("${PIDS[@]:1}"); record_result "$pid"; unset 'PID_SAMPLE[$pid]'
  done
  run_fastp_one "$sample" "$pair" "$r1" "$r2" &
  pid=$!; PIDS+=("$pid"); PID_SAMPLE["$pid"]="${sample}/${pair}"
done
while (( ${#PIDS[@]} > 0 )); do
  pid="${PIDS[0]}"; PIDS=("${PIDS[@]:1}"); record_result "$pid"; unset 'PID_SAMPLE[$pid]'
done

"${PYTHON_BIN:-python3}" - "$REPORT" "$SUMMARY" <<'PY'
import csv,json,sys
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2]); rows=[]
for p in root.rglob('*.json'):
    try: d=json.loads(p.read_text(encoding='utf-8'))
    except Exception: continue
    s=d.get('summary',{}); before=s.get('before_filtering',{}); after=s.get('after_filtering',{})
    n1=before.get('total_reads',0); n2=after.get('total_reads',0)
    # paired-end fastp reports one total_reads count for each side in some versions.
    rows.append({'sample_id':p.parts[-2] if len(p.parts)>1 else '', 'pair_id':p.stem,
                 'r1_before':n1,'r1_after':n2,'r1_percent':round(100*n2/n1,2) if n1 else 0,
                 'r2_before':before.get('total_reads',0),'r2_after':after.get('total_reads',0),
                 'r2_percent':round(100*after.get('total_reads',0)/before.get('total_reads',1),2) if before.get('total_reads') else 0,
                 'status':'OK','error':''})
fields=['sample_id','pair_id','r1_before','r1_after','r1_percent','r2_before','r2_after','r2_percent','status','error']
with out.open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
PY
log "[PIG][fastp] completed=${done_count} failed=${fail_count} total=${total} summary=${SUMMARY}"
(( fail_count == 0 ))
