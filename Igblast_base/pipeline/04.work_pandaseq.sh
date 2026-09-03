#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
RUNTIME_DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
RUNTIME_RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
source "${SCRIPT_DIR}/00.pipeline_config.env" 2>/dev/null || true
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
[[ -n "$RUNTIME_DATASET_LABEL" ]] && SCIGBLAST_DATASET_LABEL="$RUNTIME_DATASET_LABEL"
[[ -n "$RUNTIME_RAW_INPUT_DIR" ]] && RAW_INPUT_DIR="$RUNTIME_RAW_INPUT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-${SCRIPT_DIR}/../output}}"; RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "$RAW_INPUT_DIR" ]]; then DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"; fi
stage_root(){ local stage="$1"; if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi; }
MANIFEST="$(stage_root 01.match)/sample_manifest.csv"; INPUT="$(stage_root 03.clean_data)"; OUTPUT="$(stage_root 04.pandaseq)"; SUMMARY="${OUTPUT}/pandaseq_summary.csv"
PANDASEQ_BIN="${SCIGBLAST_PANDASEQ_BIN:-${PANDASEQ_BIN:-pandaseq}}"; THREADS="${SCIGBLAST_PANDASEQ_THREADS:-${PANDASEQ_THREADS:-16}}"
REQUESTED_WORKERS="${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-${PANDASEQ_MAX_PARALLEL_SAMPLES:-2}}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-${PARALLEL_THREAD_BUDGET:-128}}"; MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"; MEMORY_PER_WORKER_GB="${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-${SCIGBLAST_PANDASEQ_MEMORY_GB:-${PANDASEQ_MEMORY_GB:-32}}}"
STAGE_VERSION="4"
log(){ printf '[%s] [BASE][pandaseq] %s\n' "$(date '+%F %T')" "$*"; }; die(){ log "ERROR $*" >&2; exit 1; }; positive(){ [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
[[ -s "$MANIFEST" ]] || die "missing canonical manifest: $MANIFEST"; command -v "$PANDASEQ_BIN" >/dev/null 2>&1 || die "pandaseq not found: $PANDASEQ_BIN"
for value in "$THREADS" "$REQUESTED_WORKERS" "$THREAD_BUDGET" "$MEMORY_BUDGET_GB" "$MEMORY_PER_WORKER_GB"; do positive "$value" || die "invalid positive integer resource value: $value"; done
by_threads=$((THREAD_BUDGET/THREADS)); by_memory=$((MEMORY_BUDGET_GB/MEMORY_PER_WORKER_GB)); ((by_threads>0))||by_threads=1; ((by_memory>0))||by_memory=1
EFFECTIVE_WORKERS="$REQUESTED_WORKERS"; ((EFFECTIVE_WORKERS>by_threads))&&EFFECTIVE_WORKERS="$by_threads"; ((EFFECTIVE_WORKERS>by_memory))&&EFFECTIVE_WORKERS="$by_memory"; mkdir -p "$OUTPUT"

mapfile -t JOBS < <("$PYTHON_BIN" - "$MANIFEST" "$RAW_INPUT_DIR" <<'PY'
import csv,re,sys
from pathlib import Path, PurePosixPath
manifest,raw=Path(sys.argv[1]),Path(sys.argv[2]).resolve() if sys.argv[2] else None; seen=set()
def safe(v): return re.sub(r'[^A-Za-z0-9._-]+','_',str(v)).strip('_') or 'unnamed'
def stage_path(rel, pair_id, sample):
    pair_stem=PurePosixPath(pair_id).name
    values=[]
    for value in (rel if rel != '.' else '', pair_stem, sample):
        for part in PurePosixPath(value).parts:
            part=safe(part)
            if part and (not values or values[-1] != part): values.append(part)
    return pair_stem, '/'.join(values)
for row in csv.DictReader(manifest.open(encoding='utf-8-sig',newline='')):
    if row.get('status','').upper()!='OK': continue
    key=(row.get('r1_path',''),row.get('r2_path',''),row.get('sample_id',''))
    if key in seen: continue
    seen.add(key); p=Path(row['r1_path']).resolve(); rel='.'
    if raw:
        try: rel=p.parent.relative_to(raw).as_posix() or '.'
        except ValueError: pass
    rel='/'.join(safe(x) for x in Path(rel).parts) if rel!='.' else '.'
    pair_stem, stage_rel=stage_path(rel, row.get('pair_id',''), row.get('sample_id',''))
    print('\t'.join((row.get('sample_id',''),pair_stem,stage_rel)))
PY
)
total=${#JOBS[@]}; completed=0; success=0; failed=0; declare -a PIDS=(); declare -A PID_LABEL=() PID_ROW=()
log "stage_start total=${total} requested_workers=${REQUESTED_WORKERS} effective_workers=${EFFECTIVE_WORKERS} threads_per_worker=${THREADS} memory_per_worker_gb=${MEMORY_PER_WORKER_GB}"

run_one(){
  local sample="$1" pair="$2" rel="$3" index="$4" ibase="$INPUT" obase="$OUTPUT" ss pp r1 r2 fasta marker logfile fingerprint tmp input_pairs merged pct
  ss=$(printf '%s' "$sample"|tr -cs 'A-Za-z0-9._-' '_'); pp=$(printf '%s' "$pair"|tr -cs 'A-Za-z0-9._-' '_'); [[ "$rel" != "." ]]&&ibase="${ibase}/${rel}"&&obase="${obase}/${rel}"; mkdir -p "$obase"
  r1="${ibase}/${pp}_R1.fq.gz"; r2="${ibase}/${pp}_R2.fq.gz"; fasta="${obase}/${pp}_merged.fasta"; marker="${obase}/.DONE"; logfile="${obase}/pandaseq.log"; row_file="${SUMMARY}.row.${index}"
  if [[ ! -s "$r1" || ! -s "$r2" ]]; then printf '%s,%s,0,0,0,ERROR,missing_canonical_clean_FASTQ\n' "$sample" "$pair" > "$row_file"; return 1; fi
  fingerprint=$(printf '%s\0%s\0%s\0%s\0%s' "$r1" "$r2" "$(stat -c '%s:%Y' "$r1")" "$(stat -c '%s:%Y' "$r2")" "$THREADS" | sha256sum | awk '{print $1}')
  if [[ -s "$fasta" && -s "$marker" ]]&&grep -qx 'status=DONE' "$marker"&&grep -qx "job_fingerprint=${fingerprint}" "$marker"; then
    input_pairs=$(awk -F= '$1=="input_pairs"{print $2}' "$marker"); merged=$(awk -F= '$1=="merged_sequences"{print $2}' "$marker"); input_pairs=${input_pairs:-0}; merged=${merged:-0}; pct=$(awk -v a="$merged" -v b="$input_pairs" 'BEGIN{printf "%.2f",b?a*100/b:0}'); printf '%s,%s,%s,%s,%s,OK,\n' "$sample" "$pair" "$input_pairs" "$merged" "$pct" > "$row_file"; log "skip sample=${sample} pair=${pair}"; return 0
  fi
  tmp="${fasta}.tmp.${BASHPID}"
  if "$PANDASEQ_BIN" -f "$r1" -r "$r2" -B -w "$tmp" -T "$THREADS" > "$logfile" 2>&1; then
    mv -f "$tmp" "$fasta"; merged=$(grep -c '^>' "$fasta" || true); input_pairs=$(gzip -cd "$r1" | awk 'END{print int(NR/4)}'); pct=$(awk -v a="$merged" -v b="$input_pairs" 'BEGIN{printf "%.2f",b?a*100/b:0}')
    { printf 'status=DONE\nmanifest_schema_version=2\nstage=pandaseq\nversion=%s\njob_fingerprint=%s\ninput_pairs=%s\nmerged_sequences=%s\noutput_naming_schema=baseline_pair_sample_v2\nsample_id=%s\npair_id=%s\n' "$STAGE_VERSION" "$fingerprint" "$input_pairs" "$merged" "$sample" "$pair"; } > "${marker}.tmp.${BASHPID}"; mv -f "${marker}.tmp.${BASHPID}" "$marker"
    printf '%s,%s,%s,%s,%s,OK,\n' "$sample" "$pair" "$input_pairs" "$merged" "$pct" > "$row_file"; return 0
  fi
  rm -f "$tmp"; printf '%s,%s,0,0,0,ERROR,pandaseq_failed\n' "$sample" "$pair" > "$row_file"; return 1
}
collect_one(){ local pid="$1" rc=0 label="${PID_LABEL[$1]}" row="${PID_ROW[$1]}"; wait "$pid"||rc=$?; completed=$((completed+1)); if ((rc==0));then success=$((success+1));state=OK;else failed=$((failed+1));state="ERROR(${rc})";fi; [[ -s "$row" ]]&&cat "$row" >> "$SUMMARY"; rm -f "$row"; log "progress=${completed}/${total} percent=$((completed*100/(total>0?total:1))) sample=${label} status=${state}"; }
printf 'sample_id,pair_id,input_pairs,merged_sequences,merge_percent,status,error\n' > "$SUMMARY"; index=0
for job in "${JOBS[@]}"; do IFS=$'\t' read -r sample pair rel <<< "$job"; while ((${#PIDS[@]}>=EFFECTIVE_WORKERS));do pid="${PIDS[0]}";PIDS=("${PIDS[@]:1}");collect_one "$pid";unset 'PID_LABEL[$pid]' 'PID_ROW[$pid]';done; run_one "$sample" "$pair" "$rel" "$index" & pid=$!;PIDS+=("$pid");PID_LABEL["$pid"]="${sample}/${pair}";PID_ROW["$pid"]="${SUMMARY}.row.${index}";index=$((index+1));done
while ((${#PIDS[@]}));do pid="${PIDS[0]}";PIDS=("${PIDS[@]:1}");collect_one "$pid";unset 'PID_LABEL[$pid]' 'PID_ROW[$pid]';done
log "completed=${success} failed=${failed} total=${total} summary=${SUMMARY}"; ((total>0&&failed==0))
