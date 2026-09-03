#!/bin/bash

set -u
set -o pipefail
shopt -s nullglob

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${BRANCH_ROOT}/output}"
CONFIG_FILE="${SCIGBLAST_CONFIG:-${SCRIPT_DIR}/00.pipeline_config.env}"
if [[ -f "$CONFIG_FILE" ]]; then
    set -a
    . "$CONFIG_FILE"
    set +a
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
IGBLAST_BIN="${SCIGBLAST_IGBLAST_BIN:-igblastn}"
DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
if [[ -n "$DATASET_LABEL" ]]; then
    DEFAULT_IGBLAST_INPUT="${OUTPUT_ROOT}/06.representative/${DATASET_LABEL}/representative_fasta"
    DEFAULT_IGBLAST_OUTPUT="${OUTPUT_ROOT}/07.igblastn_out/${DATASET_LABEL}"
    DEFAULT_CHAIN_SUMMARY="${OUTPUT_ROOT}/01.match/${DATASET_LABEL}/sample_barcode_summary.csv"
else
    DEFAULT_IGBLAST_INPUT="${OUTPUT_ROOT}/06.representative/representative_fasta"
    DEFAULT_IGBLAST_OUTPUT="${OUTPUT_ROOT}/07.igblastn_out"
    DEFAULT_CHAIN_SUMMARY="${OUTPUT_ROOT}/01.match/sample_barcode_summary.csv"
fi

# variables

MERGED_DIRS=(
    "$DEFAULT_IGBLAST_INPUT"
)        # IR representative FASTA directory
INPUT_MODE="representative"                                # representative | pandaseq
DATA_DIR_SET=1
OUTPUT_DIR="${SCIGBLAST_IGBLAST_OUTPUT_DIR:-$DEFAULT_IGBLAST_OUTPUT}"             # igblastn output directory
DB_DIR="${SCIGBLAST_IGBLAST_DB_DIR:-/data/scAnalyis/Scigblast/igblast}"          # igblast database directory
SPECIES="${SCIGBLAST_IGBLAST_SPECIES:-human}"                                  # "human" "mouse" "rhesus_monkey" "rat"
db_cell_type="BOTH"                                                      # "BCR" "TCR" "BOTH" "BCR,TCR"
chains_input="ALL"                                                      # TCR chains: "ALL" or "TRA,TRB,TRG,TRD"
bcr_chains_input="ALL"                                                  # BCR chains: "ALL" or "IGH,IGK,IGL"
declare -a CHAINS=()                                                    # resolved TCR chain list
declare -a BCR_CHAINS=()                                                # resolved BCR chain list
index=""
ig_cell_type=""
E_threshold='0.0001'                                                    # Default = `20'
NUM_THREADS="${SCIGBLAST_IGBLAST_THREADS:-8}"
MEMORY_LIMIT_GB="${SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB:-96}"              # 0 means disabled
MEMORY_BUDGET_GB="${SCIGBLAST_MEMORY_BUDGET_GB:-300}"
THREAD_BUDGET="${SCIGBLAST_PARALLEL_THREAD_BUDGET:-128}"
MAX_THREAD_BUDGET="${SCIGBLAST_MAX_THREAD_BUDGET:-640}"
MONITOR_INTERVAL_SEC='5'                                                # 0 means disabled
# Keep raw IgBLAST batches and job artifacts by default. They are useful for
# audit/resume and this branch does not automatically delete intermediates.
# Set to 0 only when explicit cleanup is requested.
KEEP_INTERMEDIATE="${SCIGBLAST_IGBLAST_KEEP_INTERMEDIATE:-1}"
MAX_PARALLEL_JOBS="${SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS:-2}"           # max concurrent igblastn / classify jobs
BATCH_FASTA="${SCIGBLAST_IGBLAST_BATCH_FASTA:-500}"                     # barcode FASTAs per igblastn batch
MONITOR_DIR=""
SUMMARY_FILE=""
# A direct-merge run can write results under a new output root while reusing
# the original match manifest.  Keep the historical default for the normal
# representative pipeline, but allow the caller to point at that manifest.
CHAIN_SUMMARY="${SCIGBLAST_IGBLAST_CHAIN_SUMMARY:-$DEFAULT_CHAIN_SUMMARY}"

declare -A SAMPLE_CHAIN_MAP=()
declare -A FLAT_SAMPLE_MAP=()
declare -A FLAT_SAMPLE_AMBIGUOUS=()
if [[ -f "$CHAIN_SUMMARY" ]]; then
    while IFS=$'\t' read -r _sample _chains; do
        [[ -n "$_sample" && -n "$_chains" ]] || continue
        SAMPLE_CHAIN_MAP["$_sample"]="$_chains"
    done < <(python -c 'import csv,sys; f=open(sys.argv[1],encoding="utf-8-sig",newline=""); r=csv.DictReader(f); d={}; [d.setdefault(row.get("sample_id",""),set()).update(filter(None,row.get("igblast_chains","").split(","))) for row in r if row.get("sample_id") and row.get("igblast_chains")]; [print(k+"\t"+",".join(sorted(v))) for k,v in d.items()]' "$CHAIN_SUMMARY")
fi

# In flat PANDAseq output the FASTA basename is the physical pair stem, not
# the biological sample ID.  Build an explicit pair-stem -> sample mapping
# from the file-level match summary so Chain filtering never relies on
# guessing a sample name from ``*_merged.fasta``.
load_flat_sample_map() {
    [[ -f "$CHAIN_SUMMARY" ]] || return 0
    while IFS=$'\t' read -r pair_stem sample_id; do
        [[ -n "$pair_stem" && -n "$sample_id" ]] || continue
        if [[ -n "${FLAT_SAMPLE_AMBIGUOUS[$pair_stem]:-}" ]]; then
            continue
        fi
        if [[ -n "${FLAT_SAMPLE_MAP[$pair_stem]:-}" &&
              "${FLAT_SAMPLE_MAP[$pair_stem]}" != "$sample_id" ]]; then
            FLAT_SAMPLE_AMBIGUOUS["$pair_stem"]=1
            unset "FLAT_SAMPLE_MAP[$pair_stem]"
        else
            FLAT_SAMPLE_MAP["$pair_stem"]="$sample_id"
        fi
    done < <(python - "$CHAIN_SUMMARY" <<'PY'
import csv
import sys
from pathlib import Path

for row in csv.DictReader(open(sys.argv[1], encoding="utf-8-sig", newline="")):
    if str(row.get("status", "")).strip().upper() != "OK":
        continue
    sample = str(row.get("sample_id", "")).strip()
    pair_id = str(row.get("pair_id", "")).strip().replace("\\", "/")
    if not sample or not pair_id:
        continue
    key = Path(pair_id).name
    print(key + "\t" + sample)
PY
    )
}

