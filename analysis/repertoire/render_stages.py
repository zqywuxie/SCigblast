"""Compact all-sample counts; chain tasks never inflate shared input totals."""
from __future__ import annotations

from collections import defaultdict
import textwrap
from .palette import STAGE_COLORS,STAGE_INK,mix,text_on


def render_overview(volumes, *, pyplot, save):
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    grouped=defaultdict(list)
    for row in volumes:grouped[row['sample']].append(row)
    samples=sorted(grouped)
    if not samples:return
    def value(sample,stage,chain=''):
        counts={r['count'] for r in grouped[sample] if r['stage']==stage and r['chain']==chain}
        return next(iter(counts)) if len(counts)==1 else None
    def same(left,right):
        # Unknown counts cannot justify hiding a processing stage.
        return all(value(s,left) is not None and value(s,left)==value(s,right) for s in samples)
    def change(sample,stage,chain=''):
        count=value(sample,stage,chain)
        if count is None:return ''
        if stage in {'fastp','clean'}:
            previous=value(sample,'raw' if stage=='fastp' else 'fastp')
            if previous is None or previous==0:return ''
            delta=(count-previous)*100/previous
            return 'No change' if delta==0 else f'{delta:+.1f}%'
        if stage=='merged':
            denominator=value(sample,'merge_input')
            return f'Merge {count*100/denominator:.1f}%' if denominator else ''
        if stage=='mapping_input':
            denominator=value(sample,'merged')
            return f'Input {count*100/denominator:.1f}%' if denominator else ''
        if stage=='mapping_output':
            denominator=value(sample,'mapping_input',chain)
            return f'Retained {count*100/denominator:.1f}%' if denominator else ''
        return ''
    chains=sorted({r['chain'] for r in volumes if r['chain'] and r['stage'] in {'mapping_input','mapping_output'}})
    columns=[('raw','','Raw reads\n(R1 + R2)'),('fastp','','Clean reads\n(R1 + R2)')]
    if same('fastp','clean'):
        columns[-1]=('fastp','','Clean reads\n(Fastp / Header)')
    else:columns.append(('clean','','Header-cleaned\nreads'))
    columns.append(('merged','','Merged\nreads'))
    upstream=len(columns)
    for chain in chains:
        selected=[s for s in samples if any(r['chain']==chain for r in grouped[s])]
        shared=all(value(s,'mapping_input',chain) is not None and value(s,'mapping_input',chain)==value(s,'merged') for s in selected)
        if not shared:columns.append(('mapping_input',chain,chain+' input\nreads'))
        columns.append(('mapping_output',chain,chain+'\nRetained reads'))
    maxima=[max([value(s,stage,chain) or 0 for s in samples]+[1]) for stage,chain,_ in columns]
    colors=[STAGE_COLORS[stage] for stage,_,_ in columns]
    legend_stages=list(dict.fromkeys(stage for stage,_,_ in columns))
    legend_labels={'raw':'Raw','fastp':'Clean','clean':'Header','merged':'Merged',
                   'mapping_input':'Mapping input','mapping_output':'Retained'}
    pages=(len(samples)+11)//12
    for start in range(0,len(samples),12):
        batch=samples[start:start+12]
        labels=['\n'.join(textwrap.wrap(s,22,break_on_hyphens=False)) for s in batch]
        row_height=max(.51,max(name.count('\n')+1 for name in labels)*.20+.24)
        left=max(1.7,max(len(line) for label in labels for line in label.split('\n'))*.075+.35)
        width=max(10,left+len(columns)*1.20+.4)
        height=max(4.0,len(batch)*row_height+1.9)
        fig,ax=pyplot.subplots(figsize=(width,height))
        fig.subplots_adjust(left=left/width,right=1-.25/width,bottom=.25/height,top=1-1.6/height)
        numbers=np.zeros((len(batch),len(columns)))
        for i,sample in enumerate(batch):
            for j,(stage,chain,_) in enumerate(columns):
                count=value(sample,stage,chain)
                numbers[i,j]=count/maxima[j] if count is not None else 0
        backgrounds=[[mix(colors[j],.94*(1-numbers[i,j])) for j in range(len(columns))] for i in range(len(batch))]
        from matplotlib.colors import to_rgba
        ax.imshow([[to_rgba(color) for color in row] for row in backgrounds],aspect='auto',interpolation='nearest')
        for i,sample in enumerate(batch):
            selected={r['chain'] for r in grouped[sample] if r['chain']}
            for j,(stage,chain,_) in enumerate(columns):
                count=value(sample,stage,chain)
                unused=chain and chain not in selected
                if unused:ax.add_patch(pyplot.Rectangle((j-.5,i-.5),1,1,facecolor='#F7F8FA',edgecolor='none'))
                label='' if unused else '—' if count is None else f'{count:,}'
                note='' if unused else change(sample,stage,chain)
                color='#87929D' if unused or count is None else text_on(backgrounds[i][j])
                ax.text(j,i-.13 if note else i,label,ha='center',va='center',fontsize=8.5 if unused else 9,color=color)
                if note:ax.text(j,i+.21,note,ha='center',va='center',fontsize=8,color=color)
        ax.set_xticks(range(len(columns)),[label for _,_,label in columns],fontsize=9)
        for label,(stage,_,_) in zip(ax.get_xticklabels(),columns):
            label.set_color(STAGE_INK[stage]);label.set_fontweight('bold')
        ax.xaxis.tick_top();ax.tick_params(axis='x',length=0,pad=10)
        ax.set_yticks(range(len(batch)),labels,fontsize=9.5);ax.tick_params(axis='y',length=0,pad=12)
        ax.set_xticks(np.arange(-.5,len(columns),1),minor=True)
        ax.set_yticks(np.arange(-.5,len(batch),1),minor=True)
        ax.grid(which='minor',color='white',linewidth=2);ax.tick_params(which='minor',length=0)
        if chains:ax.axvline(upstream-.5,color='white',lw=6)
        for spine in ax.spines.values():spine.set_visible(False)
        title='Read counts across samples'+(f' · {start//12+1}/{pages}' if pages>1 else '')
        fig.text(.5,1-.16/height,title,ha='center',va='top',fontsize=13,fontweight='bold')
        # Stage hues are fixed; each centered strip shows its own quantity scale.
        keys=[];span=1.25;total=span*len(legend_stages)
        for index,stage in enumerate(legend_stages):
            center=.5+(index*span-total/2+span/2)/width
            key=fig.add_axes([center-.50/width,1-.72/height,1/width,.09/height])
            cmap=LinearSegmentedColormap.from_list('quantity_'+stage,[mix(STAGE_COLORS[stage],.94),STAGE_COLORS[stage]])
            key.imshow(np.linspace(0,1,128)[None,:],cmap=cmap,aspect='auto',vmin=0,vmax=1)
            key.set_axis_off();keys.append(key)
            fig.text(center,1-.49/height,legend_labels[stage],ha='center',va='center',fontsize=8,color=STAGE_INK[stage])
        legend='Lighter → lower count; darker → higher count within each column'
        if chains:legend+='   |   Blank: unselected chain'
        fig.text(.5,1-.91/height,legend,ha='center',va='center',fontsize=8.5,color='#53616D')
        name='all_samples__stages'+(f'_{start//12+1:02}' if pages>1 else '')
        save(fig,name,title,kind='stage_overview',exclude_axes=tuple(keys))
