#!/usr/bin/env python3
"""Remove PANDAseq-incompatible FASTQ header tags after fastp."""
from __future__ import annotations
import csv, gzip, hashlib, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
PIPELINE_DIR=Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
from tools.pipeline_config import load_config

HERE=PIPELINE_DIR
CFG=load_config(); OUTPUT_ROOT=Path(CFG.get('SCIGBLAST_OUTPUT_ROOT') or CFG.get('OUTPUT_ROOT') or str(PIPELINE_DIR.parent/'output'))
RAW_INPUT_ROOT=Path(CFG.get('RAW_INPUT_DIR', '')) if CFG.get('RAW_INPUT_DIR') else None
DATASET_LABEL=os.environ.get('SCIGBLAST_DATASET_LABEL', '').strip()
if not DATASET_LABEL and RAW_INPUT_ROOT:
    DATASET_LABEL=re.sub(r'[^A-Za-z0-9._-]+', '_', RAW_INPUT_ROOT.name).strip('_')
def stage_root(stage: str) -> Path:
    return OUTPUT_ROOT/stage/DATASET_LABEL if DATASET_LABEL else OUTPUT_ROOT/stage
STAGE_MATCH=stage_root('01.match'); IN=stage_root('02.fastp')/'data'; OUT=stage_root('03.clean_data'); SUMMARY=OUT/'clean_summary.csv'
CLEAN_THREADS=max(1, int(CFG.get('SCIGBLAST_CLEAN_THREADS', CFG.get('CLEAN_THREADS', '8'))))
CLEAN_MAX_PARALLEL=max(1, int(CFG.get('SCIGBLAST_CLEAN_MAX_PARALLEL_SAMPLES', CFG.get('CLEAN_MAX_PARALLEL_SAMPLES', '4'))))
PIGZ=shutil.which('pigz')
CLEAN_HEADER_VERSION='4'

def safe(s): return re.sub(r'[^A-Za-z0-9._-]+','_',s)
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

def raw_relative_parent(path: Path) -> Path:
    if RAW_INPUT_ROOT is None:
        return Path()
    try:
        return path.resolve().parent.relative_to(RAW_INPUT_ROOT.resolve())
    except ValueError:
        return Path()
def clean_header(line: str, suffix: int) -> str:
    token=line.rstrip('\r\n').split()[0]
    token=re.sub(r'#.*$', '', token)
    token=re.sub(r'/[12]$', '', token)
    fields=token.split(':')
    limit=7 if len(fields) >= 8 else len(fields)
    for i in range(3, limit):
        fields[i]=re.sub(r'[A-Za-z]', '', fields[i])
        fields[i]=re.sub(r'^0+', '', fields[i]) or '0'
    return ':'.join(fields[:limit]) + f'/{suffix}'
def open_text(path, mode):
    compressed = False
    if 'r' in mode and str(path).endswith('.gz'):
        try:
            with open(path, 'rb') as probe:
                compressed = probe.read(2) == b'\x1f\x8b'
        except OSError:
            compressed = False
    elif 'w' in mode:
        compressed = True
    return gzip.open(path, mode+'t', encoding='utf-8', newline='') if compressed else open(path, mode, encoding='utf-8', newline='')

def is_gzip(path: Path) -> bool:
    try:
        with open(path, 'rb') as fh: return fh.read(2) == b'\x1f\x8b'
    except OSError: return False

def input_fingerprint(r1: Path, r2: Path) -> str:
    try:
        payload = '|'.join((str(r1), str(r2),
                            f'{r1.stat().st_size}:{r1.stat().st_mtime_ns}',
                            f'{r2.stat().st_size}:{r2.stat().st_mtime_ns}',
                            str(CLEAN_HEADER_VERSION)))
    except OSError:
        return ''
    return hashlib.sha256(payload.encode()).hexdigest()

