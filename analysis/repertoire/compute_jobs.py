"""Bounded sample statistics workers and versioned private snapshots."""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
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
from .performance import Performance, detail, measured


def worker_limits():
    configured=os.environ.get('SCIGBLAST_REPORT_COMPUTE_WORKERS','auto')
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
    memory=max(1,int((budget-rss-256)//per_worker))
    return dict(requested=requested,workers=max(1,min(requested,cpu,memory)),
                budget_mb=budget,coordinator_rss_mb=round(rss,1),worker_mb=per_worker,
                cpu_limit=cpu,memory_limit=memory)


def effective_workers():
    return worker_limits()['workers']


def initialize_worker(budget_mb):
    os.environ['SCIGBLAST_REPORT_MEMORY_BUDGET_MB']=str(budget_mb)
    for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'


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


def fingerprint(items,catalog,root,comparison=None):
    dependencies=[]
    from .postprocessed_usage import resolve
    by_chain={}
    for item in items:
        by_chain.setdefault(item['chain'],[]).append(item)
    for selected in by_chain.values():
        source,state=resolve(selected,root)
        dependencies.append([str(source) if source else None,state,version(source) if source else None])
    for item in items:
        if item.get('trace'):
            marker=Path(item['trace']);meta=json.loads(marker.read_text(encoding='utf-8'))
            files={meta['annotation']:meta['annotation_version'],**meta.get('query_files',{}),**(meta.get('upstream') or {}).get('files',{})}
            dependencies.extend((str(marker.parent/name),expected) for name,expected in sorted(files.items()))
            preparation=meta.get('primer_preparation')
            if preparation:dependencies.append(preparation)
    config=dict((catalog or {}).get('config',{}));config.pop('figure_primers',None)
    selected={i['sample_label'] for i in items}
    for field in ('sample_groups','sample_chain_groups','sample_aliases'):
        if field in config:
            config[field]={sample:value for sample,value in config[field].items() if sample in selected}
    assignments={s:(catalog or {}).get('assignments',{}).get(s,[]) for s in sorted(selected)}
    chain_assignments={s:(catalog or {}).get('chain_assignments',{}).get(s,{}) for s in sorted(selected)}
    code=hashlib.sha256()
    for name in ('aggregate.py','catalog.py','matching.py','io.py','compute_jobs.py','batch.py',
                 'usage.py','postprocessed_usage.py','gene_cdr3.py','isotype.py','output_metrics.py'):
        code.update(Path(__file__).with_name(name).read_bytes())
    from scigblast.trace import __file__ as trace_file
    code.update(Path(trace_file).read_bytes())
    if comparison:
        from scigblast.igblast_cache import reference
        name,_=reference(comparison['species'])
        for chain in by_chain:
            folder=Path(comparison['root'])/'database_251117'/name/chain
            dependencies.extend((str(path),version(path)) for path in sorted(folder.glob(
                f"{comparison['species']}_gl_{chain}_*.n*")) if path.is_file())
    return digest([str(root),items,dependencies,assignments,chain_assignments,config,
                   bool(catalog),comparison,code.hexdigest()])


def run_task(items,root,output,catalog,comparison=None,progress=None):
    for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
    folder=Path(output);folder.mkdir()
    report=Performance(folder/'.worker_performance.json');report.monitor(folder)
    def update(done,total,message):
        write_json(folder/'.worker_progress.json',dict(done=done,total=total,message=message))
        if progress:progress(done,total,message)
    try:
        data=report.call('aggregate',serial_aggregate,items,root,folder,catalog,update)
        from .usage import build
        usage=report.call('usage',build,items,root,folder,catalog,update,comparison=comparison,lineage_samples=set())
        write_json(folder/'aggregate.json',dict(aggregate=data,usage=usage))
        report.finish('succeeded')
    except BaseException:
        report.finish('failed');raise
    return str(folder)


def file_hash(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def statistics(items,root,output,catalog=None,progress=None,*,performance=None,comparison=None):
    output=Path(output);partitions=groups(items);limits=worker_limits();workers=limits['workers']
    budget=max(0,int(os.environ.get('SCIGBLAST_REPORT_STATISTICS_CACHE_MB','1024')))*1024**2
    # Mixed workflow adapters share a global composition mode. Keep their
    # established serial behavior when a safe sample partition is unavailable.
    workflow_modes={'05.igblastn_out' in Path(item['path']).parts for item in items}
    reason=('unsafe_or_overlapping_sources' if not partitions else 'mixed_workflows' if len(workflow_modes)>1
            else 'single_worker_cache_disabled' if workers==1 and not budget else '')
    if reason:
        if performance is not None:performance['compute']=dict(workers=1,cache_hits=0,reason=reason,limits=limits,cache_budget_bytes=budget)
        if progress:progress(0,len(items),'单进程统计 · '+('缓存已关闭' if not budget else '来源需按原流程处理'))
        from .usage import build
        data=serial_aggregate(items,root,output,catalog,progress)
        usage=build(items,root,output,catalog,progress,comparison=comparison,lineage_samples=set())
        return data,usage
    work=output/'.compute';work.mkdir()
    cache=output.parent/'.statistics_cache'
    pending=[];folders={};keys={};hits=0;cache_states=[]
    try:
        for index,selected in enumerate(partitions):
            destination=work/str(index)
            with detail('sample_fingerprint',sample=selected[0]['sample_label']):
                key=fingerprint(selected,catalog,root,comparison)
            keys[index]=key
            reused=False
            if budget:
                # Each entry has its own lease. Hashing/copying a large snapshot
                # must not block every other cache entry or its metadata cleanup.
                with locked(cache/'.entries'/key):
                    try:
                        receipt=json.loads(safe_file(cache/(key+'.json'),cache).read_text(encoding='utf-8'))
                        database=safe_file(cache/(key+'.sqlite'),cache)
                        with detail('sample_cache_read',sample=selected[0]['sample_label']):
                            if receipt.get('key')!=key or digest(receipt['data'])!=receipt.get('data_sha256') or file_hash(database)!=receipt['sha256']:
                                raise ValueError('Invalid statistics cache')
                            destination.mkdir();copy_snapshot(database,destination/'details.sqlite')
                        write_json(destination/'aggregate.json',receipt['data'])
                        os.utime(database,None);os.utime(cache/(key+'.json'),None)
                        reused=True;hits+=1
                    except (OSError,ValueError,KeyError,TypeError):
                        if destination.exists():
                            if destination.is_symlink() or destination.resolve().parent!=work.resolve():
                                raise ValueError('Unsafe cached compute directory')
                            shutil.rmtree(destination)
            cache_states.append(dict(sample=selected[0]['sample_label'],state='hit' if reused else 'disabled' if not budget else 'miss_or_invalid'))
            if reused:folders[index]=destination
            else:pending.append((index,selected,destination))
        # Stable longest-first scheduling reduces the final large-sample tail.
        pending.sort(key=lambda task:-sum(Path(i['path']).stat().st_size for i in task[1]))
        workers=min(workers,max(1,len(pending)))
        if performance is not None:performance['compute']=dict(workers=workers,groups=len(partitions),cache_hits=hits,
            limits=limits,cache_budget_bytes=budget,cache_samples=cache_states)
        if progress:progress(len(folders),len(partitions),f'样本统计 · {workers} 个进程 · 复用 {hits} 个样本')
        def completed(index):
            receipt=folders[index]/'.worker_performance.json'
            if performance is not None and receipt.is_file():
                performance.setdefault('sample_statistics_receipts',[]).append(dict(sample=partitions[index][0]['sample_label'],
                    **json.loads(receipt.read_text(encoding='utf-8'))))
            if progress:progress(len(folders),len(partitions),'样本归属与基因组成统计完成')
        if workers<2:
            for index,selected,destination in pending:
                def update(done,total,message):
                    if progress:progress(len(folders),len(partitions),message)
                run_task(selected,root,destination,catalog,comparison,update);folders[index]=destination
                completed(index)
        else:
            from .figure_jobs import stop_workers
            with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),
                    initializer=initialize_worker,initargs=(limits['worker_mb'],)) as pool:
                # At most two jobs are in flight; never queue every large source.
                remaining=iter(pending);active={}
                def submit():
                    try:index,selected,destination=next(remaining)
                    except StopIteration:return
                    active[pool.submit(run_task,selected,str(root),str(destination),catalog,comparison)]=index
                for _ in range(workers):submit()
                try:
                    while active:
                        ready,_=wait(active,timeout=2,return_when=FIRST_COMPLETED)
                        if not ready and progress:
                            messages=[]
                            for index in active.values():
                                try:messages.append(json.loads((work/str(index)/'.worker_progress.json').read_text(encoding='utf-8'))['message'])
                                except (OSError,ValueError,KeyError):pass
                            if messages:progress(len(folders),len(partitions),' · '.join(messages))
                        for future in ready:
                            index=active.pop(future);folders[index]=Path(future.result());completed(index);submit()
                except BaseException:
                    for future in active:future.cancel()
                    stop_workers(pool);raise
        if budget:
            for index,folder in folders.items():
                if index not in {i for i,_,_ in pending}:continue
                database=folder/'details.sqlite';key=keys[index]
                cache_states[index]['bytes']=database.stat().st_size
                if database.stat().st_size>budget//2:
                    cache_states[index]['state']='too_large';continue
                with locked(cache/'.entries'/key), detail('sample_cache_write',sample=partitions[index][0]['sample_label']):
                    temp=cache/(key+'.sqlite.tmp')
                    temp.unlink(missing_ok=True)
                    copy_snapshot(database,temp);os.replace(temp,cache/(key+'.sqlite'))
                    data=json.loads((folder/'aggregate.json').read_text(encoding='utf-8'))
                    write_json(cache/(key+'.json'),dict(key=key,sha256=file_hash(database),data_sha256=digest(data),data=data))
                    cache_states[index]['state']='stored'
            with locked(cache):
                prune_files(cache,budget)
        if len(folders)==1:
            source=next(iter(folders.values()))
            # This work DB is owned by this run; it is already independent from
            # any cached snapshot, so no second multi-GB copy is necessary.
            os.replace(source/'details.sqlite',output/'details.sqlite')
            result=json.loads((source/'aggregate.json').read_text(encoding='utf-8'))
            return result['aggregate'],result['usage']
        return merge(items,partitions,folders,output)
    finally:
        if work.resolve().parent!=output.resolve() or work.is_symlink():raise ValueError('Unsafe compute work directory')
        shutil.rmtree(work)


@measured('statistics_merge')
def merge(items,partitions,folders,output):
    """Merge disjoint sample databases, rebasing every integer row reference."""
    from .batch import tune
    merged={}
    usages=[]
    target=output/'details.sqlite'
    with closing(sqlite3.connect(target,uri=True)) as connection:
        tune(connection)
        indexes=[]
        offsets={'observations':0,'usage_records':0,'composition_records':0}
        reference_columns={
            'observations':{'id':'observations'},
            'observation_links':{'first_id':'observations'},
            'usage_records':{'id':'usage_records'},
            'usage_calls':{'record_id':'usage_records'},
            'usage_candidates':{'record_id':'usage_records'},
            'composition_records':{'id':'composition_records'},
            'composition_calls':{'record_id':'composition_records'},
        }
        for number in sorted(folders):
            folder=folders[number]
            result=json.loads((folder/'aggregate.json').read_text(encoding='utf-8'))
            for key,values in result['aggregate'].items():
                merged.setdefault(key,[]).extend(values)
            usages.append(result['usage'])
            connection.execute('ATTACH DATABASE ? AS part',
                               ((folder/'details.sqlite').resolve().as_uri()+'?mode=ro',))
            schemas=connection.execute(
                "SELECT type,name,sql FROM part.sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            tables=[(name,sql) for kind,name,sql in schemas if kind=='table']
            if number==0:
                for _,sql in tables:
                    connection.execute(sql)
                indexes=[sql for kind,_,sql in schemas if kind=='index']
            for name,_ in tables:
                columns=[row[1] for row in connection.execute(f'PRAGMA part.table_info("{name}")')]
                expressions=[]
                for column in columns:
                    parent=reference_columns.get(name,{}).get(column)
                    expressions.append(f'"{column}"+{offsets[parent]}' if parent else f'"{column}"')
                names=','.join('"'+column+'"' for column in columns)
                connection.execute(f'INSERT INTO "{name}"({names}) SELECT '+','.join(expressions)+f' FROM part."{name}"')
            for name in offsets:
                if any(table==name for table,_ in tables):
                    offsets[name]=connection.execute(f'SELECT coalesce(max(id),0) FROM "{name}"').fetchone()[0]
            connection.commit()
            connection.execute('DETACH DATABASE part')
        for sql in indexes:
            connection.execute(sql)
        connection.commit()
    sample_order={item['id']:number for number,item in enumerate(items)}
    merged['samples'].sort(key=lambda row:sample_order[row['id']])
    extras={'match_counts':['match_class'],'family_counts':['match_class'],'trim_counts':['trim_state'],
        'primer_counts':['subset','primer'],'gene_counts':['subset','primer','trim_state','segment','gene'],'filter_counts':['reason']}
    for name,extra in extras.items():
        merged[name].sort(key=lambda row:tuple(row[key] for key in ['sample','sample_key','chain','scope',*extra]))
    merged['gene_coverage'].sort(key=lambda row:tuple(row[key] for key in ['sample','sample_key','chain','scope','segment','gene','subset']))
    merged['matching_totals'].sort(key=lambda row:tuple(row[key] for key in ['sample','sample_key','scope','unit','chains','match_class']))
    merged['matching_total_summary']=summarize_matching(merged['matching_totals'])
    usage=dict(usages[0])
    usage['groups']=sorted([group for value in usages for group in value['groups']],
                          key=lambda group:(group['sample'],group['chain'],group['group_id']))
    usage['isotypes']=[row for value in usages for row in value['isotypes']]
    return merged,usage
