"""Sample outputs from Merged receipts and published AIRR preprocessing metrics."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from .io import safe_file, table

METRICS = (
    ('total_reads', 'Total Reads', '#567FB5'),
    ('total_receptor_rna', 'Total Receptor RNA', '#419A8B'),
    ('total_unique_cdr3s', 'Total Unique CDR3s', '#D57856'),
)


def count(value):
    """Missing/invalid counts are unknown; genuine zero is retained."""
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number >= 0 and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def published(folder, root, selected):
    """Check the Base adapter's completion receipt without rerunning models."""
    marker = json.loads(safe_file(folder/'.preprocessing_state/base_completion.json', root).read_text(encoding='utf-8'))
    state = json.loads(safe_file(folder/'.preprocessing_state/run.json', root).read_text(encoding='utf-8'))
    if state.get('status') != 'DONE':return False
    outputs = marker.get('outputs', {})
    for path in (folder/'Datapoint.csv', folder/'processing_manifest.csv', folder/'.preprocessing_state/run.json'):
        stat = safe_file(path, root).stat()
        if outputs.get(str(path)) != [stat.st_size, stat.st_mtime_ns]:return False
    inputs = {r['path']: r for r in marker.get('signature', {}).get('inputs', [])}
    for path in selected:
        receipt = inputs.get(path)
        source = safe_file(path, root)
        stat = source.stat()
        if not receipt or [receipt.get('size'), receipt.get('mtime_ns')] != [stat.st_size, stat.st_mtime_ns]:return False
        with source.open('rb') as handle:
            if hashlib.sha256(handle.read(65536)).hexdigest() != receipt.get('head_sha256'):return False
    return True


def build(data, items, root):
    """One row per selected sample/chain; never add per-gene unique counts."""
    root = Path(root).resolve()
    grouped = defaultdict(list)
    for item in items:grouped[item['sample_label'], item['chain']].append(item)
    result = []
    for (sample, chain), selected in sorted(grouped.items()):
        row = dict(sample=sample, chain=chain, total_reads=None, total_receptor_rna=None,
                   total_unique_cdr3s=None, status='unavailable', note='', source='')
        reasons = []
        pairs = {item.get('pair', '') for item in selected}
        sample_pairs = {item.get('pair', '') for item in items if item['sample_label'] == sample}
        if pairs == sample_pairs:
            counts = {count(r.get('count')) for r in data.get('stage_volumes', [])
                      if r['sample'] == sample and r['stage'] == 'merged' and not r.get('chain')}
            if len(counts) == 1:row['total_reads'] = next(iter(counts))
        else:
            counts = [{count(r.get('count')) for r in data.get('stages', [])
                       if r['sample'] == sample and r['pair'] == pair and r['stage'] == 'merge'
                       and r['metric'] == 'merged_sequences' and r['unit'] == 'queries' and not r.get('chain')}
                      for pair in pairs]
            if counts and all(len(c) == 1 and None not in c for c in counts):
                row['total_reads'] = sum(next(iter(c)) for c in counts)
        if row['total_reads'] is None:reasons.append('Merged count unavailable')
        datasets={str(Path(i['summary_path']).parent) for i in selected if i.get('summary_path')}
        if len(datasets)>1:
            row['total_reads']=None
            reasons.append('Multiple input batches; select one batch')
        sources = set()
        for item in selected:
            if not item.get('summary_path'):continue
            parent = Path(item['summary_path']).parent
            if parent.parent.name == '05.igblastn_out':
                sources.add((parent.parent.parent/'06.preprocessing'/parent.name/'Datapoint.csv').relative_to(root).as_posix())
            elif parent.name == '05.igblastn_out':
                sources.add((parent.parent/'06.preprocessing'/'Datapoint.csv').relative_to(root).as_posix())
        records = [r for r in data.get('preprocessing_metrics', [])
                   if r.get('sample') == sample and Path(r.get('source', '')).as_posix() in sources]
        if len(records) != 1:
            reasons.append('AIRR preprocessing unavailable' if not records else 'Multiple preprocessing batches; select one batch')
        else:
            record = records[0]
            folder = (root/record['source']).parent
            expected = {str(Path(i['path']).resolve()) for i in selected}
            try:
                manifests = [r for r in table(safe_file(folder/'processing_manifest.csv', root))
                             if r.get('sample') == sample and r.get('batch', '') == str(record.get('batch', ''))]
                valid = len(manifests) == 1 and manifests[0].get('status') == 'OK'
                if valid:
                    manifest = manifests[0]
                    paths = (manifest.get('bcr_path', '') if chain.startswith('IG') else manifest.get('tcr_path', '')).split(';')
                    actual = {str(Path(p).resolve()) for p in paths if p and Path(p).stem.upper() == chain}
                    valid = actual == expected and published(folder, root, expected)
                if not valid:reasons.append('Preprocessing source/scope unverified')
                else:
                    row['source'] = Path(record['source']).as_posix()
                    row['total_receptor_rna'] = count(record.get(chain+'_umi_counts'))
                    row['total_unique_cdr3s'] = count(record.get(chain+'_uCDR3'))
                    if any(row[key] is None for key, _, _ in METRICS[1:]):reasons.append('AIRR metric missing or invalid')
            except (OSError, ValueError, KeyError, TypeError):reasons.append('Preprocessing source/scope unverified')
        row['status'] = 'complete' if not reasons else 'partial' if any(row[k] is not None for k, _, _ in METRICS) else 'unavailable'
        row['note'] = '; '.join(reasons)
        result.append(row)
    return result


