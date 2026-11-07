"""Sample matching composition with adjacent, explicitly suspected alternatives."""
from collections import Counter, defaultdict
import textwrap

from .matching import CATEGORIES, summarize_matching
from .palette import MATCH_COLORS
from .io import label_name
from .workbook_overview import sample_sort_key


def render(data, *, pyplot, save, stage='', target_sample=''):
    grouped = defaultdict(dict)
    for row in summarize_matching(data.get('matching_totals', [])):
        grouped[(row['sample'], row['unit'])][row['match_class']] = row
    if not any(r['count'] for values in grouped.values() for r in values.values()):
        return
    order = {sample: i for i, sample in enumerate(data.get('matching_sample_order') or [])}
    anchors={}
    for row in data.get('samples',[]):
        key=sample_sort_key(row['sample'],row['chain'])
        anchors[row['sample']]=min(anchors.get(row['sample'],key),key)
    keys = sorted(grouped, key=lambda key: (order.get(key[0], len(order)),anchors.get(key[0],sample_sort_key(key[0],'')),key))
    repeated = Counter(sample for sample, _ in keys)
    alternatives = defaultdict(list)
    for row in data.get('primer_group_comparisons', []):
        if row['status'] == 'suspected':
            alternatives[(row['sample'], row['unit'])].append(row)
    assignments = (data.get('catalog') or {}).get('assignments', {})
    labels_map = data.get('matching_sample_labels') or {}
    batches, batch = [], []
    for key in keys:
        sample, unit = key
        groups = sorted({p.get('group', '') for p in assignments.get(sample, []) if p.get('group')})
        original = dict(key=key, sample=labels_map.get(sample, sample), group=' / '.join(groups),
                        suspected=False, values={c: grouped[key][c]['percent'] for c in CATEGORIES})
        choices = alternatives[key]
        # Repeat the original on each page if one sample has many alternatives.
        for start in range(0, max(1, len(choices)), 11):
            block = [original] + [dict(key=key, sample=labels_map.get(sample, sample),
                group=r['tested_group'], suspected=True,
                values={c: r[c+'_percent'] for c in CATEGORIES}) for r in choices[start:start+11]]
            if batch and len(batch)+len(block)>12:
                batches.append(batch)
                batch = []
            batch.extend(block)
    if batch:
        batches.append(batch)
    for page, batch in enumerate(batches, 1):
        labels = []
        for row in batch:
            lines = [(row['group'] or 'Assigned')+('*' if row['suspected'] else '')]
            if repeated[row['key'][0]]>1:
                lines.append(row['key'][1])
            labels.append('\n'.join('\n'.join(textwrap.wrap(line, 14, break_on_hyphens=False)) for line in lines))
        max_lines = max(label.count('\n')+1 for label in labels)
        bottom_in = 1.15+max_lines*.16
        height = 3.65+bottom_in
        fig, ax = pyplot.subplots(figsize=(max(8.2, len(batch)*1.35+1.4), height))
        fig.subplots_adjust(left=.075, right=.985, top=1-.95/height, bottom=bottom_in/height)
        positions, x = [], 0
        for i, row in enumerate(batch):
            if i and row['key'] != batch[i-1]['key']:
                x += .35
            positions.append(x)
            x += 1
        bottom = [0.]*len(batch)
        for category, label in zip(CATEGORIES, ('Unique', 'Multiple', 'Unmatched')):
            values = [row['values'][category] or 0 for row in batch]
            ax.bar(positions, values, bottom=bottom, width=.66, color=MATCH_COLORS[category],
                   label=label, edgecolor='white', linewidth=.45)
            bottom = [b+v for b, v in zip(bottom, values)]
        for i, row in enumerate(batch):
            value = row['values']['unique']
            label = 'N/A' if value is None else f'{value:.4f}%' if 0<value<.1 else f'{value:.1f}%'
            ax.text(positions[i], value/2 if value is not None and value>=12 else 5,
                    label, ha='center', va='center', fontsize=9,
                    color='white' if value is not None and value>=12 else '#233343')
            if row['suspected']:
                from matplotlib.patches import Rectangle
                ax.add_patch(Rectangle((positions[i]-.36, 0), .72, 100,
                    fill=False, edgecolor='#B84A48', linewidth=1.1, clip_on=False))
        ax.set_ylim(0,100)
        center=(positions[0]+positions[-1])/2
        span=max(1.2,(positions[-1]-positions[0])/2+.7)
        ax.set_xlim(center-span,center+span)
        ax.set_xticks(positions,labels,fontsize=8.5)
        for label, row in zip(ax.get_xticklabels(),batch):
            if row['suspected']:
                label.set_color('#B84A48')
        ax.tick_params(axis='x',length=0,pad=9)
        # Put the sample once below its assigned and alternative primer groups.
        start=0
        for end in range(len(batch)):
            if end+1<len(batch) and batch[end+1]['key']==batch[start]['key']:continue
            center=(positions[start]+positions[end])/2
            width=max(18,min(36,(end-start+1)*18))
            sample='\n'.join(textwrap.wrap(batch[start]['sample'],width,break_on_hyphens=True))
            ax.text(center,-(.21+max_lines*.14)/(height-bottom_in-.95),sample,
                transform=ax.get_xaxis_transform(),ha='center',va='top',fontsize=8.5,color='#53616D',clip_on=False)
            start=end+1
        ax.set_yticks([0,25,50,75,100],['0%','25%','50%','75%','100%'])
        ax.spines['left'].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis='y',color='#E7EBEF',linewidth=.65)
        handles,legend_labels=ax.get_legend_handles_labels()
        if any(r['suspected'] for r in batch):
            from matplotlib.patches import Patch
            handles.append(Patch(facecolor='none',edgecolor='#B84A48',label='Suspected group (*)'))
            legend_labels.append('Suspected group (*)')
        ax.legend(handles,legend_labels,loc='lower center',bbox_to_anchor=(.5,1.035),ncol=len(handles),borderaxespad=0,handlelength=1.2)
        title='Primer matching composition · '+(target_sample or 'All samples')
        if stage:title+=' · '+{'raw_R1':'Raw','mapping':'Mapping reads'}.get(stage,stage)
        if len(batches)>1:
            title+=f' · {page}/{len(batches)}'
        fig.text(.5,1-.16/height,title,ha='center',va='top',fontsize=12,fontweight='bold')
        name=('primer_matching__'+label_name(target_sample) if target_sample else 'primer_matching_total')+('__'+stage if stage else '')+(f'_{page}' if page>1 else '')
        save(fig,name,title,sample=target_sample,kind='matching',preparation=stage)
