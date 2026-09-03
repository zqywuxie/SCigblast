#!/usr/bin/env python3
"""Build one representative sequence per bulk-IR sample/UMI group.

The script is deliberately small and streaming-friendly.  PANDAseq FASTA is
joined to the UMI sidecars produced by 03.split_barcode.py using a canonical
read id.  Primer barcode is demultiplexing/audit metadata only; it never
participates in molecular grouping.  All FASTAs of the same biological sample
are accumulated before one representative is selected per UMI.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from Bio import SeqIO
except ImportError as exc:  # pragma: no cover
    raise SystemExit("biopython is required for IR representative stage") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
BRANCH_ROOT = SCRIPT_DIR.parent
OUTPUT_ROOT = Path(os.environ.get("SCIGBLAST_OUTPUT_ROOT", "") or str(BRANCH_ROOT / "output"))
DATASET_LABEL = os.environ.get("SCIGBLAST_DATASET_LABEL", "").strip()
_dataset_suffix = Path(DATASET_LABEL) if DATASET_LABEL else Path()
PANDASEQ_DIR = Path(os.environ.get("SCIGBLAST_IR_REP_INPUT", str(OUTPUT_ROOT / "05.pandaseq" / _dataset_suffix)))
SPLIT_DIR = Path(os.environ.get("SCIGBLAST_IR_SPLIT_INPUT", str(OUTPUT_ROOT / "03.IR_split_output" / _dataset_suffix)))
OUTPUT_DIR = Path(os.environ.get("SCIGBLAST_IR_REP_OUTPUT", str(OUTPUT_ROOT / "06.representative" / _dataset_suffix)))
FASTA_OUTPUT_DIR = Path(os.environ.get(
    "SCIGBLAST_IR_REP_FASTA_OUTPUT", str(OUTPUT_DIR / "representative_fasta")
))
SUMMARY_NAME = "ir_split_summary.csv"
STATE_NAME = ".representative_state.tsv.gz"
LEGACY_STATE_NAME = ".representative_state.csv"
# Representative grouping keeps one Counter set per biological sample.  It has
# a different memory profile from split, so do not inherit the split worker
# count unless explicitly requested for backward compatibility.
WORKERS = max(1, int(os.environ.get(
    "SCIGBLAST_IR_REPRESENTATIVE_WORKERS",
    os.environ.get("SCIGBLAST_IR_WORKERS", "4"),
)))
PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_IR_REP_PROGRESS_EVERY", "100000")))
# Version 2 changes the biological identity from sample+barcode+UMI to
# sample+UMI.  A schema marker prevents an old barcode-keyed checkpoint from
# being reported as complete after its source FASTA has been cleaned up.
REPRESENTATIVE_SCHEMA_VERSION = "3"
SCHEMA_MARKER_NAME = ".representative_schema"


def canonical_id(header: str) -> str:
    """Return the same read key produced by 04.clean_header.sh.

    The split-stage sidecar stores the pre-clean read ID, while PANDAseq
    receives the clean FASTA ID.  Both forms must go through this exact
    normalization (remove temporary tags/read suffixes, then normalize the
    CASAVA coordinate fields) before the UMI lookup is attempted.
    """
    token = str(header).lstrip("@>").split(None, 1)[0]
    token = token.split("#", 1)[0]
    token = re.sub(r"/[12]$", "", token)
    fields = token.split(":")
    limit = 7 if len(fields) >= 8 else len(fields)
    # Some instrument exports omit the usual flow-cell fields and start with
    # ``...:L04:R001C001:0000:0293``.  The clean stage normalizes that short
    # coordinate form to ``...:4:1001:0:293``.  Detect it explicitly while
    # keeping the normal CASAVA rule (normalize from field 4) for full IDs.
    start = 3
    short_coordinate = (
        len(fields) < 8 and len(fields) >= 3
        and re.fullmatch(r"L\d+", fields[1], re.IGNORECASE)
        and re.fullmatch(r"R\d+C\d+", fields[2], re.IGNORECASE)
    )
    if short_coordinate:
        start = 1
    for index in range(start, limit):
        fields[index] = re.sub(r"[A-Za-z]", "", fields[index])
        fields[index] = re.sub(r"^0+", "", fields[index]) or "0"
    return ":".join(fields[:limit])


def normalize_umi(value: object) -> str:
    """Accept legacy ``#UMI:ACGT`` and current ``#ACGT`` UMI values."""
    umi = str(value or "").strip().upper()
    if umi.startswith("#UMI:"):
        return umi[5:]
    if umi.startswith("#"):
        return umi[1:]
    return umi


