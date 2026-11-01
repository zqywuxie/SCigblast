"""Explicit IGH constant-gene labels and SQL-backed CDR3 composition."""
from __future__ import annotations

import re

MAPPING_VERSION = 'igh-isotype-gene-labels-v1'
MAIN_ORDER = ('IgM', 'IgD', 'IgA', 'IgG', 'IgE')
AA = re.compile(r'^[ACDEFGHIKLMNPQRSTVWY]+$')


def classify(call):
    """Return (main class, subtype, state); never select the first candidate."""
    from .aggregate import calls
    genes = calls(call)
    if not genes:
        return '', '', 'unannotated'
    labels = []
    for gene in genes:
        match = re.fullmatch(r'IGH([MDAGE])([0-9]+(?:-[0-9]+)?)?', gene.upper())
        if match is None:
            return '', '', 'unclassified'
        labels.append(('Ig' + match[1], 'Ig' + match[1] + (match[2] or '')))
    main = {label[0] for label in labels}
    subtypes = {label[1] for label in labels}
    if len(main) != 1:
        return '', '', 'ambiguous'
    return next(iter(main)), next(iter(subtypes)) if len(subtypes) == 1 else '', 'classified'


def summarize(connection):
    """Keep distinct AA sequences on disk, including after subclass merging."""
    connection.row_factory = __import__('sqlite3').Row
    result = []
    groups = connection.execute("SELECT * FROM usage_groups WHERE chain='IGH' ORDER BY sample,id").fetchall()
    for group in groups:
        base = dict(group_id=group['id'], sample=group['sample'], chain='IGH',
                    reference_id=group['reference_id'], unit='AIRR rows', subset='retained')
        total = connection.execute('SELECT count(*) FROM usage_records WHERE group_id=? AND eligible=1', (group['id'],)).fetchone()[0]
        for level, column in (('main', 'isotype_main'), ('subclass', 'isotype_subclass')):
            usable = "group_id=? AND eligible=1 AND " + column + "<>''"
            rows = connection.execute('SELECT '+column+' AS label,count(*) AS count FROM usage_records WHERE '+usable+' GROUP BY '+column, (group['id'],)).fetchall()
            denominator = sum(row['count'] for row in rows)
            for row in rows:
                result.append(dict(base, level=level, metric='abundance', aa_field='', isotype=row['label'],
                    count=row['count'], denominator=denominator, percent=row['count']*100/denominator if denominator else None,
                    total_records=total, annotated_records=denominator, excluded_records=total-denominator,
                    valid_aa_records=None, global_unique_cdr3=None))
            fields = connection.execute('SELECT DISTINCT aa_field FROM usage_records WHERE group_id=? AND eligible=1 AND aa_field<>\'\' ORDER BY aa_field', (group['id'],)).fetchall()
            for field in fields:
                where = usable + " AND aa_field=? AND aa_valid=1"
                counts = connection.execute('SELECT '+column+' AS label,count(DISTINCT aa_value) AS count FROM usage_records WHERE '+where+' GROUP BY '+column, (group['id'],field[0])).fetchall()
                denominator = sum(row['count'] for row in counts)
                valid, global_unique = connection.execute('SELECT count(*),count(DISTINCT aa_value) FROM usage_records WHERE '+where, (group['id'],field[0])).fetchone()
                for row in counts:
                    result.append(dict(base, level=level, metric='unique_cdr3', aa_field=field[0], isotype=row['label'],
                        count=row['count'], denominator=denominator, percent=row['count']*100/denominator if denominator else None,
                        total_records=total, annotated_records=sum(r['count'] for r in rows), excluded_records=total-valid,
                        valid_aa_records=valid, global_unique_cdr3=global_unique))
                if not counts:
                    result.append(dict(base,level=level,metric='unique_cdr3',aa_field=field[0],isotype='',
                        count=0,denominator=0,percent=None,total_records=total,annotated_records=sum(r['count'] for r in rows),
                        excluded_records=total,valid_aa_records=0,global_unique_cdr3=0))
    return result