def process_python(src: Path, dst: Path, suffix: int) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True); tmp=dst.with_suffix(dst.suffix+'.tmp')
    total=0
    with open_text(src,'r') as fi, gzip.open(tmp,'wt',encoding='utf-8',newline='') as fo:
        while True:
            h=fi.readline()
            if not h: break
            seq=fi.readline(); plus=fi.readline(); qual=fi.readline()
            if not seq or not plus or not qual:
                raise ValueError(f'truncated FASTQ record in {src} at record {total + 1}')
            if not h.startswith('@') or not plus.startswith('+'):
                raise ValueError(f'invalid FASTQ header/plus line in {src} at record {total + 1}')
            if len(seq.rstrip('\r\n')) != len(qual.rstrip('\r\n')):
                raise ValueError(f'sequence/quality length mismatch in {src} at record {total + 1}')
            fo.write(clean_header(h, suffix)+'\n'+seq+plus+qual); total+=1
    tmp.replace(dst); return total

def process_fast(src: Path, dst: Path, suffix: int) -> int:
    """Multi-threaded pigz/awk stream; falls back to portable Python."""
    if not PIGZ: return process_python(src, dst, suffix)
    dst.parent.mkdir(parents=True, exist_ok=True); tmp=dst.with_suffix(dst.suffix+'.tmp'); count_file=dst.with_suffix(dst.suffix+'.count.tmp')
    source_handle=None; decoder=None
    try:
        if is_gzip(src):
            decoder=subprocess.Popen([PIGZ,'-dc','-p',str(CLEAN_THREADS),str(src)], stdout=subprocess.PIPE)
            source=decoder.stdout
        else:
            source_handle=open(src,'rb'); source=source_handle
        awk_script=r'''BEGIN { n=0 }
(NR-1) % 4 == 0 { n++; split($0,f,/[[:space:]]+/); h=f[1]; sub(/^@/,"",h); sub(/#.*/,"",h); sub(/\/[12]$/, "", h); m=split(h,a,":"); limit=(m>=8?7:m); for(i=4;i<=limit;i++){gsub(/[A-Za-z]/,"",a[i]); sub(/^0+/,"",a[i]); if(a[i]=="")a[i]="0"}; out=a[1]; for(i=2;i<=limit;i++)out=out ":" a[i]; print "@" out "/" suffix; next }
{ print }
END { print n > count_file }'''
        awk=subprocess.Popen(['awk','-v',f'count_file={count_file}','-v',f'suffix={suffix}',awk_script], stdin=source, stdout=subprocess.PIPE)
        with open(tmp,'wb') as out_handle:
            compressor=subprocess.Popen([PIGZ,'-p',str(CLEAN_THREADS)], stdin=awk.stdout, stdout=out_handle)
            awk.stdout.close(); rc_compressor=compressor.wait(); rc_awk=awk.wait()
        rc_decoder=decoder.wait() if decoder is not None else 0
        if rc_compressor or rc_awk or rc_decoder: raise RuntimeError(f'pigz/awk failed ({rc_decoder},{rc_awk},{rc_compressor})')
        tmp.replace(dst); return int(count_file.read_text(encoding='utf-8').strip() or '0')
    finally:
        if source_handle is not None: source_handle.close()
        tmp.unlink(missing_ok=True); count_file.unlink(missing_ok=True)

def process(src: Path, dst: Path, read_count: int):
    return process_fast(src, dst, read_count)
def count_records(path: Path) -> int:
    try:
        with gzip.open(path, 'rt', encoding='utf-8', errors='ignore') as fh:
            return sum(1 for _ in fh) // 4
    except OSError:
        return 0
def marker_value(marker: Path, key: str, default: int = 0) -> int:
    try:
        for line in marker.read_text(encoding='utf-8', errors='ignore').splitlines():
            if line.startswith(key+'='): return int(line.split('=',1)[1])
    except (OSError, ValueError): pass
    return default
