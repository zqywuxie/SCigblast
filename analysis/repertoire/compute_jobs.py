"""Sample-scoped aggregate workers; all later statistics use one SQLite owner."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sqlite3

from .aggregate import aggregate as serial_aggregate
from .io import digest, safe_file, version, write_json
from .matching import summarize_matching
from .private_cache import locked, prune_files, copy_snapshot


def effective_workers():
    configured=os.environ.get('SCIGBLAST_REPORT_COMPUTE_WORKERS','1')
    requested=2 if configured=='auto' else max(1,min(2,int(configured)))
    budget=max(256,int(os.environ.get('SCIGBLAST_REPORT_MEMORY_BUDGET_MB','2048')))
    try:budget=min(budget,int(Path('/sys/fs/cgroup/memory.max').read_text())//1024**2)
    except (OSError,ValueError):pass
    per_worker=max(256,int(os.environ.get('SCIGBLAST_REPORT_COMPUTE_WORKER_MB','640')))
    try:rss=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1024**2
    except (OSError,ValueError,AttributeError):rss=384
    cpu=len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else os.cpu_count() or 1
    try:
        quota,period=Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota!='max':cpu=min(cpu,max(1,int(quota)//int(period)))
    except (OSError,ValueError):pass
    return max(1,min(requested,cpu,int((budget-rss-256)//per_worker)))


def groups(items):
    # Include every chain/pair of a logical sample. If sources overlap across
    # samples, leave this invocation serial rather than guessing a partition.
    grouped={};owners={}
    if len({(i['sample_key'],i['chain']) for i in items})!=len(items):return []
    if any(len({i.get('dataset','') for i in items if i['sample_label']==s})>1 for s in {i['sample_label'] for i in items}):return []
    for item in items:
        key=(item.get('dataset',''),item['sample_label'])
        grouped.setdefault(key,[]).append(item)
        sources=[]
        if item.get('trace'):
            meta=json.loads(Path(item['trace']).read_text(encoding='utf-8'))
            sources.append(('query',meta.get('query_version')))
            sources.extend(('raw',digest(value)) for value in (meta.get('upstream') or {}).get('sources',{}).values())
        elif item.get('merged'):sources.append(('merged',item['merged']['path']))
        for source in sources:
            if source[1] is None:continue
            if source in owners and owners[source]!=key:return []
            owners[source]=key
    return list(grouped.values())


def fingerprint(items,catalog,root):
    dependencies=[]
    for item in items:
        if item.get('trace'):
            marker=Path(item['trace']);meta=json.loads(marker.read_text(encoding='utf-8'))
            files={meta['annotation']:meta['annotation_version'],**meta.get('query_files',{}),**(meta.get('upstream') or {}).get('files',{})}
            dependencies.extend((str(marker.parent/name),expected) for name,expected in sorted(files.items()))
            preparation=meta.get('primer_preparation')
            if preparation:dependencies.append(preparation)
    config=dict((catalog or {}).get('config',{}));config.pop('figure_primers',None)
    selected={i['sample_label'] for i in items}
    assignments={s:(catalog or {}).get('assignments',{}).get(s,[]) for s in sorted(selected)}
    code=hashlib.sha256()
    for name in ('aggregate.py','catalog.py','matching.py','io.py','compute_jobs.py','batch.py'):
        code.update(Path(__file__).with_name(name).read_bytes())
    from scigblast.trace import __file__ as trace_file
    code.update(Path(trace_file).read_bytes())
    return digest([str(root),items,dependencies,assignments,config,bool(catalog),code.hexdigest()])


def run_task(items,root,output,catalog):
    for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
    folder=Path(output);folder.mkdir()
    data=serial_aggregate(items,root,folder,catalog)
    write_json(folder/'aggregate.json',data)
    return str(folder)


def file_hash(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def aggregate(items,root,output,catalog=None,progress=None,*,performance=None):
    output=Path(output);partitions=groups(items);workers=effective_workers()
    budget=max(0,int(os.environ.get('SCIGBLAST_REPORT_STATISTICS_CACHE_MB','0')))*1024**2
    if len(partitions)<2 or (workers==1 and not budget):
        if performance is not None:performance['compute']=dict(workers=1,cache_hits=0)
        return serial_aggregate(items,root,output,catalog,progress)
    # The default remains serial. Cache and two workers are separately opt-in.
    work=output/'.compute';work.mkdir()
    cache=output.parent/'.statistics_cache'
    pending=[];folders={};keys={};hits=0
    try:
        for index,selected in enumerate(partitions):
            destination=work/str(index)
            key=fingerprint(selected,catalog,root);keys[index]=key
            reused=False
            if budget:
                with locked(cache):
                    try:
                        receipt=json.loads(safe_file(cache/(key+'.json'),cache).read_text(encoding='utf-8'))
                        database=safe_file(cache/(key+'.sqlite'),cache)
                        if receipt.get('key')!=key or digest(receipt['data'])!=receipt.get('data_sha256') or file_hash(database)!=receipt['sha256']:
                            raise ValueError('Invalid statistics cache')
                        destination.mkdir();copy_snapshot(database,destination/'details.sqlite')
                        write_json(destination/'aggregate.json',receipt['data']);reused=True;hits+=1
                    except (OSError,ValueError,KeyError,TypeError):
                        if destination.exists():
                            if destination.is_symlink() or destination.resolve().parent!=work.resolve():
                                raise ValueError('Unsafe cached compute directory')
                            shutil.rmtree(destination)
            if reused:folders[index]=destination
            else:pending.append((index,selected,destination))
        workers=min(workers,max(1,len(pending)))
        if performance is not None:performance['compute']=dict(workers=workers,groups=len(partitions),cache_hits=hits)
        if workers<2:
            for index,selected,destination in pending:
                run_task(selected,root,destination,catalog);folders[index]=destination
                if progress:progress(len(folders),len(partitions),'样本归属统计完成')
        else:
            from .figure_jobs import stop_workers
            with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
                # At most two jobs are in flight; never queue every large source.
                remaining=iter(pending);active={}
                def submit():
                    try:index,selected,destination=next(remaining)
                    except StopIteration:return
                    active[pool.submit(run_task,selected,str(root),str(destination),catalog)]=index
                for _ in range(workers):submit()
                try:
                    while active:
                        future=next(as_completed(active));index=active.pop(future)
                        folders[index]=Path(future.result());submit()
                        if progress:progress(len(folders),len(partitions),'样本归属统计完成')
                except BaseException:
                    for future in active:future.cancel()
                    stop_workers(pool);raise
        if budget:
            with locked(cache):
                for index,folder in folders.items():
                    if index not in {i for i,_,_ in pending}:continue
                    database=folder/'details.sqlite';key=keys[index]
                    if database.stat().st_size>budget//2:continue
                    temp=cache/(key+'.sqlite.tmp')
                    temp.unlink(missing_ok=True)
                    copy_snapshot(database,temp);os.replace(temp,cache/(key+'.sqlite'))
                    data=json.loads((folder/'aggregate.json').read_text(encoding='utf-8'))
                    write_json(cache/(key+'.json'),dict(key=key,sha256=file_hash(database),data_sha256=digest(data),data=data))
                prune_files(cache,budget)
        return merge(items,partitions,folders,output)
    finally:
        if work.resolve().parent!=output.resolve() or work.is_symlink():raise ValueError('Unsafe compute work directory')
        shutil.rmtree(work)


def merge(items,partitions,folders,output):
    merged={};by_item={item['id']:index for index,part in enumerate(partitions) for item in part}
    for index in sorted(folders):
        data=json.loads((folders[index]/'aggregate.json').read_text(encoding='utf-8'))
        for key,values in data.items():merged.setdefault(key,[]).extend(values)
    target=output/'details.sqlite'
    with closing(sqlite3.connect(target)) as conn:
        with closing(sqlite3.connect(folders[0]/'details.sqlite')) as first:
            conn.execute(first.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()[0])
        columns=[r[1] for r in conn.execute('PRAGMA table_info(observations)') if r[1]!='id']
        names=','.join(columns);placeholders=','.join('?' for _ in columns)
        for item in items:
            path=folders[by_item[item['id']]]/'details.sqlite'
            with closing(sqlite3.connect(path)) as source:
                conn.executemany(f'INSERT INTO observations({names}) VALUES({placeholders})',
                    source.execute(f'SELECT {names} FROM observations WHERE sample_key=? AND chain=? ORDER BY id',(item['sample_key'],item['chain'])))
        conn.execute('CREATE INDEX observations_view ON observations(sample_key,chain,match_class,filter_reason,id)')
        conn.execute('CREATE INDEX observations_mapping_id ON observations(sample_key,chain,mapping_sequence_id)')
        conn.commit()
    sample_order={item['id']:n for n,item in enumerate(items)}
    merged['samples'].sort(key=lambda r:sample_order[r['id']])
    extras={'match_counts':['match_class'],'family_counts':['match_class'],'trim_counts':['trim_state'],
        'primer_counts':['subset','primer'],'gene_counts':['subset','primer','trim_state','segment','gene'],'filter_counts':['reason']}
    for name,extra in extras.items():merged[name].sort(key=lambda r:tuple(r[k] for k in ['sample','sample_key','chain','scope',*extra]))
    merged['gene_coverage'].sort(key=lambda r:tuple(r[k] for k in ['sample','sample_key','chain','scope','segment','gene','subset']))
    merged['matching_totals'].sort(key=lambda r:tuple(r[k] for k in ['sample','sample_key','scope','unit','chains','match_class']))
    merged['matching_total_summary']=summarize_matching(merged['matching_totals'])
    return merged
