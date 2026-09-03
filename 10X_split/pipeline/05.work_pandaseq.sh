#!/bin/bash
set -euo pipefail

# Pipeline order: 02.run_fastp.sh -> 04.clean_header.sh -> 05.work_pandaseq.sh

# =============================================================================
# pandaseq 双端序列拼接脚本
#   - 输入：fastp 过滤后的 FASTQ（默认读取 1.fastp/data，支持子目录 + flat-file）
#   - 输出：按样本分子目录，生成 merged.fasta + 统计日志 + unaligned.fasta
#   - 子目录模式：遍历 DATA_DIRS 下每个子目录，查找 *_R1.fq.gz / *_R2.fq.gz
#   - flat-file 模式：直接在 DATA_DIRS 根目录下按后缀配对
#   - 支持多数据目录（空格/逗号分隔），跨目录同名样本只保留首次出现的
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
if [[ -z "$DATASET_LABEL" && -n "${SCIGBLAST_RAW_INPUT_DIR:-}" ]]; then DATASET_LABEL="$(basename "${SCIGBLAST_RAW_INPUT_DIR%/}" | tr -cs 'A-Za-z0-9._-' '_')"; fi
stage_root(){ local stage="$1"; if [[ -n "$DATASET_LABEL" ]]; then printf '%s/%s/%s\n' "$OUTPUT_ROOT" "$stage" "$DATASET_LABEL"; else printf '%s/%s\n' "$OUTPUT_ROOT" "$stage"; fi; }

DATA_DIRS=(
    "$(stage_root 04.clean_data)"
)
DATA_DIR_SET=0
OUTPUT_DIR="$(stage_root 05.pandaseq)"
STATS_FILE="${OUTPUT_DIR}/pandaseq_summary.csv"

FORWARD_INPUT_SUFFIX="_R1.fq.gz"
REVERSE_INPUT_SUFFIX="_R2.fq.gz"
PANDASEQ_INPUT_VERSION=3
PANDASEQ_MARKER_VERSION=3
NUM_THREADS="${SCIGBLAST_PANDASEQ_THREADS:-64}"
PANDASEQ_BIN="${SCIGBLAST_PANDASEQ_BIN:-pandaseq}"
PANDASEQ_OUTPUT_FORMAT="${SCIGBLAST_PANDASEQ_OUTPUT_FORMAT:-fastq}"
MEMORY_LIMIT_GB="${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-64}"
MEMORY_LIMIT_MB=0
MAX_PARALLEL_SAMPLES="${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-2}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MONITOR_INTERVAL_SEC=5

case "$PANDASEQ_OUTPUT_FORMAT" in
    fastq) PANDASEQ_OUTPUT_EXT="fastq"; PANDASEQ_UNALIGNED_EXT="fastq"; PANDASEQ_FORMAT_FLAG=(-F) ;;
    fasta) PANDASEQ_OUTPUT_EXT="fasta"; PANDASEQ_UNALIGNED_EXT="fasta"; PANDASEQ_FORMAT_FLAG=() ;;
    *) printf '[ERROR] SCIGBLAST_PANDASEQ_OUTPUT_FORMAT must be fastq or fasta\n' >&2; exit 2 ;;
esac

count_assembled_records() {
    local path="$1"
    if [[ "$PANDASEQ_OUTPUT_FORMAT" == "fastq" ]]; then
        awk 'NR%4==1 && /^@/{n++} END{print n+0}' "$path" 2>/dev/null || echo 0
    else
        awk '/^>/{n++} END{print n+0}' "$path" 2>/dev/null || echo 0
    fi
}

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

