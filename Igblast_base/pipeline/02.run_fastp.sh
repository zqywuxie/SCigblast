#!/bin/bash
set -euo pipefail

# Pipeline order: matched raw FASTQ -> fastp -> clean header
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
RUNTIME_RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    . "$CONFIG_FILE"
    set +a
fi
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
[[ -n "$RUNTIME_DATASET_LABEL" ]] && SCIGBLAST_DATASET_LABEL="$RUNTIME_DATASET_LABEL"
[[ -n "$RUNTIME_RAW_INPUT_DIR" ]] && RAW_INPUT_DIR="$RUNTIME_RAW_INPUT_DIR"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${BRANCH_ROOT}/output}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -z "$DATASET_LABEL" && -n "${RAW_INPUT_DIR:-}" ]]; then DATASET_LABEL="$(basename "${RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"; fi
stage_root(){ local stage="$1"; if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi; }

INPUT_DIR="${SCIGBLAST_RAW_INPUT_DIR:-${RAW_INPUT_DIR:-/colddata/zqy/XYFY_HZJ1}}"
FASTP_ROOT="$(stage_root 02.fastp)"
OUTPUT_DIR="${FASTP_ROOT}/data"
REPORT_DIR="${FASTP_ROOT}/report"
MAPPING_SUMMARY="${SCIGBLAST_FASTP_MAPPING_SUMMARY:-$(stage_root 01.match)/sample_manifest.csv}"
FORWARD_OUTPUT_SUFFIX="_R1.fq.gz"
REVERSE_OUTPUT_SUFFIX="_R2.fq.gz"
QUAL_THRESHOLD="${SCIGBLAST_FASTP_QUAL_THRESHOLD:-20}"
N_BASE_LIMIT="${SCIGBLAST_FASTP_N_BASE_LIMIT:-20}"
THREADS="${SCIGBLAST_FASTP_THREADS:-4}"
FASTP_BIN="${SCIGBLAST_FASTP_BIN:-fastp}"
MEMORY_LIMIT_GB="${SCIGBLAST_FASTP_MEMORY_LIMIT_GB:-8}"
MEMORY_LIMIT_MB=0
MONITOR_INTERVAL_SEC=5
MAX_PARALLEL_SAMPLES="${SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES:-2}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"

# Raw filename conventions are interpreted only by 01.match_sample.py.
# This stage consumes manifest r1_path/r2_path and writes canonical names.

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
        info "[BASE][fastp] heartbeat task=${label} elapsed=${elapsed}s rss=$(kb_to_mb "$current_rss")MB peak=$(kb_to_mb "$peak_rss")MB"
        sleep "$MONITOR_INTERVAL_SEC"
    done

    wait "$cmd_pid"
    exit_code=$?
    elapsed=$(( $(date +%s) - start_ts ))
    info "[BASE][fastp] heartbeat_done task=${label} exit=${exit_code} elapsed=${elapsed}s peak=$(kb_to_mb "$peak_rss")MB"
    return "$exit_code"
}

# ---- 样本发现（唯一入口：manifest） ----

