"""Discover Treemap-ready AIRR files without loading their records.

The discovery functions only read summary files and candidate table headers.
Clone rows are left for the analysis worker to stream when it runs.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CHAINS = frozenset({"IGH", "IGK", "IGL", "TRA", "TRB", "TRD", "TRG"})
TABLE_SUFFIXES = (".csv", ".tsv", ".csv.gz", ".tsv.gz")
REQUIRED_COLUMNS = frozenset({"productive", "v_call", "j_call"})
JUNCTION_COLUMNS = frozenset({"junction_aa", "cdr3_aa"})
MAX_HEADER_BYTES = 1024 * 1024
MAX_SUMMARY_ROWS = 250_000
SUMMARY_NAMES = frozenset({
    "chain_summary.csv", "igblast_summary.tsv", "igblastn_run_summary.tsv",
    "processing_manifest.csv", "stage8_summary.csv", "conversion_summary.csv",
    "umi_count_summary.csv", "datapoint.csv",
})
SKIP_DIRS = frozenset({
    ".pipeline_state", ".tasks", ".batches", ".tmp", ".analysis_state",
    ".preprocessing_state", ".cluster_state", "logs", "reports", "report",
    "raw", "analysis", "treemap_report",
})


def _normal_rel(value: str | Path) -> str | None:
    text = str(value).replace("\\", "/").strip()
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix()


def _safe_root(value: str | Path) -> Path:
    if not str(value).strip():
        raise ValueError("discovery root is empty")
    root = Path(value)
    if root.is_symlink():
        raise ValueError("discovery root must not be a symlink")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("discovery root is not a directory")
    return resolved


def _confined_file(root: Path, value: str | Path) -> Path:
    raw = Path(value)
    lexical = Path(os.path.abspath(raw if raw.is_absolute() else root / raw))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise ValueError("source path is outside the task output directory") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("source path contains a symlink")
    resolved = lexical.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("resolved source path is outside the task output directory") from exc
    if not resolved.is_file():
        raise ValueError("source is not a regular file")
    return resolved


def _open_text(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open("r", encoding="utf-8-sig", newline="")


def _header(path: Path) -> list[str]:
    columns = _raw_header(path)
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise ValueError("missing required AIRR columns: " + ", ".join(missing))
    if not columns.intersection(JUNCTION_COLUMNS):
        raise ValueError("missing required AIRR column: junction_aa or cdr3_aa")
    return sorted(columns)


def _raw_header(path: Path) -> set[str]:
    with _open_text(path) as handle:
        line = handle.readline(MAX_HEADER_BYTES + 1)
    if len(line.encode("utf-8", errors="replace")) > MAX_HEADER_BYTES:
        raise ValueError("table header exceeds the 1 MiB discovery limit")
    if not line:
        raise ValueError("table is empty")
    lowered = path.name.lower()
    delimiter = "\t" if ".tsv" in lowered else "," if ".csv" in lowered else ("\t" if line.count("\t") > line.count(",") else ",")
    return {str(column).lstrip("\ufeff") for column in next(csv.reader([line], delimiter=delimiter))}


def _valid_table(path: Path) -> str:
    try:
        _header(path)
    except (OSError, EOFError, UnicodeError, csv.Error, ValueError) as exc:
        return str(exc)
    return ""


def _entry_id(identity: str, paths: Iterable[str]) -> str:
    material = identity + "\0" + "\0".join(sorted(paths))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _entry(sample_key: str, sample_label: str, chain: str | None,
           sources: list[dict[str, str]], *, error: str = "") -> dict[str, Any]:
    safe_chain = chain.upper() if chain and chain.upper() in CHAINS else None
    ordered_sources = sorted(sources, key=lambda item: (item.get("path", ""), item.get("chain", "")))
    public_sources = [{key: value for key, value in item.items() if key in {"path", "chain"}} for item in ordered_sources]
    source_files = [item.get("relative_path") or Path(item.get("path", "")).name for item in ordered_sources]
    return {
        "id": _entry_id(f"{sample_key}\0{safe_chain or ''}\0{error}", (item["path"] for item in public_sources)),
        "sample_key": sample_key,
        "sample_label": sample_label,
        "chain": safe_chain,
        "sources": public_sources,
        "source_files": source_files,
        "valid": not error and bool(public_sources),
        "error": error,
    }


def _issue(identity: str, label: str, chain: str | None, error: str) -> dict[str, Any]:
    return _entry(identity, label, chain, [], error=error)


def _is_table(path: Path) -> bool:
    return path.name.lower().endswith(TABLE_SUFFIXES)


def _known_chain(value: str | None) -> str | None:
    candidate = (value or "").strip().upper()
    return candidate if candidate in CHAINS else None


def _stable_sample_key(value: str | None, fallback: str) -> str:
    safe = _normal_rel(value or "")
    return safe or fallback


def _read_dict_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        for index, row in enumerate(reader):
            if index >= MAX_SUMMARY_ROWS:
                raise ValueError(f"summary exceeds {MAX_SUMMARY_ROWS} rows")
            yield {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}


def _summary_path(root: Path, stage: str, dataset: str, name: str) -> Path | None:
    candidates = (root / stage / dataset / name, root / stage / name)
    return next((candidate for candidate in candidates if candidate.is_file() and not candidate.is_symlink()), None)


def _checked_source(root: Path, path_value: str | Path) -> tuple[str | None, str]:
    try:
        path = _confined_file(root, path_value)
        error = _valid_table(path)
        return (str(path), "") if not error else (None, error)
    except (OSError, ValueError) as exc:
        return None, str(exc)


def _source(root: Path, path: str, chain: str | None = None) -> dict[str, str]:
    result = {"path": path, "relative_path": Path(path).relative_to(root).as_posix()}
    if chain:
        result["chain"] = chain
    return result


def _consolidate(entries: Iterable[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str | None], dict[str, Any]] = {}
    diagnostics: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if not entry["valid"]:
            diagnostics[(entry["id"], entry["error"])] = entry
            continue
        key = (entry["sample_key"], entry["chain"])
        group = grouped.setdefault(key, {"label": entry["sample_label"], "sources": {}})
        for index, source in enumerate(entry["sources"]):
            path = source["path"]
            relative = entry["source_files"][index] if index < len(entry["source_files"]) else Path(path).name
            old = group["sources"].get(path)
            if old is None or (not old.get("chain") and source.get("chain")):
                group["sources"][path] = {**source, "relative_path": relative}
    merged = [
        _entry(sample_key, values["label"], chain, list(values["sources"].values()))
        for (sample_key, chain), values in grouped.items()
    ]
    return sorted([*merged, *diagnostics.values()], key=lambda item: (item["sample_key"], item["chain"] or "", item["id"]))


def _labels_for_inputs(input_roots: Iterable[str]) -> dict[str, str]:
    roots = sorted(set(value for value in input_roots if value))
    by_name: defaultdict[str, list[str]] = defaultdict(list)
    for value in roots:
        by_name[Path(value).name].append(value)
    labels: dict[str, str] = {}
    used: set[str] = set()
    for value in roots:
        path = Path(value)
        base = path.name or "input"
        label = base if len(by_name[base]) == 1 else f"{path.parent.name}__{base}"
        if label in used:
            label = f"{label}__{hashlib.sha1(value.encode()).hexdigest()[:8]}"
        used.add(label)
        labels[value] = label
    return labels


def _discover_ir(root: Path, dataset: str, stage_path: Path | None = None) -> list[dict[str, Any]] | None:
    stage = stage_path or (root / "08.preprocessing" / dataset)
    manifest = stage / "processing_manifest.csv"
    if not manifest.is_file() or manifest.is_symlink():
        return None
    try:
        manifest = _confined_file(root, manifest)
        rows = list(_read_dict_rows(manifest))
    except (OSError, ValueError) as exc:
        return [_issue(dataset, dataset, None, f"invalid IR processing manifest: {exc}")]
    labels = _labels_for_inputs(row.get("input_root", "") for row in rows)
    entries: list[dict[str, Any]] = []
    for row in rows:
        sample = row.get("sample_id") or row.get("sample") or "unknown sample"
        label = labels.get(row.get("input_root", ""), "input")
        relative_sample = _normal_rel(row.get("relative_sample", ""))
        sample_key = f"{label}/{relative_sample}" if relative_sample else label
        status = row.get("status", "").upper()
        if status not in {"OK", "EMPTY"}:
            entries.append(_issue(sample_key, sample, None, f"IR preprocessing status is {status or 'unknown'}"))
            continue
        receptor_names = [name.strip() for name in row.get("available_receptors", "").split(",") if name.strip()]
        if not receptor_names:
            if status == "OK":
                entries.append(_issue(sample_key, sample, None, "IR manifest lists no available receptor files"))
            continue
        for receptor in sorted(set(name.upper() for name in receptor_names)):
            if receptor not in {"TCR", "BCR"} or not relative_sample:
                entries.append(_issue(sample_key, sample, None, "IR manifest contains an invalid sample or receptor identity"))
                continue
            folder = stage / "junction_peps" / label / Path(*PurePosixPath(relative_sample).parts) / receptor
            try:
                safe_folder = folder.resolve(strict=True)
                safe_folder.relative_to(root)
                if any(part.is_symlink() for part in _path_chain(root, folder)):
                    raise ValueError("IR junction path contains a symlink")
                files = sorted(path for path in folder.iterdir() if path.is_file() and _known_chain(path.stem))
            except (OSError, ValueError) as exc:
                files = []
                entries.append(_issue(sample_key, sample, None, f"unsafe IR junction path for {receptor}: {exc}"))
            if not files and not any(entry["sample_key"] == sample_key and entry["error"].startswith("unsafe IR junction path") for entry in entries[-1:]):
                entries.append(_issue(sample_key, sample, None, f"IR junction_peps files missing for {receptor}"))
                continue
            for path in files:
                chain = _known_chain(path.stem)
                checked, error = _checked_source(root, path)
                if checked:
                    entries.append(_entry(sample_key, sample, chain, [_source(root, checked, chain)]))
                else:
                    entries.append(_issue(sample_key, sample, chain, error))
    return entries


def _path_chain(root: Path, candidate: Path) -> Iterable[Path]:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return ()
    current = root
    paths = [root]
    for part in relative.parts:
        current = current / part
        paths.append(current)
    return paths


def _discover_mapping(root: Path, dataset: str, stage_path: Path | None = None,
                      selected_samples: set[str] | None = None,
                      selected_chains: set[str] | None = None) -> list[dict[str, Any]] | None:
    stage = stage_path or (root / "03.umi_count" / dataset)
    summary = stage / "umi_count_summary.csv"
    if not summary.is_file() or summary.is_symlink():
        return None
    entries: list[dict[str, Any]] = []
    try:
        summary = _confined_file(root, summary)
        rows = list(_read_dict_rows(summary))
    except (OSError, ValueError) as exc:
        return [_issue(dataset, dataset, None, f"invalid reMapping summary: {exc}")]
    for row in rows:
        sample_key = _stable_sample_key(row.get("sample_key"), row.get("sample_id") or "unknown sample")
        sample_label = row.get("sample_id") or sample_key
        chain = _known_chain(row.get("chain"))
        if selected_samples is not None and sample_key not in selected_samples and sample_label not in selected_samples:
            continue
        if selected_chains is not None and chain not in selected_chains:
            continue
        relative = _normal_rel(row.get("source_file", ""))
        if row.get("status", "").upper() != "OK":
            entries.append(_issue(sample_key, sample_label, chain, f"reMapping UMI count status is {row.get('status') or 'unknown'}"))
            continue
        if not chain or not relative:
            entries.append(_issue(sample_key, sample_label, chain, "reMapping summary lacks a valid sample file or chain"))
            continue
        output = stage / Path(*PurePosixPath(relative).parts).with_suffix(".tsv")
        checked, error = _checked_source(root, output)
        if checked:
            entries.append(_entry(sample_key, sample_label, chain, [_source(root, checked, chain)]))
        else:
            entries.append(_issue(sample_key, sample_label, chain, error))
    return entries


def _discover_base_or_pig(root: Path, dataset: str, pipeline: str, stage_path: Path | None = None) -> list[dict[str, Any]] | None:
    stage = stage_path or (root / "05.igblastn_out" / dataset)
    summary = next((p for p in (stage / "chain_summary.csv", stage / "igblastn_run_summary.tsv", stage / "igblast_summary.tsv") if p.is_file() and not p.is_symlink()), None)
    if summary is None:
        return None
    entries: list[dict[str, Any]] = []
    try:
        summary = _confined_file(root, summary)
        rows = _read_dict_rows(summary)
    except (OSError, ValueError) as exc:
        return [_issue(dataset, dataset, None, f"invalid {pipeline} IgBLAST summary: {exc}")]
    for row in rows:
        sample = row.get("sample_id") or "unknown sample"
        pair = row.get("pair_id") or ""
        sample_key = _stable_sample_key(f"{pair}/{sample}" if pair else sample, sample)
        chain = _known_chain(row.get("chain"))
        status = row.get("status", "").upper()
        if status != "OK":
            entries.append(_issue(sample_key, sample, chain, f"{pipeline} IgBLAST status is {status or 'unknown'}"))
            continue
        if not chain:
            entries.append(_issue(sample_key, sample, None, f"{pipeline} summary has an unknown chain"))
            continue
        output = row.get("output_path", "").strip()
        if not output:
            entries.append(_issue(sample_key, sample, chain, f"{pipeline} success summary has no output_path"))
            continue
        checked, error = _checked_source(root, output)
        if checked:
            entries.append(_entry(sample_key, sample, chain, [_source(root, checked, chain)]))
        else:
            entries.append(_issue(sample_key, sample, chain, error))
    return entries


def _summary_loci(path: Path) -> tuple[list[str], str]:
    try:
        loci: set[str] = set()
        for row in _read_dict_rows(path):
            if "retained" in row and row["retained"].upper() not in {"T", "TRUE", "1", "YES"}:
                continue
            chain = _known_chain(row.get("locus"))
            if chain:
                loci.add(chain)
        if not loci:
            return [], "per-file 10X summary has no retained known locus"
        return sorted(loci), ""
    except (OSError, ValueError, csv.Error) as exc:
        return [], f"could not read 10X per-file summary: {exc}"


def _table_for_summary(stage: Path, summary: Path) -> Path | None:
    suffix = ".stage8_summary.csv"
    stem = summary.name[:-len(suffix)] if summary.name.lower().endswith(suffix) else ""
    if not stem:
        return None
    candidates = [path for path in summary.parent.glob(stem + ".*") if _is_table(path) and ".stage8_summary." not in path.name.lower()]
    return candidates[0] if len(candidates) == 1 else None


def _discover_10x(root: Path, dataset: str, stage_path: Path | None = None) -> list[dict[str, Any]] | None:
    stage = stage_path or (root / "08.cluster" / dataset)
    if not stage.is_dir():
        stage = root / "08.cluster"
    aggregate = stage / "stage8_summary.csv"
    if stage.is_symlink():
        return [_issue(dataset, dataset, None, "10X cluster output path contains a symlink")]
    per_file = sorted(path for path in stage.rglob("*.stage8_summary.csv") if path.name.lower() != "stage8_summary.csv" and not any(part.is_symlink() for part in _path_chain(stage, path))) if stage.is_dir() else []
    if not aggregate.is_file() and not per_file:
        return None
    entries: list[dict[str, Any]] = []
    for summary in per_file:
        try:
            summary = _confined_file(root, summary)
        except (OSError, ValueError) as exc:
            entries.append(_issue(dataset, dataset, None, f"invalid 10X per-file summary: {exc}"))
            continue
        loci, error = _summary_loci(summary)
        table = _table_for_summary(stage, summary)
        relative_parent = summary.parent.relative_to(stage).as_posix()
        sample_key = _stable_sample_key(relative_parent, summary.parent.name or "sample")
        sample_label = summary.parent.name or sample_key
        if error:
            entries.append(_issue(sample_key, sample_label, None, error))
            continue
        if table is None:
            entries.append(_issue(sample_key, sample_label, None, "10X per-file summary does not resolve to exactly one output table"))
            continue
        checked, table_error = _checked_source(root, table)
        if not checked:
            entries.extend(_issue(sample_key, sample_label, chain, table_error) for chain in loci)
            continue
        # A 10X output file can contain multiple loci. Keep the table intact;
        # consumers filter its locus column using the entry's chain.
        for chain in loci:
            entries.append(_entry(sample_key, sample_label, chain, [_source(root, checked)]))
    if not per_file and aggregate.is_file():
        entries.append(_issue(dataset, dataset, None, "10X aggregate summary has no per-file locus identities"))
    return entries


def discover_job(row: dict) -> list[dict[str, Any]]:
    """Return final AIRR sources for a Web job row.

    Paths are checked against ``row['output_root']`` and no symlink is accepted.
    """
    try:
        root = _safe_root(row.get("output_root", ""))
    except (OSError, TypeError, ValueError) as exc:
        return [_issue("", "", None, f"invalid task output root: {exc}")]
    pipeline = str(row.get("pipeline", "")).strip()
    dataset = str(row.get("dataset", "")).strip()
    if not dataset or _normal_rel(dataset) is None:
        return [_issue("", "", None, "job row has no valid dataset label")]
    if pipeline == "mapping":
        options = row.get("options_json", {})
        if isinstance(options, str):
            try:
                options = json.loads(options) if options.strip() else {}
            except (TypeError, ValueError):
                options = {}
        if not isinstance(options, dict):
            options = {}
        raw_samples = options.get("mapping_samples")
        raw_chains = options.get("mapping_chains")
        selected_samples = {str(item) for item in raw_samples} if isinstance(raw_samples, (list, tuple, set)) else None
        selected_chains = {_known_chain(str(item)) for item in raw_chains} if isinstance(raw_chains, (list, tuple, set)) else None
        selected_chains = {chain for chain in selected_chains if chain} if selected_chains is not None else None
        entries = _discover_mapping(root, dataset, selected_samples=selected_samples, selected_chains=selected_chains)
    elif pipeline == "ir_split":
        entries = _discover_ir(root, dataset)
    elif pipeline == "10x_split":
        entries = _discover_10x(root, dataset)
    elif pipeline in {"igblast_base", "pig_igblast"}:
        entries = _discover_base_or_pig(root, dataset, pipeline)
    else:
        entries = [_issue(dataset, dataset, None, f"unsupported pipeline: {pipeline or 'unknown'}")]
    if entries is None:
        entries = [_issue(dataset, dataset, None, f"no final-stage source summary found for {pipeline}")]
    return _consolidate(entries, root)


def _walk_tables(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and name.lower() not in SKIP_DIRS and not (current_path / name).is_symlink())
        for name in sorted(files):
            path = current_path / name
            if _is_table(path) and name.lower() not in SUMMARY_NAMES and ".stage8_summary." not in name.lower():
                yield path


def _filename_identity(path: Path, root: Path) -> tuple[str, str, str | None, bool]:
    relative = path.relative_to(root)
    name = path.name
    for suffix in (".csv.gz", ".tsv.gz", ".csv", ".tsv"):
        if name.lower().endswith(suffix):
            stem = name[:-len(suffix)]
            break
    else:
        stem = path.stem
    match = re.match(r"^(?P<sample>.+?)(?:__|_)(?P<chain>IGH|IGK|IGL|TRA|TRB|TRD|TRG)$", stem, re.IGNORECASE)
    sample = match.group("sample") if match else stem
    chain = _known_chain(match.group("chain")) if match else None
    sample_key = _stable_sample_key(relative.parent.as_posix() + ("/" + sample if sample not in {"", "."} else ""), sample)
    combined = stem.upper() in {"TCR", "BCR"} or stem.upper().endswith(("_TCR", "_BCR"))
    return sample_key, sample, chain, combined


def discover_directory(root: Path) -> list[dict[str, Any]]:
    """Discover final outputs in a user-selected directory.

    Recognized stage summaries take precedence. Generic scans accept only
    explicitly chain-named AIRR files; combined or unnameable AIRR files are
    returned as invalid diagnostics rather than guessed from a partial read.
    """
    try:
        directory = _safe_root(root)
    except (OSError, TypeError, ValueError) as exc:
        return [_issue("", "", None, f"invalid source directory: {exc}")]

    # If the user points at a task output root, prefer final-stage summaries so
    # intermediate products and aggregate duplicates are ignored. Also support
    # selecting a dataset or stage directory directly.
    summaries: list[tuple[str, Path, Any]] = []
    stage_specs = (
        ("03.umi_count", "umi_count_summary.csv", _discover_mapping),
        ("08.preprocessing", "processing_manifest.csv", _discover_ir),
        ("08.cluster", "stage8_summary.csv", _discover_10x),
    )
    final_stage_present = directory.name in {"03.umi_count", "08.preprocessing", "08.cluster"}
    for stage_name, summary_name, discover in stage_specs:
        candidates = [path for path in directory.rglob(summary_name)
                      if path.is_file() and not any(part.is_symlink() for part in _path_chain(directory, path))]
        if directory.name == summary_name and directory.is_file():
            candidates.append(directory)
        for summary in sorted(set(candidates)):
            summaries.append((stage_name, summary, discover))
        if stage_name == "08.cluster":
            per_file_summaries = [path for path in directory.rglob("*.stage8_summary.csv")
                                  if path.is_file() and not any(part.is_symlink() for part in _path_chain(directory, path))]
            stage_dirs: set[Path] = set()
            for per_file in per_file_summaries:
                for ancestor in per_file.parents:
                    if ancestor == directory.parent:
                        break
                    if ancestor.parent.name == "08.cluster":
                        stage_dirs.add(ancestor)
                        break
                    if ancestor == directory:
                        break
            for stage_dir in sorted(stage_dirs):
                aggregate = stage_dir / "stage8_summary.csv"
                if not any(existing[0] == stage_name and existing[1].parent == stage_dir for existing in summaries):
                    summaries.append((stage_name, aggregate, discover))
    base_names = ("chain_summary.csv", "igblastn_run_summary.tsv", "igblast_summary.tsv")
    base_summaries = [path for name in base_names for path in directory.rglob(name)
                      if path.is_file() and not any(part.is_symlink() for part in _path_chain(directory, path))]
    summaries.extend(("05.igblastn_out", summary, _discover_base_or_pig) for summary in sorted(set(base_summaries)))

    # Stage folders without their authoritative final summaries are diagnostic
    # entries. In particular, an IR/10X folder must not fall back to earlier
    # IgBLAST intermediates.
    final_stage_present = final_stage_present or any(
        current.name in {"03.umi_count", "08.preprocessing", "08.cluster"}
        for current in directory.rglob("*") if current.is_dir() and not current.is_symlink()
    )
    discovered: list[dict[str, Any]] = []
    represented_stages: set[str] = set()
    for stage_name, summary, discover in summaries:
        stage_path = summary.parent
        if stage_path.name == stage_name:
            # A user can select a stage folder directly, with summary files
            # directly inside it.
            stage_path = stage_path / directory.name
        dataset = stage_path.name or directory.name
        try:
            if discover is _discover_base_or_pig:
                found = discover(directory, dataset, "igblast_base", stage_path)
            else:
                found = discover(directory, dataset, stage_path)
        except (OSError, ValueError) as exc:
            found = [_issue(dataset, dataset, None, f"could not discover {stage_name} outputs: {exc}")]
        if found is None:
            continue
        represented_stages.add(stage_name)
        summary_parent = summary.parent if summary.exists() else stage_path
        relative_parts = list(summary_parent.relative_to(directory).parts)
        if stage_name in relative_parts:
            relative_parts = relative_parts[relative_parts.index(stage_name) + 1:]
        namespace = "/".join(relative_parts) or dataset
        for item in found:
            new_key = f"{namespace}/{item['sample_key']}" if item["sample_key"] else namespace
            discovered.append(_entry(new_key, item["sample_label"], item["chain"],
                                     [{"path": source["path"], **({"chain": source["chain"]} if source.get("chain") else {}),
                                       "relative_path": item["source_files"][index]}
                                      for index, source in enumerate(item["sources"])], error=item["error"]))

    if discovered:
        return _consolidate(discovered, directory)
    if final_stage_present:
        return [_issue(directory.name, directory.name, None,
                       "recognized final-stage output directory has no authoritative summary")]
    if summaries:
        return [_issue(directory.name, directory.name, None, "recognized summaries did not yield final AIRR sources")]

    grouped: defaultdict[tuple[str, str | None], list[dict[str, str]]] = defaultdict(list)
    sample_labels: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    for path in _walk_tables(directory):
        if path.is_symlink():
            continue
        sample_key, sample_label, chain, combined = _filename_identity(path, directory)
        sample_labels.setdefault(sample_key, sample_label)
        # Ignore clearly non-AIRR CSV/TSV files; report partially AIRR-like
        # tables so a typo in one required field remains visible to the user.
        try:
            columns = _raw_header(path)
            airr_like = bool(columns.intersection(REQUIRED_COLUMNS | JUNCTION_COLUMNS | {"sequence_id", "sequence", "junction", "cdr3", "locus"}))
            error = "" if REQUIRED_COLUMNS <= columns and columns.intersection(JUNCTION_COLUMNS) else "missing required AIRR columns"
        except (OSError, EOFError, UnicodeError, csv.Error, ValueError) as exc:
            error = str(exc)
            airr_like = False
        if not airr_like:
            continue
        if combined:
            error = "combined receptor file has no per-chain summary; provide a manifest or chain-named files"
        elif not chain:
            error = "filename does not identify a sample and supported chain"
        checked, path_error = _checked_source(directory, path)
        error = error or path_error
        if checked and not error:
            grouped[(sample_key, chain)].append(_source(directory, checked, chain))
        else:
            diagnostics.append(_issue(sample_key, sample_label, chain, error or "invalid AIRR source"))
    entries = [
        _entry(sample_key, sample_labels.get(sample_key, sample_key), chain, sources)
        for (sample_key, chain), sources in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or ""))
    ]
    entries.extend(diagnostics)
    return sorted(entries, key=lambda item: (item["sample_key"], item["chain"] or "", item["id"]))