run_with_monitor_logged() {
    local label="$1"
    local log_file="$2"
    shift 2

    local start_ts
    local current_rss=0
    local peak_rss=0
    local exit_code
    local cmd_pid
    local elapsed

    (
        apply_memory_limit
        "$@"
    ) >"$log_file" 2>&1 &
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

# Checkpoint identity is based on the exact canonical inputs, not only on the
# existence of a previous FASTA.  This prevents stale PANDAseq output when a
# corrected fastp/clean sample is rerun.
file_metadata() {
    local path="$1"
    if stat -c '%n|%s|%Y' "$path" 2>/dev/null; then return 0; fi
    stat -f '%N|%z|%m' "$path"
}

pair_fingerprint() {
    local forward_file="$1" reverse_file="$2" payload
    payload=$(printf 'marker_version=%s\ninput_version=%s\nthreads=%s\n' \
        "$PANDASEQ_MARKER_VERSION" "$PANDASEQ_INPUT_VERSION" "$NUM_THREADS")
    payload+=$(printf '%s\n%s\n' "$(file_metadata "$forward_file")" "$(file_metadata "$reverse_file")")
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$payload" | sha256sum | awk '{print $1}'
    else
        printf '%s' "$payload" | shasum -a 256 | awk '{print $1}'
    fi
}

marker_value() {
    local marker="$1" key="$2"
    awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$marker"
}

marker_matches() {
    local marker="$1" fingerprint="$2"
    [[ -f "$marker" ]] || return 1
    [[ "$(marker_value "$marker" marker_version)" == "$PANDASEQ_MARKER_VERSION" ]] || return 1
    [[ "$(marker_value "$marker" input_version)" == "$PANDASEQ_INPUT_VERSION" ]] || return 1
    [[ "$(marker_value "$marker" input_fingerprint)" == "$fingerprint" ]] || return 1
}

# ---- 统计提取 ----

extract_stat_from_log() {
    local key="$1"
    local log_file="$2"
    local reducer="${3:-sum}"

    # PANDAseq emits one final STAT block per worker when -T/--threads is
    # enabled.  Keep the last value for every worker token (e.g. 0x...:12),
    # which drops earlier progress lines, then sum counters across workers.
    # ELAPSED is a wall-clock value and is reduced with max.  Legacy logs
    # without worker tokens fall back to their last value.
    awk -v key="$key" -v reducer="$reducer" '
        function numeric(x) { return x ~ /^[0-9]+([.][0-9]+)?$/ }
        $0 ~ /\tSTAT\t/ && $3 == key && numeric($4) {
            token=$1
            if (token ~ /:[0-9]+$/) {
                worker[token]=$4
                has_worker=1
            } else {
                legacy=$4
                has_legacy=1
            }
        }
        END {
            if (has_worker) {
                if (reducer == "max") {
                    result=0
                    for (token in worker) if (worker[token] > result) result=worker[token]
                } else {
                    result=0
                    for (token in worker) result += worker[token]
                }
                print result
            } else if (has_legacy) {
                print legacy
            } else {
                exit 1
            }
        }
    ' "$log_file" 2>/dev/null || true
}

# ---- 文件解析 ----

resolve_input_file() {
    local dir="$1"
    local base="$2"
    local suffix="$3"

    # Fastp/clean publish only the canonical compressed pair.  Do not guess
    # another extension here: a missing canonical mate must be visible.
    local exact="${dir}/${base}${suffix}"
    if [[ -f "$exact" ]]; then
        printf '%s\n' "$exact"
        return 0
    fi

    return 1
}

# ---- 样本发现 ----

collect_samples() {
    local data_dir forward_file reverse_file base sample_base parent_dir relative_parent sample source_type

    SAMPLE_NAMES=()
    declare -gA SAMPLE_FORWARD=()
    declare -gA SAMPLE_REVERSE=()
    declare -gA SAMPLE_SOURCE=()
    declare -gA SAMPLE_REL_PARENT=()
    declare -gA SAMPLE_FILE_STEM=()
    declare -A SEEN_DATA_DIRS=()

    for data_dir in "${DATA_DIRS[@]}"; do
        data_dir="${data_dir%/}"
        if [[ -n "${SEEN_DATA_DIRS[$data_dir]:-}" ]]; then
            continue
        fi
        SEEN_DATA_DIRS["$data_dir"]=1
        [[ -d "$data_dir" ]] || { warn "数据目录不存在，跳过: ${data_dir}"; continue; }

        # Recursive discovery handles <pair_stem>/<sample_id>/<FASTQ>.
        while IFS= read -r -d '' forward_file; do
            base=$(basename "$forward_file")
            sample_base="${base%$FORWARD_INPUT_SUFFIX}"
            parent_dir=$(dirname "$forward_file")
            reverse_file="${parent_dir}/${sample_base}${REVERSE_INPUT_SUFFIX}"
            if [[ ! -f "$reverse_file" ]]; then
                warn "缺少配对文件，跳过 ${forward_file}: ${reverse_file}"
                continue
            fi
            if [[ "$parent_dir" == "$data_dir" ]]; then
                relative_parent=""
                sample="$sample_base"
                source_type="flat-file:${data_dir}"
            else
                relative_parent="${parent_dir#${data_dir}/}"
                sample=$(printf '%s' "$relative_parent" | sed 's#[/\\]#__#g')
                source_type="recursive:${data_dir}/${relative_parent}"
            fi
            if [[ -n "${SAMPLE_SOURCE[$sample]:-}" ]]; then
                warn "重复样本 ${sample}，保留 ${SAMPLE_SOURCE[$sample]} 来源，跳过 (${data_dir})"
                continue
            fi
            SAMPLE_NAMES+=("$sample")
            SAMPLE_FORWARD["$sample"]="$forward_file"
            SAMPLE_REVERSE["$sample"]="$reverse_file"
            SAMPLE_SOURCE["$sample"]="$source_type"
            SAMPLE_REL_PARENT["$sample"]="$relative_parent"
            SAMPLE_FILE_STEM["$sample"]="$sample_base"
        done < <(find "$data_dir" -type f -name "*${FORWARD_INPUT_SUFFIX}" -print0 | sort -z)
    done
}

# ---- 帮助 ----

usage() {
    cat <<'EOF'
用法:
  bash 05.work_pandaseq.sh [选项]

选项:
  --data-dir DIR[,DIR...]        输入目录（可多次指定，也支持空格/逗号分隔多个路径）
  --output-dir DIR               pandaseq 输出目录
  FASTQ naming is fixed after fastp/clean: *_R1.fq.gz and *_R2.fq.gz.
  --threads N                    pandaseq 线程数，默认 64
  --memory-limit-gb N            每个任务虚拟内存上限（GB），默认 64
  --monitor-interval-sec N       内存监控间隔 (秒)，默认 5
  --help                         显示帮助

说明:
  --data-dir 支持三种形式:
    1. 单路径:     --data-dir /path/to/fastq
    2. 多次指定:   --data-dir /path/A --data-dir /path/B
    3. 逗号分隔:   --data-dir /path/A,/path/B
    跨目录的同名样本只保留首次出现的。

  支持递归输入结构，包括:
    <data_dir>/<pair_stem>/<sample_id>/<FASTQ>
    <data_dir>/<sample>/<sample>_R1.fq.gz / <sample>_R2.fq.gz
    <data_dir>/<sample>_R1.fq.gz / <sample>_R2.fq.gz

  默认输入来自 04.clean_header.sh 的输出目录 3.clean_data。
  输出生成 merged.fasta, pandaseq.log, unaligned.fasta 及汇总 CSV。
EOF
}

# ---- 参数解析 ----

while [[ $# -gt 0 ]]; do
    case "$1" in
        --data-dir)
            if [[ "$DATA_DIR_SET" -eq 0 ]]; then
                DATA_DIRS=()
                DATA_DIR_SET=1
            fi
            IFS=',' read -ra _paths <<< "$2"
            DATA_DIRS+=("${_paths[@]}")
            shift 2
            ;;
        --output-dir)             OUTPUT_DIR="$2"; STATS_FILE="${OUTPUT_DIR}/pandaseq_summary.csv"; shift 2 ;;
        --forward-input-suffix|--reverse-input-suffix)
            die "$1 is no longer configurable; downstream FASTQ names are fixed _R1/_R2" ;;
        --threads)                NUM_THREADS="$2";           shift 2 ;;
        --memory-limit-gb)        MEMORY_LIMIT_GB="$2";       shift 2 ;;
        --monitor-interval-sec)   MONITOR_INTERVAL_SEC="$2";  shift 2 ;;
        --help)                   usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
