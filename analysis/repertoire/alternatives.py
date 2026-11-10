"""Stage-specific primer-group review without changing primer attribution."""
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .catalog import Matcher
from .matching import CATEGORIES, review_settings

COMPARISON_COLUMNS = ['sample','stage','unit','assigned_group','tested_group','denominator',
    'original_unique_percent','threshold_percent','unique_count','multiple_count','unmatched_count',
    'unique_percent','multiple_percent','unmatched_percent','status']


def _targets(catalog, totals):
    if not catalog:return [], 50
    enabled, threshold = review_settings(catalog.get('config', {}))
    return ([r for r in totals if r['match_class']=='unique' and r['percent'] is not None
             and r['percent']<threshold] if enabled else []), threshold


def _evaluate(sample, targets, records, catalog, stage, threshold, progress=None, connection=None):
    from .primer_metrics import match
    assigned=sorted({p.get('group','') for p in catalog['assignments'].get(sample,[]) if p.get('group')})
    groups=catalog.get('groups',{})
    candidates={g:values for g,values in groups.items() if g not in assigned}
    membership,primers=defaultdict(set),{}
    for group,values in candidates.items():
        for primer in values:
            membership[primer['name']].add(group);primers[primer['name']]=primer
    matcher=Matcher(list(primers.values())) if primers else None
    # A repeated primer name with a different sequence cannot use union hits to
    # reconstruct that group's individual matcher. Keep its original fallback.
    reusable=[g for g,values in candidates.items()
              if all(primers[p['name']]['sequence']==p['sequence'] for p in values)]
    pending=[];source_ids={}
    if connection is not None:
        connection.executescript('''
          CREATE TABLE IF NOT EXISTS alternative_match_sources(id INTEGER PRIMARY KEY,sample TEXT,stage TEXT,source TEXT,
            UNIQUE(sample,stage,source));
          CREATE TABLE IF NOT EXISTS alternative_match_records(source_id INTEGER,ordinal INTEGER,names TEXT,
            PRIMARY KEY(source_id,ordinal));
          CREATE TABLE IF NOT EXISTS alternative_match_scopes(sample TEXT,stage TEXT,catalog_version TEXT,groups_json TEXT,
            PRIMARY KEY(sample,stage));''')
    counts,seen,conflicts=defaultdict(Counter),Counter(),set()
    units={r['unit'] for r in targets}
    if matcher:
        for ordinal,record in enumerate(records,1):
            unit,sequence=record[:2]
            if unit not in units:continue
            if not sequence:
                conflicts.add(unit);continue
            category,names=match(sequence,matcher,catalog['config'])
            if category not in CATEGORIES:
                conflicts.add(unit);continue
            hits=Counter(group for name in names for group in membership[name])
            if connection is not None and len(record)>2:
                sample_key,source,record_ordinal=record[2]
                source_key=json.dumps([sample_key,source],separators=(',',':'))
                if source_key not in source_ids:
                    cursor=connection.execute('INSERT INTO alternative_match_sources(sample,stage,source) VALUES(?,?,?)',(sample,stage,source_key))
                    source_ids[source_key]=cursor.lastrowid
                pending.append((source_ids[source_key],record_ordinal,json.dumps(names,ensure_ascii=False)))
                if len(pending)>=10000:
                    connection.executemany('INSERT INTO alternative_match_records VALUES(?,?,?)',pending)
                    pending.clear()
            for group,n in hits.items():counts[(unit,group)]['unique' if n==1 else 'multiple']+=1
            seen[unit]+=1
            if progress and ordinal%20000==0:progress(0,1,f'{sample} · {stage}: alternative groups {ordinal:,}')
    if connection is not None:
        connection.executemany('INSERT INTO alternative_match_records VALUES(?,?,?)',pending)
        if matcher and not conflicts:
            connection.execute('INSERT OR REPLACE INTO alternative_match_scopes VALUES(?,?,?,?)',
                (sample,stage,catalog['version'],json.dumps(reusable)))
    reviews,comparisons=[],[]
    for target in targets:
        unit,denominator=target['unit'],target['denominator']
        reason=('other_groups_unavailable' if not groups else 'no_other_groups' if not candidates else
                'original_sequences_unavailable' if unit in conflicts or seen[unit]!=denominator else '')
        base=dict(sample=sample,stage=stage,unit=unit,assigned_group='; '.join(assigned),
                  denominator=denominator,original_unique_percent=target['percent'],threshold_percent=threshold)
        review=dict(base,status='unavailable' if reason else 'no_candidate',reason=reason,candidates=[])
        if not reason:
            for group in sorted(candidates):
                unique,multiple=(counts[(unit,group)][key] for key in ('unique','multiple'))
                unmatched=denominator-unique-multiple
                if unmatched<0:raise ValueError('Primer group counts exceed their stage denominator')
                percent=100*unique/denominator if denominator else None
                suspected=percent is not None and percent>=threshold and percent>target['percent']
                comparisons.append(dict(base,tested_group=group,unique_count=unique,multiple_count=multiple,
                    unmatched_count=unmatched,unique_percent=percent,multiple_percent=100*multiple/denominator,
                    unmatched_percent=100*unmatched/denominator,status='suspected' if suspected else 'below_threshold'))
                if suspected:review['candidates'].append(group)
            if review['candidates']:review['status']='suspected'
        reviews.append(review)
    return dict(reviews=reviews,comparisons=comparisons)