def main():
    manifest=STAGE_MATCH/'sample_manifest.csv'
    if not manifest.exists(): raise SystemExit('missing sample_manifest.csv')
    print(f'[clean] backend={"pigz+awk" if PIGZ else "python"} threads={CLEAN_THREADS} max_parallel={CLEAN_MAX_PARALLEL}')
    rows=[]; seen=set(); jobs=[]
    with manifest.open(encoding='utf-8',newline='') as fh:
        for row in csv.DictReader(fh):
            # Only matched R1/R2 pairs may enter downstream processing.  Keep
            # unmatched/error rows in the match manifest for audit, but never
            # let them create clean outputs or fail this stage.
            if str(row.get('status', '')).strip().upper() != 'OK':
                continue
            key=(row['sample_id'],row['pair_id'],row.get('r1_path',''))
            if key in seen: continue
            seen.add(key); jobs.append(row)

    def handle(row):
            sample=safe(row['sample_id']); pair=safe(row['pair_id'])
            rel_parent=Path(*[safe(part) for part in raw_relative_parent(Path(row['r1_path'])).parts])
            in_base=IN/rel_parent/pair/sample; out_base=OUT/rel_parent/pair/sample
            r1=in_base/(pair+'_R1.fq.gz'); r2=in_base/(pair+'_R2.fq.gz')
            o1=out_base/(pair+'_R1.fq.gz'); o2=out_base/(pair+'_R2.fq.gz'); marker=out_base/'.DONE'
            fingerprint=input_fingerprint(r1, r2)
            marker_text=marker.read_text(encoding='utf-8', errors='ignore') if marker.exists() else ''
            if fingerprint and marker.exists() and f'version={CLEAN_HEADER_VERSION}' in marker_text and f'input_fingerprint={fingerprint}' in marker_text and o1.exists() and o2.exists() and all(is_gzip(p) for p in (o1, o2)):
                n1,n2=marker_value(marker,'r1_reads',-1),marker_value(marker,'r2_reads',-1)
                return {'sample_id':row['sample_id'],'pair_id':row['pair_id'],'r1_reads':n1,'r2_reads':n2,'r1_percent':100.0,'r2_percent':100.0,'status':'OK','error':''}
            try:
                n1=process(r1,o1,1); n2=process(r2,o2,2)
                atomic_write_text(marker, f'stage=clean_header\nversion={CLEAN_HEADER_VERSION}\ninput_fingerprint={fingerprint}\nr1_reads={n1}\nr2_reads={n2}\n')
                return {'sample_id':row['sample_id'],'pair_id':row['pair_id'],'r1_reads':n1,'r2_reads':n2,'r1_percent':100.0,'r2_percent':100.0,'status':'OK','error':''}
            except Exception as exc:
                return {'sample_id':row['sample_id'],'pair_id':row['pair_id'],'r1_reads':0,'r2_reads':0,'r1_percent':0,'r2_percent':0,'status':'ERROR','error':str(exc)}

    print(f'[PIG][clean] stage_start total={len(jobs)} max_parallel={CLEAN_MAX_PARALLEL}')
    with ThreadPoolExecutor(max_workers=CLEAN_MAX_PARALLEL) as pool:
        futures={pool.submit(handle,row): row for row in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            result=future.result(); rows.append(result)
            print(f"[PIG][clean] progress={done}/{len(jobs)} percent={done*100//max(1,len(jobs))} sample={result['sample_id']} status={result['status']}", flush=True)
    rows.sort(key=lambda r: (r['sample_id'], r['pair_id']))
    SUMMARY.parent.mkdir(parents=True,exist_ok=True)
    fields=['sample_id','pair_id','r1_reads','r2_reads','r1_percent','r2_percent','status','error']
    with SUMMARY.open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(rows)
    failed=sum(r['status'] != 'OK' for r in rows)
    print(f'[PIG][clean] completed={len(rows)-failed} failed={failed} total={len(rows)} summary={SUMMARY}')
    return 0 if all(r['status']=='OK' for r in rows) else 1
if __name__=='__main__': raise SystemExit(main())