flat_sample_for_fasta() {
    local fasta="$1"
    local pair_stem
    pair_stem="$(basename "$fasta")"
    pair_stem="${pair_stem%_merged.fasta}"
    pair_stem="${pair_stem%_merged.fa}"
    if [[ -n "${FLAT_SAMPLE_AMBIGUOUS[$pair_stem]:-}" ]]; then
        fail "Flat PANDAseq FASTA ${fasta} maps to multiple samples in ${CHAIN_SUMMARY}; use nested split output"
    fi
    if [[ -z "${FLAT_SAMPLE_MAP[$pair_stem]:-}" ]]; then
        fail "No file-level sample mapping for flat PANDAseq FASTA ${fasta} in ${CHAIN_SUMMARY}"
    fi
    printf '%s\n' "${FLAT_SAMPLE_MAP[$pair_stem]}"
}

chain_allowed_for_sample() {
    local sample="$1" chain="$2" rel_path="${3:-}" known chains candidate
    [[ "${#SAMPLE_CHAIN_MAP[@]}" -gt 0 ]] || return 0
    for known in "${!SAMPLE_CHAIN_MAP[@]}"; do
        chains=",${SAMPLE_CHAIN_MAP[$known]},"
        [[ "$chains" == *",$chain,"* ]] || continue
        # The input hierarchy can be Lane/pair_stem/sample_id.  The sample
        # label alone may therefore be a physical Lane or pair directory;
        # accept a match from any path component, especially the leaf sample.
        for candidate in "$sample" "${rel_path//\//__}"; do
            case "$candidate" in
                "$known"|"$known"__*|*__"$known"|*__"$known"__*|*/"$known"|*/"$known"/*)
                    return 0
                    ;;
            esac
        done
    done
    return 1
}

echo "MERGED_DIRS paths: ${MERGED_DIRS[@]}"

# These two functions must exist before argument parsing so --help and invalid
# options work reliably.
usage() {
    cat <<'EOF'
Usage: bash 07.work_igblastn.sh [options] [BCR|TCR|BOTH]
  --input-mode representative|pandaseq
  --data-dir DIR[,DIR...]   replace the default input directories
  --output-dir DIR          output directory
  --db-dir DIR              IgBLAST database root
  --species NAME            human, mouse, rhesus_monkey or rat
  --chains LIST             TCR chains or ALL
  --bcr-chains LIST         BCR chains or ALL
  --threads N               threads per IgBLAST job
  --parallel N              maximum concurrent jobs
  --batch-fasta N           FASTA records per batch
EOF
}
fail() { printf '[%s] ERROR: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }
positive_int() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
nonnegative_int() { [[ "$1" =~ ^[0-9]+$ ]]; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-mode)
            INPUT_MODE="$2"; shift 2 ;;
        --data-dir)
            IFS=',' read -ra _paths <<< "$2"
            MERGED_DIRS=("${_paths[@]}")
            DATA_DIR_SET=1
            shift 2
            ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --db-dir) DB_DIR="$2"; shift 2 ;;
        --species) SPECIES="$2"; shift 2 ;;
        --chains) chains_input="$2"; shift 2 ;;
        --bcr-chains) bcr_chains_input="$2"; shift 2 ;;
        --evalue) E_threshold="$2"; shift 2 ;;
        --threads) NUM_THREADS="$2"; shift 2 ;;
        --memory-limit-gb) MEMORY_LIMIT_GB="$2"; shift 2 ;;
        --monitor-interval-sec) MONITOR_INTERVAL_SEC="$2"; shift 2 ;;
        --parallel) MAX_PARALLEL_JOBS="$2"; shift 2 ;;
        --batch-fasta) BATCH_FASTA="$2"; shift 2 ;;
        --help) usage; exit 0 ;;
        BCR|TCR|BOTH|BCR,TCR|TCR,BCR) db_cell_type="$1"; shift ;;
        *) fail "未知参数: $1" ;;
    esac
done

positive_int "$NUM_THREADS" || fail "--threads must be a positive integer: $NUM_THREADS"
positive_int "$MAX_PARALLEL_JOBS" || fail "--parallel must be a positive integer: $MAX_PARALLEL_JOBS"
positive_int "$BATCH_FASTA" || fail "--batch-fasta must be a positive integer: $BATCH_FASTA"
nonnegative_int "$MEMORY_LIMIT_GB" || fail "--memory-limit-gb must be a non-negative integer: $MEMORY_LIMIT_GB"
nonnegative_int "$MEMORY_BUDGET_GB" || fail "SCIGBLAST_MEMORY_BUDGET_GB must be a non-negative integer: $MEMORY_BUDGET_GB"
positive_int "$THREAD_BUDGET" || fail "SCIGBLAST_PARALLEL_THREAD_BUDGET must be a positive integer: $THREAD_BUDGET"
positive_int "$MAX_THREAD_BUDGET" || fail "SCIGBLAST_MAX_THREAD_BUDGET must be a positive integer: $MAX_THREAD_BUDGET"
if (( THREAD_BUDGET > MAX_THREAD_BUDGET )); then
    fail "SCIGBLAST_PARALLEL_THREAD_BUDGET must be <= ${MAX_THREAD_BUDGET}"
fi
if (( NUM_THREADS * MAX_PARALLEL_JOBS > THREAD_BUDGET )); then
    fail "IgBLAST concurrency ${NUM_THREADS} x ${MAX_PARALLEL_JOBS} exceeds ${THREAD_BUDGET} thread budget"
fi
if (( MEMORY_LIMIT_GB > 0 && MEMORY_BUDGET_GB > 0 && MEMORY_LIMIT_GB * MAX_PARALLEL_JOBS > MEMORY_BUDGET_GB )); then
    fail "IgBLAST memory allocation ${MEMORY_LIMIT_GB}GB x ${MAX_PARALLEL_JOBS} jobs exceeds ${MEMORY_BUDGET_GB}GB budget"
fi

if [ -z "$MONITOR_DIR" ]; then
    MONITOR_DIR="${OUTPUT_DIR}/monitor_logs"
fi

if [ -z "$SUMMARY_FILE" ]; then
    SUMMARY_FILE="${OUTPUT_DIR}/igblastn_run_summary.tsv"
fi

case "$INPUT_MODE" in
    representative) ;;
    pandaseq)
        if [ "$DATA_DIR_SET" -eq 0 ]; then
            if [[ -n "$DATASET_LABEL" ]]; then
                MERGED_DIRS=("${OUTPUT_ROOT}/05.pandaseq/${DATASET_LABEL}")
            else
                MERGED_DIRS=("${OUTPUT_ROOT}/05.pandaseq")
            fi
        fi
        ;;
    *) fail "--input-mode must be representative or pandaseq" ;;
esac

if [ "$INPUT_MODE" = "pandaseq" ]; then
    load_flat_sample_map
fi

usage() {
    cat <<'EOF'
Usage:
  bash 07.work_igblastn.sh [options] [BCR|TCR|BOTH|BCR,TCR]

Options:
  --data-dir DIR[,DIR...]     pandaseq 输出目录（可多次指定，支持逗号分隔）
  --output-dir DIR            igblastn 输出目录，默认 ${SCRIPT_DIR}/3.igblastn_out
  --db-dir DIR                igblast 数据库目录
  --species NAME              物种: human / mouse / rhesus_monkey / rat，默认 human
  --chains TRA,TRB,TRG,TRD    TCR 链（逗号分隔），ALL=全部4个链，默认 ALL
  --bcr-chains IGH,IGK,IGL    BCR 链（逗号分隔），ALL=全部3个链，默认 ALL
  --evalue N                  e-value 阈值，默认 0.0001
  --threads N                 igblastn 线程数，默认 256
  --memory-limit-gb N         虚拟内存限制（GB），0=不限制，默认 50
  --monitor-interval-sec N    内存监控间隔秒数，0=禁用，默认 5
  --help                      显示帮助

也可通过位置参数指定细胞类型:
  bash 07.work_igblastn.sh BOTH
  bash 07.work_igblastn.sh --species mouse TCR

Examples:
  bash 07.work_igblastn.sh BOTH
  bash 07.work_igblastn.sh --threads 64 --memory-limit-gb 128 BCR
  bash 07.work_igblastn.sh --chains TRA,TRB TCR
  bash 07.work_igblastn.sh --species mouse --chains TRB TCR
EOF
}

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

fail() {
    log "ERROR: $*"
    exit 1
}

normalize_cell_types() {
    local raw
    raw="$(printf '%s' "$db_cell_type" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"

    case "$raw" in
        BCR)
            CELL_TYPES=("BCR")
            ;;
        TCR)
            CELL_TYPES=("TCR")
            ;;
        BOTH|BCR,TCR|TCR,BCR)
            CELL_TYPES=("BCR" "TCR")
            ;;
        -H|--HELP|HELP)
            usage
            exit 0
            ;;
        *)
            usage
            fail "Unsupported cell type input: $db_cell_type"
            ;;
    esac
}

set_memory_limit() {
    if ! [[ "$MEMORY_LIMIT_GB" =~ ^[0-9]+$ ]]; then
        fail "MEMORY_LIMIT_GB must be a non-negative integer."
    fi

    if [ "$MEMORY_LIMIT_GB" -gt 0 ]; then
        local limit_kb
        limit_kb=$((MEMORY_LIMIT_GB * 1024 * 1024))
        if ulimit -v "$limit_kb" 2>/dev/null; then
            log "Virtual memory limit enabled: ${MEMORY_LIMIT_GB} GB"
        else
            log "WARNING: Failed to apply ulimit -v ${limit_kb}; memory limit not enforced."
        fi
    else
        log "Virtual memory limit disabled."
    fi
}

get_db_config() {
    local db_cell_type="$1"

    case "$db_cell_type" in
        BCR)
            INDEX="IG"
            IG_CELL_TYPE="Ig"
            index="$INDEX"
            ig_cell_type="$IG_CELL_TYPE"
            ;;
        TCR)
            INDEX="TR"
            IG_CELL_TYPE="TCR"
            index="$INDEX"
            ig_cell_type="$IG_CELL_TYPE"
            ;;
        *)
            fail "Unsupported db cell type: $db_cell_type"
            ;;
    esac
}

# Build germline DB paths for a given cell-type and chain.
# Both BCR and TCR use per-chain databases under database_251117/.
get_germline_paths() {
    local ct="$1"   # BCR or TCR
    local chain="$2" # TRA/TRB/TRG/TRD for TCR; IGH/IGK/IGL for BCR

    GERM_V="${DB_DIR}/database_251117/${SPECIES}/${chain}/${SPECIES}_gl_${chain}_V"
    GERM_D="${DB_DIR}/database_251117/${SPECIES}/${chain}/${SPECIES}_gl_${chain}_D"
    GERM_J="${DB_DIR}/database_251117/${SPECIES}/${chain}/${SPECIES}_gl_${chain}_J"
    GERM_C="${DB_DIR}/database_251117/${SPECIES}/${chain}/${SPECIES}_gl_${chain}_C"
    AUX="${DB_DIR}/optional_file/${SPECIES}_gl.aux"
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
        END { print sum(root) }
        function sum(pid, total, count, i, ids) {
            if (pid == "" || seen[pid]++) return 0
            total = rss[pid] + 0
            count = split(children[pid], ids, " ")
            for (i = 1; i <= count; i++) if (ids[i] != "") total += sum(ids[i])
            return total
        }
    '
}

monitor_process_memory() {
    local pid="$1"
    local label="$2"
    local monitor_file="$3"
    local interval="$4"
    local peak_rss_kb=0

    while kill -0 "$pid" 2>/dev/null; do
        local timestamp
        local rss_kb
        local rss_mb
        local peak_rss_mb

        timestamp="$(date '+%F %T')"
        # Include the igblastn process and its monitor/tool descendants; the
        # root-only RSS previously under-reported peak usage.
        rss_kb="$(process_tree_rss_kb "$pid" 2>/dev/null || true)"
        rss_kb="${rss_kb:-0}"

        if [[ "$rss_kb" =~ ^[0-9]+$ ]] && [ "$rss_kb" -gt "$peak_rss_kb" ]; then
            peak_rss_kb="$rss_kb"
        fi

        rss_mb=$(((rss_kb + 1023) / 1024))
        peak_rss_mb=$(((peak_rss_kb + 1023) / 1024))

        printf '%s\tpid=%s\trss_mb=%s\tpeak_rss_mb=%s\n' \
            "$timestamp" "$pid" "$rss_mb" "$peak_rss_mb" >> "$monitor_file"
        log "[IR][igblast] heartbeat task=${label} rss=${rss_mb}MB peak=${peak_rss_mb}MB"

        sleep "$interval"
    done

    printf '%s\n' "$peak_rss_kb" > "${monitor_file}.peak"
}

read_peak_rss_mb() {
    local peak_file="$1"
    local peak_kb=0

    if [ -f "$peak_file" ]; then
        peak_kb="$(tr -d '[:space:]' < "$peak_file")"
    fi

    if [[ ! "$peak_kb" =~ ^[0-9]+$ ]]; then
        peak_kb=0
    fi

    printf '%s' $(((peak_kb + 1023) / 1024))
}

normalize_cell_types

# Resolve TCR chains
resolve_tcr_chains() {
    local raw
    raw="$(printf '%s' "$chains_input" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
    CHAINS=()
    if [ "$raw" = "ALL" ]; then
        CHAINS=(TRA TRB TRG TRD)
    else
        IFS=',' read -ra CHAINS <<< "$raw"
        for ch in "${CHAINS[@]}"; do
            [[ "$ch" =~ ^(TRA|TRB|TRG|TRD)$ ]] || fail "Unknown TCR chain: $ch (must be TRA, TRB, TRG, TRD)"
        done
    fi
}

# Resolve BCR chains
resolve_bcr_chains() {
    local raw
    raw="$(printf '%s' "$bcr_chains_input" | tr '[:lower:]' '[:upper:]' | tr -d ' ')"
    BCR_CHAINS=()
    if [ "$raw" = "ALL" ]; then
        BCR_CHAINS=(IGH IGK IGL)
    else
        IFS=',' read -ra BCR_CHAINS <<< "$raw"
        for ch in "${BCR_CHAINS[@]}"; do
            [[ "$ch" =~ ^(IGH|IGK|IGL)$ ]] || fail "Unknown BCR chain: $ch (must be IGH, IGK, IGL)"
        done
    fi
}
resolve_tcr_chains
resolve_bcr_chains

command -v "$IGBLAST_BIN" >/dev/null 2>&1 || fail "IgBLAST command not found: $IGBLAST_BIN"

[ -d "$DB_DIR" ] || fail "DB_DIR does not exist: $DB_DIR"


declare -a BATCH_FASTAS=()
declare -a BATCH_MAPS=()
declare -a BATCH_SAMPLES=()
declare -a BATCH_SUBDIRS=()

BATCH_TEMP_DIR="${OUTPUT_DIR}/.batches"
BATCH_MANIFEST="${BATCH_TEMP_DIR}/manifest.tsv"

# Manifest format uses "-" for batches that intentionally have no map.
# In pandaseq mode any real map path was produced by the old broken batching
# logic and must force a rebuild.
MANIFEST_VALID=0
MANIFEST_ROWS=0
if [ -s "$BATCH_MANIFEST" ]; then
    MANIFEST_VALID=1
    while IFS=$'\t' read -r _fa _mp _sm _sd; do
        [[ -n "$_fa" && -n "$_mp" && -n "$_sm" && -n "$_sd" ]] || { MANIFEST_VALID=0; break; }
        ((MANIFEST_ROWS += 1))
        [[ -f "$_fa" ]] || { MANIFEST_VALID=0; break; }
        if [ "$INPUT_MODE" = "pandaseq" ]; then
            [[ "$_mp" == "-" ]] || { MANIFEST_VALID=0; break; }
        else
            [[ "$_mp" == "-" || -f "$_mp" ]] || { MANIFEST_VALID=0; break; }
        fi
    done < "$BATCH_MANIFEST"
    (( MANIFEST_ROWS > 0 )) || MANIFEST_VALID=0
fi

if [ -d "$BATCH_TEMP_DIR" ] && [ "$MANIFEST_VALID" -eq 1 ]; then
    # ---- Resume: load existing batches ----
    log "Found existing batch directory, loading manifest..."
    while IFS=$'\t' read -r fa mp sm sd; do
        [[ "$mp" == "-" ]] && mp=""
        if [ -f "$fa" ] && { [ -z "$mp" ] || [ -f "$mp" ]; }; then
            BATCH_FASTAS+=("$fa")
            BATCH_MAPS+=("$mp")
            BATCH_SAMPLES+=("$sm")
            BATCH_SUBDIRS+=("$sd")
        fi
    done < "$BATCH_MANIFEST"
    log "Loaded ${#BATCH_FASTAS[@]} existing batches."
else
    # ---- Build new batches ----
    if [ -d "$BATCH_TEMP_DIR" ]; then
        if [ "$KEEP_INTERMEDIATE" = "0" ]; then
            rm -rf "$BATCH_TEMP_DIR"
        else
            stale_dir="${OUTPUT_DIR}/.batches.stale.$(date +%Y%m%d%H%M%S).${BASHPID:-$$}"
            mv -- "$BATCH_TEMP_DIR" "$stale_dir"
            log "Archived invalid IgBLAST batch directory: $stale_dir"
        fi
    fi
    mkdir -p "$BATCH_TEMP_DIR"
    : > "$BATCH_MANIFEST"

    for MERGED_DIR in "${MERGED_DIRS[@]}"; do
        MERGED_DIR="$(printf '%s' "$MERGED_DIR" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        log "检查输入目录: [$MERGED_DIR]"

        if [ ! -d "$MERGED_DIR" ]; then
            log "ERROR: MERGED_DIR does not exist: [$MERGED_DIR]"
            exit 1
        fi

        input_base="$(basename "$MERGED_DIR")"

        if [ "$INPUT_MODE" = "pandaseq" ]; then
            FIND_EXPR=( -name "*_merged.fasta" -o -name "*_merged.fa" )
        else
            FIND_EXPR=( -name "*.fasta" -o -name "*.fa" )
        fi

        # Discover leaf sample directories recursively.  Pandaseq/representative
        # outputs preserve the source Lane/batch hierarchy, so a direct
        # maxdepth=1 scan would mistake the Lane directory for a sample.
        SUBDIRS=()
        while IFS= read -r -d '' fasta_file; do
            subdir="$(dirname "$fasta_file")"
            [[ "$subdir" == "$MERGED_DIR" ]] && continue
            already=0
            for known_subdir in "${SUBDIRS[@]}"; do
                [[ "$known_subdir" == "$subdir" ]] && already=1 && break
            done
            (( already == 0 )) && SUBDIRS+=("$subdir")
        done < <(find "$MERGED_DIR" -type f \( "${FIND_EXPR[@]}" \) -print0 | sort -z)

        if [ "${#SUBDIRS[@]}" -eq 0 ]; then
            log "Flat mode (no subdirectories)"
            while IFS= read -r -d '' file; do
                flat_sample="$(flat_sample_for_fasta "$file")"
                BATCH_FASTAS+=("$file")
                BATCH_MAPS+=("")
                BATCH_SAMPLES+=("$flat_sample")
                BATCH_SUBDIRS+=("$input_base")
                printf '%s\t%s\t%s\t%s\n' "$file" "-" "$flat_sample" "$input_base" >> "$BATCH_MANIFEST"
            done < <(find "$MERGED_DIR" -maxdepth 1 -type f \( "${FIND_EXPR[@]}" \) -print0)
        else
            for subdir in "${SUBDIRS[@]}"; do
                subdir_base="$(basename "$subdir")"
                subdir_rel="${subdir#${MERGED_DIR}/}"
                log "  Sample: $subdir_base"

                BC_FILES=()
                while IFS= read -r -d '' f; do
                    BC_FILES+=("$f")
                done < <(find "$subdir" -type f \( "${FIND_EXPR[@]}" \) -print0 | sort -z)

                if [ "${#BC_FILES[@]}" -eq 0 ]; then
                    log "    WARNING: no FASTA files in $subdir_base"
                    continue
                fi

                batch_idx=0
                batch_start=0
                while [ "$batch_start" -lt "${#BC_FILES[@]}" ]; do
                    batch_end=$(( batch_start + BATCH_FASTA ))
                    [ "$batch_end" -gt "${#BC_FILES[@]}" ] && batch_end="${#BC_FILES[@]}"

                    BATCH_DIR="${BATCH_TEMP_DIR}/${subdir_rel}"
                    mkdir -p "$BATCH_DIR"
                    batch_fa="${BATCH_DIR}/batch_${batch_idx}.fasta"
                    batch_map=""

                    log "    Batch $batch_idx: ${batch_start}-$((batch_end-1)) (${BATCH_FASTA} files/batch)"

                    if [ "$INPUT_MODE" = "representative" ]; then
                        batch_map="${BATCH_DIR}/batch_${batch_idx}.map"
                        : > "$batch_map"
                    fi
                    single_pandaseq_batch=0
                    if [ "$INPUT_MODE" = "pandaseq" ] && [ $((batch_end - batch_start)) -eq 1 ]; then
                        # Avoid duplicating a potentially huge merged FASTA.
                        rm -f "$batch_fa"
                        ln -s "${BC_FILES[$batch_start]}" "$batch_fa"
                        single_pandaseq_batch=1
                    else
                        : > "$batch_fa"
                    fi
                    for ((i=batch_start; i<batch_end; i++)); do
                        bf="${BC_FILES[$i]}"
                        if [ "$INPUT_MODE" = "representative" ]; then
                            bc_name="$(basename "$bf" .fasta)"
                            bc_name="${bc_name%.fa}"
                            awk -v bc="$bc_name" '/^>/{h=substr($0,2); split(h,a,/[ \t]/); print a[1] "\t" bc}' "$bf" >> "$batch_map"
                        fi
                        if [ "$single_pandaseq_batch" -eq 0 ]; then
                            cat "$bf" >> "$batch_fa"
                        fi
                    done

                    # Validate the complete representative map once after all
                    # source FASTAs have been appended.  The old placement in
                    # the source-file loop repeatedly rescanned an ever-
                    # growing map and made large batches needlessly slow.
                    if [ "$INPUT_MODE" = "representative" ]; then
                        if [ ! -s "$batch_map" ]; then
                            log "ERROR: representative batch produced an empty map: $batch_map"
                            exit 1
                        fi
                        if ! awk -F '\t' '!NF || NF < 2 || seen[$1]++ { bad=1 } END { exit bad ? 1 : 0 }' "$batch_map"; then
                            log "ERROR: duplicate or malformed representative FASTA header in $batch_map"
                            exit 1
                        fi
                    fi

                    BATCH_FASTAS+=("$batch_fa")
                    BATCH_MAPS+=("$batch_map")
                    BATCH_SAMPLES+=("$subdir_base")
                    BATCH_SUBDIRS+=("$subdir_rel")
                    manifest_map="${batch_map:--}"
                    printf '%s\t%s\t%s\t%s\n' "$batch_fa" "$manifest_map" "$subdir_base" "$subdir_rel" >> "$BATCH_MANIFEST"

                    batch_idx=$((batch_idx + 1))
                    batch_start=$batch_end
                done
                log "    Total: ${#BC_FILES[@]} barcodes → $batch_idx batches"
            done
        fi
    done
fi

TOTAL_BATCHES="${#BATCH_FASTAS[@]}"
[ "$TOTAL_BATCHES" -gt 0 ] || fail "No FASTA files found in input directories"

log "[IR][igblast] stage_start batches=${TOTAL_BATCHES} max_parallel=${MAX_PARALLEL_JOBS} threads=${NUM_THREADS}"

if ! [[ "$MONITOR_INTERVAL_SEC" =~ ^[0-9]+$ ]]; then
    fail "MONITOR_INTERVAL_SEC must be a non-negative integer."
fi

if [ "$MONITOR_INTERVAL_SEC" -gt 0 ]; then
    command -v ps >/dev/null 2>&1 || fail "ps command not found; required for memory monitoring."
fi

mkdir -p "$OUTPUT_DIR" "$MONITOR_DIR"
set_memory_limit

printf 'cell_type\tchain\tsample\tbatch\texit_code\telapsed_sec\tpeak_rss_mb\tinput_seqs\tmapped_seqs\tunmapped_seqs\tmapping_percent\tproductive_seqs\tfiltered_seqs\tfiltered_percent\n' > "$SUMMARY_FILE"

# Calculate total tasks: batches × BCR chains + batches × TCR chains
TOTAL_TASKS=0
for ct in "${CELL_TYPES[@]}"; do
    if [ "$ct" = "BCR" ]; then JOB_CHAINS_COUNT=(${BCR_CHAINS[@]}); else JOB_CHAINS_COUNT=(${CHAINS[@]}); fi
    for chain_name in "${JOB_CHAINS_COUNT[@]}"; do
        for si in "${!BATCH_SAMPLES[@]}"; do
            sample_name="${BATCH_SAMPLES[$si]}"
            subdir_name="${BATCH_SUBDIRS[$si]}"
            chain_allowed_for_sample "$sample_name" "$chain_name" "$subdir_name" && TOTAL_TASKS=$((TOTAL_TASKS + 1))
        done
    done
done
DONE_TASKS=0
FAILED_TASKS=0

log "Found ${TOTAL_BATCHES} batches."
log "TCR chains: ${CHAINS[*]}"
log "BCR chains: ${BCR_CHAINS[*]}"
log "Planned tasks: ${TOTAL_TASKS}"
log "Batch size: ${BATCH_FASTA} files"
log "Output directory: $OUTPUT_DIR"
log "Summary file: $SUMMARY_FILE"

cd "$DB_DIR" || fail "Cannot cd to DB_DIR: $DB_DIR"

for DB_CELL_TYPE in "${CELL_TYPES[@]}"; do
    get_db_config "$DB_CELL_TYPE"

    if [ "$DB_CELL_TYPE" = "BCR" ]; then
        JOB_CHAINS=("${BCR_CHAINS[@]}")
    else
        JOB_CHAINS=("${CHAINS[@]}")
    fi

    for CHAIN in "${JOB_CHAINS[@]}"; do
        get_germline_paths "$DB_CELL_TYPE" "$CHAIN"

        CHAIN_MON_DIR="${MONITOR_DIR}/${CHAIN}"
        mkdir -p "$CHAIN_MON_DIR"

        JOB_RESULT_DIR="${OUTPUT_DIR}/.job_results_${DB_CELL_TYPE}_${CHAIN}"
        mkdir -p "$JOB_RESULT_DIR"

        if [ "$DB_CELL_TYPE" = "TCR" ]; then
            log "Starting: ${DB_CELL_TYPE} / ${CHAIN}  (max parallel: ${MAX_PARALLEL_JOBS})"
        else
            log "Starting: ${DB_CELL_TYPE}  (max parallel: ${MAX_PARALLEL_JOBS})"
        fi

        active_jobs=0
        # Bash 5.1+ exposes the PID reaped by wait -n through -p.  Keep a
        # registry so a failed/empty wait cannot silently free a concurrency
        # slot.  The fallback is retained for older Bash versions.
        declare -A ACTIVE_JOB_PIDS=()
        WAIT_SUPPORTS_P=0
        if help wait 2>/dev/null | grep -q -- '-p'; then
            WAIT_SUPPORTS_P=1
        fi
        for ((bi=0; bi<TOTAL_BATCHES; bi++)); do
            BATCH_FA="${BATCH_FASTAS[$bi]}"
            BATCH_MAP="${BATCH_MAPS[$bi]}"
            SAMPLE="${BATCH_SAMPLES[$bi]}"
            SUBDIR="${BATCH_SUBDIRS[$bi]}"

            if ! chain_allowed_for_sample "$SAMPLE" "$CHAIN" "$SUBDIR"; then
                log "Skipping ${SAMPLE}/batch_${bi}: Chain summary does not request ${CHAIN}"
                continue
            fi

            MONITOR_FILE="${CHAIN_MON_DIR}/${SAMPLE}__batch_${bi}_${CHAIN}.mem.log"
            JOB_TAG="${DB_CELL_TYPE}__${CHAIN}__${SAMPLE}__batch_${bi}"
            LABEL_CHAIN=" ${CHAIN}"

            DONE_TASKS=$((DONE_TASKS + 1))
            TASK_PERCENT=$((DONE_TASKS * 100 / TOTAL_TASKS))
            TASK_LABEL="[${DONE_TASKS}/${TOTAL_TASKS} ${TASK_PERCENT}%] [${DB_CELL_TYPE}${LABEL_CHAIN}] ${SAMPLE}/batch_${bi}"

            # Throttle concurrency
            while [ "$active_jobs" -ge "$MAX_PARALLEL_JOBS" ]; do
                if [ "$WAIT_SUPPORTS_P" -eq 1 ]; then
                    completed_pid=""
                    if wait -n -p completed_pid 2>/dev/null; then
                        wait_status=0
                    else
                        wait_status=$?
                    fi
                    if [[ "$completed_pid" =~ ^[0-9]+$ ]] && [[ -n "${ACTIVE_JOB_PIDS[$completed_pid]+yes}" ]]; then
                        unset "ACTIVE_JOB_PIDS[$completed_pid]"
                        active_jobs=$((active_jobs - 1))
                    elif [ "$wait_status" -ne 127 ]; then
                        # A non-zero status is still a successfully reaped
                        # child (for example igblastn exit=1).  If Bash did
                        # not return its PID, consume one known slot rather
                        # than allowing the counter to grow unbounded.
                        active_jobs=$((active_jobs - 1))
                    else
                        log "wait -n found no active IgBLAST child; rebuilding active count"
                        active_jobs=0
                        for known_pid in "${!ACTIVE_JOB_PIDS[@]}"; do
                            if kill -0 "$known_pid" 2>/dev/null; then
                                active_jobs=$((active_jobs + 1))
                            else
                                unset "ACTIVE_JOB_PIDS[$known_pid]"
                            fi
                        done
                        [ "$active_jobs" -lt "$MAX_PARALLEL_JOBS" ] || sleep 1
                    fi
                else
                    if wait -n 2>/dev/null; then
                        wait_status=0
                    else
                        wait_status=$?
                    fi
                    # wait -n returns the child's status; only 127 means that
                    # no child was available and must not decrement the slot.
                    if [ "$wait_status" -ne 127 ]; then
                        active_jobs=$((active_jobs - 1))
                    else
                        log "wait -n found no active IgBLAST child; keeping slot count"
                        sleep 1
                    fi
                fi
            done

            # Keep the representative map discoverable next to the chain
            # result.  The source map lives beside the sample-local batch
            # FASTA; a symlink avoids copying a potentially large map.  The
            # postprocessor also supports the legacy sibling-map layout.
            BATCH_OUT_DIR="${BATCH_TEMP_DIR}/${SUBDIR}/${CHAIN}"
            mkdir -p "$BATCH_OUT_DIR"
            BATCH_FASTA_OUT="${BATCH_OUT_DIR}/batch_${bi}.fasta"
            if [ ! -e "$BATCH_FASTA_OUT" ] && [ ! -L "$BATCH_FASTA_OUT" ]; then
                if ! ln -s "$BATCH_FA" "$BATCH_FASTA_OUT" 2>/dev/null; then
                    fail "Cannot create FASTA symlink without copying large input: $BATCH_FASTA_OUT"
                fi
            fi
            if [ "$INPUT_MODE" = "representative" ] && [ -n "$BATCH_MAP" ] && [ -f "$BATCH_MAP" ]; then
                BATCH_MAP_OUT="${BATCH_OUT_DIR}/batch_${bi}.map"
                if [ ! -e "$BATCH_MAP_OUT" ] && [ ! -L "$BATCH_MAP_OUT" ]; then
                    ln -s "$BATCH_MAP" "$BATCH_MAP_OUT" 2>/dev/null || cp -f "$BATCH_MAP" "$BATCH_MAP_OUT"
                fi
            fi

            (
                log "[IR][igblast] worker_start task=${TASK_LABEL}"
                START_TS="$(date +%s)"

                RAW_OUT="${BATCH_FA}.raw.tsv"
                "$IGBLAST_BIN" \
                    -query "$BATCH_FA" \
                    -germline_db_V "$GERM_V" \
                    -germline_db_D "$GERM_D" \
                    -germline_db_J "$GERM_J" \
                    -c_region_db "$GERM_C" \
                    -auxiliary_data "$AUX" \
                    -organism "$SPECIES" \
                    -ig_seqtype "$IG_CELL_TYPE" \
                    -num_alignments_V 1 -num_alignments_D 1 -num_alignments_J 1 -num_alignments_C 1 \
                    -show_translation \
                    -evalue "$E_threshold" \
                    -num_threads "$NUM_THREADS" \
                    -outfmt 19 \
                    -out "$RAW_OUT" &
                IGBLAST_PID=$!

                MONITOR_PID=""
                PEAK_RSS_MB=0
                if [ "$MONITOR_INTERVAL_SEC" -gt 0 ]; then
                    : > "$MONITOR_FILE"
                    monitor_process_memory "$IGBLAST_PID" "$TASK_LABEL" "$MONITOR_FILE" "$MONITOR_INTERVAL_SEC" &
                    MONITOR_PID=$!
                fi

                wait "$IGBLAST_PID"
                EXIT_CODE=$?

                if [ -n "$MONITOR_PID" ]; then
                    wait "$MONITOR_PID" 2>/dev/null || true
                    PEAK_RSS_MB="$(read_peak_rss_mb "${MONITOR_FILE}.peak")"
                fi

                END_TS="$(date +%s)"
                ELAPSED_SEC=$(( END_TS - START_TS ))

                # Preserve the IgBLAST output byte-for-byte. No barcode or
                # UMI substitution, productive/v_call filtering, extra
                # columns, or comment injection is performed here; the
                # independent mapping/productive/filter counts below are
                # summary-only metrics.
                INPUT_SEQS=0
                MAPPED_SEQS=0
                PRODUCTIVE_SEQS=0
                FILTERED_SEQS=0
                if [ "$EXIT_CODE" -eq 0 ] && [ -f "$RAW_OUT" ]; then
                    BATCH_OUT_DIR="${BATCH_TEMP_DIR}/${SUBDIR}/${CHAIN:-BCR}"
                    mkdir -p "$BATCH_OUT_DIR"
                    BATCH_OUT="${BATCH_OUT_DIR}/batch_${bi}.tsv"
                    mv -f "$RAW_OUT" "$BATCH_OUT"
                    INPUT_SEQS=$(grep -c '^>' "$BATCH_FA" 2>/dev/null || true)
                    INPUT_SEQS="${INPUT_SEQS:-0}"
                    # Mapping is an actual V-gene hit, independent of the
                    # later productive/v_call filter used for final output.
                    read -r MAPPED_SEQS PRODUCTIVE_SEQS FILTERED_SEQS < <(awk -F '\t' '
                        function valid_v(v, u) { u=toupper(v); return u!="" && u!="*" && u!="-" && u!="NA" && u!="N/A" && u!="NONE" && u!="NULL" && u!="NO_HIT" && u!="UNMAPPED" && u!="NOT_FOUND" }
                        !/^#/ && NF {
                            if (!header_seen) {
                                for (i=1; i<=NF; i++) { if ($i=="v_call") vcol=i; if ($i=="productive") pcol=i }
                                header_seen=1; next
                            }
                            vv=(vcol>0 && valid_v($vcol)); pp=(pcol>0 && (toupper($pcol)=="T" || toupper($pcol)=="TRUE"))
                            mapped+=vv; productive+=pp; filtered+=(vv && pp)
                        }
                        END { printf "%d %d %d\n", mapped+0, productive+0, filtered+0 }
                    ' "$BATCH_OUT")
                fi
                UNMAPPED_SEQS=$(( INPUT_SEQS > MAPPED_SEQS ? INPUT_SEQS - MAPPED_SEQS : 0 ))

                # Write result (added: input/mapped/unmapped)
                MAPPING_PERCENT=$(awk -v mapped="$MAPPED_SEQS" -v total="$INPUT_SEQS" 'BEGIN {if (total > 0) printf "%.2f", mapped*100/total; else printf "0.00"}')
                FILTERED_PERCENT=$(awk -v filtered="$FILTERED_SEQS" -v total="$INPUT_SEQS" 'BEGIN {if (total > 0) printf "%.2f", filtered*100/total; else printf "0.00"}')
                printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                    "$DB_CELL_TYPE" "${CHAIN:-BCR}" "$SAMPLE" "$bi" "$EXIT_CODE" \
                    "$ELAPSED_SEC" "$PEAK_RSS_MB" "$INPUT_SEQS" "$MAPPED_SEQS" "$UNMAPPED_SEQS" "$MAPPING_PERCENT" "$PRODUCTIVE_SEQS" "$FILTERED_SEQS" "$FILTERED_PERCENT" \
                    > "${JOB_RESULT_DIR}/${JOB_TAG}.result"

                if [ "$EXIT_CODE" -eq 0 ]; then
                    log "[IR][igblast] worker_done task=${TASK_LABEL} status=OK elapsed=${ELAPSED_SEC}s peak=${PEAK_RSS_MB}MB"
                else
                    log "[IR][igblast] worker_done task=${TASK_LABEL} status=FAILED exit=${EXIT_CODE} elapsed=${ELAPSED_SEC}s peak=${PEAK_RSS_MB}MB"
                fi
            ) &
            ACTIVE_JOB_PIDS["$!"]=1
            active_jobs=$((active_jobs + 1))
        done

        # Wait for all jobs in this chain
        wait

        # Collect results
        for rf in "${JOB_RESULT_DIR}"/*.result; do
            [ -f "$rf" ] || continue
            cat "$rf" >> "$SUMMARY_FILE"
            if grep -qP '^[^\t]+\t[^\t]+\t[^\t]+\t[^\t]+\t[1-9]' "$rf" 2>/dev/null; then
                FAILED_TASKS=$((FAILED_TASKS + 1))
            fi
        done
        if [ "$KEEP_INTERMEDIATE" = "0" ]; then
            rm -rf "$JOB_RESULT_DIR"
        else
            log "Retaining IgBLAST job result directory: $JOB_RESULT_DIR"
        fi
    done
done

SUCCESS_TASKS=$((TOTAL_TASKS - FAILED_TASKS))
SKIPPED_TASKS=0
log "[IR][igblast] completed=${SUCCESS_TASKS} failed=${FAILED_TASKS} skipped=${SKIPPED_TASKS} total=${TOTAL_TASKS}"

SUMMARY_INPUT_TOTAL="$(awk -F'\t' 'NR > 1 {sum += $8} END {print sum + 0}' "$SUMMARY_FILE")"
if [ "$TOTAL_TASKS" -gt 0 ] && [ "$SUMMARY_INPUT_TOTAL" -eq 0 ]; then
    fail "All IgBLAST tasks reported input=0 although batches were scheduled; refusing to publish empty results"
fi

# Publish only filtered, sample-local results.  The batch TSVs below are
# temporary working files and are removed after the atomic publish succeeds;
# no raw IgBLAST output is exposed in the final directory.
log "Filtering productive records and merging chains within each sample..."
"${PYTHON_BIN}" - "${BATCH_TEMP_DIR}" "${OUTPUT_DIR}" "${INPUT_MODE}" "${OUTPUT_DIR}/postprocess_summary.tsv" <<'PY'
import csv, os, shutil, sys, tempfile
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2]); mode=sys.argv[3]; summary=Path(sys.argv[4])
bad={"","*","-","NA","N/A","NONE","NULL","NO_HIT","UNMAPPED","NOT_FOUND"}; tcr={"TRA","TRB","TRD","TRG"}; bcr={"IGH","IGK","IGL"}
def count_fasta(path):
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.open(encoding='utf-8',errors='ignore') if line.startswith('>'))
    except OSError:
        return 0
def count_batch_input(batch):
    # A result TSV is normally beside a chain-local FASTA symlink. Older
    # batches kept the canonical FASTA one directory above the chain folder,
    # and a resumed run may have lost the symlink. Try both layouts.
    fasta_name=batch.with_suffix('.fasta').name
    candidates=[batch.with_suffix('.fasta'), batch.parent.parent/fasta_name]
    seen=set()
    for candidate in candidates:
        candidate=Path(candidate)
        if candidate in seen: continue
        seen.add(candidate)
        count=count_fasta(candidate)
        if count: return count
    return 0
files=sorted(root.rglob('batch_*.tsv')); tmp=Path(tempfile.mkdtemp(prefix='.postprocess.',dir=out)); handles={}; stats=[]; failed=False
for batch in files:
    rel=batch.relative_to(root); chain=rel.parent.name.upper(); sample=rel.parent.parent.as_posix(); raw=valid=prod=kept=0; errors=[]; header=None; saw_noncomment=False; empty_result=False
    try:
        inp=count_batch_input(batch)
        with batch.open(encoding='utf-8',errors='replace') as fh:
            for line in fh:
                if not line.strip() or line.startswith('#'): continue
                saw_noncomment=True
                fields=line.rstrip('\r\n').split('\t')
                if header is None:
                    if 'sequence_id' not in fields or 'v_call' not in fields: raise ValueError(f'missing AIRR header: {batch}')
                    header=fields; continue
                if len(fields)<len(header): fields += ['']*(len(header)-len(fields))
                raw+=1; si=header.index('sequence_id'); vi=header.index('v_call'); pi=header.index('productive') if 'productive' in header else -1; vv=fields[vi].strip().upper() not in bad; pp=pi>=0 and fields[pi].strip().upper() in {'T','TRUE'}
                valid += int(vv); prod += int(pp)
                if not (vv and pp): continue
                # Keep IgBLAST's query ``sequence_id`` unchanged.  IR primer
                # barcode is only a pre-split demultiplexing tag, not a bulk
                # sample/cell identity and must not overwrite read identity.
                group='TCR' if chain in tcr else 'BCR' if chain in bcr else 'OTHER'; key=(sample,group)
                if key not in handles:
                    target=tmp/Path(*sample.split('/'))/(group+'.tsv'); target.parent.mkdir(parents=True,exist_ok=True); h=target.open('w',encoding='utf-8',newline=''); h.write('\t'.join(header)+'\n'); handles[key]=(h,target)
                handles[key][0].write('\t'.join(fields[:len(header)])+'\n'); kept+=1
        if header is None or raw == 0:
            # IgBLAST emits an empty/comment-only file when a scheduled chain
            # has no input sequences (some versions still emit only a header).
            # This is a valid empty result, not a malformed AIRR file. A
            # non-comment line would have raised above.
            empty_result=True
        # IgBLAST outfmt 19 emits one data row per query. If the sidecar FASTA
        # is unavailable after resume/cleanup, raw rows avoid a false zero
        # denominator while retaining the real mapping/productive counts.
        if inp == 0:
            if raw > 0:
                inp=raw
            elif not empty_result:
                raise ValueError(f"input FASTA missing or empty: {batch.with_suffix('.fasta')}")
    except Exception as exc: errors.append(str(exc))
    status='ERROR' if errors else ('EMPTY' if empty_result else 'OK')
    stats.append({'sample':sample,'chain':chain,'input_sequences':inp,'raw_rows':raw,'mapped_rows':valid,'productive_rows':prod,'output_rows':kept,'status':status,'error':'; '.join(errors)}); failed |= bool(errors)
for h,_ in handles.values(): h.close()
if failed: shutil.rmtree(tmp,ignore_errors=True); print('[postprocess] errors: '+ '; '.join(str(x['error']) for x in stats if x['error']),file=sys.stderr); raise SystemExit(1)
for sample in {x['sample'] for x in stats}:
    d=out/Path(*sample.split('/'))
    for name in ('TCR.tsv','BCR.tsv','.NO_RESULTS'): (d/name).unlink(missing_ok=True)
for target in tmp.rglob('*.tsv'):
    final=out/target.relative_to(tmp); final.parent.mkdir(parents=True,exist_ok=True); os.replace(target,final)
agg={}
for row in stats:
    key=(row['sample'],row['chain'])
    if key not in agg: agg[key]=dict(row); continue
    item=agg[key]
    for k in ('input_sequences','raw_rows','mapped_rows','productive_rows','output_rows'): item[k]=int(item[k])+int(row[k])
    if row['status']=='ERROR': item['status']='ERROR'; item['error']='; '.join(filter(None,(item['error'],row['error'])))
    elif item['status']=='EMPTY' and row['status']=='OK': item['status']='OK'
rows=list(agg.values())
for row in rows:
    n=int(row['input_sequences']); mapped=int(row['mapped_rows']); productive=int(row['productive_rows']); filtered=int(row['output_rows'])
    row['mapped_seqs']=mapped; row['productive_seqs']=productive; row['filtered_seqs']=filtered
    row['unmapped_seqs']=max(n-mapped,0)
    row['mapping_percent']=f"{mapped*100/n:.2f}" if n else '0.00'
    row['productive_percent']=f"{productive*100/n:.2f}" if n else '0.00'
    row['filtered_percent']=f"{filtered*100/n:.2f}" if n else '0.00'
    # Keep the historical name as an alias; it now has the same explicit filtered meaning.
    row['retained_percent']=row['filtered_percent']
summary.parent.mkdir(parents=True,exist_ok=True); fields=['sample','chain','input_sequences','raw_rows','mapped_rows','mapped_seqs','unmapped_seqs','productive_rows','productive_seqs','productive_percent','output_rows','filtered_seqs','mapping_percent','filtered_percent','retained_percent','status','error']
with summary.open('w',encoding='utf-8',newline='') as fh: w=csv.DictWriter(fh,fieldnames=fields,delimiter='\t'); w.writeheader(); w.writerows(rows)
with (out/'chain_summary.csv').open('w',encoding='utf-8',newline='') as fh: w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(rows)
for sample in sorted({x['sample'] for x in rows}):
    d=out/Path(*sample.split('/'))
    if not any((d/n).is_file() for n in ('TCR.tsv','BCR.tsv')): d.mkdir(parents=True,exist_ok=True); (d/'.NO_RESULTS').write_text('No productive records with a valid v_call\n',encoding='utf-8')
shutil.rmtree(tmp,ignore_errors=True)
PY
status=$?
if [ "$status" -ne 0 ]; then
    fail "IgBLAST post-processing failed; temporary batches were retained for retry"
fi
if [ "$KEEP_INTERMEDIATE" = "0" ]; then
    rm -rf "${OUTPUT_DIR}/raw" "${OUTPUT_DIR}/.tmp" "${OUTPUT_DIR}/.job_results"*
    log "Published filtered sample-local TCR.tsv/BCR.tsv; explicit cleanup removed raw/job artifacts."
else
    log "Published filtered sample-local TCR.tsv/BCR.tsv; raw/job artifacts retained (SCIGBLAST_IGBLAST_KEEP_INTERMEDIATE=1)."
fi
if [ "$FAILED_TASKS" -gt 0 ]; then
    log "Some IgBLAST tasks failed; retaining the temporary batch directory for retry."
    exit 1
fi
if [ "$KEEP_INTERMEDIATE" = "0" ]; then
    rm -rf "${BATCH_TEMP_DIR}"
else
    log "IgBLAST batch directory retained: ${BATCH_TEMP_DIR}"
fi
exit 0
