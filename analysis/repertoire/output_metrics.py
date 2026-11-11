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


def render(rows, *, pyplot, save, inputs=()):
    """Three aligned, full-width metrics with shared sample pages."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    from .workbook_overview import sample_sort_key
    import textwrap
    ordered=sorted(rows,key=lambda r:sample_sort_key(r['sample'],r['chain']))
    names=list(dict.fromkeys(r['sample'] for r in ordered))
    colors=dict(zip(('IGH','IGK','IGL','TRA','TRB','TRD','TRG'),
                    ('#315D8B','#419A8B','#CE8351','#8675A9','#A09552','#598CA7','#9B667D')))
    shared={r['sample']:r.get('total_reads') for r in inputs}
    for chain in ['',*sorted({r['chain'] for r in ordered},key=lambda c:sample_sort_key('',c))]:
        selected_rows=[r for r in ordered if not chain or r['chain']==chain]
        sample_names=[s for s in names if any(r['sample']==s for r in selected_rows)]
        pages=(len(sample_names)+5)//6
        for start in range(0,len(sample_names),6):
            batch=sample_names[start:start+6]
            chains=[c for c in colors if any(r['chain']==c and r['sample'] in batch for r in selected_rows)]
            fig,axes=pyplot.subplots(3,1,figsize=(max(9,len(batch)*1.5),11.5))
            fig.subplots_adjust(left=.10,right=.97,top=.92,bottom=.06,hspace=.65)
            for ax,(key,title,base) in zip(axes,METRICS):
                series=[('',base)] if key=='total_reads' else [(c,colors[c]) for c in chains]
                width=.72/max(1,len(series))
                for index,(current,color) in enumerate(series):
                    values=[]
                    for sample in batch:
                        matches=[r for r in selected_rows if r['sample']==sample and (not current or r['chain']==current)]
                        if key=='total_reads' and not chain:
                            fallback={r[key] for r in matches}
                            value=shared.get(sample,next(iter(fallback)) if len(fallback)==1 else None)
                        else:value=matches[0][key] if matches else None
                        values.append(value)
                    positions=[j+(index-(len(series)-1)/2)*width for j in range(len(batch))]
                    ax.bar(positions,[v or 0 for v in values],width=width*.90,color=color,label=current or title,zorder=3)
                    for sample,x,value in zip(batch,positions,values):
                        present=key=='total_reads' or any(r['sample']==sample and r['chain']==current for r in selected_rows)
                        if value is None and present:
                            ax.annotate('N/A',(x,0),xytext=(0,4),textcoords='offset points',ha='center',fontsize=8,color='#647586')
                        elif value is not None and len(series)<=2:
                            ax.annotate(f'{value:,}',(x,value),xytext=(0,4),textcoords='offset points',ha='center',fontsize=8)
                ax.set_title(title,fontsize=12,fontweight='bold',pad=27)
                ax.set_xticks(range(len(batch)),['\n'.join(textwrap.wrap(s,22,break_on_hyphens=False)) for s in batch],fontsize=9)
                ax.yaxis.set_major_formatter(FuncFormatter(lambda n,_:f'{n:,.0f}'))
                ax.yaxis.set_major_locator(MaxNLocator(integer=True,nbins=5))
                ax.set_ylim(0,max(ax.get_ylim()[1]*1.15,1));ax.set_axisbelow(True)
                ax.grid(axis='y',color='#E7EBEF',linewidth=.65);ax.spines[['top','right']].set_visible(False)
                if len(series)>1:ax.legend(loc='lower center',bbox_to_anchor=(.5,1.01),ncol=len(series),fontsize=8,frameon=False)
            page=start//6+1
            title='Sample output comparison · '+(chain or 'All samples')+(f' · {page}/{pages}' if pages>1 else '')
            fig.suptitle(title,fontsize=13,fontweight='bold',y=.98)
            name=(chain or 'all_samples')+'__sample_output_comparison'+(f'_{page}' if pages>1 else '')
            save(fig,name,title,chain=chain,kind='sample_outputs')
