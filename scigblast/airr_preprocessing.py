"""Adapt selected Base AIRR chains to the existing IR preprocessing stage."""
from __future__ import annotations

import argparse
from collections import defaultdict
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import time
import subprocess

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHAINS=frozenset(('IGH','IGK','IGL','TRA','TRB','TRD','TRG'))
VERSION='base-airr-preprocessing-v2-sample-overlap'
WARM_KEYS={'SCIGBLAST_BASE_PREPROCESSING_READY_MANIFEST','SCIGBLAST_BASE_PREPROCESSING_WARMUP'}


def discover(roots):
    tasks=[];seen=set();seen_paths=set()
    for configured in roots:
        root=Path(configured).resolve()
        summary=root/'chain_summary.csv'
        ready=os.environ.get('SCIGBLAST_BASE_PREPROCESSING_READY_MANIFEST','')
        if ready:
            payload=json.loads(Path(ready).read_text(encoding='utf-8'))
            if Path(payload['input_root']).resolve()!=root:raise ValueError('ready sample root mismatch')
            rows=payload['rows']
        else:
            with summary.open(encoding='utf-8-sig',newline='') as handle:
                reader=csv.DictReader(handle)
                if not {'sample_id','chain','status','output_path'}.issubset(reader.fieldnames or []):
                    raise ValueError('chain_summary.csv missing required columns')
                rows=list(reader)
        for row in rows:
                if row['status']!='OK':raise ValueError('mapping tasks are incomplete')
                chain=row['chain'].strip().upper()
                if chain not in CHAINS:raise ValueError('unknown mapping chain: '+chain)
                sample=row['sample_id'].strip()
                # Base matcher publishes sample IDs as names, never output paths.
                if not sample or sample in {'.','..'} or '/' in sample or '\\' in sample or ':' in sample:
                    raise ValueError('unsafe sample ID in mapping summary')
                source=Path(row['output_path'])
                source=source if source.is_absolute() else root/source
                source=source.resolve()
                source.relative_to(root)
                if not source.is_file() or source.suffix!='.tsv' or any(part.startswith('.') for part in source.relative_to(root).parts):
                    raise ValueError('invalid final chain output: '+str(source))
                identity=(str(root),sample,chain,str(source))
                if identity in seen:raise ValueError('duplicate chain source in mapping summary')
                if str(source) in seen_paths:raise ValueError('one output assigned to multiple tasks')
                seen.add(identity)
                seen_paths.add(str(source))
                tasks.append(dict(source=str(source),input_root=str(root),relative_sample=sample,sample=sample,
                    chain=chain,receptor='BCR' if chain.startswith('IG') else 'TCR'))
    if not tasks:raise ValueError('no selected chain outputs')
    return sorted(tasks,key=lambda t:(t['input_root'],t['relative_sample'],t['receptor'],t['chain'],t['source']))


def read_sample(tasks,filter_and_count):
    import pandas as pd
    grouped=defaultdict(list);errors=[];stats=[]
    for task in tasks:
        try:
            frame=pd.read_csv(task['source'],sep='\t',dtype=str,keep_default_na=False,comment='#')
            if 'locus' not in frame or any(value!=task['chain'] for value in frame['locus']):
                raise ValueError('chain output contains a different or unknown locus')
            grouped[task['receptor']].append((task,frame))
        except Exception as error:errors.append(f"{task['source']}: {type(error).__name__}: {error}")
    result={}
    for receptor,parts in sorted(grouped.items()):
        try:
            frame=pd.concat([frame for _,frame in parts],ignore_index=True,copy=False)
            filtered,report=filter_and_count(frame,sample_id=tasks[0]['sample'],representative_index=None,allow_row_contract=True)
            task=dict(parts[0][0],source=';'.join(t['source'] for t,_ in parts),chains=','.join(sorted({t['chain'] for t,_ in parts})))
            result[receptor]=(task,filtered);stats.append(report)
        except Exception as error:errors.append(f'{receptor}: {type(error).__name__}: {error}')
    return result,errors,stats


