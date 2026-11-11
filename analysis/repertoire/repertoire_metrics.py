"""Published diversity values and the two B cell Metrics from the PDF template."""
from __future__ import annotations

from contextlib import closing
import json
import math
from pathlib import Path
import re

from .io import label_name, digest, version, safe_file, write_json
from .usage import connect

DIVERSITY = (
    ('D50', 'd50', '%', 1),
    ('Shannon_index', 'Shannon entropy', '', 1),
    ('Pielou_evenness', "Pielou's evenness", '', 1),
    ('Gini_index', 'Gini index', '', 1),
    ('Simpson_index', 'Simpson index', '', 1),
    ('Simpson_evenness', 'Simpson evenness', '', 1),
    ('Largest_clone_percent', 'Largest clone %', '%', 100),
)


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def label(gene):
    return {'IGHM': 'IgM', 'IGHD': 'IgD', 'IGHA': 'IgA', 'IGHE': 'IgE'}.get(gene, gene.replace('IGHG', 'IgG', 1))


def isotype_order(gene):
    """Keep the PDF's M, D, A, G, E order and sort numbered genes naturally."""
    return ({'IGHM':0,'IGHD':1,'IGHA':2,'IGHG':3,'IGHE':4}.get(gene[:4],9),
            tuple((1,int(part)) if part.isdigit() else (0,part) for part in re.split(r'(\d+)',gene)))


def build(data, index, root, *, cache_root=None):
    import os
    import hashlib
    from .private_cache import locked, prune_files
    from .performance import detail

    budget=max(0,int(os.environ.get('SCIGBLAST_REPORT_STATISTICS_CACHE_MB','1024')))*1024**2
    if cache_root is None or not budget:
        return _build(data,index,root)
    result=dict(diversity_summary=[],bcell_metrics=[],isotype_distribution=[])
    hits=0
    implementation=hashlib.sha256()
    for name in ('repertoire_metrics.py','postprocessed_usage.py','usage.py','isotype.py','aggregate.py'):
        implementation.update(Path(__file__).with_name(name).read_bytes())
    for group in data.get('usage',{}).get('groups',[]):
        composition=group.get('composition',{})
        if composition.get('status')!='complete':
            continue
        source=safe_file(composition['source'],root)
        records=[row for row in data.get('preprocessing_metrics',[]) if row.get('sample')==group['sample']]
        selected=dict(usage=dict(groups=[group]),preprocessing_metrics=records)
        key='metrics-'+digest([selected,version(source),implementation.hexdigest()])
        with locked(cache_root) as cache:
            try:
                receipt=json.loads(safe_file(cache/(key+'.json'),cache).read_text(encoding='utf-8'))
                values=receipt['data']
                if receipt['sha256']!=digest(values):
                    raise ValueError('Invalid sample metrics cache')
                hits+=1
            except (OSError,ValueError,KeyError,TypeError):
                values=_build(selected,index,root)
                write_json(cache/(key+'.json'),dict(data=values,sha256=digest(values)))
            prune_files(cache,budget)
        for name in result:
            result[name].extend(values[name])
    with detail('sample_metrics_cache',groups=len(data.get('usage',{}).get('groups',[])),cache_hits=hits):
        pass
    return result