def umi_from_header(header: object) -> str:
    """Read a UMI tag from the first FASTQ/FASTA header token, if present."""
    token = str(header or "").lstrip("@>").split(None, 1)[0]
    match = re.search(r"#(?:UMI:)?([ACGTN]+)$", token, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def sequence_format(path: Path) -> str:
    """Infer FASTA/FASTQ input while keeping legacy FASTA compatibility."""
    name = str(path).lower()
    return "fastq" if any(name.endswith(suffix) for suffix in
                          (".fastq", ".fq", ".fastq.gz", ".fq.gz")) else "fasta"


def record_quality(record):
    """Return assembled-record (mean PHRED, expected errors), if available."""
    qualities = record.letter_annotations.get("phred_quality")
    if not qualities:
        return None, None
    values = [int(value) for value in qualities]
    if not values:
        return None, None
    mean_quality = sum(values) / len(values)
    expected_errors = sum(10.0 ** (-value / 10.0) for value in values)
    return mean_quality, expected_errors


def sample_key(path: Path) -> Path:
    rel = path.relative_to(PANDASEQ_DIR)
    return rel.parent


def summary_sources() -> dict[str, tuple[str, str]]:
    """Map split-output directories to biological sample and source barcode."""
    result: dict[str, tuple[str, str]] = {}
    summary = SPLIT_DIR / SUMMARY_NAME
    if not summary.is_file():
        return result
    with summary.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            out = str(row.get("output_r1", "")).strip()
            sample_id = str(row.get("sample_id", "")).strip()
            if not out or not sample_id:
                continue
            barcode = str(row.get("barcode_sequence", "")).strip().upper()
            key = str(Path(out).resolve().parent)
            value = barcode or str(row.get("barcode_name", "")).strip()
            previous = result.get(key)
            if previous and previous[0] == sample_id:
                # One physical sample directory can be represented by more
                # than one submission row/barcode.  Preserve every source
                # barcode for audit instead of letting the last CSV row win.
                values = {item for item in (previous[1] + ";" + value).split(";") if item}
                result[key] = (sample_id, ";".join(sorted(values)))
            else:
                result[key] = (sample_id, value)
    return result


def sidecar_index(sample_dir: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    indexed_rows = 0
    # Sidecars are next to split FASTQ files; recursive lookup handles mixed
    # sample/pair layouts without assuming a particular Lane depth.
    for path in sorted((SPLIT_DIR / sample_dir).rglob("*.umi.tsv.gz")):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for line_no, row in enumerate(reader, 2):
                indexed_rows += 1
                read_id = canonical_id(row.get("read_id", ""))
                umi = normalize_umi(row.get("umi", "")) or umi_from_header(row.get("read_id", ""))
                if not read_id or not umi:
                    raise ValueError(f"malformed UMI sidecar {path}:{line_no}")
                if not re.fullmatch(r"[ACGTN]+", umi):
                    raise ValueError(f"invalid UMI in {path}:{line_no}: {umi}")
                previous = index.get(read_id)
                if previous and previous != umi:
                    # A clean id mapping to multiple UMIs is unsafe; retain a
                    # sentinel so the read is excluded rather than guessed.
                    index[read_id] = "__CONFLICT__"
                else:
                    index[read_id] = umi
                if indexed_rows % PROGRESS_EVERY == 0:
                    print(
                        f"[IR representative umi-index] sample={sample_dir} "
                        f"rows={indexed_rows:,} reads={len(index):,}",
                        flush=True,
                    )
    print(
        f"[IR representative umi-index] sample={sample_dir} "
        f"rows={indexed_rows:,} reads={len(index):,} status=done",
        flush=True,
    )
    return index


def atomic_text(path: Path, mode: str = "w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    return os.fdopen(fd, mode, encoding="utf-8", newline=""), Path(name)


STATE_FIELDS = [
    "representative_id", "sample_id", "barcode", "umi", "total_reads",
    "representative_reads", "representative_percent", "unique_sequence_count",
    "top_candidate_count", "ambiguity_status", "alternative_top_sequences", "source_barcodes",
    "sequence", "header", "mean_quality", "expected_errors", "selection_method",
]


def _state_key(row: dict[str, str]) -> tuple[str, str]:
    """Bulk molecule identity: an UMI is scoped to the biological sample."""
    return (str(row.get("sample_id", "")), str(row.get("umi", "")))


def _int_or_zero(value: object) -> int:
    try:
        return int(str(value or "0"))
    except (TypeError, ValueError):
        return 0


def compact_state_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep one representative row per sample/UMI.

    IR already emits one row per UMI, but this also makes migration from an
    interrupted/older state deterministic and prevents duplicate rows from
    expanding the durable checkpoint.
    """
    selected: dict[tuple[str, str], dict[str, str]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        normalized = {field: str(row.get(field, "") or "") for field in STATE_FIELDS}
        key = _state_key(normalized)
        if key not in selected:
            selected[key] = normalized
            order.append(key)
            continue
        old = selected[key]
        # Prefer a row that has a sequence/header and the largest read count.
        old_score = (bool(old.get("sequence")), bool(old.get("header")),
                     _int_or_zero(old.get("total_reads")))
        new_score = (bool(normalized.get("sequence")), bool(normalized.get("header")),
                     _int_or_zero(normalized.get("total_reads")))
        if new_score > old_score:
            selected[key] = normalized
    return [selected[key] for key in order]


def load_state(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        mode = "rt" if path.suffix == ".gz" else "r"
        with opener(path, mode, encoding="utf-8", newline="") as handle:
            rows = [{field: row.get(field, "") for field in STATE_FIELDS}
                    for row in csv.DictReader(handle)]
        return compact_state_rows(rows)
    except (OSError, csv.Error):
        return []


def write_state(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    # ``load_state`` already compacts durable rows and the current run emits
    # one row per sample/UMI.  Re-compacting here would create another large
    # selected-dict/list pair immediately before writing the gzip snapshot.
    opener = gzip.open if path.suffix == ".gz" else open
    mode = "wt" if path.suffix == ".gz" else "w"
    handle = opener(temp, mode, encoding="utf-8", newline="")
    try:
        writer = csv.DictWriter(handle, fieldnames=STATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for index, row in enumerate(rows, 1):
            writer.writerow(row)
            if index == 1 or index % PROGRESS_EVERY == 0:
                print(f"[IR representative state] written={index:,} rows", flush=True)
        handle.close()
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def process_fasta(fasta: Path, sample_id: str, umi_by_read: dict[str, str]):
    """Collect sequence counts per UMI for one source FASTA.

    Representative selection happens only after all source FASTAs of the same
    biological sample have been merged.
    """
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    headers_by_umi_seq: dict[tuple[str, str], str] = {}
    quality_by_umi_seq: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    unmatched = 0
    processed = 0
    for record in SeqIO.parse(str(fasta), sequence_format(fasta)):
        processed += 1
        read_key = canonical_id(record.description)
        sidecar_umi = umi_by_read.get(read_key, "")
        header_umi = umi_from_header(record.description)
        # Presplit IR data already carries ``#UMI`` in the read header and
        # intentionally has no split-stage sidecar.  Raw/split data uses the
        # sidecar; when both are present, a disagreement is unsafe and the
        # read is excluded rather than silently assigned to the wrong UMI.
        if sidecar_umi == "__CONFLICT__":
            umi = "__CONFLICT__"
        elif sidecar_umi and header_umi and sidecar_umi != header_umi:
            umi = "__CONFLICT__"
        else:
            umi = sidecar_umi or header_umi
        if not umi or umi == "__CONFLICT__":
            unmatched += 1
            if processed % PROGRESS_EVERY == 0:
                print(
                    f"[IR representative read] sample={sample_id} reads={processed:,} "
                    f"matched={processed - unmatched:,} unmatched={unmatched:,}",
                    flush=True,
                )
            continue
        sequence = str(record.seq).upper()
        groups[umi][sequence] += 1
        mean_quality, expected_errors = record_quality(record)
        if mean_quality is not None or expected_errors is not None:
            quality = quality_by_umi_seq[(umi, sequence)]
            quality[0] += mean_quality or 0.0
            quality[1] += expected_errors or 0.0
            quality[2] += 1.0
        key = (umi, sequence)
        header = str(record.description).strip()
        if header and (key not in headers_by_umi_seq or header < headers_by_umi_seq[key]):
            headers_by_umi_seq[key] = header
        if processed % PROGRESS_EVERY == 0:
            print(
                f"[IR representative read] sample={sample_id} reads={processed:,} "
                f"matched={processed - unmatched:,} unmatched={unmatched:,} "
                f"umis={len(groups):,}",
                flush=True,
            )
    print(
        f"[IR representative read] sample={sample_id} reads={processed:,} "
        f"matched={processed - unmatched:,} unmatched={unmatched:,} "
        f"umis={len(groups):,} status=done",
        flush=True,
    )
    return groups, headers_by_umi_seq, quality_by_umi_seq, unmatched


def prepare_sample(index: int, fasta: Path, sources: dict[str, tuple[str, str]]):
    """Read one PANDAseq FASTA and its UMI sidecar without shared writes."""
    rel_sample = sample_key(fasta)
    print(f"[IR representative start] sample={rel_sample} fasta={fasta}", flush=True)
    split_sample_dir = SPLIT_DIR / rel_sample
    umi_index = sidecar_index(rel_sample)
    source_sample, source_barcode = sources.get(
        str(split_sample_dir.resolve()), (rel_sample.name, "")
    )
    groups, headers, quality, unmatched = process_fasta(fasta, source_sample, umi_index)
    return index, fasta, rel_sample, source_sample, source_barcode, groups, headers, quality, unmatched


def representative_rows(
    sample_id: str,
    groups: dict[str, Counter[str]],
    headers_by_umi_seq: dict[tuple[str, str], str],
    quality_by_umi_seq: dict[tuple[str, str], list[float]],
    source_barcodes: set[str],
) -> list[dict[str, str]]:
    """Select a deterministic representative for each sample-local UMI."""
    barcode_text = ";".join(sorted(value for value in source_barcodes if value))
    rows: list[dict[str, str]] = []
    for umi, counts in sorted(groups.items()):
        total = sum(counts.values())
        maximum = max(counts.values())
        top = [seq for seq, count in counts.items() if count == maximum]

        def quality_stats(seq):
            total_mean, total_errors, n = quality_by_umi_seq.get((umi, seq), [0.0, 0.0, 0.0])
            if not n:
                return None, None
            return total_mean / n, total_errors / n

        def quality_key(seq):
            mean_quality, expected_errors = quality_stats(seq)
            return (
                expected_errors is None,
                expected_errors if expected_errors is not None else float("inf"),
                -(mean_quality if mean_quality is not None else float("-inf")),
                seq.count("N"),
            )

        quality_available = any(quality_stats(seq) != (None, None) for seq in top)
        if quality_available:
            ranked = sorted(top, key=lambda seq: (quality_key(seq), seq))
            selection_method = "COUNT_THEN_QUALITY"
        else:
            ranked = sorted(top, key=lambda seq: (seq.count("N"), seq))
            selection_method = "COUNT_ONLY_NO_QUALITY"
        selected = ranked[0]
        if len(top) == 1:
            ambiguity_status = "CLEAR"
        elif quality_available:
            best_key = quality_key(selected)
            ambiguity_status = (
                "TIED_AFTER_QUALITY"
                if sum(quality_key(seq) == best_key for seq in top) > 1
                else "RESOLVED_BY_QUALITY"
            )
        else:
            ambiguity_status = "TIED_NO_QUALITY"
        selected_mean, selected_errors = quality_stats(selected)
        rows.append({
            "representative_id": "REP_" + hashlib.sha256(
                f"{sample_id}\0{umi}".encode()
            ).hexdigest()[:24],
            "sample_id": sample_id,
            # Retained only for audit/backward compatibility. It is excluded
            # from the UMI grouping key and stable representative ID.
            "barcode": barcode_text,
            "source_barcodes": barcode_text,
            "umi": umi,
            "total_reads": total,
            "representative_reads": counts[selected],
            "representative_percent": f"{counts[selected] * 100 / total:.2f}",
            "unique_sequence_count": len(counts),
            "top_candidate_count": len(top),
            "ambiguity_status": ambiguity_status,
            "alternative_top_sequences": "" if len(top) == 1 else ";".join(
                f"{seq}:{counts[seq]}:{quality_stats(seq)[0] if quality_stats(seq)[0] is not None else ''}:{quality_stats(seq)[1] if quality_stats(seq)[1] is not None else ''}"
                for seq in top),
            "sequence": selected,
            # Preserve the original PANDAseq header in final representative
            # FASTA; representative_id remains an auditable stable key.
            "header": headers_by_umi_seq.get((umi, selected), ""),
            "mean_quality": "" if selected_mean is None else f"{selected_mean:.4f}",
            "expected_errors": "" if selected_errors is None else f"{selected_errors:.8g}",
            "selection_method": selection_method,
        })
    return rows


def main() -> int:
    # PANDAseq also writes *_unaligned.*.  Those records do not have the
    # merged-read contract expected here and may begin with invalid headers;
    # representative construction must consume merged products only.  FASTQ
    # is preferred for new runs so assembled quality can resolve count ties;
    # legacy FASTA remains readable.
    sequence_files = sorted(
        set(PANDASEQ_DIR.rglob("*_merged.fastq"))
        | set(PANDASEQ_DIR.rglob("*_merged.fq"))
        | set(PANDASEQ_DIR.rglob("*_merged.fasta"))
        | set(PANDASEQ_DIR.rglob("*_merged.fa"))
    )
    state_path = OUTPUT_DIR / STATE_NAME
    legacy_state_path = OUTPUT_DIR / LEGACY_STATE_NAME
    schema_marker = OUTPUT_DIR / SCHEMA_MARKER_NAME
    previous_rows = load_state(state_path)
    legacy_state_loaded = False
    if not previous_rows and legacy_state_path != state_path and legacy_state_path.is_file():
        previous_rows = load_state(legacy_state_path)
        legacy_state_loaded = bool(previous_rows)
        if legacy_state_loaded:
            print(
                f"[IR representative] loaded legacy state {legacy_state_path}; "
                "will compact it into the gzip state", flush=True,
            )
    if not sequence_files:
        marker_ok = False
        if schema_marker.is_file():
            try:
                marker_ok = schema_marker.read_text(encoding="utf-8").strip() == REPRESENTATIVE_SCHEMA_VERSION
            except OSError:
                marker_ok = False
        if previous_rows and marker_ok and (OUTPUT_DIR / "representative_map.tsv.gz").is_file():
            print("[IR representative] no pending merged FASTA; durable representative state/output already exists")
            return 0
        if previous_rows and (OUTPUT_DIR / "representative_map.tsv.gz").is_file():
            print(
                "[IR representative] existing representative state has no current schema marker; "
                "source FASTA is unavailable, refusing to reuse a possibly barcode-keyed checkpoint",
                file=sys.stderr, flush=True,
            )
            return 1
        print(f"[IR representative] no FASTA in {PANDASEQ_DIR}")
        return 1
    sources = summary_sources()
    current_rows: list[dict[str, str]] = []
    total_unmatched = 0

    # Group source FASTAs by biological sample before reading them.  The old
    # implementation kept every source's groups in ``prepared`` and then
    # copied them into a second all-sample dictionary, making peak memory grow
    # with the number of samples.  Here only one sample's aggregate is live at
    # a time; completed sample counters are released before the next sample.
    sample_files: dict[str, list[Path]] = defaultdict(list)
    sample_barcodes: dict[str, set[str]] = defaultdict(set)
    source_meta: dict[Path, tuple[Path, str]] = {}
    for fasta in sequence_files:
        rel_sample = sample_key(fasta)
        split_sample_dir = SPLIT_DIR / rel_sample
        sample_id, source_barcode = sources.get(
            str(split_sample_dir.resolve()), (rel_sample.name, "")
        )
        sample_files[sample_id].append(fasta)
        source_meta[fasta] = (rel_sample, source_barcode)
        if source_barcode:
            sample_barcodes[sample_id].update(
                value for value in source_barcode.split(";") if value
            )

    print(f"[IR representative] workers={WORKERS} samples={len(sample_files)} "
          f"sequence_files={len(sequence_files)}", flush=True)
    processed_sources = 0
    for sample_id in sorted(sample_files):
        sample_groups: dict[str, Counter[str]] = defaultdict(Counter)
        sample_headers: dict[tuple[str, str], str] = {}
        sample_quality: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        files = sorted(sample_files[sample_id])

        # Build one sidecar index per split sample directory.  The index is
        # read-only and safely shared by file workers for this sample.
        sidecar_cache: dict[Path, dict[str, str]] = {}
        for fasta in files:
            rel_sample, _ = source_meta[fasta]
            if rel_sample not in sidecar_cache:
                sidecar_cache[rel_sample] = sidecar_index(rel_sample)

        def read_one(path: Path):
            rel_sample, _ = source_meta[path]
            groups, headers, quality, unmatched = process_fasta(
                path, sample_id, sidecar_cache[rel_sample]
            )
            return path, rel_sample, groups, headers, quality, unmatched

        with ThreadPoolExecutor(max_workers=min(WORKERS, len(files))) as executor:
            futures = [executor.submit(read_one, fasta) for fasta in files]
            for future in as_completed(futures):
                fasta, rel_sample, groups, headers, quality, unmatched = future.result()
                total_unmatched += unmatched
                for umi, counts in groups.items():
                    sample_groups[umi].update(counts)
                for key, header in headers.items():
                    previous = sample_headers.get(key)
                    if not previous or header < previous:
                        sample_headers[key] = header
                for key, values in quality.items():
                    aggregate = sample_quality[key]
                    aggregate[0] += values[0]
                    aggregate[1] += values[1]
                    aggregate[2] += values[2]
                processed_sources += 1
                print(f"[IR representative source {processed_sources}/{len(sequence_files)}] "
                      f"{rel_sample} sample_id={sample_id} UMI={len(groups)} "
                      f"unmatched={unmatched}", flush=True)

        rows = representative_rows(
            sample_id, sample_groups, sample_headers, sample_quality,
            sample_barcodes[sample_id]
        )
        current_rows.extend(rows)
        # A single sample-local FASTA prevents a second barcode-level
        # partition from reaching IgBLAST.
        out_fasta = FASTA_OUTPUT_DIR / sample_id / "representative.fasta"
        handle, temp = atomic_text(out_fasta)
        try:
            seen_headers: set[str] = set()
            for row in rows:
                header = str(row.get("header", "")).strip()
                if not header:
                    raise ValueError(f"representative header missing: {sample_id} / {row['umi']}")
                if header in seen_headers:
                    raise ValueError(f"duplicate representative header in sample: {header}")
                seen_headers.add(header)
                handle.write(f">{header}\n{row['sequence']}\n")
            handle.close(); os.replace(temp, out_fasta)
        finally:
            temp.unlink(missing_ok=True)
        print(f"[IR representative sample] sample_id={sample_id} UMI={len(rows)} "
              f"source_barcodes={len(sample_barcodes[sample_id])} "
              f"sources={len(files)}", flush=True)

        # Make the release point explicit.  This matters for large samples
        # where a completed sample can otherwise remain reachable through a
        # closure or a future object until the whole stage exits.
        del sample_groups, sample_headers, sidecar_cache

    # Replace only samples observed in this run; completed samples retained in
    # the hidden state remain part of the next global snapshot.
    refreshed_samples = set(sample_files)
    all_rows = [row for row in previous_rows if row.get("sample_id", "") not in refreshed_samples]
    all_rows.extend(current_rows)

    # An empty representative set is not a successful stage: it usually
    # means that FASTA IDs could not be joined to the UMI sidecars.  Returning
    # non-zero prevents the runner from publishing a DONE marker for an empty
    # result and makes the failure visible before IgBLAST starts.
    if not all_rows:
        print(f"[IR representative] no UMI-linked representative records; "
              f"unmatched_reads={total_unmatched}", file=sys.stderr, flush=True)
        return 1

    print(f"[IR representative state] writing rows={len(all_rows):,} path={state_path}", flush=True)
    write_state(all_rows, state_path)
    if legacy_state_loaded and os.environ.get("SCIGBLAST_IR_DELETE_LEGACY_STATE", "0") == "1":
        try:
            legacy_state_path.unlink()
            print(f"[IR representative] removed legacy state {legacy_state_path}", flush=True)
        except OSError as exc:
            print(f"[IR representative] warning: cannot remove legacy state: {exc}", file=sys.stderr)

    map_path = OUTPUT_DIR / "representative_map.tsv.gz"
    print(f"[IR representative map] writing rows={len(all_rows):,}", flush=True)
    map_fields = ["representative_id", "sample_id", "barcode", "source_barcodes", "umi", "total_reads",
                  "representative_reads", "representative_percent", "unique_sequence_count",
                  "top_candidate_count", "ambiguity_status", "alternative_top_sequences",
                  "mean_quality", "expected_errors", "selection_method"]
    handle, temp = atomic_text(map_path)
    handle.close()
    try:
        with gzip.open(temp, "wt", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=map_fields, delimiter="\t")
            writer.writeheader(); writer.writerows({k: row.get(k, "") for k in map_fields} for row in all_rows)
        os.replace(temp, map_path)
    finally:
        temp.unlink(missing_ok=True)

    summary_path = OUTPUT_DIR / "representative_summary.csv"
    by_sample = defaultdict(list)
    for row in all_rows:
        by_sample[row["sample_id"]].append(row)
    print(f"[IR representative summary] writing samples={len(by_sample):,} rows={len(all_rows):,}", flush=True)
    handle, temp = atomic_text(summary_path)
    try:
        fields = ["sample_id", "barcode", "input_reads", "matched_reads", "umi_count",
                  "representative_count", "ambiguous_umi_count", "ambiguous_umi_percent",
                  "status", "error"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for sample, rows in sorted(by_sample.items()):
            barcode = ";".join(sorted({r.get("source_barcodes", r.get("barcode", "")) for r in rows if r.get("source_barcodes", r.get("barcode", ""))}))
            n = len(rows); ambiguous = sum(
                r.get("ambiguity_status") in {"TIED_TOP", "TIED_NO_QUALITY", "TIED_AFTER_QUALITY"}
                for r in rows
            )
            writer.writerow({"sample_id": sample, "barcode": barcode,
                "input_reads": sum(int(r["total_reads"]) for r in rows),
                "matched_reads": sum(int(r["total_reads"]) for r in rows),
                "umi_count": n, "representative_count": n,
                "ambiguous_umi_count": ambiguous,
                "ambiguous_umi_percent": f"{ambiguous * 100 / n if n else 0:.2f}",
                "status": "OK", "error": ""})
        handle.close(); os.replace(temp, summary_path)
    finally:
        temp.unlink(missing_ok=True)
    schema_tmp = schema_marker.with_name(f".{schema_marker.name}.tmp.{os.getpid()}")
    schema_tmp.write_text(REPRESENTATIVE_SCHEMA_VERSION + "\n", encoding="utf-8")
    os.replace(schema_tmp, schema_marker)
    print(f"[IR representative] records={len(all_rows)} unmatched_reads={total_unmatched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
