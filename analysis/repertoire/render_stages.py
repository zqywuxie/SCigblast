"""One sample matrix with stage groups and paired R1/R2 subcolumns."""
from __future__ import annotations

from collections import defaultdict
import textwrap
from .palette import STAGE_COLORS,STAGE_INK,mix,text_on
from .io import label_name


def render_overview(volumes, *, pyplot, save, target_sample=''):
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap,to_rgba

    grouped=defaultdict(list)
    for row in volumes:grouped[row['sample']].append(row)
    samples=sorted(grouped)
    if not samples:return
    def value(sample,stage,chain=''):
        counts={r['count'] for r in grouped[sample] if r['stage']==stage and r['chain']==chain}
        return next(iter(counts)) if len(counts)==1 else None
    def change(sample,stage,chain=''):
        count=value(sample,stage,chain)
        if count is None:return ''
        if stage in {'fastp_r1','fastp_r2'}:
            before=value(sample,stage.replace('fastp_','raw_'))
            if before is None or before==0:return ''
            delta=(count-before)*100/before
            return 'No change' if delta==0 else f'{delta:+.1f}%'
        previous={'merged':('merge_input','Merge'),'mapping_input':('merged','Input'),
                  'mapping_output':('mapping_input','Retained')}.get(stage)
        if previous:
            before=value(sample,previous[0],chain if stage=='mapping_output' else '')
            if before:return f'{previous[1]} {count*100/before:.1f}%'
        return ''

    # Each processing stage shares a color, scale and header across its mates.
    columns=[('raw_r1','','R1','raw'),('raw_r2','','R2','raw'),
             ('fastp_r1','','R1','fastp'),('fastp_r2','','R2','fastp'),('merged','','reads','merged')]
    chains=sorted({r['chain'] for r in volumes if r['chain'] and r['stage'] in {'mapping_input','mapping_output'}})
    for chain in chains:
        selected=[s for s in samples if any(r['chain']==chain for r in grouped[s])]
        shared=all(value(s,'mapping_input',chain) is not None and value(s,'mapping_input',chain)==value(s,'merged') for s in selected)
        if not shared:columns.append(('mapping_input',chain,chain,'mapping_input'))
    columns.extend(('mapping_output',chain,chain,'mapping_output') for chain in chains)
    blocks=[]
    for j,(_,_,_,family) in enumerate(columns):
        if blocks and blocks[-1][0]==family:blocks[-1][2]=j
        else:blocks.append([family,j,j])
    maxima={family:max([value(s,stage,chain) or 0 for s in samples
                        for stage,chain,_,group in columns if group==family]+[1]) for family,_,_ in blocks}
    colors=[STAGE_COLORS[family] for _,_,_,family in columns]
    titles={'raw':'Raw reads','fastp':'Clean reads (Fastp)','merged':'Merged',
            'mapping_input':'Mapping input','mapping_output':'Retained reads'}
    pages=(len(samples)+11)//12
    for start in range(0,len(samples),12):
        batch=samples[start:start+12]
        labels=['\n'.join(textwrap.wrap(s,22,break_on_hyphens=False)) for s in batch]
        row_height=max(.52,max(name.count('\n')+1 for name in labels)*.20+.24)
        left=max(1.7,max(len(line) for label in labels for line in label.split('\n'))*.075+.35)
        width=max(10,left+len(columns)*1.12+.4)
        height=max(2.65,len(batch)*row_height+1.45)
        fig,ax=pyplot.subplots(figsize=(width,height))
        fig.subplots_adjust(left=left/width,right=1-.25/width,bottom=.22/height,top=1-1.05/height)
        backgrounds=[[mix(colors[j],.94*(1-(value(sample,stage,chain) or 0)/maxima[family]))
                      for j,(stage,chain,_,family) in enumerate(columns)] for sample in batch]
        ax.imshow([[to_rgba(color) for color in row] for row in backgrounds],aspect='auto',interpolation='nearest')
        for i,sample in enumerate(batch):
            selected={r['chain'] for r in grouped[sample] if r['chain']}
            for j,(stage,chain,_,_) in enumerate(columns):
                count=value(sample,stage,chain);unused=chain and chain not in selected
                if unused:ax.add_patch(pyplot.Rectangle((j-.5,i-.5),1,1,facecolor='#F7F8FA',edgecolor='none'))
                label='' if unused else 'N/A' if count is None else f'{count:,}'
                note='' if unused else change(sample,stage,chain)
                color='#87929D' if unused or count is None else text_on(backgrounds[i][j])
                ax.text(j,i-.12 if note else i,label,ha='center',va='center',fontsize=9,color=color)
                if note:ax.text(j,i+.22,note,ha='center',va='center',fontsize=8,color=color)
        ax.set_xticks(range(len(columns)),[label for _,_,label,_ in columns],fontsize=9)
        for label,(_,_,_,family) in zip(ax.get_xticklabels(),columns):label.set_color(STAGE_INK[family])
        ax.xaxis.tick_top();ax.tick_params(axis='x',length=0,pad=8)
        ax.set_yticks(range(len(batch)),labels,fontsize=9.5);ax.tick_params(axis='y',length=0,pad=12)
        ax.set_xticks(np.arange(-.5,len(columns),1),minor=True);ax.set_yticks(np.arange(-.5,len(batch),1),minor=True)
        ax.grid(which='minor',color='white',linewidth=1.5);ax.tick_params(which='minor',length=0)
        for _,_,end in blocks[:-1]:ax.axvline(end+.5,color='white',lw=6)
        for spine in ax.spines.values():spine.set_visible(False)
        title=('Read counts · '+target_sample if target_sample else 'Read counts across samples')+(f' · {start//12+1}/{pages}' if pages>1 else '')
        fig.text(.5,1-.15/height,title,ha='center',va='top',fontsize=13,fontweight='bold')
        position=ax.get_position();column_width=position.width/len(columns);keys=[]
        for family,first,last in blocks:
            x=position.x0+first*column_width;span=(last-first+1)*column_width;center=x+span/2
            fig.text(center,1-.48/height,titles[family],ha='center',va='center',fontsize=9,fontweight='bold',color=STAGE_INK[family])
            key=fig.add_axes([x+.05/width,1-.69/height,span-.10/width,.07/height])
            cmap=LinearSegmentedColormap.from_list('quantity_'+family,[mix(STAGE_COLORS[family],.94),STAGE_COLORS[family]])
            key.imshow(np.linspace(0,1,128)[None,:],cmap=cmap,aspect='auto',vmin=0,vmax=1);key.set_axis_off();keys.append(key)
        name=(label_name(target_sample)+'__stages_overview' if target_sample else 'all_samples__stages')+(f'_{start//12+1:02}' if pages>1 else '')
        save(fig,name,title,sample=target_sample,kind='stage_overview',exclude_axes=tuple(keys))
