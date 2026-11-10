"""Bounded, spawn-based rendering of independent sample/reference groups."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor,as_completed
from contextlib import closing
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
from importlib.metadata import version as package_version
from functools import lru_cache

from .io import safe_file,write_json
from .usage import connect
from .private_cache import locked, prune_files


@lru_cache(maxsize=1)
def implementation_signature():
    digest=hashlib.sha256()
    for package in ('matplotlib','numpy'):digest.update((package+'='+package_version(package)).encode())
    for filename in ('render_usage.py','palette.py','gene_cdr3.py','io.py','usage.py','__init__.py','figure_jobs.py'):
        digest.update(Path(__file__).with_name(filename).read_bytes())
    return digest.digest()


def fingerprint(task,root,index=None):
    digest=hashlib.sha256(json.dumps(task,sort_keys=True,ensure_ascii=False).encode())
    digest.update(implementation_signature())
    with closing(connect(index or root/'details.sqlite')) as conn:
        sql='SELECT * FROM gene_usage WHERE group_id=?'
        args=[task['usage']['groups'][0]['group_id']]
        if task.get('_gene_scope')=='primer':
            sql+=' AND primer=?'
            args.append(task['figure_primers'][0])
        for row in conn.execute(sql+' ORDER BY primer,segment,gene,metric,aa_field',args):
            digest.update(json.dumps(dict(row),sort_keys=True,ensure_ascii=False).encode())
    return digest.hexdigest()


def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        while chunk:=handle.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def effective_workers():
    configured=os.environ.get('SCIGBLAST_REPORT_FIGURE_WORKERS','auto')
    requested=2 if configured=='auto' else max(1,min(2,int(configured)))
    budget=max(256,int(os.environ.get('SCIGBLAST_REPORT_MEMORY_BUDGET_MB','2048')))
    try:budget=min(budget,int(Path('/sys/fs/cgroup/memory.max').read_text())//1024**2)
    except (OSError,ValueError):pass
    try:rss=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1024**2
    except (OSError,ValueError,AttributeError):rss=384
    per_worker=max(256,int(os.environ.get('SCIGBLAST_REPORT_FIGURE_WORKER_MB','640')))
    cpus=len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else os.cpu_count() or 1
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max':cpus=min(cpus,max(1,int(quota)//int(period)))
    except (OSError,ValueError):pass
    return max(1,min(requested,cpus,int((budget-rss-256)//per_worker)))


def initialize(root):
    for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
    folder=Path(root)/'.render_workers'/str(os.getpid());folder.mkdir(parents=True,exist_ok=True)
    os.environ['MPLCONFIGDIR']=str(folder)


def run_task(task,output,index=None):
    from .render_usage import render
    return render(task,Path(output),skip_isotypes=True,index=index)


def stop_workers(pool):
    # Python 3.11-3.13 lack Executor.terminate_workers(). These handles belong
    # exclusively to this executor; never discover or signal unrelated PIDs.
    processes=list((getattr(pool,'_processes',None) or {}).values())
    for process in processes:
        if process.is_alive():process.terminate()
    for process in processes:
        process.join(timeout=3)
        if process.is_alive():process.kill();process.join(timeout=3)
    pool.shutdown(wait=True,cancel_futures=True)


def reuse(cache_root,key,output,receipt_name='.figure_cache.json'):
    if cache_root is None:return None
    try:
        root=Path(cache_root)
        receipt=json.loads(safe_file(root/receipt_name,root).read_text(encoding='utf-8'))[key]
        for artifact in receipt['figures']:
            name=artifact['png'];source=safe_file(root/receipt.get('files',{}).get(name,name),root)
            if Path(name).is_absolute() or '..' in Path(name).parts or Path(name).suffix!='.png':return None
            (output/name).resolve().relative_to(output.resolve())
            with source.open('rb') as handle:
                if handle.read(8)!=b'\x89PNG\r\n\x1a\n':return None
            if file_hash(source)!=receipt['hashes'][name]:return None
        for artifact in receipt['figures']:
            target=output/artifact['png'];target.parent.mkdir(parents=True,exist_ok=True)
            source=safe_file(root/receipt.get('files',{}).get(artifact['png'],artifact['png']),root)
            if source.resolve()!=target.resolve():
                shutil.copyfile(source,target)
        return receipt['figures']
    except (OSError,ValueError,KeyError,TypeError):return None



def cached_figures(source,key,output,receipt_name,shared_cache=None):
    figures=reuse(source,key,output,receipt_name)
    if figures is not None or shared_cache is None:
        return figures
    if int(os.environ.get('SCIGBLAST_REPORT_FIGURE_CACHE_MB','256'))<=0:
        return None
    with locked(shared_cache):
        return reuse(shared_cache,key,output,key+'.json')


def remember_figures(shared_cache,key,output,figures):
    if shared_cache is None:
        return
    budget=max(0,int(os.environ.get('SCIGBLAST_REPORT_FIGURE_CACHE_MB','256')))*1024**2
    files={figure['png']:key+'-'+str(index)+'.png' for index,figure in enumerate(figures)}
    if not budget or sum((output/name).stat().st_size for name in files)>budget//2:
        return
    with locked(shared_cache) as root:
        for name,cached_name in files.items():
            temporary=root/(cached_name+'.tmp')
            shutil.copyfile(output/name,temporary)
            os.replace(temporary,root/cached_name)
        receipt=dict(figures=figures,files=files,
                     hashes={name:file_hash(output/name) for name in files})
        write_json(root/(key+'.json'),{key:receipt})
        prune_files(root,budget)


def render_usage(data,output,*,figure_audit=None,figure_progress=None,cache_root=None,index=None,receipt_name='.figure_cache.json',skip_isotypes=False,shared_cache=None):
    from .render_usage import render
    output=Path(output)
    groups=[g for g in data['usage']['groups'] if not data.get('figure_groups') or g['group_id'] in data['figure_groups']]
    tasks=[]
    requested=set(data.get('figure_primers',[]))
    for group in groups:
        tasks.append(dict(usage=dict(groups=[group],isotypes=[]),
                          figure_primers=data.get('figure_primers',[]),_gene_scope='sample'))
        for primer in group['primers']:
            if not requested or primer in requested:
                tasks.append(dict(usage=dict(groups=[dict(group,primers=[primer])],isotypes=[]),
                                  figure_primers=[primer],_gene_scope='primer'))
    keys=[fingerprint(task,output,index) for task in tasks]
    results,receipts={},{}
    pending=[]
    for i,(task,key) in enumerate(zip(tasks,keys)):
        cached=None if figure_audit is not None else cached_figures(cache_root,key,output,receipt_name,shared_cache)
        if cached is not None:results[i]=cached
        else:pending.append((i,task))
    workers=min(effective_workers(),len(pending))
    # Small reports rarely amortize importing Matplotlib in two spawn workers.
    if os.environ.get('SCIGBLAST_REPORT_FIGURE_WORKERS','auto')=='auto' and len(pending)<4:workers=min(workers,1)
    print(f'[REPORT figures] workers={workers or 0}; groups={len(tasks)}; reused={len(results)}',flush=True)
    try:
        if workers<2 or figure_audit is not None:
            for i,task in pending:
                results[i]=render(task,output,skip_isotypes=True,figure_audit=figure_audit,index=index)
                if figure_progress:figure_progress(len(results),len(tasks),'样本基因图组')
        else:
            with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),initializer=initialize,initargs=(str(output),),max_tasks_per_child=12) as pool:
                remaining=iter(pending)
                futures={}
                def submit():
                    try:
                        i,task=next(remaining)
                    except StopIteration:
                        return
                    futures[pool.submit(run_task,task,str(output),str(index) if index else None)]=i
                for _ in range(workers):
                    submit()
                try:
                    while futures:
                        future=next(as_completed(futures))
                        results[futures.pop(future)]=future.result()
                        if figure_progress:figure_progress(len(results),len(tasks),'样本或引物基因图组')
                        submit()
                except BaseException:
                    for future in futures:future.cancel()
                    stop_workers(pool)
                    raise
        artifacts=[]
        generated={number for number,_ in pending}
        for i,key in enumerate(keys):
            figures=results[i];artifacts.extend(figures)
            if i in generated:
                remember_figures(shared_cache,key,output,figures)
            receipts[key]=dict(figures=figures,hashes={f['png']:file_hash(output/f['png']) for f in figures})
        if not skip_isotypes:
            from .io import digest
            isotype_key=digest([data['usage'],implementation_signature().hex(),'isotype'])
            isotype_receipt='.isotype_figure_cache.json'
            figures=None if figure_audit is not None else cached_figures(cache_root,isotype_key,output,isotype_receipt,shared_cache)
            if figures is None:
                figures=render(data,output,isotype_only=True,figure_audit=figure_audit,index=index)
                remember_figures(shared_cache,isotype_key,output,figures)
            write_json(output/isotype_receipt,{isotype_key:dict(figures=figures,
                hashes={figure['png']:file_hash(output/figure['png']) for figure in figures})})
            artifacts.extend(figures)
        write_json(output/receipt_name,receipts)
        return artifacts
    finally:
        folder=output/'.render_workers'
        if folder.is_dir():
            if folder.is_symlink() or folder.resolve().parent!=output.resolve():raise ValueError('Unsafe render cache directory')
            shutil.rmtree(folder)