def signature(root,output):
    project=Path(__file__).resolve().parents[1]
    entry=project/'IR_split/pipeline/08.preprocessing.py'
    implementation=[Path(__file__),entry,*sorted((entry.parent/'models').glob('*.py'))]
    dependencies={str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation}
    settings={key:value for key,value in os.environ.items() if key.startswith('SCIGBLAST_BASE_PREPROCESSING_') and key not in WARM_KEYS}
    manifest=Path(os.environ.get('SCIGBLAST_BASE_PREPROCESSING_READY_MANIFEST','') or Path(root)/'chain_summary.csv')
    files=[manifest,*[Path(t['source']) for t in discover([root])]]
    inputs=[]
    for path in files:
        stat=path.stat()
        with path.open('rb') as handle:head=hashlib.sha256(handle.read(65536)).hexdigest()
        inputs.append(dict(path=str(path.resolve()),size=stat.st_size,mtime_ns=stat.st_mtime_ns,head_sha256=head))
    return dict(version=VERSION,inputs=inputs,implementation=dependencies,settings=settings,output=str(Path(output).resolve()))


def versions(paths):
    return {str(path):[path.stat().st_size,path.stat().st_mtime_ns] for path in paths}


def ready(root,output):
    output=Path(output).resolve()
    try:
        marker=json.loads((output/'.preprocessing_state/base_completion.json').read_text(encoding='utf-8'))
        run=json.loads((output/'.preprocessing_state/run.json').read_text(encoding='utf-8'))
        required={str(output/'Datapoint.csv'),str(output/'processing_manifest.csv'),str(output/'.preprocessing_state/run.json')}
        return (marker['signature']==signature(root,output) and run['status']=='DONE' and
            required.issubset(marker['outputs']) and
            marker['outputs']==versions([Path(p) for p in marker['outputs']]))
    except (OSError,ValueError,KeyError,TypeError):return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--ready-manifest',type=Path)
    args=parser.parse_args()
    if args.ready_manifest and args.check:raise ValueError('warmup does not publish stage completion')
    if args.check:return 0 if ready(args.input,args.output) else 1
    warmup=args.ready_manifest is not None
    for key in WARM_KEYS:os.environ.pop(key,None)
    if warmup:
        os.environ.update(SCIGBLAST_BASE_PREPROCESSING_READY_MANIFEST=str(args.ready_manifest.resolve()),SCIGBLAST_BASE_PREPROCESSING_WARMUP='1')
    root,output=args.input.resolve(),args.output.resolve()
    if root==output or output.is_relative_to(root):raise ValueError('preprocessing output must be outside mapping inputs')
    before=signature(root,output)
    state=output/'.preprocessing_state';state.mkdir(parents=True,exist_ok=True)
    marker=state/'base_completion.json'
    try:old=json.loads(marker.read_text(encoding='utf-8')) if not warmup else {}
    except (OSError,ValueError):old={}
    old_junctions=[Path(p) for p in old.get('outputs',{}) if Path(p).resolve().is_relative_to(output/'junction_peps')]
    if any(not p.is_file() or [p.stat().st_size,p.stat().st_mtime_ns]!=old['outputs'][str(p)] for p in old_junctions):
        os.environ['SCIGBLAST_IR_PREPROCESSING_FORCE_RERUN']='1'
    if not warmup:marker.unlink(missing_ok=True)
    project=Path(__file__).resolve().parents[1]
    entry=project/'IR_split/pipeline/08.preprocessing.py'
    # Reuse its orchestration and frozen calculation functions. Base inputs are
    # aligned query records, rather than UMI representatives.
    os.environ.update(SCIGBLAST_IR_PREPROCESSING_INPUTS_SERIALIZED=str(root),
        SCIGBLAST_IR_PREPROCESSING_INPUT=str(root),SCIGBLAST_IR_PREPROCESSING_OUTPUT=str(output),
        SCIGBLAST_IR_PREPROCESSING_INPUT_LAYOUT='chains',SCIGBLAST_IR_PREPROCESSING_UMI_SOURCE='row_contract',
        SCIGBLAST_IR_PREPROCESSING_ALLOW_ROW_CONTRACT='1')
    # Parallelism belongs to the bounded sample/SHM workers. Each worker must
    # not spawn a host-sized BLAS/OpenMP thread pool during NumPy imports.
    for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
        os.environ[variable]='1'
    for suffix,default in [('FILTER_WORKERS','1'),('SAMPLES_PER_WINDOW','1'),('SHM_WORKERS','4'),
        ('TOTAL_PROCESS_BUDGET','4'),('MEMORY_BUDGET_GB','32'),('MIN_AVAILABLE_GB','16')]:
        os.environ['SCIGBLAST_IR_PREPROCESSING_'+suffix]=os.environ.get('SCIGBLAST_BASE_PREPROCESSING_'+suffix,default)
    # Force invalidation also on resource/adapter changes; existing model hashes
    # and per-sample source checks remain in the reused stage.
    os.environ['SCIGBLAST_IR_PREPROCESSING_ADAPTER_SIGNATURE']=hashlib.sha256(json.dumps({key:before[key] for key in ('version','implementation','settings')},sort_keys=True).encode()).hexdigest()
    if os.environ.get('SCIGBLAST_FORCE_RERUN')=='1':
        os.environ['SCIGBLAST_IR_PREPROCESSING_FORCE_RERUN']='1'
    sys.path.insert(0,str(entry.parent))
    status=0
    try:runpy.run_path(str(entry),run_name='__main__')
    except SystemExit as error:status=int(error.code or 0)
    if status:return status
    if signature(root,output)!=before:raise ValueError('mapping results changed during preprocessing')
    if warmup:
        print('[BASE preprocessing] ready sample checkpoints complete; final tables deferred',flush=True)
        return 0
    required=[output/'Datapoint.csv',output/'processing_manifest.csv',state/'run.json']
    if json.loads(required[-1].read_text(encoding='utf-8'))['status']!='DONE':raise ValueError('preprocessing did not complete')
    expected={output/'junction_peps'/root.name/t['relative_sample']/t['receptor']/(t['chain']+'.csv') for t in discover([root])}
    for path in old_junctions:
        if path not in expected:path.unlink(missing_ok=True)
    required.extend(sorted(path for path in expected if path.is_file()))
    temporary=marker.with_name('.base_completion.'+str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(dict(signature=before,outputs=versions(required),written_at=time.time()),ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,marker)
    print('[BASE preprocessing] completed; support counts describe aligned records, not validated molecular UMIs',flush=True)
    return 0


class SamplePreprocessing:
    """One bounded warmup worker; enqueue only after all tasks of a sample succeed."""
    def __init__(self,root,output,jobs):
        self.root,self.output=Path(root),Path(output)
        self.expected=Counter(job[0] for job in jobs)
        self.completed=defaultdict(list)
        self.executor=ThreadPoolExecutor(max_workers=1) if os.environ.get('SCIGBLAST_BASE_PIPELINED_PREPROCESSING')=='1' else None
        self.futures=[]

    def __enter__(self):return self

    def completed_task(self,result):
        if self.executor is None:return
        sample=result['sample_id'];self.completed[sample].append(dict(result))
        rows=self.completed[sample]
        if len(rows)==self.expected[sample] and all(r['status']=='OK' for r in rows):
            self.futures.append(self.executor.submit(self._run,sample,rows))

    def _run(self,sample,rows):
        try:
            from scigblast.locus_split import atomic_text
            from scigblast.progress import inherited_resource_fds
            folder=self.output/'.preprocessing_state'/'ready';folder.mkdir(parents=True,exist_ok=True)
            manifest=folder/(hashlib.sha256(sample.encode()).hexdigest()[:24]+'.json')
            payload=dict(input_root=str(self.root.resolve()),rows=[dict(sample_id=sample,chain=r['chain'],status='OK',output_path=str(r.get('path') or r['output_path'])) for r in rows])
            atomic_text(manifest,lambda handle:json.dump(payload,handle,ensure_ascii=False))
            print(f'[BASE overlap] sample={sample} start chains={len(rows)}',flush=True)
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--input',str(self.root),'--output',str(self.output),'--ready-manifest',str(manifest)],pass_fds=inherited_resource_fds())
            print(f'[BASE overlap] sample={sample} '+('checkpoint ready' if result.returncode==0 else 'warmup failed; final stage will retry'),flush=True)
        except Exception as error:
            print(f'[BASE overlap] sample={sample} warmup unavailable: {error}; final stage will retry',flush=True)

    def __exit__(self,kind,value,traceback):
        if self.executor is not None:self.executor.shutdown(wait=True,cancel_futures=kind is not None)


if __name__=='__main__':
    raise SystemExit(main())
