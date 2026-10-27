#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
RUNTIME_DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
source "${SCRIPT_DIR}/00.pipeline_config.env" 2>/dev/null || true
[[ -n "$RUNTIME_OUTPUT_ROOT" ]] && SCIGBLAST_OUTPUT_ROOT="$RUNTIME_OUTPUT_ROOT"
[[ -n "$RUNTIME_DATASET_LABEL" ]] && SCIGBLAST_DATASET_LABEL="$RUNTIME_DATASET_LABEL"
export SCIGBLAST_BASE_PIPELINE_DIR="$SCRIPT_DIR"
export OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-${SCRIPT_DIR}/../output}}"
export RAW_INPUT_DIR="${RAW_INPUT_DIR:-}"
export SCIGBLAST_DATASET_LABEL="${SCIGBLAST_DATASET_LABEL:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) export SCIGBLAST_IGBLAST_INPUT_DIR="$2"; shift 2;;
    --output-dir) export SCIGBLAST_IGBLAST_OUTPUT_DIR="$2"; shift 2;;
    --db-dir) export SCIGBLAST_IGBLAST_DB_DIR="$2"; shift 2;;
    --species) export SCIGBLAST_IGBLAST_SPECIES="$2"; shift 2;;
    --threads) export SCIGBLAST_IGBLAST_THREADS="$2"; shift 2;;
    --parallel) export SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS="$2"; shift 2;;
    --input-mode) shift 2;;
    --help) echo '05.work_igblastn.sh reads the canonical manifest and 00.pipeline_config.env'; exit 0;;
    *) echo "[BASE][igblast] unknown argument: $1" >&2; exit 2;;
  esac
