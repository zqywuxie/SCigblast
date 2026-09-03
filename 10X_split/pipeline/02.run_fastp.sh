#!/bin/bash
set -euo pipefail

# Pipeline order: selected FASTQ (raw or IR_split output) -> fastp -> clean header
# Contract: 01.match_sample.py is the only stage that interprets raw suffixes;
# this stage reads manifest paths and writes only canonical *_R1.fq.gz and
# *_R2.fq.gz files.

# =============================================================================
# fastp 数据过滤脚本
#   - 输入：01.match manifest 中的 status=OK FASTQ（原始后缀仅由 match 解释）
#   - 输出：按样本分子目录，生成 R1/R2 过滤后 fastq.gz + HTML/JSON 质控报告
#   - 子目录模式：遍历 INPUT_DIR 下每个子目录，查找 *_forward_cleaned.fq / *_reverse_cleaned.fq
#   - flat-file 模式：直接在 INPUT_DIR 下按后缀配对
#   - 使用 fastp 进行质量过滤（可配置质量阈值、N 碱基上限、线程数）
#   - 默认关闭 adapter trimming 与 polyG trimming，避免 read 被剪短
# =============================================================================

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${BRANCH_ROOT}/output}"
CONFIG_FILE="${SCRIPT_DIR}/00.pipeline_config.env"
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
RUNTIME_DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    . "$CONFIG_FILE"
    set +a
fi
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
[[ -n "$RUNTIME_DATASET_LABEL" ]] && SCIGBLAST_DATASET_LABEL="$RUNTIME_DATASET_LABEL"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${BRANCH_ROOT}/output}"

DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "${RAW_INPUT_DIR:-}" ]]; then DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"; fi
stage_root(){ local stage="$1"; if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi; }

INPUT_DIR="${SCIGBLAST_RAW_INPUT_DIR:-/colddata/zqy/XYFY_HZJ1}"
FASTP_ROOT="$(stage_root 02.fastp)"
OUTPUT_DIR="${FASTP_ROOT}/data"
REPORT_DIR="${FASTP_ROOT}/report"
MAPPING_SUMMARY="${SCIGBLAST_FASTP_MAPPING_SUMMARY:-}"
FORWARD_OUTPUT_SUFFIX="_R1.fq.gz"
REVERSE_OUTPUT_SUFFIX="_R2.fq.gz"
QUAL_THRESHOLD="${SCIGBLAST_FASTP_QUAL_THRESHOLD:-20}"
N_BASE_LIMIT="${SCIGBLAST_FASTP_N_BASE_LIMIT:-20}"
THREADS="${SCIGBLAST_FASTP_THREADS:-4}"
FASTP_BIN="${SCIGBLAST_FASTP_BIN:-fastp}"
# Configuration is expressed in GB.  Keep the MB option only as a temporary
# compatibility override for older launchers; normal runs derive it from the
# canonical GB setting.
MEMORY_LIMIT_GB="${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-16}"
MEMORY_LIMIT_MB=0
MAX_PARALLEL_SAMPLES="${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-2}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"
MONITOR_INTERVAL_SEC=5

# ---- 日志工具 ----

timestamp() {
    date '+%F %T'
}

log() {
    local level="$1"
    shift
    printf '[%s] [%s] %s\n' "$(timestamp)" "$level" "$*"
}

info()  { log INFO "$@"; }
warn()  { log WARN "$@"; }