done

# ---- 主流程 ----

require_command "$PANDASEQ_BIN"

mkdir -p "$OUTPUT_DIR"

collect_samples
TOTAL_SAMPLES=${#SAMPLE_NAMES[@]}
(( TOTAL_SAMPLES > 0 )) || die "在 ${DATA_DIRS[*]} 中未找到可配对的样本"

info "开始 pandaseq，样本数=${TOTAL_SAMPLES} data_dirs=${DATA_DIRS[*]} output=${OUTPUT_DIR}"
info "后缀映射 forward=${FORWARD_INPUT_SUFFIX} reverse=${REVERSE_INPUT_SUFFIX}"
info "线程=${NUM_THREADS} 内存限制=${MEMORY_LIMIT_MB}MB 监控间隔=${MONITOR_INTERVAL_SEC}s max_parallel=${MAX_PARALLEL_SAMPLES}"

[[ "$MAX_PARALLEL_SAMPLES" =~ ^[1-9][0-9]*$ ]] || die "PANDAseq 并发样本数无效"
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]] || die "并发线程预算无效"
(( MAX_PARALLEL_SAMPLES * NUM_THREADS <= THREAD_BUDGET )) || die "PANDAseq 并发超过线程预算"
[[ "$MEMORY_LIMIT_GB" =~ ^[1-9][0-9]*$ ]] || die "SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB must be a positive integer"
MEMORY_LIMIT_MB=$((MEMORY_LIMIT_GB * 1024))
if (( MAX_PARALLEL_SAMPLES * MEMORY_LIMIT_GB > ${SCIGBLAST_MEMORY_BUDGET_GB:-300} )); then
    die "PANDAseq 并发超过内存预算"
