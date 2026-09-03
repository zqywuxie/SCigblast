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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PIPELINE = Path(os.environ["SCIGBLAST_BASE_PIPELINE_DIR"])
ROOT = Path(os.environ["OUTPUT_ROOT"])
RAW_ROOT = Path(os.environ["RAW_INPUT_DIR"]).resolve() if os.environ.get("RAW_INPUT_DIR") else None
DATASET_LABEL = os.environ.get("SCIGBLAST_DATASET_LABEL", "").strip()
MANIFEST = ROOT / "01.match" / DATASET_LABEL / "sample_manifest.csv" if DATASET_LABEL else ROOT / "01.match/sample_manifest.csv"
INPUT = Path(os.environ.get("SCIGBLAST_IGBLAST_INPUT_DIR", ROOT / "04.pandaseq"))
OUTPUT = Path(os.environ.get("SCIGBLAST_IGBLAST_OUTPUT_DIR", ROOT / "05.igblastn_out"))
DB_ROOT = Path(os.environ.get("SCIGBLAST_IGBLAST_DB_DIR", "/data/scAnalyis/Scigblast/igblast"))
SPECIES = os.environ.get("SCIGBLAST_IGBLAST_SPECIES", "human")
THREADS = max(1, int(os.environ.get("SCIGBLAST_IGBLAST_THREADS", "8")))
REQUESTED = max(1, int(os.environ.get("SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS", "2")))
THREAD_BUDGET = max(1, int(os.environ.get("SCIGBLAST_PARALLEL_THREAD_BUDGET", "128")))
MEMORY_BUDGET = max(1, int(os.environ.get("SCIGBLAST_MEMORY_BUDGET_GB", "300")))
MEMORY_PER_JOB = max(1, int(os.environ.get("SCIGBLAST_IGBLAST_MEMORY_LIMIT_GB", "48")))
MAX_JOBS = max(1, min(REQUESTED, THREAD_BUDGET // THREADS or 1, MEMORY_BUDGET // MEMORY_PER_JOB or 1))
BIN = shutil.which(os.environ.get("SCIGBLAST_IGBLAST_BIN", "igblastn"))
if not BIN:
    candidate = DB_ROOT / "bin/igblastn"
    BIN = str(candidate) if candidate.is_file() else None
BAD_V = {"", "*", "-", "NA", "N/A", "NONE", "NULL", "NO_HIT", "UNMAPPED", "NOT_FOUND"}
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
    try:
        with source.open(encoding="utf-8", errors="ignore") as handle:
            return sum(line.startswith(">") for line in handle)
    except OSError: return 0

def valid_v(value: str) -> bool:
    return value.strip().upper() not in BAD_V

def filter_airr(source: Path, destination: Path) -> tuple[int, int, int, int]:
    header = None; raw_rows = mapped = productive = output_rows = 0
    temp = destination.with_suffix(destination.suffix + ".tmp")
    with source.open(encoding="utf-8", errors="replace") as src, temp.open("w", encoding="utf-8") as dst:
        for line in src:
            text = line.rstrip("\r\n")
            if not text or text.startswith("#"): continue
            fields = text.split("\t")
            if header is None:
                if "sequence_id" not in fields or "v_call" not in fields: continue
                header = fields; vi = header.index("v_call"); pi = header.index("productive") if "productive" in header else -1
                dst.write("\t".join(header) + "\n"); continue
            fields += [""] * max(0, len(header) - len(fields)); raw_rows += 1
            has_v = valid_v(fields[vi]); mapped += int(has_v)
            is_productive = pi >= 0 and fields[pi].strip().upper() in {"T", "TRUE"}; productive += int(is_productive)
            if has_v and is_productive:
                dst.write("\t".join(fields[:len(header)]) + "\n"); output_rows += 1
    if header is None:
        temp.unlink(missing_ok=True); raise ValueError(f"malformed IgBLAST AIRR TSV: {source}")
    os.replace(temp, destination); return raw_rows, mapped, productive, output_rows

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
    filtered = task_dir / f"{chain}.tsv"; raw = Path(str(filtered) + ".raw.tsv"); log = Path(str(filtered) + ".log"); marker = Path(str(filtered) + ".DONE")
    result = {"sample_id":sample,"pair_id":pair,"species":species,"chain":chain,"input_sequences":count_fasta(fasta),
              "raw_rows":0,"mapped_rows":0,"productive_rows":0,"output_rows":0,"mapping_percent":"0.00","retained_percent":"0.00",
              "status":"ERROR","error":"","path":None,"parent":str(parent)}
    if not fasta.is_file(): result["error"]="missing merged FASTA"; return result
    stat=fasta.stat(); fingerprint=hashlib.sha256(f"{fasta}|{stat.st_size}|{stat.st_mtime_ns}|{chain}|{SPECIES}|{THREADS}|productive+v1".encode()).hexdigest()
    if marker.is_file() and filtered.is_file():
        cached=dict(line.split("=",1) for line in marker.read_text(encoding="utf-8",errors="ignore").splitlines() if "=" in line)
        if cached.get("status")=="DONE" and cached.get("job_fingerprint")==fingerprint:
            for name in ("raw_rows","mapped_rows","productive_rows","output_rows"): result[name]=int(cached.get(name,0))
            result.update(status="OK",path=filtered); return result
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
        raw_n,mapped,productive,out_n=filter_airr(raw,filtered); raw.unlink(missing_ok=True)
        result.update(raw_rows=raw_n,mapped_rows=mapped,productive_rows=productive,output_rows=out_n,status="OK",path=filtered)
        marker.write_text("\n".join(("status=DONE","manifest_schema_version=2",f"job_fingerprint={fingerprint}",f"raw_rows={raw_n}",f"mapped_rows={mapped}",f"productive_rows={productive}",f"output_rows={out_n}"))+"\n",encoding="utf-8")
    except Exception as exc:
        result["error"]=str(exc); raw.unlink(missing_ok=True); filtered.unlink(missing_ok=True)
    return result

def merge_group(paths: list[Path], destination: Path):
    destination.parent.mkdir(parents=True,exist_ok=True); temp=destination.with_suffix(destination.suffix+".tmp")
    with temp.open("w",encoding="utf-8") as out:
        wrote_header=False
        for source in sorted(paths,key=lambda p:p.name):
            with source.open(encoding="utf-8",errors="replace") as src:
                for index,line in enumerate(src):
                    if not wrote_header or index > 0: out.write(line)
                wrote_header=True
    os.replace(temp,destination)

def main() -> int:
    if not MANIFEST.is_file(): raise SystemExit("missing canonical sample_manifest.csv")
    if not BIN: raise SystemExit("igblastn not found")
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
    print(f"[BASE][igblast] stage_start total={len(jobs)} requested_workers={REQUESTED} effective_workers={MAX_JOBS} threads_per_worker={THREADS} memory_per_worker_gb={MEMORY_PER_JOB}",flush=True)
    with ThreadPoolExecutor(max_workers=MAX_JOBS) as executor:
        futures=[executor.submit(run_task,job) for job in jobs]
        for index,future in enumerate(as_completed(futures),1):
            result=future.result(); results.append(result); print(f"[BASE][igblast] progress={index}/{len(jobs)} percent={index*100//len(jobs)} sample={result['sample_id']}/{result['pair_id']}/{result['chain']} status={result['status']}",flush=True)
    grouped={}
    for result in results:
        if result["status"]!="OK" or not result["path"]: continue
        cell="TCR" if result["chain"] in TCR else "BCR"
        rel_stage = stage_dir(Path(result["parent"]), safe(Path(str(result["pair_id"]).replace("\\", "/")).name), safe(result["sample_id"]))
        key=(str(rel_stage),cell)
        grouped.setdefault(key,[]).append(result["path"])
    for (rel_stage,cell),paths in grouped.items(): merge_group(paths,OUTPUT/Path(rel_stage)/f"{cell}.tsv")
    fields=["sample_id","pair_id","species","chain","input_sequences","raw_rows","mapped_rows","mapped_seqs","unmapped_seqs","productive_rows","productive_seqs","productive_percent","output_rows","filtered_seqs","mapping_percent","filtered_percent","retained_percent","status","error"]
    for result in results:
        n=result["input_sequences"]; mapped=int(result["mapped_rows"]); productive=int(result["productive_rows"]); filtered=int(result["output_rows"])
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