done
exec "${PYTHON_BIN:-python3}" - <<'PY'
from __future__ import annotations
import csv, hashlib, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PIPELINE = Path(os.environ["SCIGBLAST_BASE_PIPELINE_DIR"])
sys.path.insert(0, str(PIPELINE.parents[1]))
from pipeline_igblast_cache import dependency_snapshot, resolve_binary, task_fingerprint
from pipeline_parallel import FastaCountCache, bounded_completed
from pipeline_locus_split import filter_mapping_airr
ROOT = Path(os.environ["OUTPUT_ROOT"])
RAW_ROOT = Path(os.environ["RAW_INPUT_DIR"]).resolve() if os.environ.get("RAW_INPUT_DIR") else None
DATASET_LABEL = os.environ.get("SCIGBLAST_DATASET_LABEL", "").strip()
MANIFEST = ROOT / "01.match" / DATASET_LABEL / "sample_manifest.csv" if DATASET_LABEL else ROOT / "01.match/sample_manifest.csv"
INPUT = Path(os.environ.get("SCIGBLAST_IGBLAST_INPUT_DIR", ROOT / "04.pandaseq"))
OUTPUT = Path(os.environ.get("SCIGBLAST_IGBLAST_OUTPUT_DIR", ROOT / "05.igblastn_out"))
DB_ROOT = Path(os.environ.get("SCIGBLAST_IGBLAST_DB_DIR", "/data/scAnalyis/Scigblast/igblast"))
SPECIES = os.environ.get("SCIGBLAST_IGBLAST_SPECIES", "human")
THREADS = max(1, int(os.environ.get("SCIGBLAST_IGBLAST_THREADS", "8")))
REQUESTED = max(1, int(os.environ.get("SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS", "4")))
THREAD_BUDGET = max(1, int(os.environ.get("SCIGBLAST_PARALLEL_THREAD_BUDGET", "128")))
MEMORY_BUDGET = max(1, int(os.environ.get("SCIGBLAST_MEMORY_BUDGET_GB", "300")))
MEMORY_PER_JOB = max(1, int(os.environ.get("SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB", "48")))
MAX_JOBS = max(1, min(4, REQUESTED, THREAD_BUDGET // THREADS or 1, MEMORY_BUDGET // MEMORY_PER_JOB or 1))
BIN = resolve_binary(os.environ.get("SCIGBLAST_IGBLAST_BIN", "igblastn"), DB_ROOT, "base")
DEPENDENCIES = {}
FASTA_COUNTS = FastaCountCache()
TCR = {"TRA", "TRB", "TRD", "TRG"}
SUPPORTED = TCR | {"IGH", "IGK", "IGL"}

def safe(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unnamed"

def relative_parent(raw_path: str) -> Path:
    if RAW_ROOT is None: return Path()
    try: rel = Path(raw_path).resolve().parent.relative_to(RAW_ROOT)
    except ValueError: return Path()
    return Path(*(safe(part) for part in rel.parts))

def stage_dir(parent: Path, pair_stem: str, sample: str) -> Path:
    """Build the same deduplicated relative stage path as clean/PANDAseq."""
    parts = []
    for value in (parent.as_posix(), pair_stem, sample):
        for part in Path(value).parts:
            if part in {"", ".", ".."}:
                continue
            item = safe(part)
            if item and (not parts or parts[-1] != item):
                parts.append(item)
    return Path(*parts) if parts else Path()

def database(chain: str, part: str) -> Path:
    return DB_ROOT / "database_251117" / SPECIES / chain / f"{SPECIES}_gl_{chain}_{part}"

def blastdb_exists(prefix: Path) -> bool:
    return any(prefix.parent.glob(prefix.name + ".*"))

def count_fasta(source: Path) -> int:
    return FASTA_COUNTS.count(source)

def run_task(job: tuple[str, str, str, str, str]):
    sample, pair, chain, raw_r1, species = job
    # Manifest pair_id may include the raw relative parent (e.g.
    # ``Lane03/file``).  The stage path already carries the parent, so only
    # the basename is the physical pair stem here.
    pp, ss = safe(Path(str(pair).replace("\\", "/")).name), safe(sample)
    parent = relative_parent(raw_r1)
    relative_stage = stage_dir(parent, pp, ss)
    fasta = INPUT / relative_stage / f"{pp}_merged.fasta"
    task_dir = OUTPUT / ".tasks" / relative_stage; task_dir.mkdir(parents=True, exist_ok=True)
    filtered = OUTPUT / relative_stage / f"{chain}.tsv"
    raw = task_dir / f"{chain}.tsv.raw.tsv"; log = task_dir / f"{chain}.tsv.log"; marker = task_dir / f"{chain}.tsv.DONE"
    result = {"sample_id":sample,"pair_id":pair,"species":species,"chain":chain,"input_sequences":count_fasta(fasta),
              "raw_rows":0,"mapped_rows":0,"productive_rows":0,"output_rows":0,"mapping_percent":"0.00","retained_percent":"0.00",
              "status":"ERROR","error":"","path":None,"parent":str(parent)}
    result.update(locus_input_rows=0, excluded_locus_rows=0, unknown_locus_rows=0, locus_filter_version=1)
    if not fasta.is_file(): result["error"]="missing merged FASTA"; return result
    fingerprint=task_fingerprint(fasta, DEPENDENCIES["chains"][chain])
    try:
        if marker.is_file() and filtered.is_file() and os.environ.get("SCIGBLAST_FORCE_RERUN") != "1":
            cached=dict(line.split("=",1) for line in marker.read_text(encoding="utf-8",errors="ignore").splitlines() if "=" in line)
            if cached.get("status")=="DONE" and cached.get("job_fingerprint")==fingerprint:
                for name in ("raw_rows","mapped_rows","productive_rows","output_rows"): result[name]=int(cached.get(name,0))
                for name in ('locus_input_rows', 'excluded_locus_rows', 'unknown_locus_rows'):
                    result[name] = int(cached.get(name, 0))
                result.update(status="OK",path=filtered); return result
    except (OSError, ValueError):
        pass
    marker.unlink(missing_ok=True)
    filtered.unlink(missing_ok=True)
    v,j=database(chain,"V"),database(chain,"J")
    if not blastdb_exists(v) or not blastdb_exists(j): result["error"]="missing V/J database"; return result
    aux=DB_ROOT / "optional_file" / f"{SPECIES}_gl.aux"
    if not aux.is_file(): result["error"]="missing auxiliary file"; return result
    # IgBLAST accepts ``Ig`` (not the pipeline label ``BCR``) for
    # immunoglobulin sequences.  Keep BCR/TCR only for our internal grouping.
    seqtype="TCR" if chain in TCR else "Ig"
    args=[str(BIN),"-query",str(fasta),"-germline_db_V",str(v),"-germline_db_J",str(j),"-auxiliary_data",str(aux),"-organism",SPECIES,"-ig_seqtype",seqtype,"-num_threads",str(THREADS),"-outfmt","19","-out",str(raw)]
    d=database(chain,"D")
    if blastdb_exists(d): args[args.index("-germline_db_J"):args.index("-germline_db_J")]=["-germline_db_D",str(d)]
    c=database(chain,"C")
    if blastdb_exists(c): args.extend(["-c_region_db",str(c),"-num_alignments_C","1"])
    with log.open("w",encoding="utf-8") as handle: rc=subprocess.run(args,stdout=handle,stderr=subprocess.STDOUT).returncode
    if rc or not raw.is_file(): result["error"]=f"igblast_failed exit={rc}"; raw.unlink(missing_ok=True); return result
    try:
        counts=filter_mapping_airr(raw,filtered,chain); raw.unlink(missing_ok=True)
        result.update(counts,status="OK",path=filtered)
        marker_temp=Path(str(marker)+".tmp")
        marker_temp.write_text("\n".join(("status=DONE","manifest_schema_version=2",f"job_fingerprint={fingerprint}",f"dependency_fingerprint={DEPENDENCIES['chains'][chain]}",*(f"{key}={value}" for key,value in counts.items())))+"\n",encoding="utf-8")
        os.replace(marker_temp,marker)
    except Exception as exc:
        result.update(status="ERROR",error=str(exc),path=None)
        raw.unlink(missing_ok=True); filtered.unlink(missing_ok=True)
        filtered.with_suffix(filtered.suffix+".tmp").unlink(missing_ok=True)
        Path(str(marker)+".tmp").unlink(missing_ok=True)
    return result

def main() -> int:
    global DEPENDENCIES, FASTA_COUNTS
    FASTA_COUNTS = FastaCountCache()
    if not MANIFEST.is_file(): raise SystemExit("missing canonical sample_manifest.csv")
    jobs=[]; seen=set()
    with MANIFEST.open(encoding="utf-8-sig",newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status","").upper()!="OK": continue
            chains=[chain for chain in row.get("igblast_chains","").split(",") if chain in SUPPORTED]
            key=(row.get("sample_id",""),row.get("pair_id",""),row.get("r1_path",""))
            for chain in chains:
                task_key=key+(chain,)
                if task_key not in seen:
                    seen.add(task_key); jobs.append((key[0],key[1],chain,key[2],row.get("species","") or SPECIES))
    if not jobs: raise SystemExit("no valid sample-specific Chain tasks in manifest")
    OUTPUT.mkdir(parents=True,exist_ok=True); results=[]
    # Remove legacy aggregates owned by this invocation. Publish only per-chain
    # TSVs; successful task outputs remain available for resume.
    for sample,pair,chain,raw_r1,_ in jobs:
        rel_stage=stage_dir(relative_parent(raw_r1),safe(Path(str(pair).replace("\\","/")).name),safe(sample))
        (OUTPUT/rel_stage/("TCR.tsv" if chain in TCR else "BCR.tsv")).unlink(missing_ok=True)
    for name in ("igblast_summary.tsv","chain_summary.csv"):
        (OUTPUT/name).unlink(missing_ok=True)
    if not shutil.which(BIN): raise SystemExit("igblastn not found")
    DEPENDENCIES=dependency_snapshot(mode="base",db_root=DB_ROOT,binary=BIN,species=SPECIES,
        database_version=SPECIES,threads=THREADS,chains=[job[2] for job in jobs],
        implementation=PIPELINE/"05.work_igblastn.sh",inherited=os.environ.get("SCIGBLAST_IGBLAST_DEPENDENCIES",""))
    print(f"[BASE][igblast] stage_start total={len(jobs)} requested_workers={REQUESTED} effective_workers={MAX_JOBS} threads_per_worker={THREADS} memory_per_worker_gb={MEMORY_PER_JOB}",flush=True)
    with ThreadPoolExecutor(max_workers=MAX_JOBS) as executor:
        for index,(_,result) in enumerate(bounded_completed(executor,run_task,jobs,MAX_JOBS*2),1):
            results.append(result); print(f"[BASE][igblast] progress={index}/{len(jobs)} percent={index*100//len(jobs)} sample={result['sample_id']}/{result['pair_id']}/{result['chain']} status={result['status']}",flush=True)
    fields=["sample_id","pair_id","species","chain","input_sequences","raw_rows","mapped_rows","mapped_seqs","unmapped_seqs","productive_rows","productive_seqs","productive_percent","output_rows","filtered_seqs","mapping_percent","filtered_percent","retained_percent","locus_input_rows","excluded_locus_rows","unknown_locus_rows","locus_filter_version","status","error","output_path"]
    for result in results:
        n=result["input_sequences"]; mapped=int(result["mapped_rows"]); productive=int(result["productive_rows"]); filtered=int(result["output_rows"])
        result["output_path"]=str(result.get("path") or "")
        result.update(mapped_seqs=mapped, unmapped_seqs=max(n-mapped,0), productive_seqs=productive, productive_percent=f"{productive*100/n:.2f}" if n else "0.00", filtered_seqs=filtered, mapping_percent=f"{mapped*100/n:.2f}" if n else "0.00", filtered_percent=f"{filtered*100/n:.2f}" if n else "0.00")
        result["retained_percent"]=result["filtered_percent"]
    for destination,delimiter in ((OUTPUT/"igblast_summary.tsv","\t"),(OUTPUT/"chain_summary.csv",",")):
        temp=destination.with_suffix(destination.suffix+".tmp")
        with temp.open("w",encoding="utf-8",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=fields,delimiter=delimiter); writer.writeheader(); writer.writerows({field:result.get(field,"") for field in fields} for result in sorted(results,key=lambda x:(x["sample_id"],x["pair_id"],x["chain"])))
        os.replace(temp,destination)
    failed=sum(result["status"]!="OK" for result in results); print(f"[BASE][igblast] completed={len(results)-failed} failed={failed} total={len(results)}",flush=True); return 1 if failed else 0

raise SystemExit(main())
PY
