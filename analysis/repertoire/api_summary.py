"""Bounded, version-keyed query summaries for immutable completed reports."""
from contextlib import closing
from functools import lru_cache

from .io import version
from .usage import connect


@lru_cache(maxsize=256)
def _cached(owner, path, signature, group_id, primer, segment, metric, aa_field, modern):
    with closing(connect(path)) as conn:
        if tuple(version(path))!=signature:raise ValueError('Statistics changed during query')
        result=calculate(conn,group_id,primer,segment,metric,aa_field,modern)
        if tuple(version(path))!=signature:raise ValueError('Statistics changed during query')
        return result


def summary(owner,path,group_id,primer,segment,metric,aa_field,modern):
    # Ownership is checked by the route before this function. Never key by a
    # user-visible sample label or share a scope between different owners.
    return dict(_cached(owner,str(path),tuple(version(path)),group_id,primer,segment,metric,aa_field,modern))


def calculate(conn,group_id,primer,segment,metric,aa_field,modern):
    result={r[0]:r[1] for r in conn.execute("SELECT assignment,sum(count) FROM usage_counts WHERE group_id=? AND primer=? AND segment=? AND assignment<>'ambiguous' GROUP BY assignment",(group_id,primer,segment))}
    result['ambiguous']=conn.execute('''SELECT count(DISTINCT r.id) FROM usage_records r JOIN usage_calls c ON c.record_id=r.id
        WHERE r.group_id=? AND r.eligible=1 AND c.segment=? AND c.assignment='ambiguous' '''+(' AND r.primer=?' if primer else ''),
        (group_id,segment,primer) if primer else (group_id,segment)).fetchone()[0]
    result['mapped']=conn.execute('SELECT count(*) FROM usage_records WHERE group_id=? AND eligible=1'+(' AND primer=?' if primer else ''),
        (group_id,primer) if primer else (group_id,)).fetchone()[0]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='composition_records'").fetchone():
        scope='r.group_id=? AND r.sample_record=?'+(' AND r.primer=?' if primer else '')
        args=(group_id,0,primer) if primer else (group_id,1)
        result['composition_copies']=conn.execute('SELECT coalesce(sum(r.weight),0) FROM composition_records r WHERE '+scope,args).fetchone()[0]
        result['ambiguous']=conn.execute('''SELECT coalesce(sum(r.weight),0) FROM composition_records r
            WHERE '''+scope+''' AND EXISTS(SELECT 1 FROM composition_calls c WHERE c.record_id=r.id
            AND c.segment=? AND c.assignment='ambiguous')''',(*args,segment)).fetchone()[0]
        result['expression_unit']='copy'
    denominator=conn.execute('SELECT max(denominator) FROM gene_usage WHERE group_id=? AND primer=? AND segment=?'+(" AND metric='expression'" if modern else ''),(group_id,primer,segment)).fetchone()
    result['denominator']=denominator[0] if denominator and denominator[0] is not None else 0
    if metric=='unique_cdr3':
        result={**result,**{key:0 for key in ('unique','ambiguous','unannotated')}}
        scope=(group_id,primer,segment,aa_field)
        result['unique']=conn.execute("SELECT coalesce(sum(count),0) FROM usage_aa_counts WHERE group_id=? AND primer=? AND segment=? AND aa_field=? AND assignment='unique'",scope).fetchone()[0]
        result.update({r[0]:r[1] for r in conn.execute('SELECT assignment,count FROM usage_aa_other_counts WHERE group_id=? AND primer=? AND segment=? AND aa_field=?',scope)})
        denominator=conn.execute('SELECT total FROM usage_aa_denominators WHERE group_id=? AND primer=? AND segment=? AND aa_field=?',scope).fetchone()
        result['denominator']=denominator[0] if denominator else 0
    result.update(metric=metric,aa_field=aa_field if metric=='unique_cdr3' else '')
    return result