die() {
    log ERROR "$@"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

# ---- 内存监控 ----

kb_to_mb() {
    awk -v kb="${1:-0}" 'BEGIN { printf "%.1f", kb / 1024 }'
}

apply_memory_limit() {
    if [[ "$MEMORY_LIMIT_MB" =~ ^[0-9]+$ ]] && (( MEMORY_LIMIT_MB > 0 )); then
        ulimit -v $((MEMORY_LIMIT_MB * 1024)) || die "无法设置内存限制 ${MEMORY_LIMIT_MB}MB"
    fi
}

process_tree_rss_kb() {
    local root_pid="$1"
    ps -e -o pid=,ppid=,rss= | awk -v root="$root_pid" '
        {
            pid = $1
            ppid = $2
            rss[pid] = $3
            children[ppid] = children[ppid] " " pid
        }
        END {
            print sum(root)
        }
        function sum(pid, total, count, i, ids) {
            if (pid == "" || seen[pid]++) {
                return 0
            }
            total = rss[pid] + 0
            count = split(children[pid], ids, " ")
            for (i = 1; i <= count; i++) {
                if (ids[i] != "") {
                    total += sum(ids[i])
                }
            }
            return total
        }
    '
}

run_with_monitor() {
    local label="$1"
    shift

    local start_ts
    local current_rss=0
    local peak_rss=0
    local exit_code
    local cmd_pid
    local elapsed

    (
        apply_memory_limit
        "$@"
    ) &
    cmd_pid=$!
    start_ts=$(date +%s)

    while kill -0 "$cmd_pid" 2>/dev/null; do
        current_rss=$(process_tree_rss_kb "$cmd_pid")
        if (( current_rss > peak_rss )); then
            peak_rss=$current_rss
        fi
        elapsed=$(( $(date +%s) - start_ts ))
        info "[monitor] ${label} elapsed=${elapsed}s rss=$(kb_to_mb "$current_rss")MB peak=$(kb_to_mb "$peak_rss")MB"
        sleep "$MONITOR_INTERVAL_SEC"
    done

    wait "$cmd_pid"
    exit_code=$?
    elapsed=$(( $(date +%s) - start_ts ))
    info "[monitor] ${label} done exit=${exit_code} elapsed=${elapsed}s peak=$(kb_to_mb "$peak_rss")MB"
    return "$exit_code"
}

# ---- 样本发现（唯一入口：manifest） ----

collect_samples() {
    local input_dir="$1"
    [[ -s "$MAPPING_SUMMARY" ]] || die "mapping summary not found or empty: $MAPPING_SUMMARY"
    local pair_id fwd rev rel_parent sample
    SAMPLE_NAMES=()
    SAMPLE_FORWARDS=()
    SAMPLE_REVERSES=()
    SAMPLE_OUTPUT_DIRS=()
    while IFS= read -r -d '' pair_id &&
          IFS= read -r -d '' fwd &&
          IFS= read -r -d '' rev; do
        [[ -f "$fwd" ]] || { warn "manifest R1 missing: $fwd"; continue; }
        [[ -f "$rev" ]] || { warn "manifest R2 missing: $rev"; continue; }
        rel_parent="$(dirname "$pair_id")"
        [[ "$rel_parent" == "." ]] && rel_parent=""
        sample="$(basename "$pair_id")"
        SAMPLE_NAMES+=("$sample")
        SAMPLE_FORWARDS+=("$fwd")
        SAMPLE_REVERSES+=("$rev")
        SAMPLE_OUTPUT_DIRS+=("$rel_parent")
    done < <("${PYTHON_BIN}" - "$MAPPING_SUMMARY" "$input_dir" <<'PY'
import csv
import os
import sys
from pathlib import Path

summary = Path(sys.argv[1])
input_root = Path(sys.argv[2]).expanduser()
pairs = {}
with summary.open("r", encoding="utf-8-sig", newline="") as handle:
    reader = csv.DictReader(handle)
    required = {"pair_id", "status", "r1_path", "r2_path"}
    missing = required - set(reader.fieldnames or ())
    if missing:
        raise SystemExit("mapping summary missing columns: " + ", ".join(sorted(missing)))
    for row in reader:
        if str(row.get("status", "")).strip().upper() != "OK":
            continue
        pair_id = str(row.get("pair_id", "")).strip().replace("\\", "/").strip("/")
        r1_raw = str(row.get("r1_path", "")).strip()
        r2_raw = str(row.get("r2_path", "")).strip()
        if not pair_id or not r1_raw or not r2_raw:
            continue
        r1 = Path(r1_raw).expanduser()
        r2 = Path(r2_raw).expanduser()
        if not r1.is_absolute(): r1 = input_root / r1
        if not r2.is_absolute(): r2 = input_root / r2
        value = (str(r1.resolve()), str(r2.resolve()))
        previous = pairs.setdefault(pair_id, value)
        if previous != value:
            raise SystemExit(f"pair_id {pair_id!r} maps to conflicting FASTQ paths")
for pair_id, (r1, r2) in sorted(pairs.items()):
    for value in (pair_id, r1, r2):
        sys.stdout.buffer.write(os.fsencode(value) + b"\0")
PY
    )
    (( ${#SAMPLE_NAMES[@]} > 0 )) || die "mapping summary has no usable status=OK pairs: $MAPPING_SUMMARY"
    info "fastp manifest input enabled: pairs=${#SAMPLE_NAMES[@]} summary=${MAPPING_SUMMARY}"
}

usage() {
    cat <<'EOF'
用法:
  bash 02.run_fastp.sh [选项]

选项:
  --input-dir DIR                输入目录（原始或 IR_split FASTQ，默认 raw_fastq）
  --mapping-summary FILE         match_sample_barcode summary；只处理 status=OK 文件
  --output-dir DIR               FASTQ 输出目录（默认 1.fastp/data）
  --report-dir DIR               HTML/JSON/summary 目录（默认 1.fastp/report）
  输入原始后缀由 01.match_sample.py 读取 00.pipeline_config.env 中的
  SCIGBLAST_RAW_READ_SUFFIX_PAIRS；fastp 输出固定为 _R1.fq.gz/_R2.fq.gz
  --quality-threshold N          fastp 质量阈值 (-q)，默认 20
  --n-base-limit N               fastp N 碱基上限 (-u)，默认 20
  --threads N                    fastp 线程数 (-w)，默认 4
  --memory-limit-gb N            每个任务虚拟内存上限 (GB)，默认 16
  --monitor-interval-sec N       内存监控间隔 (秒)，默认 5
  --help                         显示帮助

说明:
  输入只取 manifest 中 status=OK 的物理 FASTQ pair，多个样本映射同一 pair
  时 fastp 只处理一次，后续 split 阶段再按样本展开。

  默认 fastp 过滤参数: -q 20 -u 20 -A -G
  其中 -A 关闭 adapter trimming，-G 关闭 polyG trimming，避免 read 被剪短
  输出为 gzip 压缩的 FASTQ，同时生成 HTML 和 JSON 质控报告
EOF
}

# ---- 参数解析 ----

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir)              INPUT_DIR="$2";              shift 2 ;;
        --mapping-summary)        MAPPING_SUMMARY="$2";         shift 2 ;;
        --output-dir)             OUTPUT_DIR="$2";             shift 2 ;;
        --report-dir)             REPORT_DIR="$2";             shift 2 ;;
        --forward-input-suffix|--reverse-input-suffix|--forward-output-suffix|--reverse-output-suffix)
            die "$1 is no longer configurable; raw suffixes belong to match and fastp output is always _R1/_R2" ;;
        --quality-threshold)      QUAL_THRESHOLD="$2";         shift 2 ;;
        --n-base-limit)           N_BASE_LIMIT="$2";           shift 2 ;;
        --threads)                THREADS="$2";                shift 2 ;;
        --memory-limit-gb)        MEMORY_LIMIT_GB="$2";        shift 2 ;;
        --monitor-interval-sec)   MONITOR_INTERVAL_SEC="$2";   shift 2 ;;
        --help)                   usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

