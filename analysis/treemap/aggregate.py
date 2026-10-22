"""Aggregate AIRR rows into clonotypes for the QR treemap renderer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

CLONE_COLUMNS = ("V", "J", "CDR3(pep)", "copy")
READ_CHUNK_SIZE = 500_000
MERGE_EVERY_N_CHUNKS = 8
CHAIN_ALIASES = {
    "TRA": {"TRA", "TRAC", "TRAV", "TRAJ"},
    "TRB": {"TRB", "TRBC", "TRBV", "TRBD", "TRBJ"},
    "TRD": {"TRD", "TRDC", "TRDV", "TRDD", "TRDJ"},
    "TRG": {"TRG", "TRGC", "TRGV", "TRGJ"},
    "IGH": {"IGH", "IGHV", "IGHD", "IGHJ", "IGHC"},
    "IGK": {"IGK", "IGKV", "IGKJ", "IGKC"},
    "IGL": {"IGL", "IGLV", "IGLJ", "IGLC"},
}


def _source_path(source: dict[str, Any] | str | Path) -> Path:
    if isinstance(source, (str, Path)):
        return Path(source)
    raw_path = source.get("path")
    if not raw_path:
        raise ValueError("Each source must include a non-empty 'path'.")
    return Path(raw_path)


def _source_chain(source: dict[str, Any] | str | Path) -> str | None:
    if not isinstance(source, dict) or not source.get("chain"):
        return None
    return str(source["chain"]).strip().upper()


def _chain_mask(values: pd.Series, chain: str) -> pd.Series:
    """Match common AIRR locus spellings such as IGH and IGHV."""
    expected = CHAIN_ALIASES.get(chain, {chain})
    normalized = values.astype(str).str.strip().str.upper()
    return normalized.isin(expected)


def _aggregate_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=CLONE_COLUMNS)
    return (
        pd.concat(frames, ignore_index=True)
        .groupby(list(CLONE_COLUMNS[:3]), as_index=False, dropna=False)["copy"]
        .sum()
    )


def _read_source(path: Path, chain: str, known_chain: str | None) -> tuple[pd.DataFrame, int]:
    if known_chain is not None and known_chain != chain:
        return pd.DataFrame(columns=CLONE_COLUMNS), 0
    if not path.is_file():
        raise FileNotFoundError(f"Treemap source does not exist: {path}")

    sep = "," if path.name.lower().endswith((".csv", ".csv.gz")) else "\t"
    header = pd.read_csv(path, sep=sep, nrows=0, compression="infer")
    columns = set(header.columns)
    required = {"productive", "v_call", "j_call"}
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
    cdr3_col = "junction_aa" if "junction_aa" in columns else "cdr3_aa"
    if cdr3_col not in columns:
        raise ValueError(f"{path} has neither junction_aa nor cdr3_aa")
    if known_chain is None and "locus" not in columns:
        raise ValueError(f"{path} needs a source chain or a locus column")

    usecols = ["productive", "v_call", "j_call", cdr3_col]
    if known_chain is None and "locus" in columns:
        usecols.append("locus")
    reader = pd.read_csv(
        path,
        sep=sep,
        usecols=usecols,
        dtype=str,
        na_filter=False,
        compression="infer",
        chunksize=READ_CHUNK_SIZE,
    )
    pending: list[pd.DataFrame] = []
    compacted = pd.DataFrame(columns=CLONE_COLUMNS)
    productive_count = 0
    for chunk_index, chunk in enumerate(reader, 1):
        if "locus" in chunk.columns:
            chunk = chunk.loc[_chain_mask(chunk["locus"], chain)]
        chunk = chunk.loc[chunk["productive"] == "T"]
        if chunk.empty:
            continue
        chunk = chunk.loc[chunk[cdr3_col] != ""]
        productive_count += len(chunk)
        if chunk.empty:
            continue
        records = pd.DataFrame(
            {
                "V": chunk["v_call"].to_numpy(),
                "J": chunk["j_call"].to_numpy(),
                "CDR3(pep)": chunk[cdr3_col].to_numpy(),
                "copy": 1,
            }
        )
        pending.append(
            records.groupby(list(CLONE_COLUMNS[:3]), dropna=False)
            .size()
            .reset_index(name="copy")
        )
        if chunk_index % MERGE_EVERY_N_CHUNKS == 0:
            compacted = _aggregate_frames([compacted, *pending])
            pending.clear()

    clones = _aggregate_frames([compacted, *pending])
    return clones.loc[:, CLONE_COLUMNS], productive_count


def load_clones(
    sources: list[dict[str, Any] | str | Path], chain: str
) -> tuple[pd.DataFrame, int]:
    """Read chain-matched sources and count each productive record as one copy.

    A source may be a path or ``{"path": ..., "chain": ...}``. Sources with
    a known chain are skipped when it differs from ``chain``. Known single-chain
    sources retain the original script's row contract; combined sources require
    a locus column and are partitioned before aggregation.
    """
    chain = str(chain).strip().upper()
    if not chain:
        raise ValueError("chain must be a non-empty locus name")
    compacted = pd.DataFrame(columns=CLONE_COLUMNS)
    productive_count = 0
    for source in sources:
        clones, count = _read_source(_source_path(source), chain, _source_chain(source))
        productive_count += count
        if not clones.empty:
            compacted = _aggregate_frames([compacted, clones])
    clones = compacted
    return clones.loc[:, CLONE_COLUMNS], productive_count
