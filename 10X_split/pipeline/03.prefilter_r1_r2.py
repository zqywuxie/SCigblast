#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""10X pre-PANDAseq paired-read filter.

R1 must contain ``16 bp cell barcode + 10 bp UMI + TSO`` at the configured
position. R2 must start with ``NNN + sample primer barcode``; the matched
11-base prefix is removed. Only pairs passing both checks are emitted.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.pipeline_config import load_config
os.environ.setdefault("SCIGBLAST_CONFIG", str(Path(__file__).resolve().parent / "00.pipeline_config.env"))
load_config()


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.environ.get("SCIGBLAST_OUTPUT_ROOT", "") or str(ROOT / "output"))
DATASET_LABEL = os.environ.get("SCIGBLAST_DATASET_LABEL", "").strip()
if not DATASET_LABEL:
    raw_hint = os.environ.get("SCIGBLAST_RAW_INPUT_DIR", "")
    DATASET_LABEL = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(raw_hint.rstrip("/\\")).name).strip("_") if raw_hint else ""
def stage_root(stage: str) -> Path:
    return OUTPUT_ROOT / stage / DATASET_LABEL if DATASET_LABEL else OUTPUT_ROOT / stage
SUMMARY = Path(os.environ.get(
    "SCIGBLAST_10X_MAPPING_SUMMARY",
    str(stage_root("01.match") / "sample_barcode_summary.csv"),
))
BRANCH_ROOT = ROOT
INPUT_DIR = Path(os.environ.get("SCIGBLAST_10X_PREFILTER_INPUT", str(stage_root("02.fastp") / "data")))
OUTPUT_DIR = Path(os.environ.get("SCIGBLAST_10X_PREFILTER_OUTPUT", str(stage_root("03.prefilter_data"))))
REPORT = Path(os.environ.get("SCIGBLAST_10X_PREFILTER_REPORT", str(OUTPUT_DIR / "r1_r2_prefilter_summary.csv")))
TSO_SEQ = os.environ.get("SCIGBLAST_10X_TSO_SEQ", "TTTCTTATATGGG").upper()
TSO_POS = int(os.environ.get("SCIGBLAST_10X_TSO_POS", "27"))  # 1-based
WILDCARD_PREFIX = os.environ.get("SCIGBLAST_10X_WILDCARD_PREFIX", "NNN").upper()
REMOVE_R2_PREFIX = os.environ.get("SCIGBLAST_10X_REMOVE_R2_PRIMER", "1") == "1"
SEARCH_RC = os.environ.get("SCIGBLAST_10X_SEARCH_RC", "0") == "1"
SKIP_EXISTING = os.environ.get("SCIGBLAST_10X_SKIP_EXISTING", "1") == "1"
PREFILTER_WORKERS = max(1, int(os.environ.get("SCIGBLAST_10X_PREFILTER_WORKERS", "4")))
PREFILTER_READ_PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_10X_PREFILTER_READ_PROGRESS_EVERY", "100000")))
COMPRESSLEVEL = int(os.environ.get("SCIGBLAST_10X_COMPRESSLEVEL", "1"))
CANONICAL_R1_SUFFIX = "_R1.fq.gz"
CANONICAL_R2_SUFFIX = "_R2.fq.gz"
CONFIG_FINGERPRINT = hashlib.sha256(json.dumps({
    "output_layout": "relative_parent/pair_stem/sample_id/v2",
    "tso_seq": TSO_SEQ, "tso_pos": TSO_POS,
    "wildcard_prefix": WILDCARD_PREFIX,
    "remove_r2_prefix": REMOVE_R2_PREFIX, "search_rc": SEARCH_RC,
    "r1_suffix": CANONICAL_R1_SUFFIX, "r2_suffix": CANONICAL_R2_SUFFIX,
}, sort_keys=True).encode()).hexdigest()
FASTQ_EXT = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
COMPLEMENT = str.maketrans("ACGTNacgtn", "TGCANtgcan")