# ---- 主流程 ----

require_command "$FASTP_BIN"

[[ "$MAX_PARALLEL_SAMPLES" =~ ^[1-9][0-9]*$ ]] || die "invalid fastp parallel sample count"
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]] || die "invalid parallel thread budget"
[[ "$MEMORY_BUDGET_GB" =~ ^[0-9]+$ ]] || die "invalid memory budget"
(( MAX_PARALLEL_SAMPLES * THREADS <= THREAD_BUDGET )) || die "fastp concurrency exceeds thread budget"
[[ "$MEMORY_LIMIT_GB" =~ ^[1-9][0-9]*$ ]] || die "SCIGBLAST_FASTP_MEMORY_LIMIT_GB must be a positive integer"
MEMORY_LIMIT_MB=$((MEMORY_LIMIT_GB * 1024))
(( MAX_PARALLEL_SAMPLES * MEMORY_LIMIT_GB <= MEMORY_BUDGET_GB )) || die "fastp concurrency exceeds memory budget"


mkdir -p "$OUTPUT_DIR" "$REPORT_DIR"

collect_samples "$INPUT_DIR"
TOTAL_SAMPLES=${#SAMPLE_NAMES[@]}
(( TOTAL_SAMPLES > 0 )) || die "在 ${INPUT_DIR} 中未找到可配对的样本"

info "开始处理，样本数=${TOTAL_SAMPLES} input=${INPUT_DIR} output=${OUTPUT_DIR}"
info "fastp 参数 quality=${QUAL_THRESHOLD} n_limit=${N_BASE_LIMIT} threads=${THREADS}"
info "内存限制=${MEMORY_LIMIT_MB}MB 监控间隔=${MONITOR_INTERVAL_SEC}s max_parallel=${MAX_PARALLEL_SAMPLES}"

SUCCESS_COUNT=0
FAIL_COUNT=0
COMPLETED_COUNT=0
declare -a FASTP_PIDS=()
declare -A FASTP_PID_SAMPLE=()

fastp_marker_key() {
    local sample="$1" input="$2"
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s\0%s' "$sample" "$input" | sha256sum | cut -c1-16
    else
        printf '%s\0%s' "$sample" "$input" | cksum | awk '{print $1}'
    fi
}

fastp_one_sample() {
    local i="$1"
    step=$((i + 1))
    step_pct=$((step * 100 / TOTAL_SAMPLES))
    sample="${SAMPLE_NAMES[i]}"
    forward_in="${SAMPLE_FORWARDS[i]}"
    reverse_in="${SAMPLE_REVERSES[i]}"
    sample_out_dir="${OUTPUT_DIR}/${SAMPLE_OUTPUT_DIRS[i]}"
    report_out_dir="${REPORT_DIR}/${SAMPLE_OUTPUT_DIRS[i]}"
    forward_out="${sample_out_dir}/${sample}${FORWARD_OUTPUT_SUFFIX}"
    reverse_out="${sample_out_dir}/${sample}${REVERSE_OUTPUT_SUFFIX}"
    html_out="${report_out_dir}/${sample}.html"
    json_out="${report_out_dir}/${sample}.json"
    marker_key="$(fastp_marker_key "$sample" "$forward_in")"
    done_marker="${sample_out_dir}/.fastp.DONE.${marker_key}.json"
    legacy_done_marker="${sample_out_dir}/.fastp.DONE.json"

    mkdir -p "$sample_out_dir" "$report_out_dir"
    info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 准备处理样本=${sample}"

    # Per-sample checkpoint.  A rerun only touches samples whose output pair
    # is incomplete (for example, samples that were ERROR in the mapping
    # summary and were fixed later).  The marker is intentionally retained
    # when sequence payloads are cleaned by the runner.
    if [[ -f "$done_marker" && -s "$json_out" && -s "$forward_out" && -s "$reverse_out" ]]; then
        info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] sample=${sample} already complete; skip"
        return 0
    fi
    # Migrate a complete output produced by the legacy lane-shared marker.
    # The output pair and JSON are sample-specific, so this avoids rerunning
    # valid fastp results while replacing the unsafe shared marker scheme.
    if [[ ! -f "$done_marker" && -f "$legacy_done_marker" && -s "$json_out" && -s "$forward_out" && -s "$reverse_out" ]]; then
        marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
        printf '{"status":"DONE","sample":"%s","forward":"%s","reverse":"%s","migrated_from_legacy":true}\n' \
            "$sample" "$forward_out" "$reverse_out" > "$marker_tmp"
        mv -f "$marker_tmp" "$done_marker"
        info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] sample=${sample} already complete; migrated_marker=1"
        return 0
    fi

    if ! run_with_monitor "fastp ${sample}" \
        "$FASTP_BIN" \
            -i "$forward_in" \
            -I "$reverse_in" \
            -o "$forward_out" \
            -O "$reverse_out" \
            -h "$html_out" \
            -j "$json_out" \
            -q "$QUAL_THRESHOLD" \
            -u "$N_BASE_LIMIT" \
            -w "$THREADS" \
            -A \
            -G; then
        warn "样本 ${sample} fastp 处理失败"

        return 1
    fi

    marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
    printf '{"status":"DONE","sample":"%s","forward":"%s","reverse":"%s"}\n' \
        "$sample" "$forward_out" "$reverse_out" > "$marker_tmp"
    mv -f "$marker_tmp" "$done_marker"
    info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} 完成"
    return 0
}

