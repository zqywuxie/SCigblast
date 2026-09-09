#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 8: select up to two receptor-chain candidates per barcode.

The runner invokes this stage after IgBLAST.  It reads the post-IgBLAST AIRR
tables without changing them and writes a new compact table while preserving
the input hierarchy.  A direct invocation remains supported for reprocessing
an existing IgBLAST output directory.

The hierarchy is:

    barcode + UMI                    one representative was selected in Stage 6
    barcode + locus                  at most two independent chain candidates
    V + J + CDR3 + sequence_alignment sequence variants within one candidate

The post-IgBLAST files normally no longer contain the UMI column. In that
case umi_counts is the already calculated support for the row. If an input
table has no count column, this script treats each row as one existing
UMI-level representative and assigns it a support of one. It never rebuilds
consensus sequences or attempts to recover UMI sequences.

The main output contains only the requested AIRR fields, with sequence_id
renamed to barcode, plus umi_counts. A separate summary table is written for
auditing discarded third candidates and low support alternatives.
"""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import pandas as pd


AIRR_FIELDS = [
    "sequence",
    "sequence_aa",
    "cdr3_aa",
    "junction_aa",
    "cdr3",
    "locus",
    "productive",
    "v_call",
    "d_call",
    "j_call",
    "c_call",
    "v_score",
    "v_identity",
    "v_alignment_start",
    "v_alignment_end",
    "d_alignment_start",
    "d_alignment_end",
    "j_alignment_start",
    "j_alignment_end",
    "c_alignment_start",
    "c_alignment_end",
    "sequence_alignment",
    "sequence_alignment_aa",
    "germline_alignment",
    "germline_alignment_aa",
]
OUTPUT_FIELDS = [
    "barcode",
    "locus",
    "umi_counts",
    "v_call",
    "j_call",
    "cdr3",
    "cdr3_aa",
    "junction_aa",
    *[
        field
        for field in AIRR_FIELDS
        if field not in {
            "locus",
            "v_call",
            "j_call",
            "cdr3",
            "cdr3_aa",
            "junction_aa",
        }
    ],
]

BAD_CALLS = {
    "",
    "*",
    "-",
    "NA",
    "N/A",
    "NONE",
    "NULL",
    "NO_HIT",
    "UNMAPPED",
    "NOT_FOUND",
}

# ============================================================================
# Global user configuration
# ============================================================================
# Defaults remain usable for a standalone local invocation, while the pipeline
# runner supplies dataset-scoped paths through environment variables.
INPUT_DIR_VALUE = os.environ.get("SCIGBLAST_10X_CLUSTER_INPUT", "").strip()
OUTPUT_DIR_VALUE = os.environ.get("SCIGBLAST_10X_CLUSTER_OUTPUT", "").strip()
INPUT_DIR = Path(INPUT_DIR_VALUE) if INPUT_DIR_VALUE else Path()
OUTPUT_DIR = Path(OUTPUT_DIR_VALUE) if OUTPUT_DIR_VALUE else Path()
SUMMARY_OUTPUT_DIR: Path | None = None  # None = beside each output file

# Only formal post-IgBLAST tables are inputs.  In particular, never rescan
# .batches, postprocess shards, or summaries left by an interrupted run.
INPUT_PATTERNS = ("TCR.tsv", "BCR.tsv", "TCR.tsv.gz", "BCR.tsv.gz")
RECURSIVE_INPUT_FILES = os.environ.get("SCIGBLAST_10X_CLUSTER_RECURSIVE", "1") == "1"
IGNORED_DIR_NAMES = {
    ".batches", ".postprocess", ".analysis_state", ".preprocessing_state",
    ".cluster_state", "stage8_output", "cluster_output",
}
EXCLUDE_FILENAMES = {
    "Datapoint.csv",
    "chain_summary.csv",
    "igblastn_run_summary.tsv",
    "igblast_summary.csv",
}
EXCLUDE_NAME_PARTS = (
    "stage8_",
    ".stage8_summary.",
    ".sequence_id_chain_summary.",
)

# Used only in the audit summary; it is not written to the main result.
# Files belonging to one sample can be split by chain (TCR.tsv/BCR.tsv) or by
# an upstream batch.  Use their containing relative directory as the merge
# namespace so records from the same sample can cluster together; never use a
# filename, which would incorrectly prevent cross-file clustering.
MERGE_GROUP_ID_FROM_FILENAME = False
INPUT_SEPARATOR = "auto"

MAX_CHAIN_CANDIDATES = 2
MAX_SEQUENCE_VARIANTS = 2
DOMINANT_RATIO = 3.0
MIN_ALT_FRACTION = 0.10
MAX_WORKERS = max(1, int(os.environ.get("SCIGBLAST_10X_CLUSTER_WORKERS", "1")))

# Progress display. These values are also global parameters.
SHOW_PROGRESS = True
READ_CHUNK_ROWS = 100_000
PROGRESS_EVERY_CHAIN_GROUPS = 1_000
PROGRESS_EVERY_BARCODE_GROUPS = 1_000

def _separator(path: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    name = path.name.lower()
    return "\t" if name.endswith((".tsv", ".tsv.gz", ".txt", ".txt.gz")) else ","


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _normal_nt(value: object) -> str:
    """Normalize an alignment/CDR3 key without changing the output value."""

    return re.sub(r"[\s\-.]", "", _text(value).upper())


def _number(value: object) -> float:
    """Parse one numeric AIRR value without constructing a pandas Series."""

    raw = _text(value)
    if not raw:
        return math.nan
    try:
        return float(raw)
    except (TypeError, ValueError):
        return math.nan


def _normal_gene(value: object) -> str:
    """Normalize V/J calls to gene level, ignoring allele suffixes.

    IgBLAST may return TRAV1-1*01 or several equivalent allele calls.
    Alleles are retained in the output but are not allowed to split one chain
    identity cluster by themselves.
    """

    raw = _text(value).upper()
    if raw in BAD_CALLS:
        return ""
    tokens = []
    for token in re.split(r"[,;|]", raw):
        token = token.strip()
        if not token or token in BAD_CALLS:
            continue
        tokens.append(token.split("*", 1)[0])
    return "|".join(sorted(set(tokens)))


def _valid_call(series: pd.Series) -> pd.Series:
    values = series.fillna("").astype(str).str.strip().str.upper()
    return ~values.isin(BAD_CALLS)


def _progress(message: str) -> None:
    if SHOW_PROGRESS:
        print(f"[stage8] {message}", flush=True)


def _parse_counts(series: pd.Series, source: str) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.isna().any():
        bad = int(values.isna().sum())
        raise ValueError(f"{source}: umi_counts contains {bad} non-numeric value(s)")
    if (values < 0).any() or not (values % 1 == 0).all():
        raise ValueError(f"{source}: umi_counts must be non-negative integers")
    return values.astype("int64")


def _read_one(path: Path, sep: str, merge_group_id: str, order_start: int) -> pd.DataFrame:
    chunks = []
    rows_read = 0
    reader = pd.read_csv(
        path,
        sep=_separator(path, sep),
        dtype=str,
        keep_default_na=False,
        chunksize=max(1, int(READ_CHUNK_ROWS)),
    )
    for chunk in reader:
        chunks.append(chunk)
        rows_read += len(chunk)
        _progress(f"reading file={path.name} rows={rows_read:,}")
    frame = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    _progress(f"read complete file={path.name} rows={len(frame):,}")
    frame.columns = [str(column).lstrip("\ufeff").strip() for column in frame.columns]

    if "barcode" not in frame.columns and "sequence_id" not in frame.columns:
        raise ValueError(f"{path}: missing sequence_id/barcode")
    if "barcode" not in frame.columns:
        frame = frame.rename(columns={"sequence_id": "barcode"})
    elif "sequence_id" in frame.columns:
        frame["barcode"] = frame["barcode"].where(
            frame["barcode"].astype(str).str.strip().ne(""), frame["sequence_id"]
        )

    if "umi_counts" not in frame.columns:
        if "umi_count" in frame.columns:
            frame = frame.rename(columns={"umi_count": "umi_counts"})
        else:
            # This is valid only because Stage 6 emits one row per UMI-level
            # representative when the table is not already count-collapsed.
            frame["umi_counts"] = 1

    for column in AIRR_FIELDS:
        if column not in frame.columns:
            frame[column] = ""

    frame["barcode"] = frame["barcode"].map(_text).str.upper()
    frame["locus"] = frame["locus"].map(_text).str.upper()
    frame["umi_counts"] = _parse_counts(frame["umi_counts"], str(path))
    frame["_source_file"] = str(path)
    frame["_merge_group_id"] = merge_group_id
    frame["_source_order"] = range(order_start, order_start + len(frame))
    return frame


def read_inputs(paths: Iterable[Path], sep: str, merge_group_id: str) -> pd.DataFrame:
    frames = []
    order = 0
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"input file does not exist: {path}")
        frame = _read_one(path, sep, merge_group_id, order)
        frames.append(frame)
        order += len(frame)
    if not frames:
        raise ValueError("at least one input file is required")
    return pd.concat(frames, ignore_index=True)


def airr_filter(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply the existing AIRR filter to a new output frame.

    The source table is never modified. This reproduces the project's
    existing rules: productive, v_score > 150, v_identity > 85, and at least
    one valid V/J call; TCR-dominant input additionally requires identity >=
    99.7.
    """

    input_rows = len(frame)
    work = frame.copy()
    productive = work["productive"].astype(str).str.strip().str.upper().isin({"T", "TRUE"})
    has_v_or_j = _valid_call(work["v_call"]) | _valid_call(work["j_call"])
    v_score = pd.to_numeric(work["v_score"], errors="coerce")
    v_identity = pd.to_numeric(work["v_identity"], errors="coerce")
    base_keep = (
        work["barcode"].ne("")
        & work["locus"].ne("")
        & work["sequence"].astype(str).str.strip().ne("")
        & work["umi_counts"].gt(0)
        & productive
        & v_score.gt(150)
        & v_identity.gt(85)
        & has_v_or_j
    )
    filtered = work.loc[base_keep].copy()
    if not filtered.empty:
        tcr_fraction = filtered["locus"].str.contains("TR", na=False).mean()
        if tcr_fraction > 0.9:
            filtered = filtered.loc[
                pd.to_numeric(filtered["v_identity"], errors="coerce").ge(99.7)
            ].copy()

    return filtered, {
        "input_rows": int(input_rows),
        "airr_filtered_rows": int(len(filtered)),
    }


