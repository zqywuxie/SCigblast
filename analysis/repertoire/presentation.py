"""Public, sample-level presentation scopes; no cross-chain composition pooling."""
from collections import defaultdict

from .io import digest
from .output_metrics import count


def shared_inputs(data):
    grouped = defaultdict(list)
    for row in data.get('stage_volumes', []):
        if row.get('stage') == 'merged' and not row.get('chain'):
            grouped[row['sample']].append(row)
    result = []
    for sample, rows in sorted(grouped.items()):
        values = {count(row.get('count')) for row in rows}
        scopes = {row.get('pair_scope', '') for row in rows}
        verified = len(values) == len(scopes) == 1 and None not in values
        result.append(dict(sample=sample, total_reads=next(iter(values)) if verified else None,
                           input_scope=digest([sample, sorted(scopes)])[:16],
                           status='complete' if verified else 'unavailable'))
    return result


def annotation_coverage(connection):
    """Read totals once, reuse each segment's already-computed denominator."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='composition_records'").fetchone():
        return []
    totals = {(r[0], r[1]): r[2] for r in connection.execute('''
        SELECT group_id,CASE WHEN sample_record=1 THEN '' ELSE primer END,sum(weight)
        FROM composition_records WHERE eligible=1
        GROUP BY group_id,CASE WHEN sample_record=1 THEN '' ELSE primer END''')}
    rows = []
    for gid, sample, chain, primer, segment, denominator in connection.execute('''
        SELECT group_id,sample,chain,primer,segment,max(denominator)
        FROM gene_usage WHERE metric='expression'
        GROUP BY group_id,sample,chain,primer,segment'''):
        total = totals.get((gid, primer))
        if total is not None:
            rows.append(dict(group_id=gid,sample=sample,chain=chain,primer=primer,segment=segment,
                             total_count=total,classified_count=denominator,
                             coverage_percent=100*denominator/total if total else None,unit='copy'))
    return rows
