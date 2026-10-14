"""Metadata-only IgBLAST dependency snapshots shared by Base and Pig.

Collect once just before the stage, then reuse its compact snapshot in workers.
Database contents are not hashed: reference publishers must change file metadata
or SCIGBLAST_IGBLAST_REFERENCE_VERSION when replacing data in place.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil


FILTER_VERSION = "productive-and-valid-v-1"
SCHEMA_VERSION = 2
SUPPORTED = {"TRA", "TRB", "TRD", "TRG", "IGH", "IGK", "IGL"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def metadata(path: Path) -> list:
    path = path.resolve()
    try:
        stat = path.stat()
        return [str(path), stat.st_size, stat.st_mtime_ns]
    except FileNotFoundError:
        return [str(path), None, None]


def resolve_binary(binary: str, db_root: Path, mode: str) -> str:
    resolved = shutil.which(binary)
    if not resolved and mode == "base":
        candidate = db_root / "bin/igblastn"
        resolved = str(candidate) if candidate.is_file() else None
    return str(Path(resolved).resolve()) if resolved else binary


def selected_chains(manifest: Path) -> list[str]:
    with manifest.open(encoding="utf-8-sig", newline="") as handle:
        return sorted({chain for row in csv.DictReader(handle)
                       if row.get("status", "").upper() == "OK"
                       for chain in row.get("igblast_chains", "").split(",") if chain in SUPPORTED})


def dependency_snapshot(*, mode: str, db_root: Path, binary: str, species: str,
                        database_version: str, threads: int, chains: list[str],
                        implementation: Path, inherited: str = "") -> dict:
    context = {
        "schema": SCHEMA_VERSION, "mode": mode, "db_root": str(db_root.resolve()),
        "binary": resolve_binary(binary, db_root, mode), "species": species,
        "database_version": database_version, "threads": threads,
        "chains": sorted(set(chains)), "filter": FILTER_VERSION,
        "reference_version": os.environ.get("SCIGBLAST_IGBLAST_REFERENCE_VERSION", ""),
        "implementation": hashlib.sha256(implementation.read_bytes()).hexdigest(),
        "helper": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "igdata": str(Path(os.environ.get("IGDATA") or db_root).resolve()),
    }
    if inherited:
        cached = json.loads(inherited)
        if cached.get("context") == context:
            return cached
    aux = db_root / "optional_file" / ("pig.aux" if mode == "pig" else f"{species}_gl.aux")
    internal = Path(context["igdata"]) / "internal_data" / species
    common = {
        **{key: value for key, value in context.items() if key != "chains"},
        "tool": metadata(Path(context["binary"])), "aux": metadata(aux),
        "internal": [metadata(path) for path in sorted(internal.rglob("*")) if path.is_file()],
    }
    signatures = {}
    for chain in context["chains"]:
        parts = {}
        for part in (("V", "D", "J") if mode == "pig" else ("V", "D", "J", "C")):
            prefix = db_root / "database_251117" / database_version / chain / f"{species}_gl_{chain}_{part}"
            parts[part] = [metadata(path) for path in sorted(prefix.parent.glob(prefix.name + ".*")) if path.is_file()]
        signatures[chain] = digest({"common": common, "chain": chain, "databases": parts})
    return {"context": context, "chains": signatures, "fingerprint": digest(signatures)}


def task_fingerprint(source: Path, chain_signature: str) -> str:
    return digest({"input": metadata(source), "dependencies": chain_signature})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("base", "pig"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--db-root", type=Path, required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--species", required=True)
    parser.add_argument("--database-version", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--implementation", type=Path, required=True)
    args = vars(parser.parse_args())
    manifest = args.pop("manifest")
    print(json.dumps(dependency_snapshot(**args, chains=selected_chains(manifest)), separators=(",", ":")))


if __name__ == "__main__":
    main()
