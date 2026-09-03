#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a file-to-sample/barcode mapping for IR FASTQ splitting.

Matching priority for every FASTQ file is:

1. delimiter-bounded ``Sample ID File new name`` in the filename/path;
2. a unique ``Dual Index`` in the filename/path;
3. ``Dual Index`` candidates disambiguated by the workbook ``Note`` storage
   address.  A tie or a missing match is recorded as ERROR rather than guessed.

The generated CSV is the auditable contract consumed by
``IR_split/split_barcode.py --mapping-summary``.
"""

from __future__ import annotations

import csv
import os
import posixpath
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools.pipeline_config import load_config
os.environ.setdefault("SCIGBLAST_CONFIG", str(Path(__file__).resolve().parent / "00.pipeline_config.env"))
load_config()

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# =========================
# Edit these global parameters before running.
# =========================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.environ.get("SCIGBLAST_OUTPUT_ROOT", "") or str(PROJECT_ROOT / "output"))
SUBMISSION_VALUE = os.environ.get("SCIGBLAST_SUBMISSION_XLSX", "/colddata/zqy/SCigblast/results_260817_HZJ1/data/HZJ-移植/Data_submission_For_HZJ剩余.xlsx")
SUBMISSION_PATHS_VALUE = os.environ.get("SCIGBLAST_SUBMISSION_PATHS", "")
SUBMISSION_XLSX = Path(SUBMISSION_VALUE)
BARCODE_CSV = Path(os.environ.get("SCIGBLAST_BARCODE_CSV", "/colddata/zqy/SCigblast/results_260817_HZJ1/reference/8bp_barcodes.csv"))
INPUT_DIR = Path(os.environ.get("SCIGBLAST_MATCH_INPUT", os.environ.get("SCIGBLAST_RAW_INPUT_DIR", "/colddata/zqy/XYFY_HZJ1")))
OUTPUT_SUMMARY = Path(os.environ.get(
    "SCIGBLAST_MATCH_OUTPUT",
    str(OUTPUT_ROOT / "01.match" / "sample_barcode_summary.csv"),
))
SHEET: str | None = None
SAMPLE_ID_COLUMN = "Sample ID File new name"
BARCODE_COLUMN = "Barcode（引物条码）候选"
DUAL_INDEX_COLUMN = "Dual Index"
NOTE_COLUMN = "Note"
CHAIN_COLUMN = "Chain"
SPECIES_COLUMN = "Species"
MATCH_PROGRESS_EVERY = max(1, int(os.environ.get("SCIGBLAST_MATCH_PROGRESS_EVERY", "500")))
TCR_CHAIN_SET = ("TRA", "TRB", "TRD", "TRG")
BCR_CHAIN_SET = ("IGH", "IGK", "IGL")
IGH_ISOTYPES = ("IGHD", "IGHA", "IGHG", "IGHM", "IGHE")


@dataclass(frozen=True)
class SubmissionRecord:
    sample_id: str
    species: str
    barcode_candidate: str
    barcode_name: str
    barcode_sequence: str
    chain: str
    igblast_chain: str
    chain_expanded: str


@dataclass(frozen=True)
class MetadataRecord:
    sample: SubmissionRecord
    dual_index: str
    note: str


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\ufeff", "")
    return re.sub(r"\s+", "", text).casefold()


def display_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalize_chain(value: object) -> tuple[str, str, str]:
    """Return raw Chain, expanded audit tokens, and IgBLAST DB chains."""
    raw = display_text(value)
    compact = raw.upper().replace("＊", "*")
    tokens = [token for token in re.split(r"[+,;/|\s]+", compact) if token]
    expanded: list[str] = []
    database: list[str] = []

    def add(target: list[str], item: str) -> None:
        if item not in target:
            target.append(item)

    for token in tokens:
        if token in {"7C", "BOTH"}:
            for item in TCR_CHAIN_SET + BCR_CHAIN_SET:
                add(expanded, item)
            for item in BCR_CHAIN_SET:
                add(database, item)
            for item in TCR_CHAIN_SET:
                add(database, item)
        elif token in {"T", "TCR"}:
            for item in TCR_CHAIN_SET:
                add(expanded, item); add(database, item)
        elif token in {"B", "BCR"}:
            for item in BCR_CHAIN_SET:
                add(expanded, item); add(database, item)
        elif token in TCR_CHAIN_SET or token in {"IGK", "IGL"}:
            add(expanded, token); add(database, token)
        elif token in {"IGH", "IGH*"} or token in IGH_ISOTYPES:
            if token == "IGH*":
                for item in IGH_ISOTYPES:
                    add(expanded, item)
            else:
                add(expanded, token)
            add(database, "IGH")
        else:
            raise ValueError(f"Unknown Chain value: {raw!r} (token={token!r})")
    if not database:
        raise ValueError(f"Chain is empty: {raw!r}")
    return raw, ",".join(expanded), ",".join(database)


def normalize_barcode_name(value: object) -> str:
    text = display_text(value).upper().replace(" ", "")
    match = re.fullmatch(r"BC[_-]?(\d+)", text) or re.fullmatch(r"(\d+)", text)
    if not match:
        raise ValueError(f"Invalid primer barcode candidate: {text!r}")
    return str(int(match.group(1)))


def _xml_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{NS_MAIN}}}t"))


def _column_number(cell_ref: str) -> int:
    match = re.match(r"[A-Za-z]+", cell_ref)
    if not match:
        raise ValueError(f"Invalid spreadsheet cell reference: {cell_ref}")
    number = 0
    for char in match.group(0).upper():
        number = number * 26 + ord(char) - ord("A") + 1
    return number - 1


def _worksheet_path(archive: zipfile.ZipFile, sheet_name: str | None) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"]
               for rel in rels.findall(f"{{{NS_REL}}}Relationship")}
    sheets = workbook.find(f"{{{NS_MAIN}}}sheets")
    if sheets is None:
        raise ValueError("Workbook contains no sheets")
    chosen = next((sheet for sheet in sheets
                   if sheet_name is None or sheet.attrib.get("name") == sheet_name), None)
    if chosen is None:
        raise ValueError(f"Worksheet not found: {sheet_name}")
    rel_id = f"{{{NS_DOC_REL}}}id"
    return posixpath.normpath(posixpath.join("xl", rel_map[chosen.attrib[rel_id]])).lstrip("/")


def read_xlsx_rows(path: Path, sheet_name: str | None) -> list[list[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Submission workbook not found: {path}")
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_xml_text(si) for si in root.findall(f"{{{NS_MAIN}}}si")]
        sheet = ET.fromstring(archive.read(_worksheet_path(archive, sheet_name)))
        sheet_data = sheet.find(f"{{{NS_MAIN}}}sheetData")
        if sheet_data is None:
            return []
        rows: list[list[str]] = []
        for row in sheet_data:
            values: dict[int, str] = {}
            max_col = -1
            for cell in row.findall(f"{{{NS_MAIN}}}c"):
                col = _column_number(cell.attrib.get("r", "A1"))
                max_col = max(max_col, col)
                kind = cell.attrib.get("t")
                if kind == "inlineStr":
                    value = _xml_text(cell)
                else:
                    node = cell.find(f"{{{NS_MAIN}}}v")
                    value = node.text if node is not None else ""
                    if kind == "s" and value:
                        value = shared[int(value)]
                values[col] = value
            rows.append([values.get(i, "") for i in range(max_col + 1)])
        return rows


def load_barcode_reference(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Barcode CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = {normalize_text(field): field for field in reader.fieldnames or []}
        name_field = fields.get("name")
        seq_field = fields.get("seq")
        if not name_field or not seq_field:
            raise ValueError("Barcode CSV must contain name and seq columns")
        reference: dict[str, str] = {}
        for row in reader:
            raw_name = display_text(row.get(name_field, ""))
            raw_seq = display_text(row.get(seq_field, "")).upper()
            if not raw_name and not raw_seq:
                continue
            name = str(int(float(raw_name)))
            if not re.fullmatch(r"[ACGT]{8}", raw_seq):
                raise ValueError(f"Barcode {name} must be exactly 8 A/C/G/T bases")
            if name in reference and reference[name] != raw_seq:
                raise ValueError(f"Conflicting barcode reference for {name}")
            reference[name] = raw_seq
    if not reference:
        raise ValueError("Barcode CSV contains no records")
    return reference


def _config_values(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    values = tuple(item.strip() for item in raw.splitlines() if item.strip())
    return values or defaults


def _validated_fastq_extensions() -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for item in _config_values("SCIGBLAST_RAW_FASTQ_EXTENSIONS", (".fq.gz", ".fastq.gz", ".fq", ".fastq")):
        ext = item.strip()
        if not ext.startswith("."):
            ext = "." + ext
        if "/" in ext or "\\" in ext or ext == ".":
            raise ValueError(f"invalid FASTQ extension: {item!r}")
        if ext.casefold() not in seen:
            seen.add(ext.casefold()); values.append(ext)
    if not values:
        raise ValueError("SCIGBLAST_RAW_FASTQ_EXTENSIONS contains no extensions")
    return tuple(sorted(values, key=len, reverse=True))


FASTQ_SUFFIXES = _validated_fastq_extensions()
def _validated_suffix_pairs() -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in _config_values("SCIGBLAST_RAW_READ_SUFFIX_PAIRS", ("_R1|_R2", "_1|_2", "_j|_v")):
        parts = item.split("|", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid read suffix pair {item!r}; expected R1|R2")
        left, right = (part.strip() for part in parts)
        if not left or not right or left.casefold() == right.casefold():
            raise ValueError(f"invalid read suffix pair {item!r}")
        for suffix in (left, right):
            if "/" in suffix or "\\" in suffix or suffix.casefold().endswith((".fq", ".fastq", ".fq.gz", ".fastq.gz")):
                raise ValueError(f"read suffix must not contain a path or FASTQ extension: {suffix!r}")
            if suffix.casefold() in seen:
                raise ValueError(f"duplicate read suffix: {suffix!r}")
            seen.add(suffix.casefold())
        pairs.append((left, right))
    if not pairs:
        raise ValueError("SCIGBLAST_RAW_READ_SUFFIX_PAIRS contains no valid R1|R2 pairs")
    return tuple(pairs)


RAW_SUFFIX_PAIRS = _validated_suffix_pairs()
BATCH_DIR_RE = re.compile(r"(?i)^(?:lane\d+|20\d{6}(?:_[a-z0-9]+)?)$")


def fastq_suffix(path: Path) -> str | None:
    lower = path.name.lower()
    return next((suffix for suffix in FASTQ_SUFFIXES if lower.endswith(suffix)), None)


def strip_fastq_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in FASTQ_SUFFIXES:
        if lower.endswith(suffix):
            return name[:-len(suffix)]
    return name


def split_read_name(path: Path) -> tuple[str, str] | None:
    stem = strip_fastq_suffix(path.name)
    for r1_suffix, r2_suffix in RAW_SUFFIX_PAIRS:
        for suffix, read in ((r1_suffix, "1"), (r2_suffix, "2")):
            pattern = re.compile(
                rf"^(?P<pair>.+){re.escape(suffix)}(?P<lane>[._-]\d+)?$",
                flags=re.IGNORECASE,
            )
            match = pattern.match(stem)
            if match:
                lane = match.group("lane") or ""
                return match.group("pair") + lane, read
    return None


def raw_batch_note(path: Path, input_dir: Path) -> str:
    """Return the batch directory represented by a raw FASTQ path."""
    current = path.parent
    input_root = input_dir.resolve()
    while True:
        if BATCH_DIR_RE.fullmatch(current.name):
            return str(current)
        if current.resolve() == input_root or current.parent == current:
            break
        current = current.parent
    parent_prefix = path.parent.name.split("_", 1)[0]
    if "_" in path.parent.name and parent_prefix and not parent_prefix.isascii():
        return str(path.parent.parent)
    return str(path.parent)


def resolve_optional_column(headers: list[str], requested: str, kind: str) -> int | None:
    normalized = [normalize_text(value) for value in headers]
    target = normalize_text(requested)
    if target in normalized:
        return normalized.index(target)
    if kind == "dual":
        candidates = [i for i, value in enumerate(normalized) if "dual" in value and "index" in value]
    elif kind == "chain":
        candidates = [i for i, value in enumerate(normalized) if value == "chain" or value.endswith("chain")]
    elif kind == "species":
        candidates = [i for i, value in enumerate(normalized) if value in {"species", "speicies"}]
    else:
        candidates = [i for i, value in enumerate(normalized)
                      if "note" in value or "储存地址" in value or "storage" in value]
    return candidates[0] if len(candidates) == 1 else None


def load_metadata(path: Path, sheet: str | None, sample_column: str, barcode_column: str,
                  dual_column: str, note_column: str, chain_column: str,
                  species_column: str,
                  reference: dict[str, str]) -> list[MetadataRecord]:
    rows = read_xlsx_rows(path, sheet)
    sample_target = normalize_text(sample_column)
    header_index = None
    sample_idx = barcode_idx = None
    for index, row in enumerate(rows):
        normalized = [normalize_text(value) for value in row]
        if sample_target in normalized:
            header_index = index
            sample_idx = normalized.index(sample_target)
            barcode_target = normalize_text(barcode_column)
            if barcode_target in normalized:
                barcode_idx = normalized.index(barcode_target)
            else:
                candidates = [i for i, value in enumerate(normalized) if "barcode" in value and ("候选" in value or "引物" in value or "primer" in value)]
                barcode_idx = candidates[0] if len(candidates) == 1 else None
            break
    if header_index is None or sample_idx is None or barcode_idx is None:
        raise ValueError(f"Could not resolve sample/barcode columns in {path}")
    headers = rows[header_index]
    dual_idx = resolve_optional_column(headers, dual_column, "dual")
    note_idx = resolve_optional_column(headers, note_column, "note")
    chain_idx = resolve_optional_column(headers, chain_column, "chain")
    species_idx = resolve_optional_column(headers, species_column, "species")
    if chain_idx is None:
        raise ValueError(f"Could not resolve Chain column in {path}")
    records: list[MetadataRecord] = []
    seen: dict[str, MetadataRecord] = {}
    # Excel stores vertically merged Note cells only in their top row; carry
    # that batch path through subsequent sample rows until the next Note.
    current_note = ""
    for row in rows[header_index + 1:]:
        row_note = display_text(row[note_idx]) if note_idx is not None and note_idx < len(row) else ""
        if row_note:
            current_note = row_note
        sample_id = display_text(row[sample_idx] if sample_idx < len(row) else "")
        candidate = display_text(row[barcode_idx] if barcode_idx < len(row) else "")
        if not sample_id or normalize_text(sample_id) == sample_target:
            continue
        barcode_name = normalize_barcode_name(candidate)
        if barcode_name not in reference:
            raise ValueError(f"Barcode {candidate!r} ({barcode_name}) absent from reference")
        chain_raw = display_text(row[chain_idx] if chain_idx < len(row) else "")
        chain_value, chain_expanded, igblast_chain = normalize_chain(chain_raw)
        record = MetadataRecord(
            sample=SubmissionRecord(
                                    sample_id,
                                    display_text(row[species_idx]) if species_idx is not None and species_idx < len(row) else "",
                                    candidate, barcode_name, reference[barcode_name],
                                    chain_value, igblast_chain, chain_expanded),
            dual_index=display_text(row[dual_idx]) if dual_idx is not None and dual_idx < len(row) else "",
            note=current_note,
        )
        key = "|".join((normalize_text(sample_id), barcode_name, igblast_chain))
        if key in seen and seen[key] != record:
            raise ValueError(f"Conflicting duplicate Sample ID: {sample_id}")
        if key not in seen:
            seen[key] = record
            records.append(record)
    if not records:
        raise ValueError("No usable submission records found")
    return records


def load_metadata_collection(path: Path | list[Path], sheet: str | None, sample_column: str,
                             barcode_column: str, dual_column: str, note_column: str,
                             chain_column: str, reference: dict[str, str],
                             species_column: str = SPECIES_COLUMN) -> list[MetadataRecord]:
    """Merge all .xlsx files in a submission directory before Note matching."""
    configured = path if isinstance(path, list) else [path]
    paths: list[Path] = []
    for item in configured:
        if item.is_file(): paths.append(item)
        elif item.is_dir(): paths.extend(sorted(p for p in item.rglob("*") if p.is_file() and p.suffix.casefold() == ".xlsx"))
        else: raise FileNotFoundError(f"Submission workbook/directory not found: {item}")
    paths = list(dict.fromkeys(paths))
    if not paths: raise FileNotFoundError("No .xlsx submission workbooks found")
    merged: dict[tuple[str, ...], MetadataRecord] = {}
    for workbook in paths:
        for record in load_metadata(workbook, sheet, sample_column, barcode_column,
                                    dual_column, note_column, chain_column, species_column, reference):
            key = (normalize_text(record.sample.sample_id), record.sample.barcode_name,
                   record.sample.igblast_chain, normalize_text(record.dual_index),
                   normalize_text(record.note))
            previous = merged.get(key)
            if previous is not None and previous != record:
                raise ValueError(f"Conflicting duplicate Sample ID across workbooks: {record.sample.sample_id}")
            merged[key] = record
    if not merged:
        raise ValueError(f"No usable submission records found under {path}")
    return list(merged.values())


def file_labels(path: Path, input_dir: Path) -> str:
    try:
        relative = path.relative_to(input_dir)
    except ValueError:
        relative = path
    return f"{path} {path.name} {relative}".replace("\\", "/")


def bounded_match(value: str, label: str) -> bool:
    if not value:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])"
    return re.search(pattern, label, flags=re.IGNORECASE) is not None


def location_score(note: str, label: str) -> int:
    if not note:
        return 0
    note_norm = re.sub(r"/+", "/", note.replace("\\", "/")).strip("/").casefold()
    label_norm = re.sub(r"/+", "/", label.replace("\\", "/")).casefold()
    # Compare path components, not raw substrings.  Otherwise a note such as
    # ``.../20240829`` incorrectly matches ``.../20240829_A``.
    note_parts = [part for part in note_norm.split("/") if part]
    label_parts = [part for part in label_norm.split("/") if part]
    if note_parts and len(note_parts) <= len(label_parts):
        width = len(note_parts)
        for index in range(len(label_parts) - width + 1):
            if label_parts[index:index + width] == note_parts:
                return 10000 + width
    # Storage roots can differ between the workbook and rawdata (for example
    # /data2/... versus /colddata/...). Date and Lane tokens remain stable.
    date_hits = set(re.findall(r"20\d{6}", note_norm)) & set(re.findall(r"20\d{6}", label_norm))
    lane_hits = set(re.findall(r"lane\d+", note_norm)) & set(re.findall(r"lane\d+", label_norm))
    exact_part_hits = len(set(note_parts) & set(label_parts))
    score = 100 * len(date_hits) + 50 * len(lane_hits) + 10 * exact_part_hits
    tokens = [token for token in re.split(r"[^a-z0-9]+", note_norm) if len(token) >= 4]
    hits = sum(1 for token in set(tokens) if token in label_norm)
    return score + hits if score or hits >= 2 else 0


def note_path_matches(note: str, label: str) -> bool:
    """Return whether a raw path is inside one of the Note path entries.

    Note is a hard batch boundary.  Do not use date/lane/token similarity here:
    that can accidentally admit a Dual Index from another Note (for example
    ``20240829`` versus ``20240829_A``).
    """
    label_norm = re.sub(r"/+$", "", label.replace("\\", "/")).casefold()
    label_parts = [part for part in label_norm.split("/") if part]
    for entry in re.split(r"[\r\n;,]+", note or ""):
        entry_norm = re.sub(r"/+", "/", entry.strip().strip('"').replace("\\", "/")).strip("/").casefold()
        if not entry_norm:
            continue
        note_parts = [part for part in entry_norm.split("/") if part]
        width = len(note_parts)
        if width and width <= len(label_parts):
            for index in range(len(label_parts) - width + 1):
                if label_parts[index:index + width] == note_parts:
                    return True
    return False


def match_file(path: Path, input_dir: Path, records: list[MetadataRecord]) -> tuple[list[MetadataRecord], str, str]:
    label = file_labels(path, input_dir)
    records_with_notes = [record for record in records if record.note]
    if records_with_notes:
        note_candidates = [record for record in records_with_notes if note_path_matches(record.note, label)]
        if not note_candidates:
            return [], "error", f"no submission Note matches rawdata batch path in {path.name}"
        records_in_batch = note_candidates
    else:
        records_in_batch = records

    # Note is a hard batch boundary for both matching methods.  This prevents
    # a repeated sample ID or Dual Index in another submission batch from
    # leaking into the current rawdata directory.
    sample_matches = [record for record in records_in_batch if bounded_match(record.sample.sample_id, label)]
    if sample_matches:
        method = "sample_id" if len(sample_matches) == 1 else "sample_id_multi"
        return sample_matches, method, ""

    dual_candidates = [record for record in records_in_batch
                       if record.dual_index and bounded_match(record.dual_index, label)]
    if not dual_candidates:
        reason = f"no Dual Index match within the Note batch for {path.name}"
        return [], "error", reason
    if len(dual_candidates) == 1:
        candidate = dual_candidates[0]
        return [candidate], "dual_index", ""
    # Note has already been applied as a hard batch boundary above.  If the
    # same Dual Index is intentionally shared by several samples in that
    # batch, preserve every candidate so the 10X splitter can emit one
    # barcode/sample leaf per row.  Do not score against other Notes here:
    # doing so would re-introduce cross-batch leakage (for example
    # 20240829 versus 20240829_A).
    return dual_candidates, "dual_index_multi", ""


def build_rows(input_dir: Path, records: list[MetadataRecord]) -> list[dict[str, str]]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"FASTQ input directory not found: {input_dir}")
    print(f"[match] scanning FASTQ files under {input_dir}", file=sys.stderr, flush=True)
    pairs: dict[str, dict[str, Path]] = {}
    parse_errors: list[dict[str, str]] = []
    scanned = 0
    # Do not sort the complete raw tree before processing.  On large storage
    # mounts that makes the command appear frozen and holds every Path in RAM.
    # Summary grouping remains deterministic by Note, while file row order is
    # not part of the downstream contract.
    for path in input_dir.rglob("*"):
        if not path.is_file() or fastq_suffix(path) is None:
            continue
        scanned += 1
        if scanned == 1 or scanned % MATCH_PROGRESS_EVERY == 0:
            print(f"[match] scanned={scanned} pairs={len(pairs)}", file=sys.stderr, flush=True)
        read_info = split_read_name(path)
        if read_info is None:
            parse_errors.append({
                "note": raw_batch_note(path, input_dir),
                "pair_id": "",
                "sample_id": "",
                "species": "",
                "chain_raw": "",
                "igblast_chains": "",
                "barcode_candidate": "",
                "barcode_name": "",
                "barcode_sequence": "",
                "dual_index": "",
                "match_method": "error",
                "status": "ERROR",
                "error": f"Cannot parse R1/R2 suffix from FASTQ filename: {path.name}",
                "r1_path": "",
                "r2_path": "",
            })
            continue
        pair_stem, read = read_info
        relative_parent = path.parent.relative_to(input_dir)
        pair_id = (relative_parent / pair_stem).as_posix()
        slot = pairs.setdefault(pair_id, {})
        if read in slot and slot[read] != path:
            parse_errors.append({
                "note": raw_batch_note(path, input_dir), "pair_id": pair_id,
                "sample_id": "", "species": "", "chain_raw": "", "igblast_chains": "",
                "barcode_candidate": "", "barcode_name": "", "barcode_sequence": "",
                "dual_index": "", "match_method": "error", "status": "ERROR",
                "error": f"duplicate R{read} files for pair_id {pair_id}",
                "r1_path": str(path) if read == "1" else "",
                "r2_path": str(path) if read == "2" else "",
            })
            continue
        slot[read] = path

    rows = parse_errors
    for pair_id, sides in sorted(pairs.items()):
        r1, r2 = sides.get("1"), sides.get("2")
        representative = r1 or r2
        assert representative is not None
        batch_note = raw_batch_note(representative, input_dir)
        if r1 is None or r2 is None:
            missing = "R1" if r1 is None else "R2"
            rows.append({
                "note": batch_note, "pair_id": pair_id,
                "sample_id": "", "species": "", "chain_raw": "", "igblast_chains": "",
                "barcode_candidate": "", "barcode_name": "", "barcode_sequence": "",
                "dual_index": "", "match_method": "error", "status": "ERROR",
                "error": f"missing {missing} pair",
                "r1_path": str(r1 or ""), "r2_path": str(r2 or ""),
            })
            continue
        matched_records, method, error = match_file(r1, input_dir, records)
        if not matched_records:
            matched_records = [None]
        for record in matched_records:
            row = {
                "note": batch_note,
                "pair_id": pair_id,
                "sample_id": "",
                "species": "",
                "chain_raw": "",
                "igblast_chains": "",
                "barcode_candidate": "",
                "barcode_name": "",
                "barcode_sequence": "",
                "dual_index": "",
                "match_method": method,
                "status": "OK" if record else "ERROR",
                "error": error,
                "r1_path": str(r1),
                "r2_path": str(r2),
            }
            if record:
                row.update({
                    "note": record.note or batch_note,
                    "sample_id": record.sample.sample_id,
                    "species": record.sample.species,
                    "chain_raw": record.sample.chain,
                    "igblast_chains": record.sample.igblast_chain,
                    "barcode_candidate": record.sample.barcode_candidate,
                    "barcode_name": record.sample.barcode_name,
                    "barcode_sequence": record.sample.barcode_sequence,
                    "dual_index": record.dual_index,
                })
            rows.append(row)
    print(f"[match] scan complete: FASTQ={scanned}, pairs={len(pairs)}, mapping rows={len(rows)}", file=sys.stderr, flush=True)
    return rows


def write_summary(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["note", "pair_id", "sample_id", "species", "chain_raw", "igblast_chains",
              "barcode_candidate", "barcode_name", "barcode_sequence", "dual_index",
              "match_method", "status", "error", "r1_path", "r2_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            note = display_text(row.get("note", ""))
            grouped.setdefault(note, []).append(row)
        for group_rows in grouped.values():
            for row in group_rows:
                output_row = dict(row)
                # Repeat Note on every row so the mapping is auditable even
                # when a CSV row is viewed independently of its group header.
                output_row["note"] = display_text(row.get("note", ""))
                writer.writerow(output_row)


def main() -> int:
    submission_paths = [Path(item) for item in SUBMISSION_PATHS_VALUE.splitlines() if item.strip()] or [SUBMISSION_XLSX]
    print(f"[match] workbooks={len(submission_paths)}", file=sys.stderr, flush=True)
    print(f"[match] barcode reference={BARCODE_CSV}", file=sys.stderr, flush=True)
    reference = load_barcode_reference(BARCODE_CSV)
    records = load_metadata_collection(submission_paths, SHEET, SAMPLE_ID_COLUMN,
                                       "Barcode（引物条码）候选", "Dual Index", "Note", "Chain", reference)
    rows = build_rows(INPUT_DIR, records)
    if not rows:
        raise SystemExit(f"No paired FASTQ-named files found under {INPUT_DIR}")
    write_summary(OUTPUT_SUMMARY, rows)
    errors = sum(row["status"] == "ERROR" for row in rows)
    methods = ", ".join(f"{method}={sum(row['match_method'] == method for row in rows)}"
                        for method in ("sample_id", "sample_id_multi", "dual_index", "dual_index_multi", "dual_index+note", "error"))
    print(f"Wrote {len(rows)} mapping rows ({methods}) to {OUTPUT_SUMMARY}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