def _coverage(row: pd.Series) -> int:
    total = 0
    for start_name, end_name in (
        ("v_alignment_start", "v_alignment_end"),
        ("d_alignment_start", "d_alignment_end"),
        ("j_alignment_start", "j_alignment_end"),
        ("c_alignment_start", "c_alignment_end"),
    ):
        start = _number(row.get(start_name, ""))
        end = _number(row.get(end_name, ""))
        if not math.isnan(start) and not math.isnan(end) and end >= start:
            total += int(end - start + 1)
    return total


def _quality_tuple(row: pd.Series) -> tuple[float, float, float, int, int]:
    v_score = _number(row.get("v_score", ""))
    v_identity = _number(row.get("v_identity", ""))
    return (
        float(_coverage(row)),
        v_score if not math.isnan(v_score) else -math.inf,
        v_identity if not math.isnan(v_identity) else -math.inf,
        len(_normal_nt(row.get("sequence_alignment", "") or row.get("sequence", ""))),
        -int(row.get("_source_order", 0)),
    )


def _secondary_is_supported(
    top: int,
    second: int,
    dominant_ratio: float,
    min_alt_fraction: float,
) -> bool:
    """Decide whether the second candidate is substantial without MIN_UMI."""

    if second <= 0 or top <= 0:
        return False
    ratio = top / second
    alternative_fraction = second / (top + second)
    return ratio < dominant_ratio or alternative_fraction >= min_alt_fraction