def _mapping_records(connection,sample):
    cursor=connection.execute('''SELECT matching_source,MIN(sequence),MAX(sequence),
        MIN(sample_match_class),MAX(sample_match_class),MIN(matching_unit),MAX(matching_unit),sample_key,matching_ordinal
        FROM observations WHERE sample=? GROUP BY sample_key,matching_source,matching_ordinal''',(sample,))
    for source,sequence,maximum,category,category_max,unit,unit_max,sample_key,ordinal in cursor:
        if category!=category_max or category not in CATEGORIES:continue
        yield unit,sequence if sequence and sequence==maximum and unit==unit_max and not source.startswith('result:') else None,(sample_key,source,ordinal)


def compare(index,catalog,totals,progress=None):
    targets,threshold=_targets(catalog,totals)
    result=dict(reviews=[],comparisons=[])
    by_sample=defaultdict(list)
    for target in targets:by_sample[target['sample']].append(target)
    with closing(sqlite3.connect(index)) as connection:
        if targets and catalog.get('groups'):
            connection.execute('CREATE INDEX IF NOT EXISTS observations_primer_review ON observations(sample,sample_key,matching_source,matching_ordinal)')
        for number,(sample,values) in enumerate(sorted(by_sample.items()),1):
            review=_evaluate(sample,values,_mapping_records(connection,sample),catalog,'mapping',threshold,progress,connection)
            for key in result:result[key].extend(review[key])
            if progress:progress(number,len(by_sample),f'{sample}: Mapping alternative group review complete')
        connection.commit()
    result['comparisons'].sort(key=lambda r:(r['sample'],r['unit'],-r['unique_percent'],r['tested_group']))
    return result


def _raw_records(items,root,catalog,sample,allowed_roots):
    from .primer_metrics import raw_source,archived_raw,match
    from .io import safe_file,version,digest,sequences
    seen=set();original=Matcher(catalog['assignments'][sample])
    for item in items:
        if item['sample_label']!=sample:continue
        meta=json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else {}
        marker=Path(item['trace']) if item.get('trace') else None;upstream=meta.get('upstream') or {}
        if upstream.get('sources',{}).get('raw_R1'):
            path=safe_file(marker.parent/upstream['archive'],root)
            source=digest(upstream['sources']['raw_R1']);incoming=archived_raw(marker,meta,root)
        else:
            path,_=raw_source(item,root,meta,allowed_roots)
            if path is None:continue
            source=digest(dict(path=str(path),version=version(path)));incoming=sequences(path)
        if source in seen:continue
        seen.add(source);signature=version(path)
        for ordinal,(_,sequence) in enumerate(incoming,1):
            category,_=match(sequence,original,catalog['config'])
            if category in CATEGORIES:yield 'reads',sequence,('',source,ordinal)
        if version(path)!=signature:raise ValueError('原始 R1 在其他引物组测试期间发生变化')


def compare_raw(items,root,catalog,totals,*,allowed_roots=(),progress=None,index=None):
    """Rescan only low-matching samples, streaming R1 without storing sequences."""
    targets,threshold=_targets(catalog,[r for r in totals if r.get('stage')=='raw_R1'])
    result=dict(reviews=[],comparisons=[]);by_sample=defaultdict(list)
    for target in targets:by_sample[target['sample']].append(target)
    connection=sqlite3.connect(index) if index is not None else None
    try:
        for number,(sample,values) in enumerate(sorted(by_sample.items()),1):
            review=_evaluate(sample,values,_raw_records(items,root,catalog,sample,allowed_roots),catalog,'raw_R1',threshold,progress,connection)
            for key in result:result[key].extend(review[key])
            if progress:progress(number,len(by_sample),f'{sample}: Raw alternative group review complete')
        if connection is not None:connection.commit()
    finally:
        if connection is not None:connection.close()
    result['comparisons'].sort(key=lambda r:(r['sample'],r['unit'],-r['unique_percent'],r['tested_group']))
    return result