fi

echo "Sample,Total_Reads,OK_Reads,NOALGN,LOWQ,BADR,SLOW,Elapsed_sec,Merged_Percent,Merged_FASTA_Records,Unaligned_FASTA_Records" > "$STATS_FILE"

SUCCESS_COUNT=0
FAIL_COUNT=0
COMPLETED_COUNT=0
declare -a PANDASEQ_PIDS=()
declare -A PANDASEQ_PID_SAMPLE=()

pandaseq_one_sample() {
    local i="$1"
    step=$((i + 1))
    step_pct=$((step * 100 / TOTAL_SAMPLES))
    sample="${SAMPLE_NAMES[i]}"
    forward_file="${SAMPLE_FORWARD[$sample]}"
    reverse_file="${SAMPLE_REVERSE[$sample]}"
    source_type="${SAMPLE_SOURCE[$sample]}"
    sample_file_stem="${SAMPLE_FILE_STEM[$sample]:-$sample}"

    # Keep the same relative parent hierarchy as the cleaned FASTQ input.
    # This prevents equal sample names from different lanes/batches colliding.
    sample_relative_parent="${SAMPLE_REL_PARENT[$sample]:-}"
    sample_output_dir="${OUTPUT_DIR}"
    [[ -n "$sample_relative_parent" ]] && sample_output_dir="${OUTPUT_DIR}/${sample_relative_parent}"
    mkdir -p "$sample_output_dir"

    out_fasta="${sample_output_dir}/${sample_file_stem}_merged.${PANDASEQ_OUTPUT_EXT}"
    log_file="${sample_output_dir}/${sample_file_stem}_pandaseq.log"
    unaligned_file="${sample_output_dir}/${sample_file_stem}_unaligned.${PANDASEQ_UNALIGNED_EXT}"
    legacy_done_marker="${sample_output_dir}/.pandaseq.DONE"
    if [[ ! -f "$forward_file" || ! -f "$reverse_file" ]]; then
        warn "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} 缺少输入文件，跳过"
        return 1
    fi

    input_fingerprint="$(pair_fingerprint "$forward_file" "$reverse_file")"
    marker_key="${input_fingerprint:0:16}"
    done_marker="${sample_output_dir}/.pandaseq.DONE.${marker_key}"
    if [[ -f "$done_marker" && -f "$out_fasta" ]] \
        && marker_matches "$done_marker" "$input_fingerprint"; then
        info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} 已完成，跳过拼接"
    elif [[ -f "$legacy_done_marker" && -f "$out_fasta" && -f "$unaligned_file" ]]; then
        marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
        {
            printf 'marker_version=%s\n' "$PANDASEQ_MARKER_VERSION"
            printf 'input_version=%s\n' "$PANDASEQ_INPUT_VERSION"
            printf 'input_fingerprint=%s\n' "$input_fingerprint"
            printf 'timestamp=%s\n' "$(date -Iseconds)"
            printf 'migrated_from_legacy=1\n'
        } > "$marker_tmp"
        mv -f "$marker_tmp" "$done_marker"
        info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} 已完成，迁移旧 marker"
    else
        info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 开始拼接样本=${sample} source=${source_type}"

        if ! run_with_monitor_logged "pandaseq ${sample}" "$log_file" \
             "$PANDASEQ_BIN" "${PANDASEQ_FORMAT_FLAG[@]}" -f "$forward_file" -r "$reverse_file" -B \
                     -w "$out_fasta" -U "$unaligned_file" -T "$NUM_THREADS"; then
            warn "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} pandaseq 执行失败"
            return 1
        fi
        {
            printf 'marker_version=%s\n' "$PANDASEQ_MARKER_VERSION"
            printf 'input_version=%s\n' "$PANDASEQ_INPUT_VERSION"
            printf 'input_fingerprint=%s\n' "$input_fingerprint"
            printf 'timestamp=%s\n' "$(date -Iseconds)"
        } > "${done_marker}.tmp.${BASHPID:-$$}"
        mv -f "${done_marker}.tmp.${BASHPID:-$$}" "$done_marker"
    fi

    MERGED_FASTA_RECORDS=$(count_assembled_records "$out_fasta")
    UNALIGNED_FASTA_RECORDS=$(count_assembled_records "$unaligned_file")

    TOTAL=$(extract_stat_from_log "READS" "$log_file")
    OK=$(extract_stat_from_log "OK" "$log_file")
    NOALGN=$(extract_stat_from_log "NOALGN" "$log_file")
    LOWQ=$(extract_stat_from_log "LOWQ" "$log_file")
    BADR=$(extract_stat_from_log "BADR" "$log_file")
    SLOW=$(extract_stat_from_log "SLOW" "$log_file")
    ELAPSED=$(extract_stat_from_log "ELAPSED" "$log_file" max)

    TOTAL=${TOTAL:-NA}
    OK=${OK:-NA}
    NOALGN=${NOALGN:-NA}
    LOWQ=${LOWQ:-NA}
    BADR=${BADR:-NA}
    SLOW=${SLOW:-NA}
    ELAPSED=${ELAPSED:-NA}

    # Logs from a failed/older PANDAseq build can contain non-numeric values.
    # Validate both counters before using Bash's integer comparison; otherwise
    # set -e may abort the whole stage while writing the per-sample summary.
    if [[ "$TOTAL" =~ ^[0-9]+$ && "$OK" =~ ^[0-9]+$ && "$TOTAL" -gt 0 ]]; then
        PERCENT=$(awk -v ok="$OK" -v total="$TOTAL" 'BEGIN { printf "%.2f", (ok / total) * 100 }')
    else
        PERCENT="NA"
    fi

    printf '%s\n' "${sample},${TOTAL},${OK},${NOALGN},${LOWQ},${BADR},${SLOW},${ELAPSED},${PERCENT},${MERGED_FASTA_RECORDS},${UNALIGNED_FASTA_RECORDS}" > "${STATS_FILE}.row.${i}"
    info "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} 完成 merged=${PERCENT}%"
    return 0
}

