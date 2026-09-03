#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Summarise TRA/TRB UMI counts per sequence_id.

This is a standalone post-IgBLAST analysis utility.  It intentionally does
not modify the 10X pipeline runner or the original IgBLAST table.

Input rows are grouped by ``sequence_id`` and then separated by ``locus``.
For each chain the output retains the number of records, the sum and maximum
of ``umi_counts``, and the original values so that aggregation is auditable.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Global parameters.  They can be overridden with environment variables, but
# no command-line arguments are required.
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
BRANCH_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_INPUT = BRANCH_ROOT / "data" / "0705" / "all_samples_TRAB.csv"
INPUT_CSV = Path(
    os.environ.get("SCIGBLAST_SEQUENCE_ID_SUMMARY_INPUT", str(DEFAULT_INPUT))
)
OUTPUT_CSV = Path(
    os.environ.get(
        "SCIGBLAST_SEQUENCE_ID_SUMMARY_OUTPUT",
        str(INPUT_CSV.with_name(f"{INPUT_CSV.stem}.sequence_id_chain_summary.csv")),
    )
)

REQUIRED_FIELDS = {"sequence_id", "umi_counts", "locus"}
TARGET_LOCI = ("TRA", "TRB")
OUTPUT_FIELDS = [
    "sequence_id",
    "source_rows",
    "TRA_row_count",
    "TRA_umi_counts_sum",
    "TRA_umi_counts_max",
    "TRA_umi_counts_values",
    "TRB_row_count",
    "TRB_umi_counts_sum",
    "TRB_umi_counts_max",
    "TRB_umi_counts_values",
    "total_umi_counts_sum",
    "has_TRA",
    "has_TRB",
    "chain_status",
    "other_locus_rows",
    "other_locus_values",
    "status",
    "error",
]


def parse_umi_count(raw: str) -> int:
    """Parse a non-negative integer umi_counts value."""

    value = str(raw or "").strip()
    if not value:
        raise ValueError("empty umi_counts")
    # The current output is integer-valued.  Reject fractional values rather
    # than silently changing the metric.
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"negative umi_counts: {value}")
    return parsed


def _chain_status(has_tra: bool, has_trb: bool) -> str:
    if has_tra and has_trb:
        return "BOTH"
    if has_tra:
        return "TRA_ONLY"
    if has_trb:
        return "TRB_ONLY"
    return "NO_TRA_TRB"


def _format_values(values: list[int]) -> str:
    # Keep the source-row order.  This makes the summary easy to compare with
    # the original table while sum/max provide order-independent metrics.
    return ";".join(str(value) for value in values)


def load_rows(path: Path):
    """Load and group source rows, returning groups and validation counters."""

    groups: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: {"TRA": [], "TRB": [], "other": []}
    )
    source_rows = defaultdict(int)
    errors: list[tuple[int, str, str]] = []
    invalid_umi_count = 0

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = REQUIRED_FIELDS - fields
        if missing:
            raise ValueError(
                f"input CSV missing required fields: {', '.join(sorted(missing))}"
            )

        for line_no, row in enumerate(reader, start=2):
            sequence_id = str(row.get("sequence_id") or "").strip()
            locus = str(row.get("locus") or "").strip().upper()
            source_rows[sequence_id] += 1

            try:
                umi_count = parse_umi_count(row.get("umi_counts", ""))
            except ValueError as exc:
                invalid_umi_count += 1
                errors.append((line_no, sequence_id, str(exc)))
                continue

            if locus in TARGET_LOCI:
                groups[sequence_id][locus].append(umi_count)
            else:
                groups[sequence_id]["other"].append(umi_count)

    return groups, source_rows, errors, invalid_umi_count


def build_summary(groups, source_rows, errors):
    """Build one output row per sequence_id."""

    errors_by_sequence: dict[str, list[str]] = defaultdict(list)
    for _line_no, sequence_id, message in errors:
        errors_by_sequence[sequence_id].append(message)

    result = []
    for sequence_id in sorted(source_rows):
        tra = groups[sequence_id]["TRA"]
        trb = groups[sequence_id]["TRB"]
        other = groups[sequence_id]["other"]
        has_tra = bool(tra)
        has_trb = bool(trb)
        row_errors = sorted(set(errors_by_sequence.get(sequence_id, [])))
        result.append(
            {
                "sequence_id": sequence_id,
                "source_rows": source_rows[sequence_id],
                "TRA_row_count": len(tra),
                "TRA_umi_counts_sum": sum(tra),
                "TRA_umi_counts_max": max(tra, default=0),
                "TRA_umi_counts_values": _format_values(tra),
                "TRB_row_count": len(trb),
                "TRB_umi_counts_sum": sum(trb),
                "TRB_umi_counts_max": max(trb, default=0),
                "TRB_umi_counts_values": _format_values(trb),
                "total_umi_counts_sum": sum(tra) + sum(trb),
                "has_TRA": int(has_tra),
                "has_TRB": int(has_trb),
                "chain_status": _chain_status(has_tra, has_trb),
                "other_locus_rows": len(other),
                "other_locus_values": _format_values(other),
                "status": "ERROR" if row_errors else "OK",
                "error": "; ".join(row_errors),
            }
        )
    return result


def write_summary(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    if not INPUT_CSV.is_file():
        print(f"[ERROR] input CSV does not exist: {INPUT_CSV}", file=sys.stderr)
        return 1

    try:
        groups, source_rows, errors, invalid_umi_count = load_rows(INPUT_CSV)
        summary_rows = build_summary(groups, source_rows, errors)
        write_summary(OUTPUT_CSV, summary_rows)
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        print(f"[ERROR] sequence_id summary failed: {exc}", file=sys.stderr)
        return 1

    status_counts = defaultdict(int)
    for row in summary_rows:
        status_counts[row["chain_status"]] += 1

    print(f"[sequence_id summary] input={INPUT_CSV}")
    print(f"[sequence_id summary] output={OUTPUT_CSV}")
    print(f"[sequence_id summary] source_rows={sum(source_rows.values()):,}")
    print(f"[sequence_id summary] sequence_ids={len(summary_rows):,}")
    print(
        "[sequence_id summary] "
        + " ".join(f"{key}={status_counts[key]:,}" for key in (
            "BOTH", "TRA_ONLY", "TRB_ONLY", "NO_TRA_TRB"
        ))
    )
    if invalid_umi_count:
        print(
            f"[sequence_id summary] invalid_umi_counts={invalid_umi_count:,}; "
            "affected rows are marked ERROR"
        )
    else:
        print("[sequence_id summary] umi_counts validation=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
