#!/usr/bin/env python3
"""Pig IgBLAST: filter and publish each sample/chain result independently."""
from __future__ import annotations
import csv, hashlib, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tools.pipeline_config import load_config
sys.path.insert(0, str(HERE.parents[1]))
from pipeline_input_manifest import pig_output_relative
from pipeline_igblast_cache import dependency_snapshot, resolve_binary, task_fingerprint
from pipeline_parallel import FastaCountCache, bounded_completed
from pipeline_locus_split import atomic_text, filter_mapping_airr

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
BIN = resolve_binary(BIN, DB_ROOT, "pig")
DEPENDENCIES = {}
FASTA_COUNTS = FastaCountCache()
THREADS = int(cfg.get("SCIGBLAST_IGBLAST_THREADS", cfg.get("IGBLAST_THREADS", "16")))
MAX_JOBS = max(1, min(4, int(cfg.get("SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS", cfg.get("MAX_JOBS", "4"))),
                      int(cfg.get('SCIGBLAST_PARALLEL_THREAD_BUDGET', '128')) // max(1, THREADS) or 1,
                      int(cfg.get('SCIGBLAST_MEMORY_BUDGET_GB', '300')) // max(1, int(cfg.get('SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB', '32'))) or 1))
TCR = {"TRA", "TRB", "TRD", "TRG"}
def atomic_write_text(path: Path, text: str) -> None:
    atomic_text(path, lambda handle: handle.write(text))

RAW_INPUT_ROOT = Path(cfg.get("RAW_INPUT_DIR", "")) if cfg.get("RAW_INPUT_DIR") else None
def safe_parent(raw_path):
    try:
        rel = Path(raw_path).resolve().parent.relative_to(RAW_INPUT_ROOT.resolve()) if RAW_INPUT_ROOT else Path()
        return Path(*[safe(part) for part in rel.parts])
    except ValueError: return Path()
def db(chain, part): return DB_ROOT / "database_251117" / VERSION / chain / f"pig_gl_{chain}_{part}"
def db_ok(path): return any(path.parent.glob(path.name + ".*"))
def count_fasta(path):
    return FASTA_COUNTS.count(path)
def task(job):
    sample, pair, chain, raw_r1 = job; parent = safe_parent(raw_r1)
    relative = pig_output_relative(sample,pair,raw_r1,RAW_INPUT_ROOT,os.environ.get('SCIGBLAST_INPUT_MANIFEST',''))
    base = INPUT / relative; fasta = base / (safe(pair) + "_merged.fasta")
    task_dir = OUTPUT / ".tasks" / relative; task_dir.mkdir(parents=True, exist_ok=True)
    filtered = OUTPUT / relative / f"{safe(chain)}.tsv"
    raw = task_dir / f"{safe(chain)}.tsv.raw.tsv"; done = task_dir / f"{safe(chain)}.tsv.DONE"
    n = count_fasta(fasta)
    common = {"sample_id": sample, "pair_id": pair, "chain": chain, "input_sequences": n, "raw_rows": 0, "mapped_rows": 0, "productive_rows": 0, "output_rows": 0, "status": "ERROR", "error": "", "path": None, "parent": str(parent), "relative": str(relative)}
    common.update(locus_input_rows=0, excluded_locus_rows=0, unknown_locus_rows=0, locus_filter_version=1)
    if not fasta.is_file(): common["error"] = "missing merged FASTA"; return common
    try:
        fingerprint = task_fingerprint(fasta, DEPENDENCIES["chains"][chain])
        if done.is_file() and filtered.is_file() and os.environ.get("SCIGBLAST_FORCE_RERUN") != "1":
            cached = {}
            for line in done.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                if sep: cached[key] = value
            if cached.get("version") == "2" and cached.get("job_fingerprint") == fingerprint:
                common.update(raw_rows=int(cached.get("raw_rows", 0)), mapped_rows=int(cached.get("mapped_rows", 0)),
                              productive_rows=int(cached.get("productive_rows", 0)), output_rows=int(cached.get("output_rows", 0)),
                              status="OK", path=filtered)
                for key in ('locus_input_rows', 'excluded_locus_rows', 'unknown_locus_rows'):
                    common[key] = int(cached.get(key, 0))
                return common
    except (OSError, ValueError):
        pass
    done.unlink(missing_ok=True)
    filtered.unlink(missing_ok=True)
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
    c = db(chain, "C")
    if db_ok(c):
        args.extend(["-c_region_db", str(c), "-num_alignments_C", "1"])
    args.extend(["-auxiliary_data", str(aux), "-organism", "pig", "-ig_seqtype", seqtype, "-query", str(fasta), "-outfmt", "19", "-num_threads", str(THREADS)])
    with (task_dir / f"{safe(chain)}.tsv.log").open("w", encoding="utf-8") as log: rc = subprocess.run(args + ["-out", str(raw)], stdout=log, stderr=subprocess.STDOUT).returncode
    if rc or not raw.is_file(): common["error"] = "igblast_failed"; raw.unlink(missing_ok=True); return common
    try:
        counts = filter_mapping_airr(raw, filtered, chain); raw.unlink(missing_ok=True)
        common.update(counts, status="OK", path=filtered)
        atomic_write_text(done, "\n".join(("version=2", f"job_fingerprint={fingerprint}", f"dependency_fingerprint={DEPENDENCIES['chains'][chain]}",
                                             *(f"{key}={value}" for key, value in counts.items()))) + "\n")
    except Exception as exc:
        common.update(status="ERROR", error=str(exc), path=None)
        raw.unlink(missing_ok=True); filtered.unlink(missing_ok=True)
        filtered.with_suffix(filtered.suffix+".tmp").unlink(missing_ok=True)
    return common

def main():
    global DEPENDENCIES, FASTA_COUNTS
    FASTA_COUNTS = FastaCountCache()
    if not MANIFEST.is_file(): raise SystemExit("missing sample_manifest.csv")
    jobs=[]; seen=set()
    with MANIFEST.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key=(row.get("sample_id", ""), row.get("pair_id", ""), row.get("r1_path", ""))
            if row.get("status") == "OK" and key not in seen:
                seen.add(key); jobs.extend((key[0], key[1], chain, key[2]) for chain in filter(None, row.get("igblast_chains", "").split(",")))
    if not jobs:
        raise SystemExit("no usable Pig samples/chains in sample_manifest.csv")
    OUTPUT.mkdir(parents=True, exist_ok=True); results=[]; print(f"[PIG][igblast] started total={len(jobs)} max_jobs={MAX_JOBS}", flush=True)
    for sample,pair,chain,raw_r1 in jobs:
        (OUTPUT/pig_output_relative(sample,pair,raw_r1,RAW_INPUT_ROOT,os.environ.get("SCIGBLAST_INPUT_MANIFEST",""))/("TCR.tsv" if chain in TCR else "BCR.tsv")).unlink(missing_ok=True)
    for name in ("igblast_summary.tsv", "chain_summary.csv"):
        (OUTPUT/name).unlink(missing_ok=True)
    if not shutil.which(BIN): raise SystemExit(f"igblastn not found: {BIN}")
    if not (DB_ROOT / "optional_file" / "pig.aux").is_file(): raise SystemExit("missing pig auxiliary file")
    DEPENDENCIES=dependency_snapshot(mode="pig",db_root=DB_ROOT,binary=BIN,species="pig",
        database_version=VERSION,threads=THREADS,chains=[job[2] for job in jobs],
        implementation=HERE/"05.work_igblastn.py",inherited=os.environ.get("SCIGBLAST_IGBLAST_DEPENDENCIES",""))
    # Remove legacy raw/aggregate products from earlier versions. This stage
    # publishes only sample-local filtered <CHAIN>.tsv files.
    shutil.rmtree(OUTPUT / "raw", ignore_errors=True)
    for legacy in OUTPUT.glob("all_samples_*.tsv"): legacy.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=MAX_JOBS) as executor:
        for idx, (_, result) in enumerate(bounded_completed(executor, task, jobs, MAX_JOBS*2), 1):
            results.append(result); print(f"[PIG][igblast] {result['status'].lower()} {idx}/{len(jobs)} {result['sample_id']}/{result['pair_id']}/{result['chain']}", flush=True)
    fields=["sample_id","pair_id","chain","input_sequences","raw_rows","mapped_rows","mapped_seqs","unmapped_seqs","productive_rows","productive_seqs","productive_percent","output_rows","filtered_seqs","mapping_percent","filtered_percent","retained_percent","locus_input_rows","excluded_locus_rows","unknown_locus_rows","locus_filter_version","status","error","output_path"]
    summary_temp=SUMMARY.with_suffix(SUMMARY.suffix+".tmp")
    with summary_temp.open("w", encoding="utf-8", newline="") as fh:
        writer=csv.DictWriter(fh, fieldnames=fields, delimiter="\t"); writer.writeheader()
        for result in sorted(results, key=lambda r:(r["sample_id"],r["pair_id"],r["chain"])):
            n=result["input_sequences"]; mapped=int(result["mapped_rows"]); productive=int(result["productive_rows"]); filtered=int(result["output_rows"])
            result["output_path"] = str(result.get("path") or "")
            result.update(mapped_seqs=mapped, unmapped_seqs=max(n-mapped,0), productive_seqs=productive, productive_percent=f"{productive*100/n:.2f}" if n else "0.00", filtered_seqs=filtered, mapping_percent=f"{mapped*100/n:.2f}" if n else "0.00", filtered_percent=f"{filtered*100/n:.2f}" if n else "0.00")
            result["retained_percent"]=result["filtered_percent"]; writer.writerow({key:result.get(key,"") for key in fields})
    os.replace(summary_temp,SUMMARY)
    chain_summary=OUTPUT/"chain_summary.csv"
    chain_temp=chain_summary.with_suffix(".csv.tmp")
    with chain_temp.open("w", encoding="utf-8", newline="") as fh:
        writer=csv.DictWriter(fh, fieldnames=fields); writer.writeheader()
        for result in sorted(results, key=lambda r:(r["sample_id"],r["pair_id"],r["chain"])):
            writer.writerow({key:result.get(key,"") for key in fields})
    os.replace(chain_temp,chain_summary)
    failed=sum(r["status"]=="ERROR" for r in results)
    # Keep compact filtered task TSVs and DONE markers for sample-level resume;
    # raw IgBLAST TSVs are removed immediately after filtering.
    print(f"[PIG][igblast] completed={len(results)} failed={failed} summary={SUMMARY}", flush=True); return 1 if failed else 0

if __name__ == "__main__": raise SystemExit(main())