record_fastp_result() {
    local pid="$1" sample="$2" status
    if wait "$pid"; then
        ((SUCCESS_COUNT += 1)); ((COMPLETED_COUNT += 1))
        info "[fastp progress] completed=${COMPLETED_COUNT}/${TOTAL_SAMPLES} percent=$((COMPLETED_COUNT * 100 / TOTAL_SAMPLES)) sample=${sample} status=OK"
    else
        status=$?; ((FAIL_COUNT += 1)); ((COMPLETED_COUNT += 1))
        warn "[fastp progress] completed=${COMPLETED_COUNT}/${TOTAL_SAMPLES} percent=$((COMPLETED_COUNT * 100 / TOTAL_SAMPLES)) sample=${sample} status=FAILED exit=${status}"
    fi
}

for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    sample="${SAMPLE_NAMES[i]}"
    while (( ${#FASTP_PIDS[@]} >= MAX_PARALLEL_SAMPLES )); do
        pid="${FASTP_PIDS[0]}"; FASTP_PIDS=("${FASTP_PIDS[@]:1}")
        record_fastp_result "$pid" "${FASTP_PID_SAMPLE[$pid]}"
        unset 'FASTP_PID_SAMPLE[$pid]'
    done
    fastp_one_sample "$i" &
    pid=$!; FASTP_PIDS+=("$pid"); FASTP_PID_SAMPLE["$pid"]="$sample"
done
while (( ${#FASTP_PIDS[@]} > 0 )); do
    pid="${FASTP_PIDS[0]}"; FASTP_PIDS=("${FASTP_PIDS[@]:1}")
    record_fastp_result "$pid" "${FASTP_PID_SAMPLE[$pid]}"
    unset 'FASTP_PID_SAMPLE[$pid]'
done

if ! "${PYTHON_BIN}" - "$REPORT_DIR" "${REPORT_DIR}/fastp_summary.csv" <<'PY'
import csv,json,os,sys
from pathlib import Path
F=['sample','relative_dir','json_path','before_reads','after_reads','reads_retained_pct','reads_discarded','reads_discarded_pct','before_bases','after_bases','bases_retained_pct','low_quality_reads','too_many_n_reads','too_short_reads','too_long_reads','q20_rate_before','q20_rate_after','q20_pct_before','q20_pct_after','q30_rate_before','q30_rate_after','q30_pct_before','q30_pct_after']
def n(v):
    try:
        x=float(v or 0); return int(x) if x.is_integer() else x
    except (TypeError,ValueError): return 0
def p(v,t): return round(v*100/t,4) if t else 0.0
root,out=Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve(); rows=[]
for q in sorted(root.rglob('*.json')):
    try:
        d=json.loads(q.read_text(encoding='utf-8')); b=d['summary']['before_filtering']; a=d['summary']['after_filtering']; z=d.get('filtering_result',{}); br,ar=n(b.get('total_reads')),n(a.get('total_reads')); bb,ab=n(b.get('total_bases')),n(a.get('total_bases')); q20b,q20a=n(b.get('q20_rate')),n(a.get('q20_rate')); q30b,q30a=n(b.get('q30_rate')),n(a.get('q30_rate')); rel=q.parent.relative_to(root).as_posix(); rows.append({'sample':q.stem,'relative_dir':'' if rel=='.' else rel,'json_path':q.relative_to(root).as_posix(),'before_reads':br,'after_reads':ar,'reads_retained_pct':p(ar,br),'reads_discarded':br-ar,'reads_discarded_pct':p(br-ar,br),'before_bases':bb,'after_bases':ab,'bases_retained_pct':p(ab,bb),'low_quality_reads':n(z.get('low_quality_reads')),'too_many_n_reads':n(z.get('too_many_N_reads')),'too_short_reads':n(z.get('too_short_reads')),'too_long_reads':n(z.get('too_long_reads')),'q20_rate_before':q20b,'q20_rate_after':q20a,'q20_pct_before':round(q20b*100,4),'q20_pct_after':round(q20a*100,4),'q30_rate_before':q30b,'q30_rate_after':q30a,'q30_pct_before':round(q30b*100,4),'q30_pct_after':round(q30a*100,4)})
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as e: print(f'[fastp-summary] skip {q}: {e}',file=sys.stderr)
out.parent.mkdir(parents=True,exist_ok=True); tmp=out.with_name('.'+out.name+'.tmp')
with tmp.open('w',encoding='utf-8',newline='') as h: w=csv.DictWriter(h,fieldnames=F); w.writeheader(); w.writerows(rows)
os.replace(tmp,out); print(f'[fastp-summary] reports={len(rows)} output={out}')
PY
then
    warn "fastp JSON summary generation failed"
    ((FAIL_COUNT += 1))
fi

info "处理结束 success=${SUCCESS_COUNT} fail=${FAIL_COUNT} total=${TOTAL_SAMPLES}"
(( FAIL_COUNT == 0 )) || exit 1