def _build(data, index, root):
    diversity, bcell, distribution = [], [], []
    with closing(connect(index)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='composition_sources'").fetchone():
            return dict(diversity_summary=[], bcell_metrics=[], isotype_distribution=[])
        for group in data.get('usage', {}).get('groups', []):
            composition = group.get('composition', {})
            if composition.get('status') != 'complete': continue
            gid, sample, chain = group['group_id'], group['sample'], group['chain']
            source = Path(composition['source'])
            folder = next(parent for parent in source.parents if parent.name == 'junction_peps').parent
            datapoint = (folder / 'Datapoint.csv').relative_to(root).as_posix()
            records = [r for r in data.get('preprocessing_metrics', [])
                       if r.get('sample') == sample and Path(r.get('source', '')).as_posix() == datapoint]
            row = records[0] if len(records) == 1 else {}
            for key, title, unit, multiplier in DIVERSITY:
                value = number(row.get(chain + '_' + key))
                diversity.append(dict(sample=sample, chain=chain, group_id=gid, metric=title,
                    value=value * multiplier if value is not None else None, unit=unit,
                    status='complete' if value is not None else 'unavailable', source='published_06_Datapoint'))
            if chain != 'IGH': continue
            stats = {r['gene']: dict(r) for r in conn.execute('''SELECT c.gene,count(*) AS records,sum(r.weight) AS copies,
                  count(DISTINCT CASE WHEN r.aa_valid=1 THEN r.aa_value END) AS unique_cdr3
                FROM composition_records r JOIN composition_calls c ON c.record_id=r.id
                WHERE r.group_id=? AND r.sample_record=1 AND c.segment='C' AND c.assignment='unique'
                  AND r.isotype_main<>'' GROUP BY c.gene''', (gid,))}
            copy_total = sum(r['copies'] for r in stats.values())
            aa_total = sum(r['unique_cdr3'] for r in stats.values())
            for gene in sorted(stats, key=isotype_order):
                for metric, key, denominator in (('expression','copies',copy_total),('unique_cdr3','unique_cdr3',aa_total)):
                    value = stats[gene][key]
                    distribution.append(dict(sample=sample,chain=chain,group_id=gid,gene=gene,isotype=label(gene),
                        metric=metric,count=value,denominator=denominator,
                        percent=100*value/denominator if denominator else None))
            dm = [gene for gene in ('IGHD', 'IGHM') if gene in stats]
            groups = ([('IgD/M', dm)] if dm else []) + [(label(gene), [gene]) for gene in
                sorted(stats, key=lambda g: (0 if g.startswith('IGHA') else 2 if g.startswith('IGHE') else 1, g)) if gene not in dm]
            conn.execute('DROP TABLE IF EXISTS temp.report_bcell_memberships')
            conn.execute("""CREATE TEMP TABLE report_bcell_memberships AS
                SELECT DISTINCT r.aa_value,c.gene FROM composition_records r
                JOIN composition_calls c ON c.record_id=r.id
                WHERE r.group_id=? AND r.sample_record=1 AND r.aa_valid=1
                  AND c.segment='C' AND c.assignment='unique' AND r.isotype_main<>''
                  AND r.aa_value<>'' AND lower(r.aa_value) NOT IN ('nan','na','none','null')""",(gid,))
            for title, genes in groups:
                copies = sum(stats[gene]['copies'] for gene in genes)
                raw = [number(row.get(gene + '_ig_V_SHM_ratio')) for gene in genes]
                # Datapoint's legacy isotype column is mean(copy * SHM).
                # Multiply by its record count, then normalize by total copies.
                mean = sum(value * stats[gene]['records'] for gene, value in zip(genes, raw)) / copies * 100 if copies and all(value is not None for value in raw) else None
                if mean is not None and not 0 <= mean <= 100: mean = None
                marks = ','.join('?' for _ in genes)
                own, shared = conn.execute(f'''WITH presence AS (
                    SELECT aa_value,max(CASE WHEN gene IN ({marks}) THEN 1 ELSE 0 END) AS own,
                      max(CASE WHEN gene NOT IN ({marks}) THEN 1 ELSE 0 END) AS other
                    FROM report_bcell_memberships GROUP BY aa_value
                  ) SELECT coalesce(sum(own),0),coalesce(sum(own*other),0) FROM presence''',
                  (*genes, *genes)).fetchone()
                bcell.append(dict(sample=sample, chain=chain, group_id=gid, isotype=title,
                    mean_shm_percent=mean, shared_cdr3_percent=100 * shared / own if own else None,
                    unique_cdr3=own, shared_cdr3=shared, copies=copies,
                    status='complete' if mean is not None and own else 'partial'))
    return dict(diversity_summary=diversity, bcell_metrics=bcell, isotype_distribution=distribution)


def render(data, *, pyplot, save):
    groups = data.get('usage', {}).get('groups', [])
    for group in groups:
        gid, sample, chain = group['group_id'], group['sample'], group['chain']
        rows = [r for r in data.get('diversity_summary', []) if r['group_id'] == gid]
        stem = label_name(sample) + '__' + chain + '__' + gid[:8]
        if rows:
            fig, ax = pyplot.subplots(figsize=(8.6, 4.2)); ax.axis('off')
            cells = [[r['metric'], 'N/A' if r['value'] is None else f"{r['value']:.6g}" + ('%' if r['unit'] == '%' else '')] for r in rows]
            grid = ax.table(cellText=cells, colLabels=['Diversity index', 'Value'], loc='center', cellLoc='left', colWidths=[.67, .33])
            grid.auto_set_font_size(False); grid.set_fontsize(11); grid.scale(1, 1.7)
            for (i, j), cell in grid.get_celld().items():
                cell.set_edgecolor('white')
                cell.set_facecolor('#25485F' if i == 0 else '#EFF4F7' if i % 2 else '#FAFBFC')
                cell.get_text().set_color('white' if i == 0 else '#233343')
            title = sample + ' / ' + chain + ' · Diversity indexes'
            fig.suptitle(title, fontsize=12, fontweight='bold', y=.98)
            save(fig, stem + '__diversity', title, sample, chain, 'diversity',group_id=gid,metric_keys=[r['metric'] for r in rows])
        rows = [r for r in data.get('bcell_metrics', []) if r['group_id'] == gid]
        if rows:
            height = max(3.8, .40 * len(rows) + 1.8)
            fig, axes = pyplot.subplots(1, 2, figsize=(11, height))
            fig.subplots_adjust(left=.10, right=.97, bottom=.16, top=.76, wspace=.45)
            for ax, key, title, color in zip(axes, ('mean_shm_percent', 'shared_cdr3_percent'),
                ('Mean SHM', 'CDR3s observed as other isotype'), ('#4C72B0', '#D57856')):
                values = [r[key] for r in rows]
                ax.barh(range(len(rows)), [v or 0 for v in values], color=color, height=.6)
                ax.set_yticks(range(len(rows)), [r['isotype'] for r in rows]); ax.invert_yaxis()
                ax.set_xlim(0, max([v or 0 for v in values] + [1]) * 1.32)
                ax.set_xlabel('%'); ax.set_title(title, fontsize=10, pad=14)
                for i, value in enumerate(values):
                    ax.annotate('N/A' if value is None else f'{value:.2f}%', (value or 0, i), xytext=(5, 0), textcoords='offset points', va='center', fontsize=9)
                ax.spines[['top', 'right', 'left']].set_visible(False); ax.tick_params(axis='y', length=0)
                ax.grid(axis='x', color='#E6EBEF', linewidth=.6); ax.set_axisbelow(True)
            title = sample + ' / IGH · B cell Metrics'
            fig.suptitle(title, fontsize=12, fontweight='bold', y=.98)
            save(fig, stem + '__bcell_metrics', title, sample, chain, 'bcell_metrics',group_id=gid)
    from .render_metrics import render_comparisons, render_isotype_distribution
    render_comparisons(data, pyplot=pyplot, save=save)
    render_isotype_distribution(data, pyplot=pyplot, save=save)