collect_samples() {
    local input_dir="$1"
    local pair_id sample fwd rev rel_parent
    [[ -s "$MAPPING_SUMMARY" ]] || die "mapping summary not found or empty: $MAPPING_SUMMARY"
    SAMPLE_NAMES=()
    SAMPLE_FORWARDS=()
    SAMPLE_REVERSES=()
    SAMPLE_OUTPUT_DIRS=()
    while IFS=$'\t' read -r pair_id sample fwd rev; do
        [[ -n "$pair_id" && -n "$fwd" && -n "$rev" ]] || continue
        [[ -f "$fwd" ]] || { warn "manifest R1 does not exist: $fwd"; continue; }
        [[ -f "$rev" ]] || { warn "manifest R2 does not exist: $rev"; continue; }
        rel_parent="$(dirname "$pair_id")"
        [[ "$rel_parent" == "." ]] && rel_parent=""
        sample="${sample:-$(basename "$pair_id")}";
        SAMPLE_NAMES+=("$sample")
        SAMPLE_FORWARDS+=("$fwd")
        SAMPLE_REVERSES+=("$rev")
        # Keep the same directory contract consumed by clean/PANDAseq:
        # <raw-relative-parent>/<pair_stem>/<sample_id>/<canonical FASTQ>.
        # pair_id already contains the raw-relative parent and pair stem.
        SAMPLE_OUTPUT_DIRS+=("$pair_id")
    done < <("${PYTHON_BIN}" - "$MAPPING_SUMMARY" "$input_dir" <<'PY'
import csv
import re
import sys
from pathlib import Path, PurePosixPath

summary = Path(sys.argv[1])
input_root = Path(sys.argv[2]).expanduser().resolve()
seen = {}
def safe(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unnamed"

with summary.open("r", encoding="utf-8-sig", newline="") as handle:
    reader = csv.DictReader(handle)
    required = {"pair_id", "status", "r1_path", "r2_path"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise SystemExit("mapping summary missing columns: " + ", ".join(sorted(missing)))
    for row in reader:
        if str(row.get("status", "")).strip().upper() != "OK":
            continue
        pair_id = str(row.get("pair_id", "")).strip().replace("\\", "/").strip("/")
        r1_text = str(row.get("r1_path", "")).strip()
        r2_text = str(row.get("r2_path", "")).strip()
        if not pair_id or not r1_text or not r2_text:
            continue
        parts = PurePosixPath(pair_id).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise SystemExit(f"unsafe pair_id in mapping summary: {pair_id!r}")
        r1, r2 = Path(r1_text).expanduser(), Path(r2_text).expanduser()
        if not r1.is_absolute(): r1 = input_root / r1
        if not r2.is_absolute(): r2 = input_root / r2
        sample_id = str(row.get("sample_id", "")).strip()
        value = (str(r1.resolve()), str(r2.resolve()))
        key = (pair_id, sample_id)
        previous = seen.get(key)
        if previous is not None and previous != value:
            raise SystemExit(f"pair/sample resolves to conflicting FASTQ pairs: {pair_id} / {sample_id}")
        seen[key] = value
for (pair_id, sample_id), (r1, r2) in seen.items():
    # Emit a path-safe relative output key. The manifest retains the original
    # pair_id; only stage filesystem paths are normalized here.
    pair_parts = [safe(part) for part in PurePosixPath(pair_id).parts]
    deduped = []
    for part in pair_parts:
        if not deduped or deduped[-1] != part:
            deduped.append(part)
    safe_pair = "/".join(deduped)
    print(f"{safe_pair}\t{safe(sample_id) if sample_id else safe(PurePosixPath(pair_id).name)}\t{r1}\t{r2}")
PY
    )
    ((${#SAMPLE_NAMES[@]})) || die "mapping summary has no usable status=OK FASTQ pairs: $MAPPING_SUMMARY"
    info "fastp manifest input enabled: physical_pairs=${#SAMPLE_NAMES[@]} summary=${MAPPING_SUMMARY}"
}

usage() {
    cat <<'EOF'
用法:
  bash 02.run_fastp.sh [选项]

选项:
  --input-dir DIR                输入原始 FASTQ 目录
  --mapping-summary FILE         match_sample_barcode summary；只处理 status=OK 文件
  --output-dir DIR               FASTQ 输出目录（默认 02.fastp/data）
  --report-dir DIR               HTML/JSON/summary 目录（默认 02.fastp/report）
  输入原始后缀由 01.match_sample.py 读取 00.pipeline_config.env 中的
  SCIGBLAST_RAW_READ_SUFFIX_PAIRS；fastp 输出固定为 _R1.fq.gz/_R2.fq.gz
  --quality-threshold N          fastp 质量阈值 (-q)，默认 20
  --n-base-limit N               fastp N 碱基上限 (-u)，默认 20
  --threads N                    fastp 线程数 (-w)，默认 4
  --memory-limit-gb N            每个任务虚拟内存上限 (GB)，默认 8
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

if ! [[ "$THREADS" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_FASTP_THREADS must be a positive integer"
fi
if ! [[ "$MEMORY_LIMIT_GB" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_FASTP_MEMORY_LIMIT_GB must be a positive integer"
fi
MEMORY_LIMIT_MB=$((MEMORY_LIMIT_GB * 1024))
if ! [[ "$MAX_PARALLEL_SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_FASTP_MAX_PARALLEL_SAMPLES must be a positive integer"
fi
if ! [[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_PARALLEL_THREAD_BUDGET must be a positive integer"
fi
if ! [[ "$MAX_THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]] || (( THREAD_BUDGET > MAX_THREAD_BUDGET )); then
    die "SCIGBLAST_PARALLEL_THREAD_BUDGET must be <= ${MAX_THREAD_BUDGET}"
fi
if ! [[ "$MEMORY_BUDGET_GB" =~ ^[0-9]+$ ]]; then
    die "SCIGBLAST_MEMORY_BUDGET_GB must be a non-negative integer"
fi
if (( MAX_PARALLEL_SAMPLES * THREADS > THREAD_BUDGET )); then
    die "fastp concurrency exceeds thread budget: ${MAX_PARALLEL_SAMPLES} x ${THREADS} > ${THREAD_BUDGET}"
fi
if (( MAX_PARALLEL_SAMPLES * MEMORY_LIMIT_GB > MEMORY_BUDGET_GB )); then
    die "fastp concurrency exceeds memory budget: ${MAX_PARALLEL_SAMPLES} x ${MEMORY_LIMIT_GB}GB > ${MEMORY_BUDGET_GB}GB"
fi

mkdir -p "$OUTPUT_DIR" "$REPORT_DIR"

collect_samples "$INPUT_DIR"
TOTAL_SAMPLES=${#SAMPLE_NAMES[@]}
(( TOTAL_SAMPLES > 0 )) || die "在 ${INPUT_DIR} 中未找到可配对的样本"

info "开始处理，样本数=${TOTAL_SAMPLES} input=${INPUT_DIR} output=${OUTPUT_DIR}"
info "fastp 参数 quality=${QUAL_THRESHOLD} n_limit=${N_BASE_LIMIT} threads=${THREADS}"
info "内存限制=${MEMORY_LIMIT_MB}MB 监控间隔=${MONITOR_INTERVAL_SEC}s"

SUCCESS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

fastp_marker_key() {
    local sample="$1" input="$2"
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s\0%s' "$sample" "$input" | sha256sum | cut -c1-16
    else
        printf '%s\0%s' "$sample" "$input" | cksum | awk '{print $1}'
    fi
}

run_fastp_sample() {
    local i="$1"
    local step="$2"
    local step_pct="$3"
    local sample forward_in reverse_in sample_rel sample_out_dir report_out_dir
    local forward_out reverse_out html_out json_out done_marker legacy_done_marker marker_tmp marker_key
    sample="${SAMPLE_NAMES[i]}"
    forward_in="${SAMPLE_FORWARDS[i]}"
    reverse_in="${SAMPLE_REVERSES[i]}"
    sample_rel="${SAMPLE_OUTPUT_DIRS[i]}"
    if [[ "$(basename "$sample_rel")" == "$sample" ]]; then
        sample_out_dir="${OUTPUT_DIR}/${sample_rel}"
        report_out_dir="${REPORT_DIR}/${sample_rel}"
    else
        sample_out_dir="${OUTPUT_DIR}/${sample_rel}/${sample}"
        report_out_dir="${REPORT_DIR}/${sample_rel}/${sample}"
    fi
    forward_out="${sample_out_dir}/${sample}${FORWARD_OUTPUT_SUFFIX}"
    reverse_out="${sample_out_dir}/${sample}${REVERSE_OUTPUT_SUFFIX}"
    html_out="${report_out_dir}/${sample}.html"
    json_out="${report_out_dir}/${sample}.json"
    marker_key="$(fastp_marker_key "$sample" "$forward_in")"
    done_marker="${sample_out_dir}/.fastp.DONE.${marker_key}.json"
    legacy_done_marker="${sample_out_dir}/.fastp.DONE.json"

    mkdir -p "$sample_out_dir" "$report_out_dir"
    info "[BASE][fastp] worker_start sample=${sample} step=${step}/${TOTAL_SAMPLES}"

    # Per-sample checkpoint.  A rerun only touches samples whose output pair
    # is incomplete (for example, samples that were ERROR in the mapping
    # summary and were fixed later).  The marker is intentionally retained
    # when sequence payloads are cleaned by the runner.
    if [[ -f "$done_marker" && -s "$json_out" && -s "$forward_out" && -s "$reverse_out" ]]; then
        info "[BASE][fastp] worker_done sample=${sample} status=SKIPPED"
        return 2
    fi
    # Migrate complete outputs from the legacy lane-shared marker.
    if [[ ! -f "$done_marker" && -f "$legacy_done_marker" && -s "$json_out" && -s "$forward_out" && -s "$reverse_out" ]]; then
        marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
        printf '{"status":"DONE","sample":"%s","forward":"%s","reverse":"%s","migrated_from_legacy":true}\n' \
            "$sample" "$forward_out" "$reverse_out" > "$marker_tmp"
        mv -f "$marker_tmp" "$done_marker"
        info "[BASE][fastp] worker_done sample=${sample} status=SKIPPED migrated_marker=1"
        return 2
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
        warn "[BASE][fastp] worker_done sample=${sample} status=FAILED"
        return 1
    fi

    marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
    printf '{"status":"DONE","sample":"%s","forward":"%s","reverse":"%s"}\n' \
        "$sample" "$forward_out" "$reverse_out" > "$marker_tmp"
    mv -f "$marker_tmp" "$done_marker"
    info "[BASE][fastp] worker_done sample=${sample} status=OK"
    return 0
}

wait_fastp_job() {
    local pid="$1"
    local status=0
    if wait "$pid"; then
        ((SUCCESS_COUNT += 1))
    else
        status=$?
        if (( status == 2 )); then
            ((SUCCESS_COUNT += 1))
            ((SKIP_COUNT += 1))
        else
            ((FAIL_COUNT += 1))
        fi
    fi
}

info "[BASE][fastp] stage_start total=${TOTAL_SAMPLES} max_parallel=${MAX_PARALLEL_SAMPLES} threads=${THREADS}"
info "样本级并发=${MAX_PARALLEL_SAMPLES}，线程预算=${THREAD_BUDGET}，内存预算=${MEMORY_BUDGET_GB}GB"
declare -a FASTP_PIDS=()
for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    step=$((i + 1))
    step_pct=$((step * 100 / TOTAL_SAMPLES))
    while (( ${#FASTP_PIDS[@]} >= MAX_PARALLEL_SAMPLES )); do
        wait_fastp_job "${FASTP_PIDS[0]}"
        FASTP_PIDS=("${FASTP_PIDS[@]:1}")
    done
    run_fastp_sample "$i" "$step" "$step_pct" &
    FASTP_PIDS+=("$!")
done
for pid in "${FASTP_PIDS[@]}"; do
    wait_fastp_job "$pid"
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

info "[BASE][fastp] completed=${SUCCESS_COUNT} failed=${FAIL_COUNT} skipped=${SKIP_COUNT} total=${TOTAL_SAMPLES}"
(( FAIL_COUNT == 0 )) || exit 1
