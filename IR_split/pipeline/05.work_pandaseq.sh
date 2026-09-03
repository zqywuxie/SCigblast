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
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    . "$CONFIG_FILE"
    set +a
fi
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${BRANCH_ROOT}/output}"

DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -n "$DATASET_LABEL" ]]; then
    DEFAULT_PANDA_INPUT="${OUTPUT_ROOT}/04.clean_data/${DATASET_LABEL}"
    DEFAULT_PANDA_OUTPUT="${OUTPUT_ROOT}/05.pandaseq/${DATASET_LABEL}"
else
    DEFAULT_PANDA_INPUT="${OUTPUT_ROOT}/04.clean_data"
    DEFAULT_PANDA_OUTPUT="${OUTPUT_ROOT}/05.pandaseq"
fi
DATA_DIRS=("$DEFAULT_PANDA_INPUT")
DATA_DIR_SET=0
OUTPUT_DIR="${SCIGBLAST_PANDASEQ_OUTPUT_DIR:-$DEFAULT_PANDA_OUTPUT}"
STATS_FILE="${OUTPUT_DIR}/pandaseq_summary.csv"

FORWARD_INPUT_SUFFIX="_R1.fq.gz"
REVERSE_INPUT_SUFFIX="_R2.fq.gz"
PANDASEQ_INPUT_VERSION=4
PANDASEQ_MARKER_VERSION=2
NUM_THREADS="${SCIGBLAST_PANDASEQ_THREADS:-64}"
PANDASEQ_BIN="${SCIGBLAST_PANDASEQ_BIN:-pandaseq}"
PANDASEQ_OUTPUT_FORMAT="${SCIGBLAST_PANDASEQ_OUTPUT_FORMAT:-fasta}"
MEMORY_LIMIT_GB="${SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB:-8}"
MEMORY_LIMIT_MB=0
MONITOR_INTERVAL_SEC=5
MAX_PARALLEL_SAMPLES="${SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES:-2}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"

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
        info "[IR][pandaseq] heartbeat task=${label} elapsed=${elapsed}s rss=$(kb_to_mb "$current_rss")MB peak=$(kb_to_mb "$peak_rss")MB"
        sleep "$MONITOR_INTERVAL_SEC"
    done

    wait "$cmd_pid"
    exit_code=$?
    elapsed=$(( $(date +%s) - start_ts ))
    info "[IR][pandaseq] heartbeat_done task=${label} exit=${exit_code} elapsed=${elapsed}s peak=$(kb_to_mb "$peak_rss")MB"
    return "$exit_code"
}

# ---- 输入/输出指纹与统计 ----

file_metadata() {
    local path="$1"
    if stat -c '%n|%s|%Y' "$path" 2>/dev/null; then
        return 0
    fi
    stat -f '%N|%z|%m' "$path"
}

pair_fingerprint() {
    local forward_file="$1"
    local reverse_file="$2"
    local payload
    payload=$(
        printf 'pandaseq_input_version=%s\n' "$PANDASEQ_INPUT_VERSION"
        printf 'forward_suffix=%s\nreverse_suffix=%s\n' "$FORWARD_INPUT_SUFFIX" "$REVERSE_INPUT_SUFFIX"
        printf 'threads=%s\n' "$NUM_THREADS"
        printf 'memory_limit_mb=%s\nmonitor_interval_sec=%s\n' "$MEMORY_LIMIT_MB" "$MONITOR_INTERVAL_SEC"
        printf 'flags=-B -T %s\n' "$NUM_THREADS"
        file_metadata "$forward_file"
        file_metadata "$reverse_file"
    )
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$payload" | sha256sum | awk '{print $1}'
    else
        printf '%s' "$payload" | shasum -a 256 | awk '{print $1}'
    fi
}

marker_value() {
    local marker="$1"
    local key="$2"
    awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$marker"
}

marker_matches() {
    local marker="$1"
    local fingerprint="$2"
    [[ -f "$marker" ]] || return 1
    [[ "$(marker_value "$marker" marker_version)" == "$PANDASEQ_MARKER_VERSION" ]] || return 1
    [[ "$(marker_value "$marker" input_fingerprint)" == "$fingerprint" ]] || return 1
    [[ "$(marker_value "$marker" input_version)" == "$PANDASEQ_INPUT_VERSION" ]] || return 1
}

count_fastq_records() {
    local path="$1"
    local line_count
    if [[ "$path" == *.gz ]]; then
        line_count=$(gzip -dc -- "$path" | awk 'END {print NR + 0}') || return 1
    else
        line_count=$(awk 'END {print NR + 0}' "$path") || return 1
    fi
    awk -v lines="$line_count" 'BEGIN {print int(lines / 4)}'
}

