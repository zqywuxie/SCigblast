#!/bin/bash
set -euo pipefail

# Pipeline order: 02.run_fastp.sh -> 04.clean_header.sh -> 05.work_pandaseq.sh

# =============================================================================
# Step 02: clean FASTQ headers after fastp
#   - 去除 CASAVA 1.8+ 格式中 # 及其后的注释字段
#   - 解析 ":" 分隔的 header 字段，从第4个字段起剥离字母和多余前导零
#   - 在每个 read header 末尾追加 /1 (forward) 或 /2 (reverse) 标识符
#   - 流式管道处理，无临时文件
#   - 输入同时支持普通 FASTQ 和 gzip FASTQ
#   - 输入后缀通过命令行参数配置，不再维护 1.7/1.8 两套脚本
#   - 样本发现：两级搜索 (flat-file + 子目录)，同名样本优先保留 flat-file
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

INPUT_DIR="$(stage_root 03.prefilter_data)"
OUTPUT_DIR="$(stage_root 04.clean_data)"
FORWARD_INPUT_SUFFIX="_R1.fq.gz"
REVERSE_INPUT_SUFFIX="_R2.fq.gz"
FORWARD_OUTPUT_SUFFIX="_R1.fq.gz"
REVERSE_OUTPUT_SUFFIX="_R2.fq.gz"
MEMORY_LIMIT_GB="${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-8}"
MEMORY_LIMIT_MB=0
MAX_PARALLEL_SAMPLES="${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-4}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MONITOR_INTERVAL_SEC=5
VERIFY_READ_NUMBERS=1
CLEAN_HEADER_VERSION=3

timestamp() {
    date '+%F %T'
}

log() {
    local level="$1"
    shift
    printf '[%s] [%s] %s\n' "$(timestamp)" "$level" "$*"
}

info() {
    log INFO "$@"
}

warn() {
    log WARN "$@"
}