def _add_keys(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["_v_key"] = work["v_call"].map(_normal_gene)
    work["_j_key"] = work["j_call"].map(_normal_gene)
    work["_cdr3_key"] = work["cdr3"].map(_normal_nt)
    work["_variant_key"] = work["sequence_alignment"].map(_normal_nt)
    work["_variant_key"] = work["_variant_key"].where(
        work["_variant_key"].ne(""), work["sequence"].map(_normal_nt)
    )

    # Missing CDR3 must not merge all incomplete annotations into one chain.
    missing_cdr3 = work["_cdr3_key"].eq("")
    work.loc[missing_cdr3, "_cdr3_key"] = (
        "__MISSING_CDR3__" + work.loc[missing_cdr3, "_variant_key"]
    )
    return work


def _variant_rows(cluster: pd.DataFrame) -> list[dict[str, object]]:
    rows = []
    for _, group in cluster.groupby(["_variant_key"], sort=False, dropna=False):
        ordered = sorted(
            (row for _, row in group.iterrows()),
            key=_quality_tuple,
            reverse=True,
        )
        representative = ordered[0].to_dict()
        representative["_variant_umi_counts"] = int(group["umi_counts"].sum())
        representative["_variant_row_count"] = int(len(group))
        representative["_quality"] = _quality_tuple(ordered[0])
        rows.append(representative)
    rows.sort(
        key=lambda row: (
            -int(row["_variant_umi_counts"]),
            -row["_quality"][0],
            -row["_quality"][1] if math.isfinite(row["_quality"][1]) else math.inf,
            -row["_quality"][2] if math.isfinite(row["_quality"][2]) else math.inf,
            -row["_quality"][3],
            -row["_quality"][4],
        )
    )
    return rows


def _select_variants(
    variants: list[dict[str, object]],
    max_variants: int,
    dominant_ratio: float,
    min_alt_fraction: float,
) -> list[dict[str, object]]:
    if not variants or max_variants <= 0:
        return []
    selected = [variants[0]]
    if len(variants) > 1 and len(selected) < max_variants:
        top = int(variants[0]["_variant_umi_counts"])
        second = int(variants[1]["_variant_umi_counts"])
        if _secondary_is_supported(top, second, dominant_ratio, min_alt_fraction):
            selected.append(variants[1])
    return selected


def cluster_chains(
    filtered: pd.DataFrame,
    max_chain_candidates: int,
    max_sequence_variants: int,
    dominant_ratio: float,
    min_alt_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cluster by barcode/locus/V/J/CDR3 and retain at most two chains."""

    if filtered.empty:
        return pd.DataFrame(columns=OUTPUT_FIELDS), pd.DataFrame()

    work = _add_keys(filtered)
    chain_keys = [
        "_merge_group_id",
        "barcode",
        "locus",
        "_v_key",
        "_j_key",
        "_cdr3_key",
    ]
    candidate_groups = []
    grouped_chains = work.groupby(chain_keys, sort=False, dropna=False)
    total_chain_groups = grouped_chains.ngroups
    for group_index, (chain_key, cluster) in enumerate(grouped_chains, start=1):
        variants = _variant_rows(cluster)
        cluster_support = int(cluster["umi_counts"].sum())
        best_quality = max(row["_quality"] for row in variants)
        candidate_groups.append(
            {
                "chain_key": chain_key,
                "cluster": cluster,
                "variants": variants,
                "cluster_umi_counts": cluster_support,
                "best_quality": best_quality,
                "source_order": min(
                    int(row["_source_order"]) for _, row in cluster.iterrows()
                ),
            }
        )
        if (
            SHOW_PROGRESS
            and (
                group_index == 1
                or group_index % max(1, PROGRESS_EVERY_CHAIN_GROUPS) == 0
                or group_index == total_chain_groups
            )
        ):
            _progress(f"chain clusters={group_index:,}/{total_chain_groups:,}")

    # The two-chain limit is per cell barcode and locus, never per input
    # file. A single input can contain thousands of independent barcodes.
    cells = {}
    for item in candidate_groups:
        cluster = item["cluster"]
        cell_key = (
            cluster["_merge_group_id"].iloc[0],
            cluster["barcode"].iloc[0],
            cluster["locus"].iloc[0],
        )
        cells.setdefault(cell_key, []).append(item)

    retained_clusters = []
    ordered_candidate_groups = []
    total_barcode_locus_groups = len(cells)
    for cell_index, cell_groups in enumerate(cells.values(), start=1):
        cell_groups.sort(
            key=lambda item: (
                -int(item["cluster_umi_counts"]),
                -item["best_quality"][0],
                -item["best_quality"][1]
                if math.isfinite(item["best_quality"][1])
                else math.inf,
                -item["best_quality"][2]
                if math.isfinite(item["best_quality"][2])
                else math.inf,
                -item["best_quality"][3],
                int(item["source_order"]),
            )
        )
        top_support = int(cell_groups[0]["cluster_umi_counts"])
        for rank, item in enumerate(cell_groups, start=1):
            support = int(item["cluster_umi_counts"])
            if rank == 1 and max_chain_candidates > 0:
                retained = True
                reason = "PRIMARY"
            elif rank <= max_chain_candidates:
                retained = _secondary_is_supported(
                    top_support, support, dominant_ratio, min_alt_fraction
                )
                reason = "SECONDARY_SUPPORTED" if retained else "SECONDARY_DOMINATED"
            else:
                retained = False
                reason = "EXCESS_CHAIN_CANDIDATE"

            selected_variants = _select_variants(
                item["variants"],
                max_sequence_variants if retained else 0,
                dominant_ratio,
                min_alt_fraction,
            )
            item["rank"] = rank
            item["retained"] = retained
            item["reason"] = reason
            item["selected_variants"] = selected_variants
            ordered_candidate_groups.append(item)
            if retained:
                retained_clusters.append(item)
        if (
            SHOW_PROGRESS
            and (
                cell_index == 1
                or cell_index % max(1, PROGRESS_EVERY_BARCODE_GROUPS) == 0
                or cell_index == total_barcode_locus_groups
            )
        ):
            _progress(
                "barcode+locus groups="
                f"{cell_index:,}/{total_barcode_locus_groups:,}"
            )

    candidate_groups = ordered_candidate_groups

    output_rows = []
    for item in retained_clusters:
        for variant_rank, row in enumerate(item["selected_variants"], start=1):
            output = {field: row.get(field, "") for field in AIRR_FIELDS}
            output["barcode"] = row.get("barcode", "")
            # Support for the selected normalized_sequence variant. When there
            # is one variant it equals the chain-cluster total.
            output["umi_counts"] = int(row["_variant_umi_counts"])
            output["_merge_group_id"] = row.get("_merge_group_id", "")
            output["_chain_rank"] = int(item["rank"])
            output["_variant_rank"] = variant_rank
            output_rows.append(output)

    output = pd.DataFrame(output_rows)
    if output.empty:
        output = pd.DataFrame(columns=OUTPUT_FIELDS)
    else:
        output = output.sort_values(
            ["_merge_group_id", "barcode", "locus", "_chain_rank", "_variant_rank"],
            kind="stable",
        ).reset_index(drop=True)
        output = output[OUTPUT_FIELDS]

    summary_rows = []
    for item in candidate_groups:
        cluster = item["cluster"]
        variants = item["variants"]
        selected = item["selected_variants"]
        first = variants[0]
        summary_rows.append(
            {
                "merge_group_id": cluster["_merge_group_id"].iloc[0],
                "barcode": cluster["barcode"].iloc[0],
                "locus": cluster["locus"].iloc[0],
                "chain_rank": item["rank"],
                "v_call": first.get("v_call", ""),
                "j_call": first.get("j_call", ""),
                "cdr3": first.get("cdr3", ""),
                "cluster_umi_counts": item["cluster_umi_counts"],
                "all_sequence_variants": len(variants),
                "output_sequence_variants": len(selected),
                "retained": "T" if item["retained"] else "F",
                "retention_reason": item["reason"],
            }
        )
    return output, pd.DataFrame(summary_rows)


def _summary_path(output: Path) -> Path:
    name = output.name
    for suffix in (".gz", ".csv", ".tsv", ".txt"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return output.with_name(name + ".stage8_summary.csv")


def write_table(frame: pd.DataFrame, path: Path, sep: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compression = "gzip" if path.name.lower().endswith(".gz") else None
    frame.to_csv(path, sep=sep, index=False, compression=compression)


def discover_input_files(input_dir: Path, output_dir: Path) -> list[Path]:
    """Return input tables under INPUT_DIR, excluding Stage 8 outputs."""

    if not input_dir.is_dir():
        raise FileNotFoundError(f"INPUT_DIR does not exist: {input_dir}")

    candidates = []
    seen = set()
    for pattern in INPUT_PATTERNS:
        iterator = input_dir.rglob(pattern) if RECURSIVE_INPUT_FILES else input_dir.glob(pattern)
        for path in iterator:
            path = path.resolve()
            if not path.is_file() or path in seen:
                continue
            if path.name in EXCLUDE_FILENAMES or any(part in path.name for part in EXCLUDE_NAME_PARTS):
                continue
            if any(parent.name in IGNORED_DIR_NAMES for parent in (path, *path.parents)):
                continue
            output_resolved = output_dir.resolve()
            if path == output_resolved or output_resolved in path.parents:
                continue
            seen.add(path)
            candidates.append(path)
    return sorted(candidates, key=lambda path: str(path).lower())


def output_path_for(source: Path, input_dir: Path, output_dir: Path) -> Path:
    relative = source.relative_to(input_dir.resolve())
    return output_dir / relative


def summary_path_for(output_path: Path, source: Path, input_dir: Path) -> Path:
    if SUMMARY_OUTPUT_DIR is None:
        return _summary_path(output_path)
    relative = source.relative_to(input_dir.resolve())
    return Path(SUMMARY_OUTPUT_DIR) / relative.parent / f"{source.stem}.stage8_summary.csv"


def process_one_file(source: Path, input_dir: Path, output_dir: Path) -> tuple[Path, Path, int, int, int]:
    _progress(f"start file={source.name}")
    if MERGE_GROUP_ID_FROM_FILENAME:
        merge_group_id = source.stem
    else:
        try:
            relative_parent = source.parent.relative_to(input_dir.resolve())
            merge_group_id = relative_parent.as_posix() or "."
        except ValueError:
            merge_group_id = source.parent.name or "."
    raw = read_inputs([source], INPUT_SEPARATOR, merge_group_id)
    filtered, stats = airr_filter(raw)
    _progress(
        f"AIRR filter complete file={source.name} "
        f"input={stats['input_rows']:,} retained={stats['airr_filtered_rows']:,}"
    )
    output, summary = cluster_chains(
        filtered,
        max_chain_candidates=MAX_CHAIN_CANDIDATES,
        max_sequence_variants=MAX_SEQUENCE_VARIANTS,
        dominant_ratio=DOMINANT_RATIO,
        min_alt_fraction=MIN_ALT_FRACTION,
    )

    output_path = output_path_for(source, input_dir, output_dir)
    output_sep = "\t" if output_path.name.lower().endswith((".tsv", ".tsv.gz")) else ","
    write_table(output, output_path, output_sep)

    summary_path = summary_path_for(output_path, source, input_dir)
    summary = summary.copy()
    summary.insert(0, "input_rows", stats["input_rows"])
    summary.insert(1, "airr_filtered_rows", stats["airr_filtered_rows"])
    summary["max_chain_candidates"] = MAX_CHAIN_CANDIDATES
    summary["max_sequence_variants"] = MAX_SEQUENCE_VARIANTS
    summary["dominant_ratio"] = DOMINANT_RATIO
    summary["min_alt_fraction"] = MIN_ALT_FRACTION
    write_table(summary, summary_path, ",")
    _progress(f"write complete file={source.name} output_rows={len(output):,}")
    return output_path, summary_path, stats["input_rows"], stats["airr_filtered_rows"], len(output)


def main() -> int:
    input_dir = Path(INPUT_DIR).resolve()
    output_dir = Path(OUTPUT_DIR).resolve()
    if not INPUT_DIR_VALUE:
        raise ValueError("SCIGBLAST_10X_CLUSTER_INPUT is required")
    if not OUTPUT_DIR_VALUE:
        raise ValueError("SCIGBLAST_10X_CLUSTER_OUTPUT is required")
    if output_dir == input_dir:
        raise ValueError("OUTPUT_DIR must be different from INPUT_DIR")

    input_paths = discover_input_files(input_dir, output_dir)
    if not input_paths:
        # A valid IgBLAST run may contain only per-sample .NO_RESULTS markers
        # when no chain yielded productive records.  Treat that as an empty,
        # successful cluster stage rather than masking the upstream result.
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=["input_rows", "airr_filtered_rows", "output_rows"]).to_csv(
            output_dir / "stage8_summary.csv", index=False
        )
        _progress(f"no tabular IgBLAST inputs under {input_dir}; empty cluster stage")
        return 0

    _progress(f"discovered files={len(input_paths)} input_dir={input_dir}")
    total_input = total_filtered = total_output = 0
    summary_paths: list[Path] = []
    results = []
    if MAX_WORKERS == 1 or len(input_paths) == 1:
        results = [process_one_file(source, input_dir, output_dir) for source in input_paths]
    else:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(input_paths))) as pool:
            futures = {pool.submit(process_one_file, source, input_dir, output_dir): source for source in input_paths}
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: str(item[0]).lower())
    for output_path, summary_path, input_rows, filtered_rows, output_rows in results:
        total_input += input_rows
        total_filtered += filtered_rows
        total_output += output_rows
        summary_paths.append(summary_path)
        print(
            f"[stage8] file={output_path.name} input_rows={input_rows:,} "
            f"airr_filtered_rows={filtered_rows:,} output_rows={output_rows:,} "
            f"output={output_path} summary={summary_path}"
        )

    # Publish one stage-level summary in addition to the per-file summaries so
    # the runner can validate/resume the whole dataset without scanning every
    # nested sample directory.
    if summary_paths:
        frames = [pd.read_csv(path) for path in summary_paths if path.is_file()]
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(
                output_dir / "stage8_summary.csv", index=False
            )

    print(
        f"[stage8] completed files={len(input_paths)} "
        f"input_rows={total_input:,} filtered_rows={total_filtered:,} "
        f"output_rows={total_output:,}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(f"[stage8] ERROR: {exc}")