def render(rows, *, pyplot, save):
    """All sample/chain contexts together, followed by the per-chain views."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    from .workbook_overview import sample_sort_key
    import textwrap
    ordered=sorted(rows,key=lambda r:sample_sort_key(r['sample'],r['chain']))
    pages=(len(ordered)+11)//12
    batches=[('',ordered[start:start+12],start//12+1,pages) for start in range(0,len(ordered),12)]
    batches.extend((chain,[r for r in ordered if r['chain']==chain],1,1) for chain in sorted({r['chain'] for r in rows}))
    for chain,batch,page,total in batches:
        width = max(9, len(batch)*1.75+1.5)
        fig, ax = pyplot.subplots(figsize=(width, 5.3))
        fig.subplots_adjust(left=.85/width, right=1-.3/width, bottom=.24, top=.83)
        maximum = max([r[k] or 0 for r in batch for k, _, _ in METRICS]+[1])
        for index, (key, label, color) in enumerate(METRICS):
            positions = [i+(index-1)*.25 for i in range(len(batch))]
            values = [r[key] for r in batch]
            ax.bar(positions, [v or 0 for v in values], width=.22, color=color, label=label, zorder=3)
            for x, value in zip(positions, values):
                ax.annotate('N/A' if value is None else f'{value:,}', (x, value or 0),
                            xytext=(0, 5), textcoords='offset points', ha='center', va='bottom',
                            fontsize=8, color='#647586' if value is None else '#233343')
        ax.set_ylim(0, maximum*1.20)
        ax.set_xticks(range(len(batch)), ['\n'.join(textwrap.wrap(r['sample'], 20, break_on_hyphens=False))+('' if chain else '\n'+r['chain']) for r in batch])
        ax.set_ylabel('Count')
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda n, _: f'{n:,.0f}'))
        ax.set_axisbelow(True)
        ax.grid(axis='y', color='#E7EBEF', linewidth=.65)
        ax.tick_params(axis='x', length=0, pad=12)
        ax.spines[['top', 'right']].set_visible(False)
        ax.legend(loc='lower center', bbox_to_anchor=(.5, 1.03), ncol=3, frameon=False)
        title = 'Sample output comparison · '+(chain or 'All samples')
        if total>1:title+=f' · {page}/{total}'
        fig.text(.5, .96, title, ha='center', va='top', fontsize=12, fontweight='bold')
        name=(chain or 'all_samples')+'__sample_output_comparison'+(f'_{page}' if total>1 else '')
        save(fig, name, title, chain=chain, kind='sample_outputs')