die() {
    log ERROR "$@"
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

kb_to_mb() {
    awk -v kb="${1:-0}" 'BEGIN { printf "%.1f", kb / 1024 }'
}

apply_memory_limit() {
    if [[ "$MEMORY_LIMIT_MB" =~ ^[0-9]+$ ]] && (( MEMORY_LIMIT_MB > 0 )); then
        ulimit -v $((MEMORY_LIMIT_MB * 1024)) || die "failed to set memory limit ${MEMORY_LIMIT_MB}MB"
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

# Checkpoint helpers: invalidate a clean output when its canonical input or
# relevant clean configuration changes.
file_metadata() {
    local path="$1"
    if stat -c '%n|%s|%Y' "$path" 2>/dev/null; then return 0; fi
    stat -f '%N|%z|%m' "$path"
}
input_fingerprint() {
    local forward_file="$1" reverse_file="$2" payload
    payload=$(printf 'clean_version=%s\noutput_naming_schema=canonical_R1_R2_v1\nworkers=%s\n' "$CLEAN_HEADER_VERSION" "$MAX_PARALLEL_SAMPLES")
    payload+=$(printf '%s\n%s\n' "$(file_metadata "$forward_file")" "$(file_metadata "$reverse_file")")
    if command -v sha256sum >/dev/null 2>&1; then printf '%s' "$payload" | sha256sum | awk '{print $1}'; else printf '%s' "$payload" | shasum -a 256 | awk '{print $1}'; fi
}
marker_value() { local marker="$1" key="$2"; awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$marker"; }

stream_fastq() {
    local input_file="$1"
    if [[ "$input_file" == *.gz ]]; then
        gzip -dc "$input_file"
    else
        cat "$input_file"
    fi
}

read_first_line() {
    local input_file="$1"
    local line=""

    set +o pipefail
    if [[ "$input_file" == *.gz ]]; then
        line=$(gzip -dc "$input_file" 2>/dev/null | { IFS= read -r first || true; printf '%s\n' "$first"; })
    else
        IFS= read -r line < "$input_file" || true
    fi
    set -o pipefail

    printf '%s\n' "$line"
}

extract_read_number() {
    local header
    header=$(read_first_line "$1")
    awk '{
        split($2, parts, ":")
        if (parts[1] ~ /^[12]$/) {
            print parts[1]
        }
    }' <<<"$header"
}

validate_pair_orientation() {
    local forward_file="$1"
    local reverse_file="$2"
    local forward_num
    local reverse_num

    if [[ "$VERIFY_READ_NUMBERS" != "1" ]]; then
        return 0
    fi

    forward_num=$(extract_read_number "$forward_file")
    reverse_num=$(extract_read_number "$reverse_file")

    if [[ -n "$forward_num" && "$forward_num" != "1" ]]; then
        warn "forward file $(basename "$forward_file") header shows read${forward_num}, expected read1"
    fi

    if [[ -n "$reverse_num" && "$reverse_num" != "2" ]]; then
        warn "reverse file $(basename "$reverse_file") header shows read${reverse_num}, expected read2"
    fi
}

register_sample() {
    local sample_name="$1"
    local forward_file="$2"
    local reverse_file="$3"
    local source_type="$4"
    local relative_parent="${5:-}"
    local file_stem="${6:-$sample_name}"

    if [[ -n "${SAMPLE_SOURCE[$sample_name]:-}" ]]; then
        warn "duplicate sample=${sample_name}, keep ${SAMPLE_SOURCE[$sample_name]} and skip ${source_type}"
        return 0
    fi

    SAMPLE_NAMES+=("$sample_name")
    SAMPLE_FORWARD["$sample_name"]="$forward_file"
    SAMPLE_REVERSE["$sample_name"]="$reverse_file"
    SAMPLE_SOURCE["$sample_name"]="$source_type"
    SAMPLE_REL_PARENT["$sample_name"]="$relative_parent"
    SAMPLE_FILE_STEM["$sample_name"]="$file_stem"
}

collect_samples() {
    local input_dir="$1"
    local forward_file
    local reverse_file
    local base
    local sample_base
    local parent_dir
    local relative_parent
    local sample_name
    local source_type

    SAMPLE_NAMES=()
    declare -gA SAMPLE_FORWARD=()
    declare -gA SAMPLE_REVERSE=()
    declare -gA SAMPLE_SOURCE=()
    declare -gA SAMPLE_REL_PARENT=()
    declare -gA SAMPLE_FILE_STEM=()

    while IFS= read -r -d '' forward_file; do
        base=$(basename "$forward_file")
        sample_base="${base%${FORWARD_INPUT_SUFFIX}}"
        parent_dir=$(dirname "$forward_file")
        reverse_file="${parent_dir}/${sample_base}${REVERSE_INPUT_SUFFIX}"
        if [[ ! -f "$reverse_file" ]]; then
            warn "missing paired file for ${forward_file}: ${reverse_file}"
            continue
        fi

        # Handles nested split output: <pair_stem>/<sample_id>/<FASTQ>.
        if [[ "$parent_dir" == "$input_dir" ]]; then
            sample_name="$sample_base"
            relative_parent=""
            source_type="flat-file"
        else
            relative_parent="${parent_dir#${input_dir}/}"
            sample_name=$(printf '%s' "$relative_parent" | sed 's#[/\\]#__#g')
            source_type="recursive:${relative_parent}"
        fi
        register_sample "$sample_name" "$forward_file" "$reverse_file" "$source_type" "$relative_parent" "$sample_base"
    done < <(find "$input_dir" -type f -name "*${FORWARD_INPUT_SUFFIX}" -print0 | sort -z)
}

usage() {
    cat <<'EOF'
Usage:
  bash 04.clean_header.sh [options]

Options:
  --input-dir DIR                input FASTQ root directory
  --output-dir DIR               output directory
  FASTQ naming is fixed after fastp/prefilter: *_R1.fq.gz and *_R2.fq.gz.
  --memory-limit-gb N            per-task virtual memory limit in GB, default: 8
  --monitor-interval-sec N       monitor interval in seconds, default: 5
  --disable-read-check           disable read1/read2 header check
  --help                         show this help message

Notes:
  This is the single clean-header implementation for both former variants:
    plain FASTQ and .gz FASTQ are both supported
    input filenames are canonical *_R1.fq.gz/*_R2.fq.gz after fastp/prefilter
    recursively collect paired files from INPUT_DIR, including
    <pair_stem>/<sample_id>/<FASTQ> produced for multi-sample files
    relative directories are flattened into unique downstream sample names
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir) INPUT_DIR="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --forward-input-suffix|--reverse-input-suffix|--forward-output-suffix|--reverse-output-suffix)
            die "$1 is no longer configurable; raw suffixes belong to 01.match_sample.py and downstream names are fixed _R1/_R2" ;;
        --memory-limit-gb) MEMORY_LIMIT_GB="$2"; shift 2 ;;
        --monitor-interval-sec) MONITOR_INTERVAL_SEC="$2"; shift 2 ;;
        --disable-read-check) VERIFY_READ_NUMBERS=0; shift ;;
        --help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

clean_fastq_v17() {
    local input_file="$1"
    local output_file="$2"

    # CASAVA 1.8以后的格式在header中增加了一个字段来区分read1和read2
    local read_suffix="$3"   # 新增参数：1 或 2

    if [[ "$output_file" == *.gz ]]; then
        stream_fastq "$input_file" | awk -v suffix="$read_suffix" '
        NR % 4 == 1 {
            token = $1
            sub(/#.*/, "", token)
            sub(/\/[12]$/, "", token)
            n = split(token, a, ":")
            limit = n >= 8 ? 7 : n

            for (i = 4; i <= limit; i++) {
                gsub(/[A-Za-z]/, "", a[i])
                sub(/^0+/, "", a[i])
                if (a[i] == "") a[i] = "0"
            }

            out = a[1]
            for (i = 2; i <= limit; i++) {
                out = out ":" a[i]
            }

            print "@" out "/" suffix
            next
        }
        { print }
    ' | gzip -c > "$output_file"
    else
        stream_fastq "$input_file" | awk -v suffix="$read_suffix" '
            NR % 4 == 1 {
                token = $1
                sub(/#.*/, "", token)
                sub(/\/[12]$/, "", token)
                n = split(token, a, ":")
                limit = n >= 8 ? 7 : n
                for (i = 4; i <= limit; i++) {
                    gsub(/[A-Za-z]/, "", a[i]); sub(/^0+/, "", a[i])
                    if (a[i] == "") a[i] = "0"
                }
                out = a[1]
                for (i = 2; i <= limit; i++) out = out ":" a[i]
                print "@" out "/" suffix; next
            }
            { print }
        ' > "$output_file"
    fi
}
            # if (NF > 1) {
            #     print out " " $2
            # } else {
            #     print out
            # }



require_command awk
require_command gzip
require_command ps
mkdir -p "$OUTPUT_DIR"

collect_samples "$INPUT_DIR"
TOTAL_SAMPLES=${#SAMPLE_NAMES[@]}
(( TOTAL_SAMPLES > 0 )) || die "no paired samples found in ${INPUT_DIR}"

info "start processing total=${TOTAL_SAMPLES} input=${INPUT_DIR} output=${OUTPUT_DIR}"
info "suffix mapping forward=${FORWARD_INPUT_SUFFIX} reverse=${REVERSE_INPUT_SUFFIX}"
info "memory_limit=${MEMORY_LIMIT_MB}MB monitor_interval=${MONITOR_INTERVAL_SEC}s max_parallel=${MAX_PARALLEL_SAMPLES}"

[[ "$MAX_PARALLEL_SAMPLES" =~ ^[1-9][0-9]*$ ]] || die "invalid clean parallel sample count"
[[ "$THREAD_BUDGET" =~ ^[1-9][0-9]*$ ]] || die "invalid parallel thread budget"
(( MAX_PARALLEL_SAMPLES <= THREAD_BUDGET )) || die "clean concurrency exceeds thread budget"
[[ "$MEMORY_LIMIT_GB" =~ ^[1-9][0-9]*$ ]] || die "SCIGBLAST_CLEAN_MEMORY_LIMIT_GB must be a positive integer"
MEMORY_LIMIT_MB=$((MEMORY_LIMIT_GB * 1024))
if (( MAX_PARALLEL_SAMPLES * MEMORY_LIMIT_GB > ${SCIGBLAST_MEMORY_BUDGET_GB:-300} )); then
    die "clean concurrency exceeds memory budget"
fi

SUCCESS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
COMPLETED_COUNT=0
declare -a CLEAN_PIDS=()
declare -A CLEAN_PID_SAMPLE=()

clean_one_sample() {
    local i="$1"
    step=$((i + 1))
    step_pct=$((step * 100 / TOTAL_SAMPLES))
    sample_name="${SAMPLE_NAMES[i]}"
    forward_file="${SAMPLE_FORWARD[$sample_name]}"
    reverse_file="${SAMPLE_REVERSE[$sample_name]}"
    sample_relative_parent="${SAMPLE_REL_PARENT[$sample_name]:-}"
    sample_file_stem="${SAMPLE_FILE_STEM[$sample_name]:-$sample_name}"
    sample_output_dir="${OUTPUT_DIR}"
    [[ -n "$sample_relative_parent" ]] && sample_output_dir="${OUTPUT_DIR}/${sample_relative_parent}"
    forward_out="${sample_output_dir}/${sample_file_stem}${FORWARD_OUTPUT_SUFFIX}"
    reverse_out="${sample_output_dir}/${sample_file_stem}${REVERSE_OUTPUT_SUFFIX}"
    legacy_done_marker="${sample_output_dir}/.clean.DONE"

    info "[clean worker ${step}/${TOTAL_SAMPLES} ${step_pct}%] start sample=${sample_name} source=${SAMPLE_SOURCE[$sample_name]}"

    mkdir -p "$sample_output_dir"
    validate_pair_orientation "$forward_file" "$reverse_file"
    input_fp="$(input_fingerprint "$forward_file" "$reverse_file")"
    done_marker="${sample_output_dir}/.clean.DONE.${input_fp:0:16}"

    if [[ -f "$done_marker" && -f "$forward_out" && -f "$reverse_out" ]] \
        && [[ "$(marker_value "$done_marker" version)" == "$CLEAN_HEADER_VERSION" ]] \
        && [[ "$(marker_value "$done_marker" input_fingerprint)" == "$input_fp" ]]; then
        info "[clean worker ${step}/${TOTAL_SAMPLES} ${step_pct}%] sample=${sample_name} already complete; skip"
        return 0
    fi
    if [[ ! -f "$done_marker" && -f "$legacy_done_marker" && -s "$forward_out" && -s "$reverse_out" ]]; then
        marker_tmp="${done_marker}.tmp.${BASHPID:-$$}"
        {
            printf 'version=%s\n' "$CLEAN_HEADER_VERSION"
            printf 'input_fingerprint=%s\n' "$input_fp"
            printf 'timestamp=%s\n' "$(date -Iseconds)"
            printf 'migrated_from_legacy=1\n'
        } > "$marker_tmp"
        mv -f "$marker_tmp" "$done_marker"
        info "[clean worker ${step}/${TOTAL_SAMPLES} ${step_pct}%] sample=${sample_name} already complete; migrated_marker=1"
        return 0
    fi

    if ! run_with_monitor "clean-forward ${sample_name}" clean_fastq_v17 "$forward_file" "$forward_out" "1"; then
        warn "sample ${sample_name} forward clean failed"
        return 1
    fi

    if ! run_with_monitor "clean-reverse ${sample_name}" clean_fastq_v17 "$reverse_file" "$reverse_out" "2"; then
        warn "sample ${sample_name} reverse clean failed"
        return 1
    fi

    {
        printf 'version=%s\n' "$CLEAN_HEADER_VERSION"
        printf 'input_fingerprint=%s\n' "$input_fp"
        printf 'timestamp=%s\n' "$(date -Iseconds)"
    } > "${done_marker}.tmp.${BASHPID:-$$}"
    mv -f "${done_marker}.tmp.${BASHPID:-$$}" "$done_marker"
    info "[clean worker ${step}/${TOTAL_SAMPLES} ${step_pct}%] sample=${sample_name} done"
    return 0
}

record_clean_result() {
    local pid="$1" sample="$2" status
    if wait "$pid"; then
        ((SUCCESS_COUNT += 1))
        ((COMPLETED_COUNT += 1))
        info "[clean progress] completed=${COMPLETED_COUNT}/${TOTAL_SAMPLES} percent=$((COMPLETED_COUNT * 100 / TOTAL_SAMPLES)) sample=${sample} status=OK"
    else
        status=$?
        ((FAIL_COUNT += 1))
        ((COMPLETED_COUNT += 1))
        warn "[clean progress] completed=${COMPLETED_COUNT}/${TOTAL_SAMPLES} percent=$((COMPLETED_COUNT * 100 / TOTAL_SAMPLES)) sample=${sample} status=FAILED exit=${status}"
    fi
}

for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    sample_name="${SAMPLE_NAMES[i]}"
    while (( ${#CLEAN_PIDS[@]} >= MAX_PARALLEL_SAMPLES )); do
        pid="${CLEAN_PIDS[0]}"; CLEAN_PIDS=("${CLEAN_PIDS[@]:1}")
        record_clean_result "$pid" "${CLEAN_PID_SAMPLE[$pid]}"
        unset 'CLEAN_PID_SAMPLE[$pid]'
    done
    clean_one_sample "$i" &
    pid=$!; CLEAN_PIDS+=("$pid"); CLEAN_PID_SAMPLE["$pid"]="$sample_name"
done
while (( ${#CLEAN_PIDS[@]} > 0 )); do
    pid="${CLEAN_PIDS[0]}"; CLEAN_PIDS=("${CLEAN_PIDS[@]:1}")
    record_clean_result "$pid" "${CLEAN_PID_SAMPLE[$pid]}"
    unset 'CLEAN_PID_SAMPLE[$pid]'
done

info "finished success=${SUCCESS_COUNT} fail=${FAIL_COUNT} skipped=${SKIP_COUNT} total=${TOTAL_SAMPLES}"
(( FAIL_COUNT == 0 )) || exit 1