record_pandaseq_result() {
    local pid="$1" sample="$2" idx="$3" status
    if wait "$pid"; then
        [[ -f "${STATS_FILE}.row.${idx}" ]] && cat "${STATS_FILE}.row.${idx}" >> "$STATS_FILE"
        rm -f "${STATS_FILE}.row.${idx}"
        ((SUCCESS_COUNT += 1))
        ((COMPLETED_COUNT += 1))
        info "[pandaseq progress] completed=${COMPLETED_COUNT}/${TOTAL_SAMPLES} percent=$((COMPLETED_COUNT * 100 / TOTAL_SAMPLES)) sample=${sample} status=OK"
    else
        status=$?
        rm -f "${STATS_FILE}.row.${idx}"
        ((FAIL_COUNT += 1))
        ((COMPLETED_COUNT += 1))
        warn "[pandaseq progress] completed=${COMPLETED_COUNT}/${TOTAL_SAMPLES} percent=$((COMPLETED_COUNT * 100 / TOTAL_SAMPLES)) sample=${sample} status=FAILED exit=${status}"
    fi
}

for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    sample="${SAMPLE_NAMES[i]}"
    while (( ${#PANDASEQ_PIDS[@]} >= MAX_PARALLEL_SAMPLES )); do
        pid="${PANDASEQ_PIDS[0]}"; PANDASEQ_PIDS=("${PANDASEQ_PIDS[@]:1}")
        record_pandaseq_result "$pid" "${PANDASEQ_PID_SAMPLE[$pid]%%|*}" "${PANDASEQ_PID_SAMPLE[$pid]##*|}"
        unset 'PANDASEQ_PID_SAMPLE[$pid]'
    done
    pandaseq_one_sample "$i" &
    pid=$!; PANDASEQ_PIDS+=("$pid"); PANDASEQ_PID_SAMPLE["$pid"]="${sample}|${i}"
done
while (( ${#PANDASEQ_PIDS[@]} > 0 )); do
    pid="${PANDASEQ_PIDS[0]}"; PANDASEQ_PIDS=("${PANDASEQ_PIDS[@]:1}")
    record_pandaseq_result "$pid" "${PANDASEQ_PID_SAMPLE[$pid]%%|*}" "${PANDASEQ_PID_SAMPLE[$pid]##*|}"
    unset 'PANDASEQ_PID_SAMPLE[$pid]'
done

info "pandaseq 结束 success=${SUCCESS_COUNT} fail=${FAIL_COUNT} total=${TOTAL_SAMPLES}"
info "统计文件: ${STATS_FILE}"
(( FAIL_COUNT == 0 )) || exit 1
