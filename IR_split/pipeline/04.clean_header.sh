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
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    . "$CONFIG_FILE"
    set +a
fi
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${BRANCH_ROOT}/output}"

DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -n "$DATASET_LABEL" ]]; then
    DEFAULT_CLEAN_INPUT="${OUTPUT_ROOT}/03.IR_split_output/${DATASET_LABEL}"
    DEFAULT_CLEAN_OUTPUT="${OUTPUT_ROOT}/04.clean_data/${DATASET_LABEL}"
    DEFAULT_MATCH_SUMMARY="${OUTPUT_ROOT}/01.match/${DATASET_LABEL}/sample_barcode_summary.csv"
else
    DEFAULT_CLEAN_INPUT="${OUTPUT_ROOT}/03.IR_split_output"
    DEFAULT_CLEAN_OUTPUT="${OUTPUT_ROOT}/04.clean_data"
    DEFAULT_MATCH_SUMMARY="${OUTPUT_ROOT}/01.match/sample_barcode_summary.csv"
fi
INPUT_DIR="${SCIGBLAST_CLEAN_INPUT_DIR:-$DEFAULT_CLEAN_INPUT}"
OUTPUT_DIR="${SCIGBLAST_CLEAN_OUTPUT_DIR:-$DEFAULT_CLEAN_OUTPUT}"
MAPPING_SUMMARY="${SCIGBLAST_IR_MAPPING_SUMMARY:-$DEFAULT_MATCH_SUMMARY}"
FORWARD_INPUT_SUFFIX="_R1.fq.gz"
REVERSE_INPUT_SUFFIX="_R2.fq.gz"
FORWARD_OUTPUT_SUFFIX="_R1.fq.gz"
REVERSE_OUTPUT_SUFFIX="_R2.fq.gz"
MEMORY_LIMIT_GB="${SCIGBLAST_CLEAN_MEMORY_LIMIT_GB:-8}"
MEMORY_LIMIT_MB=0
MONITOR_INTERVAL_SEC=5
VERIFY_READ_NUMBERS=1
CLEAN_HEADER_VERSION=3
MAX_PARALLEL_SAMPLES="${SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES:-4}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"

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
        info "[IR][clean] heartbeat task=${label} elapsed=${elapsed}s rss=$(kb_to_mb "$current_rss")MB peak=$(kb_to_mb "$peak_rss")MB"
        sleep "$MONITOR_INTERVAL_SEC"
    done

    wait "$cmd_pid"
    exit_code=$?
    elapsed=$(( $(date +%s) - start_ts ))
    info "[IR][clean] heartbeat_done task=${label} exit=${exit_code} elapsed=${elapsed}s peak=$(kb_to_mb "$peak_rss")MB"
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

    if [[ -n "${SAMPLE_SOURCE[$sample_name]:-}" ]]; then
        warn "duplicate sample=${sample_name}, keep ${SAMPLE_SOURCE[$sample_name]} and skip ${source_type}"
        return 0
    fi

    SAMPLE_NAMES+=("$sample_name")
    SAMPLE_FORWARD["$sample_name"]="$forward_file"
    SAMPLE_REVERSE["$sample_name"]="$reverse_file"
    SAMPLE_SOURCE["$sample_name"]="$source_type"
    SAMPLE_REL_PARENT["$sample_name"]="$relative_parent"
}

declare -A FILE_SAMPLE_MAP=()