extract_stat_from_log() {
    local key="$1"
    local log_file="$2"
    local reducer="${3:-sum}"

    # With a multi-threaded PANDAseq build each worker writes its own final
    # STAT block.  The first field is normally a worker token such as
    # ``0x7f...:12``.  Keep the last value for each worker (progress/status
    # lines can occur earlier), then reduce the workers.  ELAPSED is a wall
    # clock value, so callers request ``max`` for that field; counters are
    # summed.  If a legacy build omits worker tokens, retain its last value
    # rather than accidentally summing progress lines.
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
  --memory-limit-gb N            每个任务虚拟内存上限（GB），默认 8
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
if ! [[ "$NUM_THREADS" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_PANDASEQ_THREADS must be a positive integer"
fi
if ! [[ "$MEMORY_LIMIT_GB" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_PANDASEQ_MEMORY_LIMIT_GB must be a positive integer"
fi
MEMORY_LIMIT_MB=$((MEMORY_LIMIT_GB * 1024))
if ! [[ "$MAX_PARALLEL_SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_PANDASEQ_MAX_PARALLEL_SAMPLES must be a positive integer"
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
if (( MAX_PARALLEL_SAMPLES * NUM_THREADS > THREAD_BUDGET )); then
    die "PANDAseq concurrency exceeds thread budget: ${MAX_PARALLEL_SAMPLES} x ${NUM_THREADS} > ${THREAD_BUDGET}"
fi
if (( MAX_PARALLEL_SAMPLES * MEMORY_LIMIT_GB > MEMORY_BUDGET_GB )); then
    die "PANDAseq concurrency exceeds memory budget: ${MAX_PARALLEL_SAMPLES} x ${MEMORY_LIMIT_GB}GB > ${MEMORY_BUDGET_GB}GB"
fi
mkdir -p "$OUTPUT_DIR"

collect_samples
TOTAL_SAMPLES=${#SAMPLE_NAMES[@]}
(( TOTAL_SAMPLES > 0 )) || die "在 ${DATA_DIRS[*]} 中未找到可配对的样本"

info "开始 pandaseq，样本数=${TOTAL_SAMPLES} data_dirs=${DATA_DIRS[*]} output=${OUTPUT_DIR}"
info "后缀映射 forward=${FORWARD_INPUT_SUFFIX} reverse=${REVERSE_INPUT_SUFFIX}"
info "线程=${NUM_THREADS} 内存限制=${MEMORY_LIMIT_MB}MB 监控间隔=${MONITOR_INTERVAL_SEC}s"

echo "Sample,Total_Reads,OK_Reads,NOALGN,LOWQ,BADR,SLOW,Elapsed_sec,Merged_Percent,Merged_FASTA_Records,Unaligned_FASTA_Records" > "$STATS_FILE"

SUCCESS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

process_pandaseq_sample() {
    local i="$1" step="$2" step_pct="$3"
    local sample forward_file reverse_file source_type sample_relative_parent
    local sample_output_dir out_fasta log_file unaligned_file done_marker legacy_done_marker result_file marker_tmp result_tmp
    local input_fingerprint marker_key run_start_epoch run_elapsed_sec
    local merged_records unaligned_records total ok noalgn lowq badr slow elapsed percent
    local was_skipped=0

    sample="${SAMPLE_NAMES[i]}"
    forward_file="${SAMPLE_FORWARD[$sample]}"
    reverse_file="${SAMPLE_REVERSE[$sample]}"
    source_type="${SAMPLE_SOURCE[$sample]}"
    sample_relative_parent="${SAMPLE_REL_PARENT[$sample]:-}"
    sample_output_dir="${OUTPUT_DIR}"
    [[ -n "$sample_relative_parent" ]] && sample_output_dir="${OUTPUT_DIR}/${sample_relative_parent}"
    mkdir -p "$sample_output_dir"

    out_fasta="${sample_output_dir}/${sample}_merged.${PANDASEQ_OUTPUT_EXT}"
    log_file="${sample_output_dir}/${sample}_pandaseq.log"
    unaligned_file="${sample_output_dir}/${sample}_unaligned.${PANDASEQ_UNALIGNED_EXT}"
    legacy_done_marker="${sample_output_dir}/.pandaseq.DONE"

    if [[ ! -f "$forward_file" || ! -f "$reverse_file" ]]; then
        warn "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} 缺少输入文件，跳过"
        warn "[IR][pandaseq] worker_done sample=${sample} status=FAILED"
        return 1
    fi

    input_fingerprint="$(pair_fingerprint "$forward_file" "$reverse_file")"
    marker_key="${input_fingerprint:0:16}"
    done_marker="${sample_output_dir}/.pandaseq.DONE.${marker_key}"
    result_file="${sample_output_dir}/.pandaseq_summary_row.${marker_key}"
    rm -f "$result_file"
    run_start_epoch="$(date +%s)"
    info "[IR][pandaseq] worker_start sample=${sample} step=${step}/${TOTAL_SAMPLES}"
    if marker_matches "$done_marker" "$input_fingerprint" \
        && [[ -f "$out_fasta" && -f "$unaligned_file" ]]; then
        was_skipped=1
    elif [[ -f "$legacy_done_marker" && -f "$out_fasta" && -f "$unaligned_file" ]]; then
        marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
        {
            printf 'marker_version=%s\n' "$PANDASEQ_MARKER_VERSION"
            printf 'input_version=%s\n' "$PANDASEQ_INPUT_VERSION"
            printf 'input_fingerprint=%s\n' "$input_fingerprint"
            printf 'output_format=%s\n' "$PANDASEQ_OUTPUT_FORMAT"
            printf 'timestamp=%s\n' "$(date -Iseconds)"
            printf 'migrated_from_legacy=1\n'
        } > "$marker_tmp"
        mv -f "$marker_tmp" "$done_marker"
        was_skipped=1
    else
        if ! run_with_monitor_logged "pandaseq ${sample}" "$log_file" \
            "$PANDASEQ_BIN" "${PANDASEQ_FORMAT_FLAG[@]}" -f "$forward_file" -r "$reverse_file" -B \
                     -w "$out_fasta" -U "$unaligned_file" -T "$NUM_THREADS"; then
            warn "[${step}/${TOTAL_SAMPLES} ${step_pct}%] 样本 ${sample} pandaseq 执行失败"
            warn "[IR][pandaseq] worker_done sample=${sample} status=FAILED"
            return 1
        fi
        run_elapsed_sec=$(( $(date +%s) - run_start_epoch ))
        {
            printf 'marker_version=%s\n' "$PANDASEQ_MARKER_VERSION"
            printf 'input_version=%s\n' "$PANDASEQ_INPUT_VERSION"
            printf 'input_fingerprint=%s\n' "$input_fingerprint"
            printf 'output_format=%s\n' "$PANDASEQ_OUTPUT_FORMAT"
            printf 'elapsed_sec=%s\n' "$run_elapsed_sec"
            printf 'timestamp=%s\n' "$(date -Iseconds)"
        } > "${done_marker}.tmp.${BASHPID:-$$}"
        mv -f "${done_marker}.tmp.${BASHPID:-$$}" "$done_marker"
    fi

    merged_records=$(count_assembled_records "$out_fasta")
    unaligned_records=$(count_assembled_records "$unaligned_file")
    total=$(extract_stat_from_log "READS" "$log_file")
    ok=$(extract_stat_from_log "OK" "$log_file")
    noalgn=$(extract_stat_from_log "NOALGN" "$log_file")
    lowq=$(extract_stat_from_log "LOWQ" "$log_file")
    badr=$(extract_stat_from_log "BADR" "$log_file")
    slow=$(extract_stat_from_log "SLOW" "$log_file")
    elapsed=$(extract_stat_from_log "ELAPSED" "$log_file" max)

    if ! [[ "$total" =~ ^[0-9]+$ ]]; then total="$(count_fastq_records "$forward_file")"; fi
    if ! [[ "$ok" =~ ^[0-9]+$ ]]; then ok="$merged_records"; fi
    if ! [[ "$noalgn" =~ ^[0-9]+$ ]]; then noalgn="$unaligned_records"; fi
    if ! [[ "$lowq" =~ ^[0-9]+$ ]]; then lowq="NA"; fi
    if ! [[ "$badr" =~ ^[0-9]+$ ]]; then badr="NA"; fi
    if ! [[ "$slow" =~ ^[0-9]+$ ]]; then slow="NA"; fi
    if ! [[ "$elapsed" =~ ^[0-9]+$ ]]; then elapsed="$(marker_value "$done_marker" elapsed_sec)"; fi
    total=${total:-NA}; ok=${ok:-NA}; noalgn=${noalgn:-NA}
    lowq=${lowq:-NA}; badr=${badr:-NA}; slow=${slow:-NA}; elapsed=${elapsed:-NA}
    if [[ "$total" =~ ^[0-9]+$ && "$ok" =~ ^[0-9]+$ && "$total" -gt 0 ]]; then
        percent=$(awk -v ok="$ok" -v total="$total" 'BEGIN { printf "%.2f", (ok / total) * 100 }')
    else
        percent="NA"
    fi

    result_tmp="${result_file}.tmp.${BASHPID:-$$}"
    printf '%s\n' "${sample},${total},${ok},${noalgn},${lowq},${badr},${slow},${elapsed},${percent},${merged_records},${unaligned_records}" > "$result_tmp"
    mv -f "$result_tmp" "$result_file"
    if (( was_skipped )); then
        info "[IR][pandaseq] worker_done sample=${sample} status=SKIPPED merged=${percent}%"
        return 2
    fi
    info "[IR][pandaseq] worker_done sample=${sample} status=OK merged=${percent}%"
    return 0
}

record_pandaseq_status() {
    local status="${1:-1}"
    if (( status == 0 )); then
        ((SUCCESS_COUNT += 1))
    elif (( status == 2 )); then
        ((SUCCESS_COUNT += 1))
        ((SKIP_COUNT += 1))
    else
        ((FAIL_COUNT += 1))
    fi
}

wait_pandaseq_job() {
    local pid="$1" status=0
    if wait "$pid"; then status=0; else status=$?; fi
    record_pandaseq_status "$status"
}

declare -A PANDASEQ_ACTIVE_PIDS=()
WAIT_SUPPORTS_P=0
if help wait 2>/dev/null | grep -q -- '-p'; then WAIT_SUPPORTS_P=1; fi
wait_pandaseq_any_job() {
    local completed_pid="" status=0 pid
    if (( WAIT_SUPPORTS_P )); then
        if wait -n -p completed_pid 2>/dev/null; then status=0; else status=$?; fi
        if [[ "$completed_pid" =~ ^[0-9]+$ ]] && [[ -n "${PANDASEQ_ACTIVE_PIDS[$completed_pid]+yes}" ]]; then
            unset "PANDASEQ_ACTIVE_PIDS[$completed_pid]"
            local -a remaining=()
            for pid in "${PANDASEQ_PIDS[@]}"; do
                [[ "$pid" == "$completed_pid" ]] || remaining+=("$pid")
            done
            PANDASEQ_PIDS=("${remaining[@]}")
            record_pandaseq_status "$status"
            return 0
        fi
        warn "wait -n returned an unknown PANDAseq PID; keeping tracked jobs unchanged"
        return 1
    fi
    pid="${PANDASEQ_PIDS[0]}"
    unset "PANDASEQ_ACTIVE_PIDS[$pid]"
    wait_pandaseq_job "$pid"
    PANDASEQ_PIDS=("${PANDASEQ_PIDS[@]:1}")
}

info "[IR][pandaseq] stage_start total=${TOTAL_SAMPLES} max_parallel=${MAX_PARALLEL_SAMPLES} threads=${NUM_THREADS}"
info "样本级并发=${MAX_PARALLEL_SAMPLES}，线程预算=${THREAD_BUDGET}，内存预算=${MEMORY_BUDGET_GB}GB"
declare -a PANDASEQ_PIDS=()
for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    step=$((i + 1))
    step_pct=$((step * 100 / TOTAL_SAMPLES))
    while (( ${#PANDASEQ_PIDS[@]} >= MAX_PARALLEL_SAMPLES )); do
        if ! wait_pandaseq_any_job; then
            wait_pandaseq_job "${PANDASEQ_PIDS[0]}"
            unset "PANDASEQ_ACTIVE_PIDS[${PANDASEQ_PIDS[0]}]"
            PANDASEQ_PIDS=("${PANDASEQ_PIDS[@]:1}")
        fi
    done
    process_pandaseq_sample "$i" "$step" "$step_pct" &
    PANDASEQ_PIDS+=("$!")
    PANDASEQ_ACTIVE_PIDS["${PANDASEQ_PIDS[-1]}"]=1
done
for pid in "${PANDASEQ_PIDS[@]}"; do
    wait_pandaseq_job "$pid"
done

# Workers publish one atomic row each; aggregate in deterministic sample
# order after all jobs finish to avoid concurrent CSV appends.
for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    sample="${SAMPLE_NAMES[i]}"
    forward_file="${SAMPLE_FORWARD[$sample]}"
    reverse_file="${SAMPLE_REVERSE[$sample]}"
    sample_relative_parent="${SAMPLE_REL_PARENT[$sample]:-}"
    sample_output_dir="${OUTPUT_DIR}"
    [[ -n "$sample_relative_parent" ]] && sample_output_dir="${OUTPUT_DIR}/${sample_relative_parent}"
    if [[ -f "$forward_file" && -f "$reverse_file" ]]; then
        input_fingerprint="$(pair_fingerprint "$forward_file" "$reverse_file")"
        result_file="${sample_output_dir}/.pandaseq_summary_row.${input_fingerprint:0:16}"
        [[ -s "$result_file" ]] && cat "$result_file" >> "$STATS_FILE"
    fi
done

info "[IR][pandaseq] completed=${SUCCESS_COUNT} failed=${FAIL_COUNT} skipped=${SKIP_COUNT} total=${TOTAL_SAMPLES}"
info "统计文件: ${STATS_FILE}"
(( FAIL_COUNT == 0 )) || exit 1