@dataclass(frozen=True)
class Mapping:
    sample_id: str
    barcode_candidate: str
    barcode_name: str
    barcode_sequence: str


@dataclass
class Pair:
    stem: str
    parent: str
    r1_source: Path | None
    r2_source: Path | None
    r1: Path | None
    r2: Path | None
    mappings: list[Mapping]


@dataclass
class Result:
    mapping: Mapping
    pair: Pair
    output_r1: str = ""
    output_r2: str = ""
    total: int = 0
    r1_pass: int = 0
    r2_pass: int = 0
    paired: int = 0
    discarded: int = 0
    status: str = "OK"
    error: str = ""


def clean(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def fastq_parts(path: Path) -> tuple[str, str] | None:
    name = path.name
    low = name.lower()
    for suffix in FASTQ_EXT:
        if low.endswith(suffix):
            name = name[:-len(suffix)]
            break
    else:
        return None
    for suffix, read in ((CANONICAL_R1_SUFFIX, "1"), (CANONICAL_R2_SUFFIX, "2")):
        suffix_name = suffix
        suffix_low = suffix_name.casefold()
        if not any(suffix_low.endswith(ext) for ext in FASTQ_EXT):
            suffix_name += ".fq.gz"
        suffix_core = suffix_name
        for ext in FASTQ_EXT:
            if suffix_core.casefold().endswith(ext):
                suffix_core = suffix_core[:-len(ext)]
                break
        if name.casefold().endswith(suffix_core.casefold()):
            return name[:-len(suffix_core)], read
        lane_match = re.search(re.escape(suffix_core) + r"([._-]\d+)$", name, flags=re.IGNORECASE)
        if lane_match:
            return name[:lane_match.start()] + lane_match.group(1), read
    # No raw-suffix fallback is allowed after fastp.  The canonical boundary
    # is deliberate: a wrongly named downstream file must be reported rather
    # than silently reinterpreted as another pair.
    return None


def open_fastq(path: Path) -> TextIO:
    return gzip.open(path, "rt", encoding="utf-8", errors="replace") if path.name.lower().endswith(".gz") else path.open("rt", encoding="utf-8", errors="replace")


def record(handle: TextIO) -> tuple[str, str, str, str] | None:
    h = handle.readline()
    if not h:
        return None
    s, p, q = handle.readline(), handle.readline(), handle.readline()
    if not s or not p or not q:
        raise ValueError("truncated FASTQ record")
    h, s, p, q = h.rstrip("\r\n"), s.rstrip("\r\n"), p.rstrip("\r\n"), q.rstrip("\r\n")
    if not h.startswith("@") or not p.startswith("+") or len(s) != len(q):
        raise ValueError(f"invalid FASTQ record: {h}")
    return h, s, p, q


def key(header: str) -> str:
    return re.sub(r"/[12]$", "", header[1:].split(None, 1)[0])


def write_record(handle: TextIO, rec: tuple[str, str, str, str], trim: int = 0) -> None:
    h, s, p, q = rec
    handle.write(f"{h}\n{s[trim:]}\n{p}\n{q[trim:]}\n")


def load_summary(path: Path) -> dict[str, tuple[Path, Path, list[Mapping]]]:
    groups: dict[str, tuple[Path, Path, list[Mapping]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"pair_id", "r1_path", "r2_path", "sample_id", "barcode_candidate", "barcode_name", "barcode_sequence", "status"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"summary missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            if clean(row.get("status")).upper() != "OK":
                continue
            pair_id = clean(row.get("pair_id")).replace("\\", "/").strip("/")
            source_r1 = Path(clean(row.get("r1_path")))
            source_r2 = Path(clean(row.get("r2_path")))
            sample = clean(row.get("sample_id"))
            sequence = clean(row.get("barcode_sequence")).upper()
            if not pair_id or not sample or not re.fullmatch(r"[ACGT]{8}", sequence):
                continue
            mapping = Mapping(sample, clean(row.get("barcode_candidate")), clean(row.get("barcode_name")), sequence)
            previous = groups.get(pair_id)
            if previous is None:
                groups[pair_id] = (source_r1, source_r2, [mapping])
            else:
                if previous[0] != source_r1 or previous[1] != source_r2:
                    raise ValueError(f"pair_id {pair_id!r} maps to conflicting FASTQ paths")
                if mapping not in previous[2]:
                    previous[2].append(mapping)
    return groups


def index_fastq(root: Path) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if path.is_file() and fastq_parts(path):
            index.setdefault(path.name.lower(), []).append(path)
            index.setdefault(path.name[:-3].lower() if path.name.lower().endswith(".gz") else (path.name + ".gz").lower(), []).append(path)
    return index


def resolve(source: Path | None, index: dict[str, list[Path]]) -> Path | None:
    if source is None:
        return None
    candidates = index.get(source.name.lower(), [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # The mapping summary stores the raw absolute path, while this stage
        # usually reads fastp output rooted elsewhere.  Basenames are often
        # reused across notes/lanes, so score the complete path suffix rather
        # than selecting the first parent-name match.
        source_parts = [part.casefold() for part in source.parts]

        def suffix_score(candidate: Path) -> int:
            candidate_parts = [part.casefold() for part in candidate.parts]
            score = 0
            for left, right in zip(reversed(source_parts), reversed(candidate_parts)):
                if left != right:
                    break
                score += 1
            return score

        scored = [(suffix_score(path), path) for path in candidates]
        best_score = max(score for score, _path in scored)
        best = [path for score, path in scored if score == best_score]
        # A basename-only tie is unsafe: report it as unresolved instead of
        # silently cross-matching another batch.
        return best[0] if len(best) == 1 and best_score >= 2 else None
    return None


def discover() -> tuple[list[Pair], list[Result]]:
    groups = load_summary(SUMMARY)
    pairs, errors = [], []
    for pair_id, (r1_source, r2_source, mappings) in sorted(groups.items()):
        pair_path = Path(pair_id)
        stem = pair_path.name
        parent = pair_path.parent.as_posix()
        canonical_parent = INPUT_DIR / pair_path.parent
        r1 = canonical_parent / f"{stem}{CANONICAL_R1_SUFFIX}"
        r2 = canonical_parent / f"{stem}{CANONICAL_R2_SUFFIX}"
        if not r1.is_file(): r1 = None
        if not r2.is_file(): r2 = None
        if r1 is None or r2 is None:
            missing = "R1" if r1 is None else "R2"
            errors.extend(Result(m, Pair(stem, parent, r1_source, r2_source, r1, r2, mappings), status="ERROR", error=f"cannot resolve {missing} under {INPUT_DIR}") for m in mappings)
        else:
            pairs.append(Pair(stem, parent, r1_source, r2_source, r1, r2, mappings))
    return pairs, errors


def r1_ok(sequence: str) -> bool:
    start = TSO_POS - 1
    return TSO_POS >= 16 + 10 + 1 and len(sequence) >= start + len(TSO_SEQ) and sequence[start:start + len(TSO_SEQ)].upper() == TSO_SEQ


def r2_ok(sequence: str, barcode: str) -> bool:
    pattern = (WILDCARD_PREFIX + barcode)
    if SEARCH_RC:
        pattern = pattern.translate(COMPLEMENT)[::-1]
    return len(sequence) >= len(pattern) and all(w == "N" or w == got for w, got in zip(pattern, sequence[:len(pattern)].upper()))


def safe(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .") or "sample"


def relative_parent(path: Path, root: Path) -> Path:
    """Return the source file's parent path relative to the input root."""
    try:
        return path.resolve().parent.relative_to(root.resolve())
    except ValueError:
        return Path()


def sample_output_name(pair: Pair, mapping: Mapping) -> str:
    """Use sample_id as the leaf; disambiguate duplicate sample/barcode rows."""
    same_sample = [item for item in pair.mappings if item.sample_id == mapping.sample_id]
    if len(same_sample) > 1:
        return safe(f"{mapping.sample_id}__BC_{mapping.barcode_name or mapping.barcode_candidate}")
    return safe(mapping.sample_id)


def _output_paths(pair: Pair, mapping: Mapping) -> tuple[Result, Path, Path, Path]:
    """Return result and output paths for one mapping without reading FASTQ."""
    pair_dir = safe(pair.stem)
    sample_dir = sample_output_name(pair, mapping)
    out_dir = OUTPUT_DIR / relative_parent(pair.r1, INPUT_DIR) / pair_dir / sample_dir
    file_stem = pair_dir
    return (
        Result(mapping, pair, str(out_dir / f"{file_stem}_R1.fq.gz"), str(out_dir / f"{file_stem}_R2.fq.gz")),
        out_dir,
        out_dir / f"{file_stem}_R1.fq.gz",
        out_dir / f"{file_stem}_R2.fq.gz",
    )


def _marker_matches(path: Path) -> bool:
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return marker.get("status") == "DONE" and marker.get("config_fingerprint") == CONFIG_FINGERPRINT


def process_pair(pair: Pair) -> list[Result]:
    """Read one physical pair once and route all sample/barcode mappings."""
    assert pair.r1 is not None and pair.r2 is not None
    prepared = []
    pending = []
    for mapping in pair.mappings:
        result, out_dir, out1, out2 = _output_paths(pair, mapping)
        marker = out_dir / ".DONE.json"
        if SKIP_EXISTING and marker.is_file() and out1.is_file() and out2.is_file() and _marker_matches(marker):
            result.status = "SKIPPED"
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            pending.append((result, out_dir, out1, out2, marker))
        prepared.append(result)
    if not pending:
        return prepared

    handles = []
    temp_paths = []
    try:
        for result, out_dir, out1, out2, marker in pending:
            fd1, name1 = tempfile.mkstemp(dir=out_dir, suffix=".tmp"); os.close(fd1)
            fd2, name2 = tempfile.mkstemp(dir=out_dir, suffix=".tmp"); os.close(fd2)
            temp_paths.extend([Path(name1), Path(name2)])
            h1 = gzip.open(name1, "wt", encoding="utf-8", compresslevel=COMPRESSLEVEL)
            h2 = gzip.open(name2, "wt", encoding="utf-8", compresslevel=COMPRESSLEVEL)
            handles.append((result, out1, out2, marker, Path(name1), Path(name2), h1, h2))

        with open_fastq(pair.r1) as f1, open_fastq(pair.r2) as f2:
            while True:
                a, b = record(f1), record(f2)
                if a is None and b is None:
                    break
                if a is None or b is None:
                    raise ValueError("R1/R2 record counts differ")
                if key(a[0]) != key(b[0]):
                    raise ValueError(f"R1/R2 headers do not match: {a[0]} / {b[0]}")
                for result, out1, out2, marker, tmp1, tmp2, h1, h2 in handles:
                    result.total += 1
                    if result.total % PREFILTER_READ_PROGRESS_EVERY == 0:
                        print(
                            f"[prefilter read] {result.mapping.sample_id}/{result.mapping.barcode_name} "
                            f"reads={result.total} paired={result.paired}",
                            file=sys.stderr, flush=True,
                        )
                    ok1, ok2 = r1_ok(a[1]), r2_ok(b[1], result.mapping.barcode_sequence)
                    result.r1_pass += int(ok1); result.r2_pass += int(ok2)
                    if ok1 and ok2:
                        result.paired += 1
                        write_record(h1, a)
                        write_record(h2, b, len(WILDCARD_PREFIX) + len(result.mapping.barcode_sequence) if REMOVE_R2_PREFIX else 0)
                    else:
                        result.discarded += 1

        for result, out1, out2, marker, tmp1, tmp2, h1, h2 in handles:
            h1.close(); h2.close()
            os.replace(tmp1, out1); os.replace(tmp2, out2)
            marker.write_text(json.dumps({
                "status": "DONE", "config_fingerprint": CONFIG_FINGERPRINT, "total": result.total,
                "r1_pass": result.r1_pass, "r2_pass": result.r2_pass,
                "paired": result.paired, "discarded": result.discarded,
            }, indent=2), encoding="utf-8")
    except Exception as exc:
        for result, *_rest in handles:
            result.status, result.error = "ERROR", str(exc)
    finally:
        for item in handles:
            for handle in item[-2:]:
                try:
                    handle.close()
                except Exception:
                    pass
        for path in temp_paths:
            path.unlink(missing_ok=True)
    return prepared


def process(pair: Pair, mapping: Mapping) -> Result:
    assert pair.r1 is not None and pair.r2 is not None
    pair_dir = safe(pair.stem)
    sample_dir = sample_output_name(pair, mapping)
    # Preserve the hierarchy already created by fastp.  This prevents files
    # with identical basenames from different lanes/batches from colliding.
    out_dir = OUTPUT_DIR / relative_parent(pair.r1, INPUT_DIR) / pair_dir / sample_dir
    out1, out2 = out_dir / f"{pair_dir}_R1.fq.gz", out_dir / f"{pair_dir}_R2.fq.gz"
    result = Result(mapping, pair, str(out1), str(out2))
    marker = out_dir / ".DONE.json"
    # Keep the marker after the runner removes large FASTQ payloads.  On a
    # rerun this lets completed samples be skipped while newly fixed mapping
    # errors are processed normally.
    # A marker is valid only while both payloads are present.  The runner may
    # retain markers after cleanup, but a later invocation must regenerate the
    # FASTQ pair when the payloads were removed.
    if SKIP_EXISTING and marker.is_file() and out1.is_file() and out2.is_file() and _marker_matches(marker):
        result.status = "SKIPPED"
        return result
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp1 = tmp2 = None
    h1 = h2 = None
    try:
        fd1, name1 = tempfile.mkstemp(dir=out_dir, suffix=".tmp"); os.close(fd1)
        fd2, name2 = tempfile.mkstemp(dir=out_dir, suffix=".tmp"); os.close(fd2)
        tmp1, tmp2 = Path(name1), Path(name2)
        h1 = gzip.open(tmp1, "wt", encoding="utf-8", compresslevel=COMPRESSLEVEL)
        h2 = gzip.open(tmp2, "wt", encoding="utf-8", compresslevel=COMPRESSLEVEL)
        with open_fastq(pair.r1) as f1, open_fastq(pair.r2) as f2:
            while True:
                a, b = record(f1), record(f2)
                if a is None and b is None:
                    break
                if a is None or b is None:
                    raise ValueError("R1/R2 record counts differ")
                if key(a[0]) != key(b[0]):
                    raise ValueError(f"R1/R2 headers do not match: {a[0]} / {b[0]}")
                result.total += 1
                if result.total % PREFILTER_READ_PROGRESS_EVERY == 0:
                    print(
                        f"[prefilter read] {mapping.sample_id}/{mapping.barcode_name} "
                        f"reads={result.total} paired={result.paired}",
                        file=sys.stderr,
                        flush=True,
                    )
                ok1, ok2 = r1_ok(a[1]), r2_ok(b[1], mapping.barcode_sequence)
                result.r1_pass += int(ok1); result.r2_pass += int(ok2)
                if ok1 and ok2:
                    result.paired += 1
                    write_record(h1, a)
                    write_record(h2, b, len(WILDCARD_PREFIX) + len(mapping.barcode_sequence) if REMOVE_R2_PREFIX else 0)
                else:
                    result.discarded += 1
        h1.close(); h2.close(); h1 = h2 = None
        os.replace(tmp1, out1); os.replace(tmp2, out2); tmp1 = tmp2 = None
        marker.write_text(json.dumps({"status": "DONE", "config_fingerprint": CONFIG_FINGERPRINT, "total": result.total, "r1_pass": result.r1_pass, "r2_pass": result.r2_pass, "paired": result.paired, "discarded": result.discarded}, indent=2), encoding="utf-8")
    except Exception as exc:
        result.status, result.error = "ERROR", str(exc)
    finally:
        for handle in (h1, h2):
            if handle is not None: handle.close()
        for path in (tmp1, tmp2):
            if path: path.unlink(missing_ok=True)
    return result


def write_report(results: list[Result]) -> None:
    fields = ["sample_id", "barcode_candidate", "barcode_name", "barcode_sequence", "stem", "source_r1", "source_r2", "output_r1", "output_r2", "total_reads", "r1_tso_passed", "r1_tso_pass_pct", "r2_barcode_passed", "r2_barcode_pass_pct", "paired_passed", "paired_pass_pct", "discarded", "discarded_pct", "status", "error"]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temp = REPORT.with_name("." + REPORT.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for r in results:
            total = r.total or 0
            pct = lambda value: f"{(value / total * 100):.2f}" if total else "0.00"
            writer.writerow({
                "sample_id": r.mapping.sample_id,
                "barcode_candidate": r.mapping.barcode_candidate,
                "barcode_name": r.mapping.barcode_name,
                "barcode_sequence": r.mapping.barcode_sequence,
                "stem": r.pair.stem,
                "source_r1": str(r.pair.r1_source or ""),
                "source_r2": str(r.pair.r2_source or ""),
                "output_r1": r.output_r1,
                "output_r2": r.output_r2,
                "total_reads": r.total,
                "r1_tso_passed": r.r1_pass,
                "r1_tso_pass_pct": pct(r.r1_pass),
                "r2_barcode_passed": r.r2_pass,
                "r2_barcode_pass_pct": pct(r.r2_pass),
                "paired_passed": r.paired,
                "paired_pass_pct": pct(r.paired),
                "discarded": r.discarded,
                "discarded_pct": pct(r.discarded),
                "status": r.status,
                "error": r.error,
            })
    os.replace(temp, REPORT)


def main() -> int:
    if not SUMMARY.is_file() or not INPUT_DIR.is_dir():
        raise SystemExit(f"missing summary or input: {SUMMARY} / {INPUT_DIR}")
    pairs, results = discover()
    if not pairs:
        write_report(results)
        print(f"No usable paired FASTQ mappings; summary={REPORT}", file=sys.stderr)
        return 1
    # One task per physical pair.  process_pair() routes all mappings while
    # the compressed input is open, avoiding N full scans for an N-sample mix.
    tasks = pairs
    total_tasks = len(tasks)
    print(f"Prefilter tasks={total_tasks} workers={min(PREFILTER_WORKERS, total_tasks)}", file=sys.stderr)
    # Each task writes a unique sample/barcode directory using temporary files
    # and an atomic rename.  This makes thread-level parallelism safe while
    # preserving the per-task DONE checkpoint and resumability.
    completed = [None] * total_tasks
    finished_tasks = 0

    def record_result(index: int, pair_results: list[Result]) -> None:
        nonlocal finished_tasks
        completed[index] = pair_results
        finished_tasks += 1
        pct = finished_tasks * 100 / total_tasks if total_tasks else 100.0
        for result in pair_results:
            print(
                f"[prefilter {finished_tasks}/{total_tasks} {pct:.1f}%] "
                f"{result.status} {result.mapping.sample_id}/{result.mapping.barcode_name}: "
                f"total={result.total} paired={result.paired}",
                file=sys.stderr,
                flush=True,
            )

    if PREFILTER_WORKERS == 1 or total_tasks < 2:
        for index, pair in enumerate(tasks):
            record_result(index, process_pair(pair))
    else:
        with ThreadPoolExecutor(max_workers=min(PREFILTER_WORKERS, len(tasks))) as executor:
            futures = {
                executor.submit(process_pair, pair): index
                for index, pair in enumerate(tasks)
            }
            for future in as_completed(futures):
                record_result(futures[future], future.result())
    for pair_results in completed:
        results.extend(pair_results or [])
    write_report(results)
    return 1 if any(r.status == "ERROR" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
