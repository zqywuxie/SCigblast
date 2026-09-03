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
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-${SCRIPT_DIR}/../output}}"
RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "$RAW_INPUT_DIR" ]]; then DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"; fi
stage_root(){ local stage="$1"; if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi; }
MANIFEST="$(stage_root 01.match)/sample_manifest.csv"
INPUT="$(stage_root 02.fastp)/data"; OUTPUT="$(stage_root 03.clean_data)"; SUMMARY="${OUTPUT}/clean_summary.csv"
THREADS="${SCIGBLAST_CLEAN_THREADS:-${CLEAN_THREADS:-4}}"
REQUESTED_WORKERS="${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-${CLEAN_MAX_PARALLEL_SAMPLES:-4}}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-${PARALLEL_THREAD_BUDGET:-128}}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"
MEMORY_PER_WORKER_GB="${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-${SCIGBLAST_CLEAN_MEMORY_GB:-${CLEAN_MEMORY_GB:-8}}}"
STAGE_VERSION="5"
PIGZ=$(command -v pigz || true)

log(){ printf '[%s] [BASE][clean] %s\n' "$(date '+%F %T')" "$*"; }
die(){ log "ERROR $*" >&2; exit 1; }
positive(){ [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
[[ -s "$MANIFEST" ]] || die "missing canonical manifest: $MANIFEST"
for value in "$THREADS" "$REQUESTED_WORKERS" "$THREAD_BUDGET" "$MEMORY_BUDGET_GB" "$MEMORY_PER_WORKER_GB"; do positive "$value" || die "invalid positive integer resource value: $value"; done
workers_by_threads=$(( THREAD_BUDGET / THREADS )); workers_by_memory=$(( MEMORY_BUDGET_GB / MEMORY_PER_WORKER_GB ))
(( workers_by_threads > 0 )) || workers_by_threads=1; (( workers_by_memory > 0 )) || workers_by_memory=1
EFFECTIVE_WORKERS="$REQUESTED_WORKERS"; (( EFFECTIVE_WORKERS > workers_by_threads )) && EFFECTIVE_WORKERS="$workers_by_threads"; (( EFFECTIVE_WORKERS > workers_by_memory )) && EFFECTIVE_WORKERS="$workers_by_memory"
mkdir -p "$OUTPUT"

mapfile -t JOBS < <("$PYTHON_BIN" - "$MANIFEST" "$RAW_INPUT_DIR" <<'PY'
import csv,re,sys
from pathlib import Path, PurePosixPath
manifest,raw=Path(sys.argv[1]),Path(sys.argv[2]).resolve() if sys.argv[2] else None
seen=set()
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

clean_one_read(){
  # Do not expand dst inside the same `local` declaration: with `set -u`,
  # Bash expands all RHS expressions before assigning dst, so `${dst%.gz}`
  # previously raised "dst: unbound variable" for every sample.
  local src="$1" dst="$2" read_number="$3" tmp
  tmp="${dst%.gz}.tmp.${BASHPID}.gz"
  local awk_program='(NR-1)%4==0 { split($0,f,/[[:space:]]+/); h=f[1]; sub(/^@/,"",h); sub(/#.*/,"",h); sub(/\/[12]$/, "", h); n=split(h,a,":"); limit=(n>=8?7:n); for(i=4;i<=limit;i++){gsub(/[A-Za-z]/,"",a[i]); sub(/^0+/,"",a[i]); if(a[i]=="")a[i]="0"}; out=a[1]; for(i=2;i<=limit;i++)out=out ":" a[i]; print "@" out "/" suffix; next } { print }'
  if [[ -n "$PIGZ" ]]; then
    if "$PIGZ" -dc -p "$THREADS" "$src" | awk -v suffix="$read_number" "$awk_program" | "$PIGZ" -p "$THREADS" > "$tmp"; then mv -f "$tmp" "$dst"; return 0; fi
  else
    if gzip -dc "$src" | awk -v suffix="$read_number" "$awk_program" | gzip -c > "$tmp"; then mv -f "$tmp" "$dst"; return 0; fi
  fi
  rm -f "$tmp"; return 1
}

total=${#JOBS[@]}; completed=0; success=0; failed=0; declare -a PIDS=(); declare -A PID_LABEL=()
log "stage_start total=${total} requested_workers=${REQUESTED_WORKERS} effective_workers=${EFFECTIVE_WORKERS} threads_per_worker=${THREADS} memory_per_worker_gb=${MEMORY_PER_WORKER_GB}"

run_one(){
  local sample="$1" pair="$2" rel="$3" input_base="$INPUT" output_base="$OUTPUT" ss pp r1 r2 o1 o2 marker fingerprint
  ss=$(printf '%s' "$sample" | tr -cs 'A-Za-z0-9._-' '_'); pp=$(printf '%s' "$pair" | tr -cs 'A-Za-z0-9._-' '_')
  [[ "$rel" != "." ]] && input_base="${input_base}/${rel}" && output_base="${output_base}/${rel}"
  mkdir -p "$output_base"
  r1="${input_base}/${pp}_R1.fq.gz"; r2="${input_base}/${pp}_R2.fq.gz"; o1="${output_base}/${pp}_R1.fq.gz"; o2="${output_base}/${pp}_R2.fq.gz"; marker="${output_base}/.DONE"
  [[ -s "$r1" && -s "$r2" ]] || { log "missing canonical input sample=${sample} pair=${pair}"; return 1; }
  fingerprint=$(printf '%s\0%s\0%s\0%s\0%s' "$r1" "$r2" "$(stat -c '%s:%Y' "$r1")" "$(stat -c '%s:%Y' "$r2")" 'canonical_R1_R2_v1' | sha256sum | awk '{print $1}')
  if [[ -s "$marker" && -s "$o1" && -s "$o2" ]] && grep -qx 'status=DONE' "$marker" && grep -qx "job_fingerprint=${fingerprint}" "$marker" && gzip -t "$o1" "$o2" 2>/dev/null; then log "skip sample=${sample} pair=${pair}"; return 0; fi
  clean_one_read "$r1" "$o1" 1 || return 1
  clean_one_read "$r2" "$o2" 2 || return 1
  { printf 'status=DONE\nmanifest_schema_version=2\nstage=clean\nversion=%s\njob_fingerprint=%s\noutput_naming_schema=baseline_pair_sample_v2\nsample_id=%s\npair_id=%s\n' "$STAGE_VERSION" "$fingerprint" "$sample" "$pair"; } > "${marker}.tmp.${BASHPID}"
  mv -f "${marker}.tmp.${BASHPID}" "$marker"
}

collect_one(){ local pid="$1" rc=0 label="${PID_LABEL[$1]}"; wait "$pid" || rc=$?; completed=$((completed+1)); if ((rc==0)); then success=$((success+1)); state=OK; else failed=$((failed+1)); state="ERROR(${rc})"; fi; log "progress=${completed}/${total} percent=$((completed*100/(total>0?total:1))) sample=${label} status=${state}"; }
for job in "${JOBS[@]}"; do IFS=$'\t' read -r sample pair rel <<< "$job"; while (( ${#PIDS[@]} >= EFFECTIVE_WORKERS )); do pid="${PIDS[0]}"; PIDS=("${PIDS[@]:1}"); collect_one "$pid"; unset 'PID_LABEL[$pid]'; done; run_one "$sample" "$pair" "$rel" & pid=$!; PIDS+=("$pid"); PID_LABEL["$pid"]="${sample}/${pair}"; done
while (( ${#PIDS[@]} )); do pid="${PIDS[0]}"; PIDS=("${PIDS[@]:1}"); collect_one "$pid"; unset 'PID_LABEL[$pid]'; done

"$PYTHON_BIN" - "$OUTPUT" "$SUMMARY" <<'PY'
import csv,os,sys
from pathlib import Path
root,out=Path(sys.argv[1]),Path(sys.argv[2]); rows=[]
for marker in sorted(root.rglob('.DONE')):
    values=dict(line.split('=',1) for line in marker.read_text(encoding='utf-8',errors='ignore').splitlines() if '=' in line)
    if values.get('stage')!='clean' or values.get('status')!='DONE': continue
    rows.append({'sample_id':values.get('sample_id', marker.parent.name),'pair_id':values.get('pair_id', marker.parent.parent.name),'status':'OK','error':'','output_dir':marker.parent.relative_to(root).as_posix()})
fields=['sample_id','pair_id','status','error','output_dir']; tmp=out.with_suffix(out.suffix+'.tmp')
with tmp.open('w',encoding='utf-8',newline='') as fh: writer=csv.DictWriter(fh,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
os.replace(tmp,out)
PY
log "completed=${success} failed=${failed} total=${total} summary=${SUMMARY}"
(( total > 0 && failed == 0 ))