load_file_sample_map() {
    [[ -s "$MAPPING_SUMMARY" ]] || return 0
    while IFS=$'\t' read -r parent stem sample; do
        [[ -n "$parent" && -n "$stem" && -n "$sample" ]] || continue
        local key="${parent}|${stem}" current
        current="${FILE_SAMPLE_MAP[$key]:-}"
        if [[ -z "$current" ]]; then
            FILE_SAMPLE_MAP["$key"]="$sample"
        elif [[ ",${current}," != *",${sample},"* ]]; then
            FILE_SAMPLE_MAP["$key"]="${current},${sample}"
        fi
    done < <(python - "$MAPPING_SUMMARY" <<'PY'
import csv
import sys
from pathlib import Path

with open(sys.argv[1], encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
        if str(row.get("status", "")).strip().upper() != "OK":
            continue
        pair_id = str(row.get("pair_id", "")).strip().replace("\\", "/")
        sample = str(row.get("sample_id", "")).strip()
        if pair_id and sample:
            pair = Path(pair_id)
            print(f"{pair.parent.name}\t{pair.name}\t{sample}")
PY
    )
}

mapped_sample_for_pair() {
    local parent="$1" stem="$2" value
    value="${FILE_SAMPLE_MAP["${parent}|${stem}"]:-}"
    [[ -n "$value" && "$value" != *,* ]] || return 1
    printf '%s\n' "$value"
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
    local mapped_sample

    SAMPLE_NAMES=()
    declare -gA SAMPLE_FORWARD=()
    declare -gA SAMPLE_REVERSE=()
    declare -gA SAMPLE_SOURCE=()
    declare -gA SAMPLE_REL_PARENT=()

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
            # Legacy split output placed single-sample files directly under
            # a Lane directory (Lane04/<pair>_R1.fq.gz). Recover the
            # biological sample from the match summary and create a sample
            # leaf so downstream PANDAseq cannot merge the whole Lane.
            if [[ "$relative_parent" != */* ]]; then
                mapped_sample="$(mapped_sample_for_pair "$(basename "$parent_dir")" "$sample_base" || true)"
                if [[ -n "$mapped_sample" ]]; then
                    sample_name="$mapped_sample"
                    # Recover the canonical hierarchy for legacy flat split
                    # output: <Lane>/<pair_stem>/<sample_id>.  This keeps a
                    # Lane containing many physical pairs from being merged
                    # into one clean/PANDAseq sample.
                    relative_parent="${relative_parent}/${sample_base}/${mapped_sample}"
                    source_type="legacy-lane-flat:${relative_parent}"
                fi
            fi
        fi
        register_sample "$sample_name" "$forward_file" "$reverse_file" "$source_type" "$relative_parent"
    done < <(find "$input_dir" -type f -name "*${FORWARD_INPUT_SUFFIX}" -print0 | sort -z)
}

usage() {
    cat <<'EOF'
Usage:
  bash 04.clean_header.sh [options]

Options:
  --input-dir DIR                input FASTQ root directory
  --output-dir DIR               output directory
  FASTQ naming is fixed after fastp: *_R1.fq.gz and *_R2.fq.gz.
  --memory-limit-gb N            per-task virtual memory limit in GB, default: 8
  --monitor-interval-sec N       monitor interval in seconds, default: 5
  --disable-read-check           disable read1/read2 header check
  --help                         show this help message

Notes:
  This is the single clean-header implementation for both former variants:
    plain FASTQ and .gz FASTQ are both supported
    input filenames are canonical *_R1.fq.gz/*_R2.fq.gz after fastp
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
if ! [[ "$MEMORY_LIMIT_GB" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_CLEAN_MEMORY_LIMIT_GB must be a positive integer"
fi
MEMORY_LIMIT_MB=$((MEMORY_LIMIT_GB * 1024))
if ! [[ "$MAX_PARALLEL_SAMPLES" =~ ^[1-9][0-9]*$ ]]; then
    die "SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES must be a positive integer"
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
if (( MAX_PARALLEL_SAMPLES > THREAD_BUDGET )); then
    die "clean-header concurrency exceeds thread budget: ${MAX_PARALLEL_SAMPLES} x 1 > ${THREAD_BUDGET}"
fi
if (( MAX_PARALLEL_SAMPLES * MEMORY_LIMIT_GB > MEMORY_BUDGET_GB )); then
    die "clean-header concurrency exceeds memory budget: ${MAX_PARALLEL_SAMPLES} x ${MEMORY_LIMIT_GB}GB > ${MEMORY_BUDGET_GB}GB"
fi
mkdir -p "$OUTPUT_DIR"

load_file_sample_map
collect_samples "$INPUT_DIR"
TOTAL_SAMPLES=${#SAMPLE_NAMES[@]}
(( TOTAL_SAMPLES > 0 )) || die "no paired samples found in ${INPUT_DIR}"

info "start processing total=${TOTAL_SAMPLES} input=${INPUT_DIR} output=${OUTPUT_DIR}"
info "suffix mapping forward=${FORWARD_INPUT_SUFFIX} reverse=${REVERSE_INPUT_SUFFIX}"
info "memory_limit=${MEMORY_LIMIT_MB}MB monitor_interval=${MONITOR_INTERVAL_SEC}s"

SUCCESS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

clean_sample() {
    local i="$1"
    local step="$2"
    local step_pct="$3"
    local sample_name forward_file reverse_file sample_relative_parent output_base
    local sample_output_dir forward_out reverse_out done_marker legacy_done_marker marker_tmp input_fp
    sample_name="${SAMPLE_NAMES[i]}"
    forward_file="${SAMPLE_FORWARD[$sample_name]}"
    reverse_file="${SAMPLE_REVERSE[$sample_name]}"
    sample_relative_parent="${SAMPLE_REL_PARENT[$sample_name]:-}"
    sample_output_dir="${OUTPUT_DIR}"
    [[ -n "$sample_relative_parent" ]] && sample_output_dir="${OUTPUT_DIR}/${sample_relative_parent}"
    output_base="$(basename "$forward_file")"
    output_base="${output_base%${FORWARD_INPUT_SUFFIX}}"
    forward_out="${sample_output_dir}/${output_base}${FORWARD_OUTPUT_SUFFIX}"
    reverse_out="${sample_output_dir}/${output_base}${REVERSE_OUTPUT_SUFFIX}"
    legacy_done_marker="${sample_output_dir}/.clean.DONE"

    info "[IR][clean] worker_start sample=${sample_name} step=${step}/${TOTAL_SAMPLES}"

    mkdir -p "$sample_output_dir"
    validate_pair_orientation "$forward_file" "$reverse_file"
    input_fp="$(input_fingerprint "$forward_file" "$reverse_file")"
    done_marker="${sample_output_dir}/.clean.DONE.${input_fp:0:16}"

    if [[ -f "$done_marker" && -f "$forward_out" && -f "$reverse_out" ]] \
        && [[ "$(marker_value "$done_marker" version)" == "$CLEAN_HEADER_VERSION" ]] \
        && [[ "$(marker_value "$done_marker" input_fingerprint)" == "$input_fp" ]]; then
        info "[IR][clean] worker_done sample=${sample_name} status=SKIPPED"
        return 2
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
        info "[IR][clean] worker_done sample=${sample_name} status=SKIPPED migrated_marker=1"
        return 2
    fi

    if ! run_with_monitor "clean-forward ${sample_name}" clean_fastq_v17 "$forward_file" "$forward_out" "1"; then
        warn "sample ${sample_name} forward clean failed"
        warn "[IR][clean] worker_done sample=${sample_name} status=FAILED"
        return 1
    fi

    if ! run_with_monitor "clean-reverse ${sample_name}" clean_fastq_v17 "$reverse_file" "$reverse_out" "2"; then
        warn "sample ${sample_name} reverse clean failed"
        warn "[IR][clean] worker_done sample=${sample_name} status=FAILED"
        return 1
    fi

    {
        printf 'version=%s\n' "$CLEAN_HEADER_VERSION"
        printf 'input_fingerprint=%s\n' "$input_fp"
        printf 'timestamp=%s\n' "$(date -Iseconds)"
    } > "${done_marker}.tmp.${BASHPID:-$$}"
    mv -f "${done_marker}.tmp.${BASHPID:-$$}" "$done_marker"
    info "[IR][clean] worker_done sample=${sample_name} status=OK"
    return 0
}

record_clean_status() {
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

wait_clean_job() {
    local pid="$1" status=0
    if wait "$pid"; then status=0; else status=$?; fi
    record_clean_status "$status"
}

declare -A CLEAN_ACTIVE_PIDS=()
WAIT_SUPPORTS_P=0
if help wait 2>/dev/null | grep -q -- '-p'; then WAIT_SUPPORTS_P=1; fi
wait_clean_any_job() {
    local completed_pid="" status=0 pid
    if (( WAIT_SUPPORTS_P )); then
        if wait -n -p completed_pid 2>/dev/null; then status=0; else status=$?; fi
        if [[ "$completed_pid" =~ ^[0-9]+$ ]] && [[ -n "${CLEAN_ACTIVE_PIDS[$completed_pid]+yes}" ]]; then
            unset "CLEAN_ACTIVE_PIDS[$completed_pid]"
            local -a remaining=()
            for pid in "${CLEAN_PIDS[@]}"; do
                [[ "$pid" == "$completed_pid" ]] || remaining+=("$pid")
            done
            CLEAN_PIDS=("${remaining[@]}")
            record_clean_status "$status"
            return 0
        fi
        warn "wait -n returned an unknown clean PID; keeping tracked jobs unchanged"
        return 1
    fi
    pid="${CLEAN_PIDS[0]}"
    unset "CLEAN_ACTIVE_PIDS[$pid]"
    wait_clean_job "$pid"
    CLEAN_PIDS=("${CLEAN_PIDS[@]:1}")
}

info "[IR][clean] stage_start total=${TOTAL_SAMPLES} max_parallel=${MAX_PARALLEL_SAMPLES}"
info "样本级并发=${MAX_PARALLEL_SAMPLES}，线程预算=${THREAD_BUDGET}，内存预算=${MEMORY_BUDGET_GB}GB"
declare -a CLEAN_PIDS=()
for ((i = 0; i < TOTAL_SAMPLES; i++)); do
    step=$((i + 1))
    step_pct=$((step * 100 / TOTAL_SAMPLES))
    while (( ${#CLEAN_PIDS[@]} >= MAX_PARALLEL_SAMPLES )); do
        if ! wait_clean_any_job; then
            wait_clean_job "${CLEAN_PIDS[0]}"
            unset "CLEAN_ACTIVE_PIDS[${CLEAN_PIDS[0]}]"
            CLEAN_PIDS=("${CLEAN_PIDS[@]:1}")
        fi
    done
    clean_sample "$i" "$step" "$step_pct" &
    CLEAN_PIDS+=("$!")
    CLEAN_ACTIVE_PIDS["${CLEAN_PIDS[-1]}"]=1
done
for pid in "${CLEAN_PIDS[@]}"; do
    wait_clean_job "$pid"
done

info "[IR][clean] completed=${SUCCESS_COUNT} failed=${FAIL_COUNT} skipped=${SKIP_COUNT} total=${TOTAL_SAMPLES}"
(( FAIL_COUNT == 0 )) || exit 1
