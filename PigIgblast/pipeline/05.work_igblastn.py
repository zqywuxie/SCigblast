#!/usr/bin/env python3
"""Pig IgBLAST: filter and merge results inside each sample only."""
from __future__ import annotations
import csv, hashlib, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tools.pipeline_config import load_config

def safe(value): return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value))

cfg = load_config()
OUTPUT_ROOT = Path(cfg.get("SCIGBLAST_OUTPUT_ROOT") or cfg.get("OUTPUT_ROOT") or str(HERE.parent / "output"))
DATASET_LABEL = os.environ.get("SCIGBLAST_DATASET_LABEL", "").strip()
if not DATASET_LABEL and cfg.get("RAW_INPUT_DIR"):
    DATASET_LABEL = safe(cfg["RAW_INPUT_DIR"].rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
def stage_root(stage: str) -> Path:
    return OUTPUT_ROOT / stage / DATASET_LABEL if DATASET_LABEL else OUTPUT_ROOT / stage
MANIFEST = stage_root("01.match") / "sample_manifest.csv"
INPUT = stage_root("04.pandaseq")
OUTPUT = stage_root("05.igblastn_out")
SUMMARY = OUTPUT / "igblast_summary.tsv"
BIN = cfg.get("SCIGBLAST_IGBLAST_BIN", cfg.get("IGBLAST_BIN", "igblastn"))
DB_ROOT = Path(cfg.get("DB_ROOT", "")); VERSION = cfg.get("PIG_DB_VERSION", "pig_new")
THREADS = int(cfg.get("SCIGBLAST_IGBLAST_THREADS", cfg.get("IGBLAST_THREADS", "16")))
MAX_JOBS = max(1, int(cfg.get("SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS", cfg.get("MAX_JOBS", "1"))))
BAD_V = {"", "*", "-", "NA", "N/A", "NONE", "NULL", "NO_HIT", "UNMAPPED", "NOT_FOUND"}
TCR = {"TRA", "TRB", "TRD", "TRG"}
def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    finally:
        try: os.unlink(tmp_name)
        except FileNotFoundError: pass

RAW_INPUT_ROOT = Path(cfg.get("RAW_INPUT_DIR", "")) if cfg.get("RAW_INPUT_DIR") else None
def safe_parent(raw_path):
    try:
        rel = Path(raw_path).resolve().parent.relative_to(RAW_INPUT_ROOT.resolve()) if RAW_INPUT_ROOT else Path()
        return Path(*[safe(part) for part in rel.parts])
    except ValueError: return Path()
def db(chain, part): return DB_ROOT / "database_251117" / VERSION / chain / f"pig_gl_{chain}_{part}"
def db_ok(path): return any(path.parent.glob(path.name + ".*"))
def count_fasta(path):
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh: return sum(1 for line in fh if line.startswith(">"))
    except OSError: return 0
def valid_v(value): return str(value or "").strip().upper() not in BAD_V

def filter_tsv(source, target):
    header = None; raw = mapped = productive = output = 0
    with source.open(encoding="utf-8", errors="replace") as src, target.open("w", encoding="utf-8") as dst:
        for line in src:
            text = line.rstrip("\r\n")
            if not text or text.startswith("#"): continue
            fields = text.split("\t")
            if header is None:
                if "sequence_id" not in fields or "v_call" not in fields: continue
                header = fields; vi = header.index("v_call"); pi = header.index("productive") if "productive" in header else -1
                dst.write("\t".join(header) + "\n"); continue
            fields += [""] * max(0, len(header) - len(fields)); raw += 1
            good_v = valid_v(fields[vi]); mapped += int(good_v)
            good_p = pi >= 0 and fields[pi].strip().upper() in {"T", "TRUE"}; productive += int(good_p)
            if good_v and good_p: dst.write("\t".join(fields[:len(header)]) + "\n"); output += 1
    if header is None: raise ValueError(f"malformed IgBLAST TSV: {source}")
    return raw, mapped, productive, output

def task(job):
    sample, pair, chain, raw_r1 = job; parent = safe_parent(raw_r1)
    base = INPUT / parent / safe(pair) / safe(sample); fasta = base / (safe(pair) + "_merged.fasta")
    task_dir = OUTPUT / ".tasks" / parent / safe(pair) / safe(sample); task_dir.mkdir(parents=True, exist_ok=True)
    filtered = task_dir / f"{safe(chain)}.tsv"; raw = Path(str(filtered) + ".raw.tsv"); done = Path(str(filtered) + ".DONE")
    n = count_fasta(fasta)
    common = {"sample_id": sample, "pair_id": pair, "chain": chain, "input_sequences": n, "raw_rows": 0, "mapped_rows": 0, "productive_rows": 0, "output_rows": 0, "status": "ERROR", "error": "", "path": None, "parent": str(parent)}
    if not fasta.is_file(): common["error"] = "missing merged FASTA"; return common
    try:
        stat = fasta.stat()
        if done.is_file() and filtered.is_file():
            cached = {}
            for line in done.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                if sep: cached[key] = value
            if (cached.get("version") == "1" and cached.get("input_size") == str(stat.st_size)
                    and cached.get("input_mtime_ns") == str(stat.st_mtime_ns)):
                common.update(raw_rows=int(cached.get("raw_rows", 0)), mapped_rows=int(cached.get("mapped_rows", 0)),
                              productive_rows=int(cached.get("productive_rows", 0)), output_rows=int(cached.get("output_rows", 0)),
                              status="OK", path=filtered)
                return common
    except (OSError, ValueError):
        pass
    v, j = db(chain, "V"), db(chain, "J")
    if not db_ok(v) or not db_ok(j): common["error"] = "missing V/J database"; return common
    aux = DB_ROOT / "optional_file" / "pig.aux"; token = hashlib.sha1(f"{sample}|{pair}|{chain}".encode()).hexdigest()[:12]
    # IgBLAST's -ig_seqtype accepts only ``Ig`` or ``TCR``.  Keep the
    # pipeline's internal BCR/TCR labels, but pass the documented CLI value
    # for immunoglobulin chains.
    seqtype = "Ig" if chain.upper().startswith("IG") else "TCR"
    args = [BIN, "-germline_db_V", str(v), "-germline_db_J", str(j)]
    # Omitting D makes IgBLAST load its default pig_gl_D, even for TRA.
    # Use the configured chain-specific D prefix, including VJ placeholders.
    d = db(chain, "D")
    if not db_ok(d): common["error"] = f"missing {chain} D database"; return common
    args.extend(["-germline_db_D", str(d)])
    args.extend(["-auxiliary_data", str(aux), "-organism", "pig", "-ig_seqtype", seqtype, "-query", str(fasta), "-outfmt", "19", "-num_threads", str(THREADS)])
    with Path(str(filtered) + ".log").open("w", encoding="utf-8") as log: rc = subprocess.run(args + ["-out", str(raw)], stdout=log, stderr=subprocess.STDOUT).returncode
    if rc or not raw.is_file(): common["error"] = "igblast_failed"; raw.unlink(missing_ok=True); return common
    try:
        raw_n, mapped, productive, output = filter_tsv(raw, filtered); raw.unlink(missing_ok=True)
        common.update(raw_rows=raw_n, mapped_rows=mapped, productive_rows=productive, output_rows=output, status="OK", path=filtered)
        stat = fasta.stat()
        atomic_write_text(done, "\n".join(("version=1", f"input_size={stat.st_size}", f"input_mtime_ns={stat.st_mtime_ns}",
                                             f"raw_rows={raw_n}", f"mapped_rows={mapped}", f"productive_rows={productive}", f"output_rows={output}")) + "\n")
    except Exception as exc:
        common["error"] = str(exc); raw.unlink(missing_ok=True); filtered.unlink(missing_ok=True)
    return common

def main():
    if not MANIFEST.is_file(): raise SystemExit("missing sample_manifest.csv")
    if not shutil.which(BIN): raise SystemExit(f"igblastn not found: {BIN}")
    if not (DB_ROOT / "optional_file" / "pig.aux").is_file(): raise SystemExit("missing pig auxiliary file")
    jobs=[]; seen=set()
    with MANIFEST.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key=(row.get("sample_id", ""), row.get("pair_id", ""), row.get("r1_path", ""))
            if row.get("status") == "OK" and key not in seen:
                seen.add(key); jobs.extend((key[0], key[1], chain, key[2]) for chain in filter(None, row.get("igblast_chains", "").split(",")))
    if not jobs:
        raise SystemExit("no usable Pig samples/chains in sample_manifest.csv")
    OUTPUT.mkdir(parents=True, exist_ok=True); results=[]; print(f"[PIG][igblast] started total={len(jobs)} max_jobs={MAX_JOBS}", flush=True)
    # Remove legacy raw/aggregate products from earlier versions.  This stage
    # publishes only sample-local filtered TCR.tsv/BCR.tsv files.
    shutil.rmtree(OUTPUT / "raw", ignore_errors=True)
    for legacy in OUTPUT.glob("all_samples_*.tsv"): legacy.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=MAX_JOBS) as executor:
        futures=[executor.submit(task, job) for job in jobs]
        for idx, future in enumerate(as_completed(futures), 1):
            result=future.result(); results.append(result); print(f"[PIG][igblast] {result['status'].lower()} {idx}/{len(jobs)} {result['sample_id']}/{result['pair_id']}/{result['chain']}", flush=True)
    grouped={}
    for result in results:
        if result["status"] != "OK" or not result.get("path"): continue
        cell="TCR" if result["chain"] in TCR else "BCR"
        grouped.setdefault((result["parent"], safe(result["pair_id"]), safe(result["sample_id"]), cell), []).append(result["path"])
    for (parent, pair, sample, cell), paths in grouped.items():
        dest=OUTPUT / parent / pair / sample / f"{cell}.tsv"; dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        with dest.open("w", encoding="utf-8") as out:
            first=True
            for path in sorted(paths, key=lambda p: p.name):
                with path.open(encoding="utf-8", errors="replace") as src:
                    for line_no, line in enumerate(src):
                        if first or line_no > 0: out.write(line)
                first=False
    fields=["sample_id","pair_id","chain","input_sequences","raw_rows","mapped_rows","mapped_seqs","unmapped_seqs","productive_rows","productive_seqs","productive_percent","output_rows","filtered_seqs","mapping_percent","filtered_percent","retained_percent","status","error"]
    with SUMMARY.open("w", encoding="utf-8", newline="") as fh:
        writer=csv.DictWriter(fh, fieldnames=fields, delimiter="\t"); writer.writeheader()
        for result in sorted(results, key=lambda r:(r["sample_id"],r["pair_id"],r["chain"])):
            n=result["input_sequences"]; mapped=int(result["mapped_rows"]); productive=int(result["productive_rows"]); filtered=int(result["output_rows"])
            result.update(mapped_seqs=mapped, unmapped_seqs=max(n-mapped,0), productive_seqs=productive, productive_percent=f"{productive*100/n:.2f}" if n else "0.00", filtered_seqs=filtered, mapping_percent=f"{mapped*100/n:.2f}" if n else "0.00", filtered_percent=f"{filtered*100/n:.2f}" if n else "0.00")
            result["retained_percent"]=result["filtered_percent"]; writer.writerow({key:result.get(key,"") for key in fields})
    with (OUTPUT / "chain_summary.csv").open("w", encoding="utf-8", newline="") as fh:
        writer=csv.DictWriter(fh, fieldnames=fields); writer.writeheader()
        for result in sorted(results, key=lambda r:(r["sample_id"],r["pair_id"],r["chain"])):
            writer.writerow({key:result.get(key,"") for key in fields})
    failed=sum(r["status"]=="ERROR" for r in results)
    # Keep compact filtered task TSVs and DONE markers for sample-level resume;
    # raw IgBLAST TSVs are removed immediately after filtering.
    print(f"[PIG][igblast] completed={len(results)} failed={failed} summary={SUMMARY}", flush=True); return 1 if failed else 0

if __name__ == "__main__": raise SystemExit(main())
