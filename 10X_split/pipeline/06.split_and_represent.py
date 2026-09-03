#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05.split_and_represent.py

Unified step 05:
  TSO/barcode/UMI split and representative sequence calculation

Pipeline:
  Phase 1: TSO 定位 → barcode/UMI 提取 → 按 barcode 写出中间 TSV
  Phase 2: 读取 TSV → 按 UMI 统计 → 输出 representative CSV + FASTA

Memory-conscious design:
  - Phase 1 writes per-barcode TSV files (not per-UMI, not in-memory accumulation)
  - Phase 2 processes one barcode at a time, then deletes the TSV
"""

import collections
import csv
import glob
import gzip
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean, median

try:
    import resource
except ImportError:  # Windows development environment; Linux runner has it.
    resource = None

from Bio import SeqIO

import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
from tools.pipeline_config import load_config
os.environ.setdefault("SCIGBLAST_CONFIG", str(_pathlib.Path(__file__).resolve().parent / "00.pipeline_config.env"))
load_config()


# ============================================================
# Global configuration — edit these before running
# ============================================================

# --- Input ---
# Resolve numbered data directories from the project root, not the caller's cwd.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_ROOT = os.environ.get("SCIGBLAST_OUTPUT_ROOT", os.path.join(PROJECT_ROOT, "output"))
DATASET_LABEL = os.environ.get("SCIGBLAST_DATASET_LABEL", "").strip()
if not DATASET_LABEL:
    raw_hint = os.environ.get("SCIGBLAST_RAW_INPUT_DIR", "")
    DATASET_LABEL = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(raw_hint.rstrip("/\\"))).strip("_") if raw_hint else ""
def stage_root(stage):
    return os.path.join(OUTPUT_ROOT, stage, DATASET_LABEL) if DATASET_LABEL else os.path.join(OUTPUT_ROOT, stage)
INPUT_DIR = os.environ.get("SCIGBLAST_10X_SPLIT_INPUT", stage_root("05.pandaseq"))

# --- Output ---
OUTPUT_DIR = os.environ.get("SCIGBLAST_10X_SPLIT_OUTPUT", stage_root("06.split_output"))

# --- TSO / barcode / UMI ---
TSO_SEQ = os.environ.get("SCIGBLAST_10X_TSO_SEQ", "TTTCTTATATGGG")
BARCODE_LEN = int(os.environ.get("SCIGBLAST_10X_BARCODE_LEN", "16"))
UMI_LEN = int(os.environ.get("SCIGBLAST_10X_UMI_LEN", "10"))
# The public representative FASTA is consumed by downstream IgBLAST. Keep
# raw sequences in reducer state, but trim the R1 technical prefix from the
# mapping FASTA by default.
TRIM_R1_TECHNICAL_PREFIX = os.environ.get(
    "SCIGBLAST_10X_TRIM_R1_PREFIX", "1"
) == "1"

# TSO_POS: 1-based coordinate, or None to search whole sequence
TSO_POS = int(os.environ.get("SCIGBLAST_10X_TSO_POS", "27"))
# TSO_POS = None

# --- Intermediate TSV directory (inside each sample dir) ---
BARCODE_TSV_DIRNAME = "_barcodes"

# --- Phase 1 outputs ---
TSO_NOT_FOUND_FASTA = "TSO_not_found.fasta"
EXTRACT_FAILED_FASTA = "extract_failed.fasta"
SUMMARY_CSV = "summary.csv"
FIELD_DESCRIPTION_CSV = "summary_field_description.csv"
RUN_LOG_TXT = "run_summary.txt"
# Never remove previous samples on a rerun.  Completed sample outputs are
# retained and can be skipped/updated incrementally by the phase logic.
CLEAR_OUTPUT_DIR = False

# --- Phase 2 outputs ---
REPRESENTATIVE_MAP = "representative_map.tsv.gz"
REPRESENTATIVE_SUMMARY = "representative_summary.csv"
REPRESENTATIVE_FASTA_DIR = "representative_fasta"
REPRESENTATIVE_FASTA_MANIFEST = ".representative_fasta_manifest.json"
# Hidden reducer state. Keep one selected representative per UMI only. The old
# CSV state kept every candidate sequence and could grow to hundreds of GB.
REPRESENTATIVE_STATE = ".representative_state.tsv.gz"
LEGACY_REPRESENTATIVE_STATE = ".representative_state.csv"

# --- Phase 2 parallelism ---
WORKERS = max(1, int(os.environ.get("SCIGBLAST_10X_WORKERS", "4")))
BATCH_SIZE = max(1, int(os.environ.get("SCIGBLAST_10X_PHASE2_BATCH_SIZE", "1000")))
PHASE2_MEMORY_LIMIT_GB = max(0, int(os.environ.get("SCIGBLAST_10X_PHASE2_MEMORY_LIMIT_GB", "48")))
MEMORY_BUDGET_GB = max(0, int(os.environ.get("SCIGBLAST_MEMORY_BUDGET_GB", "300")))
# Deprecated compatibility setting; compact phase 2 always writes one top
# sequence per UMI and keeps tied-top alternatives in one audit field.
MAX_SEQUENCES_PER_UMI = 0
SPLIT_READ_PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_10X_SPLIT_READ_PROGRESS_EVERY", "100000")))
PHASE2_PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_10X_PHASE2_PROGRESS_EVERY", "1")))
PHASE2_RECORD_PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_10X_PHASE2_RECORD_PROGRESS_EVERY", "100000")))
STATE_WRITE_PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_10X_STATE_WRITE_PROGRESS_EVERY", "100000")))
CONFIG_FINGERPRINT = hashlib.sha256(json.dumps({
    "tso_seq": TSO_SEQ, "barcode_len": BARCODE_LEN, "umi_len": UMI_LEN,
    "tso_pos": TSO_POS, "max_sequences_per_umi": MAX_SEQUENCES_PER_UMI,
    "trim_r1_technical_prefix": TRIM_R1_TECHNICAL_PREFIX,
    "quality_algorithm_version": "count_then_quality_v1",
}, sort_keys=True).encode()).hexdigest()

# --- Misc ---
FASTA_LINE_WIDTH = 80
CSV_FIELDNAMES = [
    "sample", "barcode", "umi",
    "total_sequences", "unique_sequences",
    "sequence", "count", "pct", "header",
    "is_representative",
    "representative_id", "top_candidate_count", "ambiguity_status",
    "alternative_top_sequences", "mean_quality", "expected_errors",
    "selection_method",
    "error",
]


# ============================================================
# Utility functions
# ============================================================

def open_maybe_gzip(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path, "r")


def sequence_format(path):
    """Infer the Biopython format from a merged PANDAseq filename."""
    name = str(path).lower()
    return "fastq" if any(name.endswith(suffix) for suffix in
                          (".fastq", ".fq", ".fastq.gz", ".fq.gz")) else "fasta"


def record_quality(record):
    """Return (mean_phred, expected_errors) or (None, None) for FASTA."""
    qualities = record.letter_annotations.get("phred_quality")
    if not qualities:
        return None, None
    values = [int(value) for value in qualities]
    if not values:
        return None, None
    mean_q = sum(values) / len(values)
    expected = sum(10.0 ** (-value / 10.0) for value in values)
    return mean_q, expected


def wrap_sequence(seq, width=FASTA_LINE_WIDTH):
    for i in range(0, len(seq), width):
        yield seq[i : i + width]


def mapping_sequence(raw_sequence):
    """Return the biological sequence used by downstream IgBLAST.

    The upstream prefilter already removes the R2 ``NNN + 8 bp`` primer
    barcode before PANDAseq.  The PANDAseq sequence still starts with the R1
    technical prefix: 16 bp cell barcode + 10 bp UMI + TSO.  Keep the raw
    sequence in reducer state for audit, but publish only the post-TSO
    biological sequence in the representative FASTA consumed by IgBLAST.
    """
    raw = str(raw_sequence or "").upper()
    if not TRIM_R1_TECHNICAL_PREFIX:
        return raw
    tso_start = find_tso_position(raw, TSO_SEQ.upper())
    if tso_start is None:
        raise ValueError("TSO not found while preparing downstream mapping sequence")
    query_start = tso_start + len(TSO_SEQ)
    query = raw[query_start:]
    if not query:
        raise ValueError("empty downstream mapping sequence after TSO")
    return query


def write_fasta_record(out_handle, header, seq):
    out_handle.write(f">{header}\n")
    for line in wrap_sequence(seq):
        out_handle.write(line + "\n")


# ============================================================
# Phase 1: File discovery
# ============================================================

def collect_fasta_samples(input_dir):
    """Discover FASTA samples under input_dir.

    Supports two modes:
      1. Subdirectory mode (preferred): <input_dir>/<sample>/*.fasta/.fastq(.gz)
         → prefers *_merged* files
      2. Flat-file mode (fallback): <input_dir>/*.fasta(.gz)

    Returns list of (sample_name, fasta_path, mode, relative_parent).
    """
    samples = []
    seen = set()
    fasta_exts = (".fastq.gz", ".fq.gz", ".fastq", ".fq",
                  ".fasta.gz", ".fa.gz", ".fasta", ".fa")

    def _has_fasta_ext(name):
        return name.endswith(fasta_exts)

    # Recursive discovery preserves the relative parent directory produced by
    # fastp/PANDAseq (e.g. Lane03/compound_sample).  One merged FASTA is chosen
    # per directory, preferring *_merged files.
    if os.path.isdir(input_dir):
        for root, _dirs, files in os.walk(input_dir):
            candidates = [os.path.join(root, f) for f in sorted(files) if _has_fasta_ext(f)]
            if not candidates:
                continue
            merged = [f for f in candidates if "_merged" in os.path.basename(f)]
            chosen = merged[0] if merged else candidates[0]
            base = os.path.basename(chosen)
            sample = base
            for ext in fasta_exts:
                if sample.endswith(ext):
                    sample = sample[:-len(ext)]
                    break
            for tag in ("_merged", "_unaligned", ".trimmed"):
                if sample.endswith(tag):
                    sample = sample[:-len(tag)]
                    break
            rel_parent = os.path.relpath(root, input_dir)
            if rel_parent == ".":
                rel_parent = ""
            key = (rel_parent, sample)
            if key not in seen:
                seen.add(key)
                samples.append((sample, chosen, "recursive", rel_parent))

    return samples


# ============================================================
# Phase 1: TSO / barcode / UMI extraction
# ============================================================

def find_tso_position(seq_upper, tso_upper):
    """Return 0-based start of TSO, or None."""
    if TSO_POS is None:
        pos = seq_upper.find(tso_upper)
        return pos if pos != -1 else None

    pos = TSO_POS - 1  # 0-based
    if pos < 0:
        raise ValueError("TSO_POS 必须是 >= 1 的整数，或者设置为 None")
    target = seq_upper[pos : pos + len(tso_upper)]
    return pos if target == tso_upper else None


def extract_barcode_umi(seq_upper, tso_start):
    """Extract barcode + UMI from before TSO.

    Layout:  barcode (BARCODE_LEN) + UMI (UMI_LEN) + TSO
    """
    umi_start = tso_start - UMI_LEN
    umi_end = tso_start
    barcode_start = umi_start - BARCODE_LEN
    barcode_end = umi_start
    if barcode_start < 0:
        return None, None
    return seq_upper[barcode_start:barcode_end], seq_upper[umi_start:umi_end]


# ============================================================
# Phase 1: Summary & logs
# ============================================================

def build_summary_rows(sample_name, barcode_read_counter, barcode_umi_set):
    rows = []
    for barcode in barcode_read_counter:
        rows.append({
            "sample": sample_name,
            "cell_barcode": barcode,
            "rank": None,
            "read_count": barcode_read_counter[barcode],
            "umi_count": len(barcode_umi_set[barcode]),
        })
    rows.sort(key=lambda x: (-x["umi_count"], -x["read_count"], x["cell_barcode"]))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


def write_summary_csv(path, rows):
    header = ["sample", "cell_barcode", "rank", "CB_count", "umi_count"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({
                "sample": r["sample"],
                "cell_barcode": r["cell_barcode"],
                "rank": r["rank"],
                "CB_count": r["read_count"],
                "umi_count": r["umi_count"],
            })


def write_field_description_csv(path):
    rows = [
        {"field": "sample", "description": "样本名", "calculation": "从输入目录名或 FASTA 文件名自动提取"},
        {"field": "cell_barcode", "description": "细胞 barcode 序列", "calculation": f"根据 TSO 起始位置向前提取；TSO 前 {BARCODE_LEN+UMI_LEN}-{UMI_LEN+1} bp，共 {BARCODE_LEN} bp"},
        {"field": "rank", "description": "barcode 排名", "calculation": "按照 umi_count 降序排序；umi_count 相同时按照 CB_count 降序排序；再按 cell_barcode 升序排序"},
        {"field": "CB_count", "description": "该 cell_barcode 下的 FASTA 序列记录数", "calculation": "每成功解析到该 cell_barcode 的一条 FASTA 记录计数 +1"},
        {"field": "umi_count", "description": "该 cell_barcode 下的唯一 UMI 数量", "calculation": "对同一 cell_barcode 下解析到的 UMI 去重后计数"},
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["field", "description", "calculation"])
        w.writeheader()
        w.writerows(rows)


def write_run_log(path, lines):
    with open(path, "w") as f:
        for line in lines:
            f.write(line + "\n")


# ============================================================
# Phase 1: Sample processing
# ============================================================

def process_sample(fasta_path, sample_name, sample_out_dir):
    """Process one sample: TSO → barcode/UMI → write per-barcode TSV.

    Returns (log_lines, summary_rows, barcode_read_counter, barcode_umi_set).
    """
    start_time = time.time()
    start_datetime = datetime.now()

    if CLEAR_OUTPUT_DIR and os.path.exists(sample_out_dir):
        shutil.rmtree(sample_out_dir)
    os.makedirs(sample_out_dir, exist_ok=True)

    tso_upper = TSO_SEQ.upper()

    # Output paths
    not_found_path = os.path.join(sample_out_dir, TSO_NOT_FOUND_FASTA)
    extract_failed_path = os.path.join(sample_out_dir, EXTRACT_FAILED_FASTA)
    summary_path = os.path.join(sample_out_dir, SUMMARY_CSV)
    run_log_path = os.path.join(sample_out_dir, RUN_LOG_TXT)
    field_desc_path = os.path.join(sample_out_dir, FIELD_DESCRIPTION_CSV)

    # Per-barcode TSV directory
    tsv_dir = os.path.join(sample_out_dir, BARCODE_TSV_DIRNAME)
    os.makedirs(tsv_dir, exist_ok=True)

    # Counters
    total_reads = 0
    tso_found_reads = 0
    tso_not_found_reads = 0
    extract_failed_reads = 0
    output_reads = 0

    barcode_read_counter = Counter()
    barcode_umi_set = defaultdict(set)

    # Open file handles for per-barcode TSV files, with LRU eviction
    # to avoid hitting the OS open-file limit when a sample has many barcodes.
    MAX_OPEN_HANDLES = 200
    barcode_handles = {}      # barcode → open file handle
    barcode_opened = set()    # barcodes ever opened (including evicted)
    _handle_access_order = []  # LRU: most recently used at the end

    def _get_tsv_handle(barcode):
        """Get or create a TSV file handle for a barcode.

        Manages a bounded LRU pool: when MAX_OPEN_HANDLES is exceeded,
        the least recently used handle is closed. First open per barcode
        uses "w" (truncate), evicted-then-reopened uses "a" (append).
        """
        nonlocal barcode_handles, barcode_opened, _handle_access_order

        # Fast path: already open → bump to MRU
        if barcode in barcode_handles:
            _handle_access_order.remove(barcode)
            _handle_access_order.append(barcode)
            return barcode_handles[barcode]

        # Evict LRU if at capacity
        while len(barcode_handles) >= MAX_OPEN_HANDLES:
            evict = _handle_access_order.pop(0)
            barcode_handles.pop(evict).close()

        # Open handle: truncate on first open, append on re-open after eviction
        path = os.path.join(tsv_dir, f"{barcode}.tsv")
        mode = "a" if barcode in barcode_opened else "w"
        fh = open(path, mode)
        barcode_handles[barcode] = fh
        barcode_opened.add(barcode)
        _handle_access_order.append(barcode)
        return fh

    try:
        with (
            open(not_found_path, "w") as not_found_fa,
            open(extract_failed_path, "w") as failed_fa,
            open_maybe_gzip(fasta_path) as fasta_handle,
        ):
            for record in SeqIO.parse(fasta_handle, sequence_format(fasta_path)):
                total_reads += 1
                if total_reads == 1 or total_reads % SPLIT_READ_PROGRESS_EVERY == 0:
                    print(
                        f"[split phase1 read] {sample_name} reads={total_reads} "
                        f"output={output_reads}",
                        flush=True,
                    )
                header = record.description
                seq = str(record.seq)
                seq_upper = seq.upper()

                tso_start = find_tso_position(seq_upper, tso_upper)

                # Case 1: TSO not found
                if tso_start is None:
                    tso_not_found_reads += 1
                    write_fasta_record(not_found_fa, f"{header}#TSO_NOT_FOUND", seq)
                    continue

                tso_found_reads += 1

                # Case 2: cannot extract barcode/UMI
                barcode, umi = extract_barcode_umi(seq_upper, tso_start)
                if barcode is None or umi is None:
                    extract_failed_reads += 1
                    write_fasta_record(failed_fa, f"{header}#EXTRACT_FAILED", seq)
                    continue

                # Case 3: success → write to per-barcode TSV
                fh = _get_tsv_handle(barcode)
                mean_quality, expected_errors = record_quality(record)
                quality_text = "" if mean_quality is None else f"{mean_quality:.4f}"
                error_text = "" if expected_errors is None else f"{expected_errors:.8g}"
                # Keep the legacy first three columns; optional scalar quality
                # fields allow phase 2 to resolve equal-count candidates.
                fh.write(f"{umi}\t{header}\t{seq}\t{quality_text}\t{error_text}\n")

                barcode_read_counter[barcode] += 1
                barcode_umi_set[barcode].add(umi)
                output_reads += 1

    finally:
        # Close all TSV handles
        for fh in barcode_handles.values():
            fh.close()

    print(
        f"[split phase1 done] {sample_name} reads={total_reads} "
        f"output={output_reads} tso_not_found={tso_not_found_reads} "
        f"extract_failed={extract_failed_reads}",
        flush=True,
    )

    # Write summaries
    summary_rows = build_summary_rows(sample_name, barcode_read_counter, barcode_umi_set)
    write_summary_csv(summary_path, summary_rows)
    write_field_description_csv(field_desc_path)

    end_time = time.time()
    elapsed = end_time - start_time

    log_lines = [
        f"Sample: {sample_name}",
        f"Input: {fasta_path}",
        f"Output dir: {sample_out_dir}",
        f"Start time: {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}",
        f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Elapsed seconds: {elapsed:.2f}",
        "",
        f"Total reads: {total_reads}",
        f"TSO found reads: {tso_found_reads}",
        f"TSO not found reads: {tso_not_found_reads}",
        f"Extract failed reads: {extract_failed_reads}",
        f"Output reads: {output_reads}",
        f"Valid cell barcodes: {len(barcode_read_counter)}",
        f"TSV files written: {len(barcode_handles)} → {tsv_dir}/",
        f"Summary CSV: {summary_path}",
        f"Field description CSV: {field_desc_path}",
        f"TSO not found FASTA: {not_found_path}",
        f"Extract failed FASTA: {extract_failed_path}",
        f"Run log TXT: {run_log_path}",
    ]
    write_run_log(run_log_path, log_lines)

    return log_lines, summary_rows, barcode_read_counter, barcode_umi_set


# ============================================================
# Phase 2: TSV parsing
# ============================================================

def parse_barcode_tsv(tsv_path):
    """Read a per-barcode TSV file and group by UMI.

    Yields (umi, list_of_(header, sequence, mean_quality, expected_errors))
    per unique UMI.  The last two fields are optional for legacy TSVs.
    """
    umi_groups = defaultdict(list)
    parsed_rows = 0
    with open(tsv_path, "r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            parsed_rows += 1
            if parsed_rows == 1 or parsed_rows % PHASE2_RECORD_PROGRESS_EVERY == 0:
                print(
                    f"[split phase2 parse] file={tsv_path} rows={parsed_rows:,} "
                    f"umis={len(umi_groups):,}",
                    flush=True,
                )
            parts = line.split("\t")
            if len(parts) not in (3, 5):
                raise ValueError(f"malformed barcode TSV {tsv_path}:{line_no}; expected 3 or 5 columns")
            umi, header, seq = (part.strip() for part in parts[:3])
            mean_quality = parts[3].strip() if len(parts) == 5 else ""
            expected_errors = parts[4].strip() if len(parts) == 5 else ""
            if not umi or not header or not seq:
                raise ValueError(f"empty UMI/header/sequence in {tsv_path}:{line_no}")
            if not re.fullmatch(r"[ACGTN]+", umi.upper()):
                raise ValueError(f"invalid UMI in {tsv_path}:{line_no}: {umi}")
            seq = seq.upper()
            for value, label in ((mean_quality, "mean quality"),
                                 (expected_errors, "expected errors")):
                if value:
                    try:
                        float(value)
                    except ValueError as exc:
                        raise ValueError(
                            f"invalid {label} in {tsv_path}:{line_no}: {value}"
                        ) from exc
            umi_groups[umi].append((header, seq, mean_quality, expected_errors))
    print(
        f"[split phase2 parse] file={tsv_path} rows={parsed_rows:,} "
        f"umis={len(umi_groups):,} status=done",
        flush=True,
    )
    for umi, records in umi_groups.items():
        yield umi, records


def process_umi_group(umi, records):
    """Count sequences within one UMI group, find representative.

    Returns dict with stats + sequences list.
    """
    counter = Counter()
    headers_by_seq = {}
    quality_by_seq = defaultdict(list)
    for record in records:
        header, seq = record[:2]
        counter[seq] += 1
        # Keep a deterministic original PANDAseq header for the selected
        # sequence.  The first input row is not stable across parallel/rerun
        # order, so choose the lexicographically smallest header.
        if seq not in headers_by_seq or header < headers_by_seq[seq]:
            headers_by_seq[seq] = header
        if len(record) >= 4:
            try:
                mean_quality = float(record[2]) if record[2] else None
                expected_errors = float(record[3]) if record[3] else None
            except (TypeError, ValueError):
                mean_quality = expected_errors = None
            if mean_quality is not None or expected_errors is not None:
                quality_by_seq[seq].append((mean_quality, expected_errors))

    total = sum(counter.values())
    if total == 0:
        return {
            "total_sequences": 0,
            "unique_sequences": 0,
            "sequences": [],
        }

    unique = len(counter)
    max_count = max(counter.values())
    top_candidates = [seq for seq, count in counter.items() if count == max_count]

    def quality_stats(seq):
        values = quality_by_seq.get(seq, [])
        if not values:
            return None, None
        mean_values = [value[0] for value in values if value[0] is not None]
        error_values = [value[1] for value in values if value[1] is not None]
        mean_quality = sum(mean_values) / len(mean_values) if mean_values else None
        expected_errors = sum(error_values) / len(error_values) if error_values else None
        return mean_quality, expected_errors

    quality_available = any(quality_stats(seq) != (None, None) for seq in top_candidates)
    if quality_available:
        def quality_key(seq):
            mean_quality, expected_errors = quality_stats(seq)
            return (
                expected_errors is None,
                expected_errors if expected_errors is not None else float("inf"),
                -(mean_quality if mean_quality is not None else float("-inf")),
                seq.count("N"),
            )

        ranked = sorted(
            top_candidates,
            key=lambda seq: (
                quality_key(seq), seq,
            ),
        )
        selection_method = "COUNT_THEN_QUALITY" if len(top_candidates) > 1 else "COUNT"
    else:
        ranked = sorted(top_candidates, key=lambda seq: (seq.count("N"), seq))
        selection_method = "COUNT_ONLY_NO_QUALITY"
    rep_seq = ranked[0]
    top_candidate_count = len(top_candidates)
    if top_candidate_count == 1:
        ambiguity_status = "CLEAR"
    elif quality_available:
        # A quality tie is still ambiguous even though the final lexical
        # order makes the selected value reproducible.
        best_q = quality_key(ranked[0])
        tied_quality = [seq for seq in top_candidates if quality_key(seq) == best_q]
        ambiguity_status = "TIED_AFTER_QUALITY" if len(tied_quality) > 1 else "RESOLVED_BY_QUALITY"
    else:
        ambiguity_status = "TIED_NO_QUALITY"
    alternative_top_sequences = ";".join(
        f"{seq}:{counter[seq]}:{quality_stats(seq)[0] if quality_stats(seq)[0] is not None else ''}:{quality_stats(seq)[1] if quality_stats(seq)[1] is not None else ''}"
        for seq in top_candidates
    ) if top_candidate_count > 1 else ""

    # Only the selected representative leaves this function.  The complete
    # candidate counter is still used to calculate the winner and ambiguity,
    # but non-representative sequences are not materialized or serialized.
    seq_list = [{
        "sequence": rep_seq,
        "count": max_count,
        "pct": round(max_count / total * 100, 2),
        "is_representative": "Yes",
        "header": headers_by_seq[rep_seq],
        "top_candidate_count": top_candidate_count,
        "ambiguity_status": ambiguity_status,
        "alternative_top_sequences": alternative_top_sequences,
        "mean_quality": "" if quality_stats(rep_seq)[0] is None else f"{quality_stats(rep_seq)[0]:.4f}",
        "expected_errors": "" if quality_stats(rep_seq)[1] is None else f"{quality_stats(rep_seq)[1]:.8g}",
        "selection_method": selection_method,
    }]

    return {
        "total_sequences": total,
        "unique_sequences": unique,
        "sequences": seq_list,
    }


def process_barcode_tsv(sample, barcode, tsv_path):
    """Process one barcode TSV: one selected representative row per UMI.

    Non-representative candidates are used only during the in-memory count.
    For tied top sequences, their counts remain in ``alternative_top_sequences``
    on the selected row for audit, rather than being repeated in the state file.
    """
    results = []
    try:
        for umi, records in parse_barcode_tsv(tsv_path):
            stats = process_umi_group(umi, records)
            ts = stats["total_sequences"]
            us = stats["unique_sequences"]
            representative_id = "REP_" + hashlib.sha256(
                f"{sample}\0{barcode}\0{umi}".encode("utf-8")
            ).hexdigest()[:24]
            representative = next(
                (candidate for candidate in stats["sequences"]
                 if candidate.get("is_representative") == "Yes"),
                None,
            )
            if representative is not None:
                results.append({
                    "sample": sample,
                    "barcode": barcode,
                    "umi": umi,
                    "total_sequences": ts,
                    "unique_sequences": us,
                    "sequence": representative["sequence"],
                    "count": representative["count"],
                    "pct": representative["pct"],
                    "is_representative": "Yes",
                    "header": representative["header"],
                    "representative_id": representative_id,
                    "top_candidate_count": representative.get("top_candidate_count", 1),
                    "ambiguity_status": representative.get("ambiguity_status", "CLEAR"),
                    "alternative_top_sequences": representative.get("alternative_top_sequences", ""),
                    "mean_quality": representative.get("mean_quality", ""),
                    "expected_errors": representative.get("expected_errors", ""),
                    "selection_method": representative.get("selection_method", "COUNT_ONLY_NO_QUALITY"),
                })
            if not stats["sequences"]:
                results.append({
                    "sample": sample,
                    "barcode": barcode,
                    "umi": umi,
                    "total_sequences": ts,
                    "unique_sequences": us,
                    "sequence": "",
                    "count": 0,
                    "pct": 0.0,
                    "is_representative": "No",
                    "header": "",
                    "representative_id": representative_id,
                    "top_candidate_count": 0,
                    "ambiguity_status": "ERROR",
                    "alternative_top_sequences": "",
                })
    except Exception as exc:
        results.append({
            "sample": sample,
            "barcode": barcode,
            "umi": "",
            "total_sequences": None,
            "unique_sequences": None,
            "sequence": None,
            "count": None,
            "pct": None,
            "is_representative": None,
            "header": None,
            "representative_id": None,
            "top_candidate_count": None,
            "ambiguity_status": "ERROR",
            "alternative_top_sequences": "",
            "mean_quality": None,
            "expected_errors": None,
            "selection_method": "ERROR",
            "error": str(exc),
        })
    return results


# ============================================================
# Phase 2: File discovery
# ============================================================

def discover_barcode_tsvs(output_dir):
    """Yield ``(sample, barcode, tsv_path)`` recursively.

    ``sample`` is the path relative to ``output_dir`` (for example
    ``Lane03/sample_A``), not only the leaf basename.  Keeping that key
    prevents equal sample names from different lanes from being aggregated.
    """
    if not os.path.isdir(output_dir):
        return
    for root, dirs, files in os.walk(output_dir):
        dirs.sort()
        if os.path.basename(root) != BARCODE_TSV_DIRNAME:
            continue
        sample_dir = os.path.dirname(root)
        sample = os.path.relpath(sample_dir, output_dir)
        for tsv_entry in sorted(files):
            if tsv_entry.endswith(".tsv"):
                barcode = tsv_entry[:-4]
                yield sample, barcode, os.path.join(root, tsv_entry)


# ============================================================
# Phase 2: Batch worker (multiprocessing)
# ============================================================

def process_batch(items):
    """Process a batch of (sample, barcode, tsv_path) tuples.

    TSVs are retained until the parent process has durably written all
    representative outputs.  This makes an interrupted phase recoverable.
    Returns list of flat result dicts.
    """
    results = []
    for sample, barcode, tsv_path in items:
        try:
            batch_results = process_barcode_tsv(sample, barcode, tsv_path)
            results.extend(batch_results)
        except Exception as exc:
            results.append({
                "sample": sample,
                "barcode": barcode,
                "umi": "",
                "total_sequences": None,
                "unique_sequences": None,
                "sequence": None,
                "count": None,
                "pct": None,
                "is_representative": None,
                "header": None,
                "error": str(exc),
            })
    return results


# ============================================================
# Phase 2: CSV output
# ============================================================

def load_umi_rows(csv_path):
    """Load compact state (or a legacy CSV state) for incremental merging."""
    rows = []
    if not os.path.isfile(csv_path):
        return rows
    try:
        opener = gzip.open if str(csv_path).endswith((".gz", ".gzip")) else open
        with opener(csv_path, "rt" if opener is gzip.open else "r", newline="") as fh:
            for row in csv.DictReader(fh):
                normalized = {field: row.get(field, "") for field in CSV_FIELDNAMES}
                rows.append(normalized)
    except (OSError, csv.Error):
        # A partially-written CSV must not be treated as a valid checkpoint.
        return []
    return rows


def _umi_row_key(row):
    """Stable identity for one compact representative row."""
    sample = row.get("sample", "")
    barcode = row.get("barcode", "")
    umi = row.get("umi", "")
    if not umi:
        return (sample, barcode, "", row.get("error", ""))
    return (sample, barcode, umi)


def compact_umi_rows(rows):
    """Collapse legacy candidate rows to one representative row per UMI."""
    selected = {}
    order = []

    def rank(row):
        if row.get("error"):
            return 0
        if row.get("is_representative") == "Yes" and row.get("sequence"):
            return 3
        if row.get("sequence"):
            return 2
        return 1

    for row in rows:
        key = _umi_row_key(row)
        if key not in selected:
            selected[key] = row
            order.append(key)
        elif rank(row) > rank(selected[key]):
            selected[key] = row
    return [selected[key] for key in order]


def merge_umi_rows(existing, new_rows):
    """Merge rerun results idempotently.

    If a rerun regenerates an existing UMI, replace all old rows for that UMI
    with the new rows.  This both prevents duplicate appends and allows a
    previously failed UMI to be repaired without retaining its old error row.
    """
    updated_umis = {
        (row.get("sample", ""), row.get("barcode", ""), row.get("umi", ""))
        for row in new_rows
        if row.get("umi", "")
    }
    repaired_pairs = {
        (row.get("sample", ""), row.get("barcode", ""))
        for row in new_rows
        if row.get("umi", "") and not row.get("error")
    }
    merged = [
        row for row in existing
        if (row.get("sample", ""), row.get("barcode", ""), row.get("umi", ""))
        not in updated_umis
        and not (row.get("error") and
                 (row.get("sample", ""), row.get("barcode", "")) in repaired_pairs)
    ]
    merged.extend(new_rows)
    return compact_umi_rows(merged)


def write_umi_csv(results, path, append=False):
    """Write compact reducer state atomically (gzip when path ends in .gz)."""
    del append  # retained for compatibility with older callers
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".umi.", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        opener = gzip.open if str(path).endswith((".gz", ".gzip")) else open
        with opener(temp_path, "wt" if opener is gzip.open else "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            written = 0
            for row in results:
                writer.writerow(row)
                written += 1
                if written == 1 or written % STATE_WRITE_PROGRESS_EVERY == 0:
                    print(f"  [representative state] written={written:,} rows", flush=True)
        os.replace(temp_path, path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
    return path


def load_processed_set(csv_path):
    """Read existing CSV and return set of (sample, barcode, umi) already processed."""
    processed = set()
    if not os.path.exists(csv_path):
        return processed
    try:
        with open(csv_path, "r", newline="") as fh:
            for row in csv.DictReader(fh):
                sm = row.get("sample", "")
                bc = row.get("barcode", "")
                um = row.get("umi", "")
                if sm and bc and um:
                    processed.add((sm, bc, um))
    except Exception:
        pass
    return processed


def count_rows_in_csv(csv_path):
    if not os.path.exists(csv_path):
        return 0
    with open(csv_path, "r") as fh:
        return max(0, sum(1 for _ in fh) - 1)


# ============================================================
# Phase 2: Barcode-level aggregation
# ============================================================

def write_barcode_csv(results, path):
    """Aggregate per-UMI results into per-barcode summary.

    Output columns:
        sample, barcode, umi_count, total_reads,
        mean_representative_pct, median_representative_pct,
        mean_total_sequences, mean_unique_sequences
    """
    from statistics import mean, median

    # Group by (sample, barcode)
    # First pass: collect UMI-level stats
    barcode_data = defaultdict(lambda: {
        "umi_set": set(),
        "umi_total_seqs": {},   # umi → total_sequences
        "umi_unique_seqs": {},  # umi → unique_sequences
        "rep_pcts": [],          # pct of representative sequence per UMI
    })

    for row in results:
        if row.get("error"):
            continue
        sm = row["sample"]
        bc = row["barcode"]
        um = row["umi"]
        if not sm or not bc or not um:
            continue

        bd = barcode_data[(sm, bc)]
        bd["umi_set"].add(um)

        # Only record each UMI once (first occurrence has total_sequences/unique_sequences)
        if um not in bd["umi_total_seqs"]:
            bd["umi_total_seqs"][um] = row.get("total_sequences", 0) or 0
            bd["umi_unique_seqs"][um] = row.get("unique_sequences", 0) or 0

        if row.get("is_representative") == "Yes":
            bd["rep_pcts"].append(row.get("pct", 0) or 0)

    # Build output rows
    rows = []
    for (sm, bc), bd in sorted(barcode_data.items()):
        umi_count = len(bd["umi_set"])
        total_reads = sum(bd["umi_total_seqs"].values())
        mean_rep = round(mean(bd["rep_pcts"]), 2) if bd["rep_pcts"] else 0.0
        median_rep = round(median(bd["rep_pcts"]), 2) if bd["rep_pcts"] else 0.0
        mean_total = round(mean(bd["umi_total_seqs"].values()), 2) if bd["umi_total_seqs"] else 0.0
        mean_unique = round(mean(bd["umi_unique_seqs"].values()), 2) if bd["umi_unique_seqs"] else 0.0

        rows.append({
            "sample": sm,
            "barcode": bc,
            "umi_count": umi_count,
            "total_reads": total_reads,
            "mean_representative_pct": mean_rep,
            "median_representative_pct": median_rep,
            "mean_total_sequences": mean_total,
            "mean_unique_sequences": mean_unique,
        })

    # Sort: umi_count desc, total_reads desc
    rows.sort(key=lambda x: (-x["umi_count"], -x["total_reads"]))

    fieldnames = [
        "sample", "barcode",
        "umi_count", "total_reads",
        "mean_representative_pct", "median_representative_pct",
        "mean_total_sequences", "mean_unique_sequences",
    ]
    temp_path = path + ".tmp"
    with open(temp_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    os.replace(temp_path, path)

    return path


# ============================================================
# Phase 2: Simplified durable outputs
# ============================================================

REPRESENTATIVE_MAP_FIELDS = [
    "representative_id", "sample_id", "barcode", "umi", "total_reads",
    "representative_reads", "representative_percent", "unique_sequence_count",
    "top_candidate_count", "ambiguity_status", "alternative_top_sequences",
    "mean_quality", "expected_errors", "selection_method",
]


def representative_rows(results):
    """Return one selected row per valid UMI."""
    return [r for r in results
            if not r.get("error") and r.get("is_representative") == "Yes"
            and r.get("sequence") and r.get("umi")]


def write_representative_map(results, path):
    rows = representative_rows(results)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".representative_map.", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        with gzip.open(temp_path, "wt", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REPRESENTATIVE_MAP_FIELDS,
                                    delimiter="\t", extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "representative_id": row["representative_id"],
                    "sample_id": row["sample"], "barcode": row["barcode"],
                    "umi": row["umi"], "total_reads": row["total_sequences"],
                    "representative_reads": row["count"],
                    "representative_percent": row["pct"],
                    "unique_sequence_count": row["unique_sequences"],
                    "top_candidate_count": row.get("top_candidate_count", 1),
                    "ambiguity_status": row.get("ambiguity_status", "CLEAR"),
                    "alternative_top_sequences": row.get("alternative_top_sequences", ""),
                    "mean_quality": row.get("mean_quality", ""),
                    "expected_errors": row.get("expected_errors", ""),
                    "selection_method": row.get("selection_method", "COUNT_ONLY_NO_QUALITY"),
                })
        os.replace(temp_path, path)
    finally:
        try: os.remove(temp_path)
        except OSError: pass
    return path


def write_representative_summary(results, path):
    groups = defaultdict(lambda: {"input": 0, "matched": 0, "umis": set(),
                                  "reps": 0, "ambiguous": 0, "errors": []})
    seen = set()
    for row in results:
        key = (row.get("sample", ""), row.get("barcode", ""))
        if not key[0] or not key[1]:
            continue
        item = groups[key]
        umi_key = (key[0], key[1], row.get("umi", ""))
        if row.get("umi") and umi_key not in seen:
            seen.add(umi_key)
            item["input"] += int(row.get("total_sequences") or 0)
            item["matched"] += int(row.get("total_sequences") or 0)
            item["umis"].add(row["umi"])
            item["reps"] += int(row.get("is_representative") == "Yes")
            item["ambiguous"] += int(
                row.get("ambiguity_status") in {
                    "TIED_TOP", "TIED_NO_QUALITY", "TIED_AFTER_QUALITY"
                }
            )
        if row.get("error"):
            item["errors"].append(str(row["error"]))
    fields = ["sample_id", "barcode", "input_reads", "matched_reads", "umi_count",
              "representative_count", "ambiguous_umi_count", "ambiguous_umi_percent",
              "status", "error"]
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp_path = path + ".tmp"
    with open(temp_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for (sample, barcode), item in sorted(groups.items()):
            n = len(item["umis"])
            writer.writerow({"sample_id": sample, "barcode": barcode,
                "input_reads": item["input"], "matched_reads": item["matched"],
                "umi_count": n, "representative_count": item["reps"],
                "ambiguous_umi_count": item["ambiguous"],
                "ambiguous_umi_percent": f"{(item['ambiguous']*100/n if n else 0):.2f}",
                "status": "ERROR" if item["errors"] else "OK",
                "error": "; ".join(sorted(set(item["errors"])))})
    os.replace(temp_path, path)
    return path


# ============================================================
# Phase 2: Representative FASTA output
# ============================================================

def write_fasta_output(results, output_dir):
    """Write representative sequences — one FASTA per barcode per sample.

    Groups by (sample, barcode), writes <output_dir>/<sample>/<barcode>.fasta.
    """
    rep_by_key = defaultdict(list)  # (sample, barcode) → [(header, seq), ...]
    seen = set()  # (sample, barcode, umi)
    for row in results:
        if row.get("error") or not row.get("sequence"):
            continue
        if row.get("is_representative") != "Yes":
            continue
        key = (row["sample"], row["barcode"], row["umi"])
        if key in seen:
            continue
        seen.add(key)
        rep_by_key[(row["sample"], row["barcode"])].append(
            (row.get("header", ""), row["sequence"])
        )

    written = 0
    t0 = time.time()

    seen_headers_by_sample = defaultdict(set)
    complete = True
    for (sample, barcode), entries in rep_by_key.items():
        sample_dir = os.path.join(output_dir, sample)
        os.makedirs(sample_dir, exist_ok=True)
        out_path = os.path.join(sample_dir, f"{barcode}.fasta")

        # The original state schema accidentally omitted ``header``.  When a
        # legacy state is incrementally upgraded, preserve the already valid
        # FASTA records and append only newly generated rows that still carry
        # their original header; never invent a replacement sequence_id.
        missing_headers = any(not header for header, _seq in entries)
        if missing_headers:
            complete = False
            if not os.path.isfile(out_path):
                raise ValueError(
                    f"representative header missing and no existing FASTA to preserve: {out_path}"
                )
            existing_headers = set()
            with open(out_path, "r", encoding="utf-8", errors="replace") as existing:
                for line in existing:
                    if line.startswith(">"):
                        existing_headers.add(line[1:].strip().split()[0])
            valid_entries = [(header, seq) for header, seq in entries if header]
            if valid_entries:
                temp_path = out_path + f".tmp.{os.getpid()}"
                shutil.copyfile(out_path, temp_path)
                try:
                    with open(temp_path, "a", encoding="utf-8") as out:
                        for header, seq in valid_entries:
                            if header in existing_headers:
                                continue
                            if header in seen_headers_by_sample[sample]:
                                raise ValueError(f"duplicate representative header in sample: {header}")
                            seen_headers_by_sample[sample].add(header)
                            out.write(f">{header}\n")
                            for line in wrap_sequence(mapping_sequence(seq)):
                                out.write(line + "\n")
                            existing_headers.add(header)
                            written += 1
                    os.replace(temp_path, out_path)
                finally:
                    try: os.remove(temp_path)
                    except OSError: pass
            continue

        temp_path = out_path + ".tmp"
        with open(temp_path, "w") as fh:
            for header, seq in entries:
                if not header:
                    raise ValueError(f"representative header missing for {sample}/{barcode}")
                # Original headers are the public FASTA IDs.  Duplicates in a
                # sample make the later IgBLAST header->barcode mapping
                # ambiguous and must fail instead of silently overwriting.
                if header in seen_headers_by_sample[sample]:
                    raise ValueError(f"duplicate representative header in {sample}: {header}")
                seen_headers_by_sample[sample].add(header)
                fh.write(f">{header}\n")
                for line in wrap_sequence(mapping_sequence(seq)):
                    fh.write(line + "\n")
        os.replace(temp_path, out_path)
        written += len(entries)

    elapsed = time.time() - t0
    if written:
        print(f"  Representative FASTA: {written} sequences "
              f"({len(rep_by_key)} sample-barcode pairs) in {elapsed:.1f}s")
    return complete


def representative_fasta_manifest_current(output_dir):
    """Return whether the public FASTA matches the current query contract."""
    path = os.path.join(output_dir, REPRESENTATIVE_FASTA_MANIFEST)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError, TypeError):
        return False
    return (
        manifest.get("schema") == "representative_fasta_mapping_v1"
        and manifest.get("config_fingerprint") == CONFIG_FINGERPRINT
        and manifest.get("trim_r1_technical_prefix") == TRIM_R1_TECHNICAL_PREFIX
    )


def write_representative_fasta_manifest(output_dir):
    """Atomically record the sequence contract used for public FASTA files."""
    path = os.path.join(output_dir, REPRESENTATIVE_FASTA_MANIFEST)
    temp_path = path + f".tmp.{os.getpid()}"
    manifest = {
        "schema": "representative_fasta_mapping_v1",
        "config_fingerprint": CONFIG_FINGERPRINT,
        "trim_r1_technical_prefix": TRIM_R1_TECHNICAL_PREFIX,
        "tso_seq": TSO_SEQ,
        "tso_pos": TSO_POS,
        "barcode_len": BARCODE_LEN,
        "umi_len": UMI_LEN,
        "mapping_sequence": "after_tso" if TRIM_R1_TECHNICAL_PREFIX else "raw",
    }
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


# ============================================================
# Phase 2: Main
# ============================================================

def configure_phase2_memory():
    """Keep phase2 worker limits within the configured machine budget."""
    global WORKERS
    if PHASE2_MEMORY_LIMIT_GB > 0 and MEMORY_BUDGET_GB > 0:
        # Reserve one worker-equivalent for the parent process and filesystem
        # cache. The runner also accounts for WORKERS × per-process memory.
        allowed_workers = max(1, MEMORY_BUDGET_GB // PHASE2_MEMORY_LIMIT_GB - 1)
        if WORKERS > allowed_workers:
            print(
                f"Reducing phase2 workers {WORKERS}->{allowed_workers} to stay "
                f"within {MEMORY_BUDGET_GB} GB budget",
                flush=True,
            )
            WORKERS = allowed_workers
    if resource is not None and PHASE2_MEMORY_LIMIT_GB > 0:
        limit_bytes = PHASE2_MEMORY_LIMIT_GB * 1024 * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
            print(f"Phase2 per-process memory limit: {PHASE2_MEMORY_LIMIT_GB} GB", flush=True)
        except (OSError, ValueError):
            print("Warning: unable to apply phase2 RLIMIT_AS; worker budget still enforced", flush=True)

def phase2_represent(output_dir):
    """Phase 2: read per-barcode TSV files, compute representative sequences."""
    print("\n" + "=" * 60)
    print("Phase 2: Computing representative sequences")
    print("=" * 60)

    configure_phase2_memory()

    output_dir = os.path.abspath(output_dir)
    fasta_out_dir = os.path.join(output_dir, REPRESENTATIVE_FASTA_DIR)
    map_path = os.path.join(output_dir, REPRESENTATIVE_MAP)
    summary_path = os.path.join(output_dir, REPRESENTATIVE_SUMMARY)
    # Remove only legacy files produced by this stage; all durable outputs
    # from the simplified schema are kept across reruns.
    for legacy_name in ("representative_stats_umi.csv", "representative_stats_barcode.csv"):
        try:
            os.remove(os.path.join(output_dir, legacy_name))
        except FileNotFoundError:
            pass

    # Incremental: load already-written rows.  TSVs are only removed after a
    # successful commit, so a rerun can safely regenerate the same UMI and the
    # merge below will replace it instead of appending duplicates.
    state_path = os.path.join(output_dir, REPRESENTATIVE_STATE)
    legacy_state_path = os.path.join(output_dir, LEGACY_REPRESENTATIVE_STATE)
    existing_rows_data = load_umi_rows(state_path)
    legacy_state_loaded = False
    if not existing_rows_data and os.path.isfile(legacy_state_path):
        existing_rows_data = load_umi_rows(legacy_state_path)
        legacy_state_loaded = bool(existing_rows_data)
        if legacy_state_loaded:
            print(
                f"  Loaded legacy state ({len(existing_rows_data):,} rows); "
                "will compact it after this commit",
                flush=True,
            )
    if existing_rows_data:
        print(f"  Loaded {len(existing_rows_data):,} rows from durable representative state")
    elif os.path.isfile(map_path):
        print("  Warning: public representative map exists without reducer state; "
              "new rows cannot safely reconstruct historical sequences. "
              "Retaining it until a full phase-2 rebuild is available.", flush=True)

    # Discover all barcode TSV files
    print("Discovering barcode TSV files...")
    t0 = time.time()
    all_files = list(discover_barcode_tsvs(output_dir))
    print(f"  Found {len(all_files)} barcode TSVs in {time.time() - t0:.1f}s")

    if not all_files:
        if os.path.isfile(map_path):
            representative_files = []
            if os.path.isdir(fasta_out_dir):
                for root, _dirs, files in os.walk(fasta_out_dir):
                    representative_files.extend(
                        os.path.join(root, name)
                        for name in files
                        if name.endswith((".fasta", ".fa"))
                    )
            if representative_files:
                if existing_rows_data and not representative_fasta_manifest_current(output_dir):
                    print("Public representative FASTA is stale; rebuilding mapping-ready sequences.")
                    if write_fasta_output(existing_rows_data, fasta_out_dir):
                        write_representative_fasta_manifest(output_dir)
                print("No pending barcode TSVs; existing representative outputs are complete.")
                return True
        print("No barcode TSV files found across sample directories.")
        return False

    # A TSV still present on disk is pending work.  It may contain UMIs that
    # already exist in the CSV after an interrupted run; merge_umi_rows() makes
    # that rerun idempotent.
    new_files = all_files

    if not new_files:
        print("All barcodes already processed — nothing to do.")
        return

    # Partition into batches
    batches = [
        new_files[i:i + BATCH_SIZE]
        for i in range(0, len(new_files), BATCH_SIZE)
    ]
    print(f"  {len(batches)} batches (batch size: {BATCH_SIZE})")

    # Multiprocessing
    print(f"Processing with {WORKERS} workers...")
    t1 = time.time()
    all_results = []

    pool = multiprocessing.Pool(processes=WORKERS)
    try:
        for i, batch_results in enumerate(
            pool.imap_unordered(process_batch, batches), 1
        ):
            all_results.extend(batch_results)
            if i % PHASE2_PROGRESS_EVERY == 0 or i == len(batches):
                elapsed = time.time() - t1
                rate = len(all_results) / elapsed if elapsed > 0 else 0
                pct = i * 100 / len(batches) if batches else 100.0
                print(f"  [split phase2 {i}/{len(batches)} {pct:.1f}%] "
                      f"{len(all_results):,} sequence rows "
                      f"({rate:.0f} rows/s)", flush=True)
    finally:
        pool.close()
        pool.join()

    elapsed = time.time() - t1
    print(f"  Done in {elapsed:.1f}s ({len(all_results) / elapsed:.0f} rows/s)")

    # Commit all outputs from one deduplicated snapshot.  Never append directly
    # to the final CSV: an interruption between append and FASTA generation
    # previously left the three outputs inconsistent.
    combined_rows = merge_umi_rows(existing_rows_data, all_results)
    # Commit the complete reducer state first.  It contains source headers and
    # sequences, unlike the compact public map, and is written atomically so a
    # later incremental run cannot drop already-completed samples.
    write_umi_csv(combined_rows, state_path)
    write_representative_map(combined_rows, map_path)
    write_representative_summary(combined_rows, summary_path)
    print(f"  Representative map: {map_path}")
    print(f"  Representative summary: {summary_path}")

    # Write representative FASTA
    if write_fasta_output(combined_rows, fasta_out_dir):
        write_representative_fasta_manifest(output_dir)

    # Keep the old full CSV for manual audit by default.  After validating the
    # compact state and public outputs, users may opt in to deleting it with
    # SCIGBLAST_10X_DELETE_LEGACY_STATE=1.
    if legacy_state_loaded and os.environ.get("SCIGBLAST_10X_DELETE_LEGACY_STATE", "0") == "1":
        try:
            os.remove(legacy_state_path)
            print(f"  Removed legacy full state: {legacy_state_path}", flush=True)
        except OSError as exc:
            print(f"  Warning: unable to remove legacy state: {exc}", flush=True)

    # Delete intermediate TSVs only after outputs are complete and no barcode
    # worker reported an error; failed TSVs remain available for retry.
    if not any(r.get("error") for r in all_results):
        for _sample, _barcode, tsv_path in all_files:
            try:
                os.remove(tsv_path)
            except OSError:
                pass

    # Clean up empty _barcodes directories
    _cleanup_empty_tsv_dirs(output_dir)

    # Summary
    error_count = sum(1 for r in all_results if r.get("error"))
    pcts = [r["pct"] for r in all_results if r.get("pct") is not None]
    barcode_set = set()
    umi_set = set()
    sample_set = set()
    for r in combined_rows:
        if not r.get("error") and r.get("barcode"):
            sample_set.add(r["sample"])
            barcode_set.add((r["sample"], r["barcode"]))
            umi_set.add((r["sample"], r["barcode"], r["umi"]))

    print(f"\n{'='*60}")
    print("Phase 2 Summary:")
    print(f"  Sequence rows written:     {len(all_results):,}")
    print(f"  Unique UMIs processed:     {len(umi_set):,}")
    print(f"  Unique sample-barcodes:    {len(barcode_set):,}")
    print(f"  Samples:                   {len(sample_set):,}")
    print(f"  Errors:                    {error_count}")
    if pcts:
        print(f"  Mean sequence pct:         {mean(pcts):.2f}%")
        print(f"  Median sequence pct:       {median(pcts):.2f}%")
    total_rows = len(combined_rows)
    print(f"  Total CSV rows (cumulative): {total_rows:,}")
    print(f"  Total elapsed:             {time.time() - t0:.1f}s")
    print(f"{'='*60}")
    return error_count == 0


def _cleanup_empty_tsv_dirs(output_dir):
    """Remove empty _barcodes directories after all TSVs have been consumed."""
    for root, dirs, files in os.walk(output_dir, topdown=False):
        if os.path.basename(root) != BARCODE_TSV_DIRNAME:
            continue
        try:
            remaining = os.listdir(root)
            if not remaining:
                os.rmdir(root)
            else:
                print(f"  Warning: {len(remaining)} TSV file(s) remain in {root}")
        except OSError:
            pass


def phase1_checkpoint_valid(marker_path, fasta_path, sample_out_dir):
    """Validate a sample checkpoint against its input and metadata outputs."""
    if not os.path.isfile(marker_path):
        return False
    if not all(os.path.isfile(os.path.join(sample_out_dir, name))
               for name in (SUMMARY_CSV, RUN_LOG_TXT)):
        return False
    try:
        with open(marker_path, "r", encoding="utf-8") as handle:
            marker = json.load(handle)
        stat = os.stat(fasta_path)
        return (
            marker.get("status") == "DONE"
            and marker.get("config_fingerprint") == CONFIG_FINGERPRINT
            and os.path.abspath(str(marker.get("input", ""))) == os.path.abspath(str(fasta_path))
            and int(marker.get("size", -1)) == int(stat.st_size)
            and int(marker.get("mtime_ns", -1)) == int(stat.st_mtime_ns)
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


# ============================================================
# Main
# ============================================================

def main():
    global_start_time = time.time()
    stage_marker = os.path.join(OUTPUT_DIR, ".split.DONE")

    # ---- Phase 1: Split ----
    print("=" * 60)
    print("Phase 1: Splitting by barcode/UMI")
    print("=" * 60)

    samples = collect_fasta_samples(INPUT_DIR)
    if not samples:
        # PANDAseq FASTA files may have been removed after a successful run.
        # Keep phase-2 outputs usable and wait for newly-added samples instead
        # of treating an otherwise complete run as a failure.
        if os.path.isfile(stage_marker):
            print(f"No new FASTA samples; existing split checkpoint found: {stage_marker}")
            return 0 if phase2_represent(OUTPUT_DIR) else 1
        print(f"[ERROR] 在 {INPUT_DIR} 中未找到任何 FASTA 样本")
        return 1

    if CLEAR_OUTPUT_DIR and os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\n找到 {len(samples)} 个样本:")
    for sample, path, mode, rel_parent in samples:
        print(f"  [{mode}] {sample}")
        print(f"    file = {path}")
    print()

    all_summary_rows = []
    total = len(samples)
    success = 0

    for i, (sample, fasta_path, mode, rel_parent) in enumerate(samples, start=1):
        # Use the source-relative sample path as the stable identity in CSVs
        # and Phase 2 aggregation.  The output directory is already that path.
        sample_key = rel_parent or sample
        pct = i * 100 / total if total else 100.0
        print(f"[split phase1 {i}/{total} {pct:.1f}%] 处理: {sample_key}", flush=True)
        # ``rel_parent`` already names the source sample directory for
        # recursive PANDAseq output (e.g. ``Lane03/sample_A``).  Do not append
        # ``sample`` again, otherwise every sample gets an artificial nested
        # ``sample_A/sample_A`` directory.  Flat FASTA input still receives a
        # leaf sample directory for isolation.
        sample_out_dir = os.path.join(OUTPUT_DIR, rel_parent or sample)
        sample_marker = os.path.join(sample_out_dir, ".phase1.DONE")

        # A checkpoint is valid only when the sample summary/log are still
        # present.  The runner may remove FASTA payloads, but these small
        # metadata files are retained to prove phase1 really completed.
        if phase1_checkpoint_valid(sample_marker, fasta_path, sample_out_dir):
            print("  [SKIPPED] phase1 checkpoint already complete")
            success += 1
            continue

        try:
            log_lines, summary_rows, bc_counter, bc_umi = process_sample(
                fasta_path, sample_key, sample_out_dir
            )
            for line in log_lines:
                print(f"  {line}")
            all_summary_rows.extend(summary_rows)
            tmp_marker = sample_marker + ".tmp"
            source_stat = os.stat(fasta_path)
            with open(tmp_marker, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "status": "DONE", "input": os.path.abspath(fasta_path),
                    "config_fingerprint": CONFIG_FINGERPRINT,
                    "size": source_stat.st_size, "mtime_ns": source_stat.st_mtime_ns,
                }, ensure_ascii=False))
            os.replace(tmp_marker, sample_marker)
            success += 1
        except Exception as e:
            print(f"  [ERROR] {sample_key} 处理失败: {e}")
        print()

    # Global summary from Phase 1
    global_summary_path = os.path.join(OUTPUT_DIR, "all_samples_summary.csv")
    # Preserve summaries for phase1 samples skipped by a checkpoint.  The
    # on-disk CSV uses CB_count while the in-memory rows use read_count.
    if os.path.isfile(global_summary_path):
        existing_rows = []
        try:
            with open(global_summary_path, "r", newline="") as handle:
                for row in csv.DictReader(handle):
                    existing_rows.append({
                        "sample": row.get("sample", ""),
                        "cell_barcode": row.get("cell_barcode", ""),
                        "rank": int(row.get("rank", 0) or 0),
                        "read_count": int(row.get("CB_count", 0) or 0),
                        "umi_count": int(row.get("umi_count", 0) or 0),
                    })
        except (OSError, ValueError):
            existing_rows = []
        all_summary_rows = existing_rows + all_summary_rows
    if all_summary_rows:
        write_summary_csv(global_summary_path, all_summary_rows)

    # Global run log
    phase1_elapsed = time.time() - global_start_time
    global_log_path = os.path.join(OUTPUT_DIR, "run_summary.txt")
    global_log_lines = [
        "Phase 1 (split) done.",
        f"Input dir: {INPUT_DIR}",
        f"Output dir: {OUTPUT_DIR}",
        f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Phase 1 elapsed: {phase1_elapsed:.2f}s",
        "",
        f"Samples found: {total}",
        f"Samples succeeded: {success}",
        f"Samples failed: {total - success}",
        f"Global summary: {global_summary_path}",
    ]
    write_run_log(global_log_path, global_log_lines)
    for line in global_log_lines:
        print(line)

    # ---- Phase 2: Representative sequences ----
    phase2_ok = phase2_represent(OUTPUT_DIR)

    if success == total and phase2_ok:
        tmp_marker = stage_marker + ".tmp"
        with open(tmp_marker, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"status": "DONE", "samples": total}, ensure_ascii=False))
        os.replace(tmp_marker, stage_marker)

    # ---- Done ----
    total_elapsed = time.time() - global_start_time
    print(f"\nTotal elapsed: {total_elapsed:.1f}s")
    return 0 if success == total and phase2_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
