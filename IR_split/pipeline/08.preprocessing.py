#!/usr/bin/env python3
"""Stage 08: preprocess IR representative IgBLAST output.

This entry point starts at ``07.igblastn_out``. It never modifies the source
TSVs and keeps the calculation functions in ``models`` unchanged. The
representative state is used as a sample/read validation index; the frozen
``models.preprocessing.AIRR_filter`` function remains the authority for
filtering and ``umi_counts`` calculation.
"""
from __future__ import annotations

import csv
import gc
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import pandas as pd

from models.csr_calculate import calculate_csr
from models.diversity import calculate_diversity_Bcell, calculate_diversity_chain
from models.expression import calculate_expresion, calculate_isotype_ratio
from models.preprocessing import AIRR_filter
from models.shm_calculate import calculate_shm_result


_READ_ID_COLUMNS = ("representative_id", "header", "sequence_id", "read_id")


def _text(value: object) -> str:
    return str(value or "").strip()


def _umi_from_header(value: object) -> str:
    token = _text(value).lstrip("@>").split(None, 1)[0]
    match = re.search(r"#(?:UMI:)?([ACGTN]+)$", token, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _normalise_umi(value: object, header: object = "") -> str:
    umi = _text(value).upper()
    if umi.startswith("#UMI:"):
        umi = umi[5:]
    elif umi.startswith("#"):
        umi = umi[1:]
    return umi or _umi_from_header(header)


def _canonical_id(value: object) -> str:
    token = _text(value).lstrip("@>").split(None, 1)[0]
    token = token.split("#", 1)[0]
    token = re.sub(r"/[12]$", "", token)
    fields = token.split(":")
    limit = 7 if len(fields) >= 8 else len(fields)
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


def _aliases(value: object) -> set[str]:
    raw = _text(value)
    if not raw:
        return set()
    token = raw.lstrip("@>").split(None, 1)[0]
    result = {raw, token, _canonical_id(token), token.split("#", 1)[0]}
    result.discard("")
    return result


def _open_state(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return path.open("r", encoding="utf-8", errors="replace", newline="")


class RepresentativeIndex:
    """Read-only representative-state lookup backed by SQLite."""

    def __init__(self, database: str | Path):
        self.connection = sqlite3.connect(str(database))
        self.connection.execute("PRAGMA query_only=ON")

    def lookup_many(self, sample_id: str, query_ids: Iterable[object]) -> dict[str, str]:
        queries = [_text(value) for value in query_ids]
        variants = {query: _aliases(query) for query in queries if query}
        aliases = sorted({alias for values in variants.values() for alias in values})
        found: dict[str, set[str]] = {}
        for offset in range(0, len(aliases), 400):
            part = aliases[offset:offset + 400]
            marks = ",".join("?" for _ in part)
            rows = self.connection.execute(
                f"SELECT alias, umi FROM aliases WHERE sample_id=? AND alias IN ({marks})",
                [sample_id, *part],
            )
            for alias, umi in rows:
                found.setdefault(str(alias), set()).add(str(umi))
        result: dict[str, str] = {}
        for query, query_aliases in variants.items():
            umis = {umi for alias in query_aliases for umi in found.get(alias, set())}
            if len(umis) == 1:
                result[query] = next(iter(umis))
            elif len(umis) > 1:
                result[query] = "__CONFLICT__"
        return result

    def close(self) -> None:
        self.connection.close()


def build_representative_index(state_paths: list[Path], database: str | Path) -> dict[str, int]:
    """Build an alias index atomically without retaining state rows in RAM."""
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(f".{database.name}.{os.getpid()}.{time.time_ns()}.tmp")
    connection = sqlite3.connect(str(temporary))
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("CREATE TABLE aliases (sample_id TEXT NOT NULL, alias TEXT NOT NULL, umi TEXT NOT NULL, PRIMARY KEY(sample_id, alias, umi))")
        connection.execute("CREATE INDEX aliases_lookup ON aliases(sample_id, alias)")
        state_rows = 0
        state_files = 0
        for state_path in state_paths:
            state_files += 1
            delimiter = "," if state_path.name.endswith((".csv", ".csv.gz")) else "\t"
            with _open_state(state_path) as handle:
                reader = csv.DictReader(handle, delimiter=delimiter)
                for row in reader:
                    sample = _text(row.get("sample_id") or row.get("sample"))
                    umi = _normalise_umi(row.get("umi"), row.get("header"))
                    if not sample or not umi or not re.fullmatch(r"[ACGTN]+", umi):
                        continue
                    aliases: set[str] = set()
                    for column in _READ_ID_COLUMNS:
                        aliases.update(_aliases(row.get(column, "")))
                    if not aliases:
                        continue
                    state_rows += 1
                    connection.executemany(
                        "INSERT OR IGNORE INTO aliases(sample_id, alias, umi) VALUES (?, ?, ?)",
                        [(sample, alias, umi) for alias in aliases],
                    )
                    if state_rows % 100_000 == 0:
                        print(f"[IR preprocessing state] files={state_files}/{len(state_paths)} rows={state_rows:,}", flush=True)
            connection.commit()
        conflicts = int(connection.execute("SELECT COUNT(*) FROM (SELECT sample_id, alias FROM aliases GROUP BY sample_id, alias HAVING COUNT(*) > 1)").fetchone()[0])
        connection.close()
        os.replace(temporary, database)
        return {"state_rows": state_rows, "state_files": state_files, "state_conflicts": conflicts}
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise


def _empty_result(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.iloc[0:0].copy()
    result["umi_counts"] = pd.Series(dtype="int64")
    return result


def filter_and_count(frame: pd.DataFrame, *, sample_id: str, representative_index: RepresentativeIndex | None, allow_row_contract: bool) -> tuple[pd.DataFrame, dict[str, int | str]]:
    input_rows = int(len(frame))
    stats: dict[str, int | str] = {"input_rows": input_rows, "state_matched_rows": 0, "state_unmatched_rows": 0, "filtered_rows": 0, "output_rows": 0}
    if input_rows == 0:
        return _empty_result(frame), stats
    if "sequence_id" not in frame.columns:
        raise ValueError("missing required AIRR column: sequence_id")
    work = frame.copy()
    if representative_index is not None:
        queries = work["sequence_id"].astype(str).tolist()
        matched = representative_index.lookup_many(sample_id, queries)
        mask = pd.Series([bool(matched.get(query)) and matched.get(query) != "__CONFLICT__" for query in queries], index=work.index)
        stats["state_matched_rows"] = int(mask.sum())
        stats["state_unmatched_rows"] = int((~mask).sum())
        work = work.loc[mask].copy()
    elif not allow_row_contract:
        raise ValueError("representative state unavailable and row-contract fallback is disabled")
    if work.empty:
        return _empty_result(work), stats
    work = work.replace(r"^\s*$", pd.NA, regex=True)
    for column in ("v_score", "v_identity"):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    filtered = AIRR_filter(work)
    stats["filtered_rows"] = int(len(filtered)); stats["output_rows"] = int(len(filtered))
    return filtered, stats


def calculate_shm_result_budgeted(frame: pd.DataFrame, workers: int) -> pd.DataFrame:
    workers = max(1, int(workers))
    original = mp.cpu_count
    mp.cpu_count = lambda: workers + 1  # type: ignore[assignment]
    try:
        return calculate_shm_result(frame)
    finally:
        mp.cpu_count = original  # type: ignore[assignment]


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR.parent / "output" / "07.igblastn_out"


def _paths_from_env() -> list[Path]:
    value = os.environ.get("SCIGBLAST_IR_PREPROCESSING_INPUTS_SERIALIZED", "").strip()
    if not value:
        value = os.environ.get("SCIGBLAST_IR_PREPROCESSING_INPUTS", "").strip()
    if value:
        return [Path(item) for item in value.split(os.pathsep) if item]
    single = os.environ.get("SCIGBLAST_IR_PREPROCESSING_INPUT", "").strip()
    return [Path(single)] if single else [DEFAULT_INPUT]


INPUT_DIRS = _paths_from_env()
OUTPUT_DIR = Path(os.environ.get("SCIGBLAST_IR_PREPROCESSING_OUTPUT", "") or str(SCRIPT_DIR.parent / "output" / "08.preprocessing"))
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = SCRIPT_DIR.parent / OUTPUT_DIR
STATE_DIR = OUTPUT_DIR / ".preprocessing_state"
SAMPLE_STATE_DIR = STATE_DIR / "samples"
def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)

FILTER_WORKERS = max(1, int(_env("SCIGBLAST_IR_PREPROCESSING_FILTER_WORKERS", "2")))
SAMPLES_PER_WINDOW = max(1, int(_env("SCIGBLAST_IR_PREPROCESSING_SAMPLES_PER_WINDOW", "1")))
SHM_WORKERS = max(1, int(_env("SCIGBLAST_IR_PREPROCESSING_SHM_WORKERS", "32")))
PROCESS_BUDGET = max(1, int(_env("SCIGBLAST_IR_PREPROCESSING_TOTAL_PROCESS_BUDGET", "64")))
MEMORY_BUDGET_GB = float(_env("SCIGBLAST_IR_PREPROCESSING_MEMORY_BUDGET_GB", "300"))
MIN_AVAILABLE_GB = float(_env("SCIGBLAST_IR_PREPROCESSING_MIN_AVAILABLE_GB", "150"))
PROGRESS_SECONDS = max(1.0, float(_env("SCIGBLAST_IR_PREPROCESSING_PROGRESS_SECONDS", "30")))
ALLOW_ROW_CONTRACT = _env("SCIGBLAST_IR_PREPROCESSING_ALLOW_ROW_CONTRACT", "1") == "1"
UMI_SOURCE = _env("SCIGBLAST_IR_PREPROCESSING_UMI_SOURCE", "auto").strip().lower()
if UMI_SOURCE not in {"auto", "state", "row_contract"}:
    raise SystemExit("SCIGBLAST_IR_PREPROCESSING_UMI_SOURCE must be auto, state or row_contract")
FINAL_FILES = {"TCR.tsv", "BCR.tsv"}
IGNORED_DIRS = {".batches", ".postprocess", ".analysis_state", ".preprocessing_state"}
JUNCTION_COLUMNS = [
    "sequence", "sequence_aa", "cdr3_aa", "junction_aa", "cdr3", "locus",
    "productive", "v_call", "d_call", "j_call", "c_call", "v_score",
    "v_identity", "v_alignment_start", "v_alignment_end", "d_alignment_start",
    "d_alignment_end", "j_alignment_start", "j_alignment_end", "c_alignment_start",
    "c_alignment_end", "sequence_alignment", "sequence_alignment_aa",
    "germline_alignment", "germline_alignment_aa", "umi_counts",
]


def canonical(path: Path) -> Path:
    return path.expanduser().resolve()


def available_memory_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        import psutil
        return float(psutil.virtual_memory().available) / (1024 ** 3)
    except Exception:
        return None


def memory_ok() -> tuple[bool, float | None]:
    value = available_memory_gb()
    return value is None or value >= MIN_AVAILABLE_GB, value


def atomic_json(payload: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_csv(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        frame.to_csv(temporary, index=False)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def source_fingerprint(path: Path) -> dict[str, str | int]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(65536))
    return {"path": str(canonical(path)), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "head_sha256": digest.hexdigest()}


def discover_inputs(roots: list[Path]) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    seen: set[str] = set()
    output = canonical(OUTPUT_DIR)
    for configured in roots:
        root = canonical(configured)
        if not root.is_dir():
            raise FileNotFoundError(f"input directory does not exist: {root}")
        if root == output:
            raise ValueError("preprocessing input and output directories must differ")
        for dirname, dirs, filenames in os.walk(root):
            dirs[:] = sorted(directory for directory in dirs if directory not in IGNORED_DIRS and not canonical(Path(dirname) / directory).is_relative_to(output))
            current = canonical(Path(dirname))
            for filename in sorted(filenames):
                if filename not in FINAL_FILES:
                    continue
                source = canonical(current / filename)
                if str(source) in seen:
                    continue
                seen.add(str(source))
                relative = source.parent.relative_to(root).as_posix() or root.name
                tasks.append({
                    "source": str(source), "input_root": str(root),
                    "relative_sample": relative, "sample": Path(relative).name,
                    "receptor": source.stem.upper(),
                })
    if not tasks:
        raise FileNotFoundError("no TCR.tsv/BCR.tsv files found under configured inputs")
    return tasks


def find_state_paths(root: Path) -> list[Path]:
    explicit = os.environ.get("SCIGBLAST_IR_PREPROCESSING_REPRESENTATIVE_ROOT", "").strip()
    candidates: list[Path] = []
    if explicit:
        base = canonical(Path(explicit))
        if base.is_file():
            candidates.append(base)
        elif base.is_dir():
            candidates.extend([base / ".representative_state.tsv.gz", base / "representative_state.tsv.gz", base / ".representative_state.tsv", base / "representative_state.tsv", base / ".representative_state.csv", base / "representative_state.csv"])
            for name in (".representative_state.tsv.gz", "representative_state.tsv.gz", ".representative_state.tsv", "representative_state.tsv", ".representative_state.csv", "representative_state.csv"):
                candidates.extend(sorted(base.rglob(name)))
    if root.name == "07.igblastn_out":
        output_sibling = root.parent / "06.representative"
    else:
        # A per-dataset input has the standard shape
        # ``<output>/07.igblastn_out/<dataset>``.
        output_sibling = root.parent.parent / "06.representative" / root.name
    if output_sibling.is_dir():
        for name in (".representative_state.tsv.gz", "representative_state.tsv.gz", ".representative_state.tsv", "representative_state.tsv", ".representative_state.csv", "representative_state.csv"):
            candidates.extend(sorted(output_sibling.rglob(name)))
    seen: set[str] = set()
    result: list[Path] = []
    for path in candidates:
        key = str(path)
        if path.is_file() and key not in seen:
            seen.add(key)
            result.append(path)
    return result


def build_index_for_inputs(roots: list[Path]) -> tuple[dict[str, RepresentativeIndex | None], dict[str, str], str, dict[str, int | str]]:
    """Build/reuse one state index per input root.

    Keeping indexes root-local is important when two datasets contain the same
    sample name or recycled instrument query IDs.  Such datasets are separate
    calculation units and must never share a UMI lookup namespace.
    """
    indexes: dict[str, RepresentativeIndex | None] = {}
    modes: dict[str, str] = {}
    aggregate: dict[str, int | str] = {"state_rows": 0, "state_conflicts": 0, "state_files": 0, "state_paths": "", "state_signature": ""}
    all_paths: list[str] = []
    all_signatures: list[dict[str, str | int]] = []
    for configured in roots:
        root = canonical(configured)
        root_key = str(root)
        if UMI_SOURCE == "row_contract":
            indexes[root_key] = None
            modes[root_key] = "row_contract"
            continue
        unique = list(dict.fromkeys(find_state_paths(root)))
        if not unique:
            if UMI_SOURCE == "state":
                raise FileNotFoundError(f"UMI_SOURCE=state but no representative state was found for {root}")
            if not ALLOW_ROW_CONTRACT:
                raise FileNotFoundError(f"representative state missing for {root} and row-contract fallback disabled")
            indexes[root_key] = None
            modes[root_key] = "row_contract"
            continue
        root_hash = hashlib.sha1(root_key.encode("utf-8")).hexdigest()[:12]
        index_path = STATE_DIR / f"representative_index_{root_hash}.sqlite3"
        meta_path = STATE_DIR / f"representative_index_{root_hash}.json"
        signature: list[dict[str, str | int]] = []
        for state in unique:
            stat = state.stat()
            with state.open("rb") as handle:
                head = hashlib.sha256(handle.read(65536)).hexdigest()
            signature.append({"path": str(canonical(state)), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns), "head_sha256": head})
        cached = None
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            cached = None
        if index_path.is_file() and isinstance(cached, dict) and cached.get("signature") == signature:
            meta = {key: value for key, value in cached.items() if key != "signature"}
            print(f"[STATE] reusing representative SQLite index root={root.name} files={len(unique)}", flush=True)
        else:
            print(f"[STATE] indexing representative state root={root.name} files={len(unique)}", flush=True)
            meta = build_representative_index(unique, index_path)
            atomic_json({**meta, "state_paths": ";".join(str(path) for path in unique), "signature": signature}, meta_path)
        if int(meta.get("state_conflicts", 0)):
            # Conflicting aliases are isolated by lookup_many (marked as
            # __CONFLICT__) and excluded for the affected rows only.  Do not
            # abort unrelated samples in the same input root.
            print(
                f"[STATE] warning root={root.name} conflicting_aliases={meta['state_conflicts']}; "
                "affected records will be counted as unmatched",
                flush=True,
            )
        indexes[root_key] = RepresentativeIndex(index_path)
        modes[root_key] = "state"
        aggregate["state_rows"] = int(aggregate.get("state_rows", 0)) + int(meta.get("state_rows", 0))
        aggregate["state_conflicts"] = int(aggregate.get("state_conflicts", 0)) + int(meta.get("state_conflicts", 0))
        aggregate["state_files"] = int(aggregate.get("state_files", 0)) + len(unique)
        all_paths.extend(str(path) for path in unique)
        all_signatures.extend(signature)
    aggregate["state_paths"] = ";".join(all_paths)
    aggregate["state_signature"] = json.dumps(all_signatures, sort_keys=True, separators=(",", ":"))
    distinct_modes = set(modes.values())
    overall_mode = next(iter(distinct_modes)) if len(distinct_modes) == 1 else "mixed"
    return indexes, modes, overall_mode, aggregate


def sample_key(task: dict[str, str]) -> tuple[str, str]:
    return task["input_root"], task["relative_sample"]


def sample_and_batch(key: tuple[str, str]) -> tuple[str, str]:
    relative = Path(key[1])
    return relative.name or Path(key[0]).name, relative.parent.name


def read_one(task: dict[str, str], umi_index: RepresentativeIndex | None, umi_mode: str) -> tuple[dict[str, str], pd.DataFrame | None, dict[str, int | str], str]:
    try:
        frame = pd.read_csv(task["source"], sep="\t", dtype=str, keep_default_na=False, comment="#")
        filtered, stats = filter_and_count(frame, sample_id=task["sample"], representative_index=umi_index if umi_mode == "state" else None, allow_row_contract=ALLOW_ROW_CONTRACT)
        return task, filtered, stats, ""
    except Exception as exc:
        return task, None, {}, f"{type(exc).__name__}: {exc}"


def one_row(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    return frame.reset_index(drop=True).iloc[[0]].copy()


def combine_metrics(frames: list[pd.DataFrame], sample: str, batch: str) -> pd.DataFrame:
    available = [one_row(frame) for frame in frames if frame is not None and not frame.empty]
    available = [frame.drop(columns=["sample", "batch"], errors="ignore") for frame in available if not frame.empty]
    if not available:
        return pd.DataFrame()
    result = pd.concat(available, axis=1)
    result = result.loc[:, ~result.columns.duplicated(keep="first")]
    result.insert(0, "batch", batch)
    result.insert(0, "sample", sample)
    return result


def compute_sample(items: dict[str, tuple[dict[str, str], pd.DataFrame]], sample: str, batch: str) -> tuple[pd.DataFrame, list[str]]:
    frames = [frame for _, frame in items.values() if not frame.empty]
    if not frames:
        return pd.DataFrame(), ["all filtered records are empty"]
    errors: list[str] = []
    metrics: list[pd.DataFrame] = []
    try:
        metrics.append(calculate_expresion(frames))
    except Exception as exc:
        errors.append(f"expression: {type(exc).__name__}: {exc}")
    for receptor, (_, frame) in items.items():
        try:
            metrics.append(calculate_diversity_chain(frame))
        except Exception as exc:
            errors.append(f"{receptor} diversity: {type(exc).__name__}: {exc}")
    base = combine_metrics(metrics, sample, batch)
    bcr = items.get("BCR")
    if bcr and not bcr[1].empty:
        extra: list[pd.DataFrame] = [base]
        for label, function in (("isotype", calculate_isotype_ratio), ("CSR", calculate_csr), ("SHM", calculate_shm_result), ("B-cell diversity", calculate_diversity_Bcell)):
            try:
                if label == "SHM":
                    extra.append(calculate_shm_result_budgeted(bcr[1], min(SHM_WORKERS, PROCESS_BUDGET)))
                else:
                    extra.append(function(bcr[1]))
            except Exception as exc:
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return combine_metrics(extra, sample, batch), errors
    return base, errors


def write_junctions(items: dict[str, tuple[dict[str, str], pd.DataFrame]], output: Path, labels: dict[str, str]) -> None:
    for receptor, (task, frame) in items.items():
        if frame.empty or "locus" not in frame:
            continue
        sample_dir = output / "junction_peps" / labels[task["input_root"]] / task["relative_sample"] / receptor
        for locus, chain in frame.groupby("locus", dropna=False):
            locus_name = str(locus).strip() or "UNKNOWN"
            columns = [column for column in JUNCTION_COLUMNS if column in chain.columns]
            atomic_csv(chain.loc[:, columns], sample_dir / f"{locus_name}.csv")


def input_labels(roots: list[str]) -> dict[str, str]:
    by_name: defaultdict[str, list[str]] = defaultdict(list)
    for root in roots:
        by_name[Path(root).name].append(root)
    result: dict[str, str] = {}
    used: set[str] = set()
    for root in roots:
        base = Path(root).name or "input"
        label = base if len(by_name[base]) == 1 else f"{Path(root).parent.name}__{base}"
        if label in used:
            label = f"{label}__{hashlib.sha1(root.encode()).hexdigest()[:8]}"
        used.add(label)
        result[root] = label
    return result


def runtime_fingerprint(umi_mode: str, state_meta: dict[str, int | str]) -> str:
    digest = hashlib.sha256()
    files = [Path(__file__), *sorted((SCRIPT_DIR / "models").glob("*.py"))]
    for path in files:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    digest.update(umi_mode.encode())
    digest.update(json.dumps(state_meta, sort_keys=True).encode())
    return digest.hexdigest()


def checkpoint_path(key: tuple[str, str]) -> Path:
    return SAMPLE_STATE_DIR / hashlib.sha256(json.dumps(list(key), ensure_ascii=False).encode()).hexdigest()[:24]


def load_checkpoint(key: tuple[str, str], tasks: list[dict[str, str]], fingerprint: str) -> tuple[pd.DataFrame, dict] | None:
    directory = checkpoint_path(key)
    marker = directory / "sample.DONE.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload.get("status") not in {"OK", "EMPTY"} or payload.get("runtime_fingerprint") != fingerprint:
            return None
        if payload.get("sources") != [source_fingerprint(Path(task["source"])) for task in tasks]:
            return None
        if payload["status"] == "EMPTY":
            return pd.DataFrame(), payload["manifest"]
        result = pd.read_pickle(directory / "metrics.pkl")
        return result, payload["manifest"]
    except (OSError, ValueError, TypeError, KeyError, EOFError, ImportError):
        return None


def save_checkpoint(key: tuple[str, str], tasks: list[dict[str, str]], fingerprint: str, result: pd.DataFrame, manifest: dict, status: str) -> None:
    directory = checkpoint_path(key)
    directory.mkdir(parents=True, exist_ok=True)
    if status == "OK":
        temporary = directory / f".metrics.{os.getpid()}.{time.time_ns()}.tmp"
        result.to_pickle(temporary)
        os.replace(temporary, directory / "metrics.pkl")
    atomic_json({"status": status, "runtime_fingerprint": fingerprint, "sources": [source_fingerprint(Path(task["source"])) for task in tasks], "manifest": manifest, "written_at": time.time()}, directory / "sample.DONE.json")


def main() -> int:
    try:
        tasks = discover_inputs(INPUT_DIRS)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        SAMPLE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        umi_indexes, umi_modes, umi_mode, state_meta = build_index_for_inputs(INPUT_DIRS)
    except Exception as exc:
        print(f"[INPUT] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    groups: OrderedDict[tuple[str, str], list[dict[str, str]]] = OrderedDict()
    for task in tasks:
        groups.setdefault(sample_key(task), []).append(task)
    roots = sorted({task["input_root"] for task in tasks})
    labels = input_labels(roots)
    fingerprint = runtime_fingerprint(umi_mode, state_meta)
    print(f"[IR preprocessing] roots={len(roots)} files={len(tasks)} samples={len(groups)} mode={umi_mode} filter_workers={FILTER_WORKERS} shm_workers={SHM_WORKERS} process_budget={PROCESS_BUDGET} memory_budget={MEMORY_BUDGET_GB:g}GB min_available={MIN_AVAILABLE_GB:g}GB", flush=True)
    metrics_rows: list[pd.DataFrame] = []
    manifest_rows: list[dict] = []
    failed = False
    group_items = list(groups.items())
    try:
        for start in range(0, len(group_items), SAMPLES_PER_WINDOW):
            window = group_items[start:start + SAMPLES_PER_WINDOW]
            for offset, (key, sample_tasks) in enumerate(window, 1):
                sample, batch = sample_and_batch(key)
                sample_umi_mode = umi_modes.get(key[0], umi_mode)
                checkpoint = load_checkpoint(key, sample_tasks, fingerprint)
                if checkpoint is not None:
                    result, manifest = checkpoint
                    manifest_rows.append(manifest)
                    if not result.empty:
                        metrics_rows.append(result)
                    print(f"[IR preprocessing] sample={start + offset}/{len(group_items)} sample={sample} batch={batch} status=SKIPPED(checkpoint)", flush=True)
                    continue
                allowed, available = memory_ok()
                if not allowed:
                    failed = True
                    manifest = {"input_root": key[0], "relative_sample": key[1], "sample_id": sample, "sample": sample, "batch": batch, "available_receptors": "", "tcr_path": "", "bcr_path": "", "umi_source": sample_umi_mode, "representative_state": state_meta.get("state_paths", ""), "state_total_rows": state_meta.get("state_rows", ""), "state_matched_rows": 0, "state_unmatched_rows": 0, "row_contract_valid": "0", "state_conflicts": state_meta.get("state_conflicts", ""), "status": "PAUSED_MEMORY", "error": f"available memory {available:.1f}GB below {MIN_AVAILABLE_GB:g}GB floor"}
                    manifest_rows.append(manifest)
                    print(f"[IR preprocessing] memory_paused sample={sample} available={available:.1f}GB", file=sys.stderr, flush=True)
                    break
                items: dict[str, tuple[dict[str, str], pd.DataFrame]] = {}
                errors: list[str] = []
                sample_stats: list[dict[str, int | str]] = []
                print(f"[IR preprocessing] read sample={sample} files={len(sample_tasks)}", flush=True)
                with ThreadPoolExecutor(max_workers=min(FILTER_WORKERS, len(sample_tasks))) as executor:
                    root_index = umi_indexes.get(key[0])
                    root_mode = umi_modes.get(key[0], "row_contract")
                    futures = [executor.submit(read_one, task, root_index, root_mode) for task in sample_tasks]
                    for future in as_completed(futures):
                        task, frame, stats, error = future.result()
                        if error:
                            errors.append(f"{task['source']}: {error}")
                        elif task["receptor"] in items:
                            errors.append(f"duplicate {task['receptor']} source")
                        elif frame is not None:
                            items[task["receptor"]] = (task, frame)
                            sample_stats.append(stats)
                result, model_errors = compute_sample(items, sample, batch)
                errors.extend(model_errors)
                try:
                    write_junctions(items, OUTPUT_DIR, labels)
                except Exception as exc:
                    errors.append(f"junction output: {type(exc).__name__}: {exc}")
                paths = {name: value[0]["source"] for name, value in items.items()}
                state_matched = sum(int(value.get("state_matched_rows", 0)) for value in sample_stats)
                state_unmatched = sum(int(value.get("state_unmatched_rows", 0)) for value in sample_stats)
                manifest = {"input_root": key[0], "relative_sample": key[1], "sample_id": sample, "sample": sample, "batch": batch, "available_receptors": ",".join(sorted(items)), "tcr_path": paths.get("TCR", ""), "bcr_path": paths.get("BCR", ""), "umi_source": sample_umi_mode, "representative_state": state_meta.get("state_paths", ""), "state_total_rows": state_meta.get("state_rows", ""), "state_matched_rows": state_matched, "state_unmatched_rows": state_unmatched, "row_contract_valid": "1" if sample_umi_mode == "row_contract" and not errors else "0", "state_conflicts": state_meta.get("state_conflicts", ""), "status": "OK" if not errors and not result.empty else ("EMPTY" if not errors else "ERROR"), "error": "; ".join(errors)}
                manifest_rows.append(manifest)
                if not result.empty:
                    metrics_rows.append(result)
                if manifest["status"] in {"OK", "EMPTY"}:
                    save_checkpoint(key, sample_tasks, fingerprint, result, manifest, manifest["status"])
                else:
                    failed = True
                print(f"[IR preprocessing] sample={start + offset}/{len(group_items)} sample={sample} batch={batch} receptors={manifest['available_receptors'] or '-'} status={manifest['status']}", flush=True)
                del items
                gc.collect()
            if failed and manifest_rows and manifest_rows[-1].get("status") == "PAUSED_MEMORY":
                break
    finally:
        for umi_index in {value for value in umi_indexes.values() if value is not None}:
            umi_index.close()
    output = pd.concat(metrics_rows, ignore_index=True) if metrics_rows else pd.DataFrame()
    atomic_csv(output, OUTPUT_DIR / "Datapoint.csv")
    manifest_columns = [
        "input_root", "relative_sample", "sample_id", "sample", "batch",
        "available_receptors", "tcr_path", "bcr_path", "umi_source",
        "representative_state", "state_total_rows", "state_matched_rows",
        "state_unmatched_rows", "row_contract_valid", "state_conflicts",
        "status", "error",
    ]
    manifest = pd.DataFrame(manifest_rows, columns=manifest_columns)
    atomic_csv(manifest, OUTPUT_DIR / "processing_manifest.csv")
    run_status = "DONE" if not failed and not output.empty else "INCOMPLETE"
    atomic_json({"status": run_status, "umi_source": umi_mode, "samples": len(manifest_rows), "retained_rows": len(output), "failed": int(failed), "written_at": time.time()}, STATE_DIR / "run.json")
    print(f"[IR preprocessing] done samples={len(manifest_rows)} retained={len(output)} failed={int(failed)} output={OUTPUT_DIR}", flush=True)
    return 1 if failed else (0 if not output.empty else 1)


if __name__ == "__main__":
    raise SystemExit(main())
