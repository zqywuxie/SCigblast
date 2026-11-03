"""Comparison figures and the migrated per-sample PDF isotype bar layout."""
from __future__ import annotations

from collections import Counter
import math
import textwrap

from .io import label_name
from .palette import text_on

CHAIN_ORDER = ('IGH','IGK','IGL','TRA','TRB','TRD','TRG')
CHAIN_COLORS = {'IGH':'#315D8B','IGK':'#4F8F91','IGL':'#4F8F91'}
CORE_METRICS = ('Shannon entropy', "Pielou's evenness", 'Gini index', 'Simpson index', 'd50', 'Largest clone %')


def wrap(value, width=20):
    return '\n'.join(textwrap.wrap(str(value),width,break_on_hyphens=False))


def group_order(group):
    return (CHAIN_ORDER.index(group['chain']) if group['chain'] in CHAIN_ORDER else 99,
            group['sample'],group['group_id'])


def sample_labels(groups, *, chain=False):
    repeats=Counter((g['sample'],g['chain']) for g in groups)
    return [g['sample']+(' / '+g['chain'] if chain else '')+
            (' / '+g['group_id'][:6] if repeats[g['sample'],g['chain']]>1 else '') for g in groups]


def render_comparisons(data, *, pyplot, save):
    """Use actual units per panel; no common normalization across indices."""
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap, to_hex
    from matplotlib.ticker import PercentFormatter
    by_group={g['group_id']:g for g in data.get('usage',{}).get('groups',[])}
    diversity=data.get('diversity_summary',[])
    groups=sorted([g for gid,g in by_group.items() if any(r['group_id']==gid for r in diversity)],key=group_order)
    values={(r['group_id'],r['metric']):r['value'] for r in diversity}
    def bars(ax, selected, metric):
        raw=[values.get((g['group_id'],metric)) for g in selected]
        colors=[CHAIN_COLORS.get(g['chain'],'#8193A5') for g in selected]
        ax.barh(range(len(selected)),[v if v is not None else 0 for v in raw],height=.62,color=colors)
        ax.set_yticks(range(len(selected)),[wrap(s,24) for s in sample_labels(selected,chain=True)],fontsize=8)
        ax.invert_yaxis();ax.set_title(metric,fontsize=10,pad=12)
        maximum=max([v for v in raw if v is not None]+[0])
        bounded=metric in {"Pielou's evenness",'Gini index','Simpson index','Simpson evenness'} and maximum<=1
        ax.set_xlim(0,1 if bounded else (maximum or 1)*1.28)
        ax.spines[['top','right','left']].set_visible(False);ax.tick_params(axis='y',length=0)
        ax.grid(axis='x',color='#E1E8EE',linewidth=.6);ax.set_axisbelow(True)
        percentage=metric in {'d50','Largest clone %'}
        if percentage:ax.xaxis.set_major_formatter(PercentFormatter(xmax=100,decimals=0))
        for i,value in enumerate(raw):
            text='N/A' if value is None else f'{value:.2f}%' if percentage else f'{value:.6f}' if metric=='Simpson index' else f'{value:.4f}'
            inside=bounded and value is not None and value>.85
            ax.annotate(text,(value or 0,i),xytext=(-4 if inside else 4,0),textcoords='offset points',
                        ha='right' if inside else 'left',va='center',fontsize=7.5,
                        color=text_on(colors[i]) if inside else '#233343')
    for start in range(0,len(groups),12):
        batch=groups[start:start+12]
        height=max(6.8,len(batch)*.56+3)
        fig,axes=pyplot.subplots(2,3,figsize=(18,height))
        fig.subplots_adjust(left=.13,right=.98,top=.88,bottom=.08,wspace=.72,hspace=.45)
        for ax,metric in zip(axes.flat,CORE_METRICS):bars(ax,batch,metric)
        title='Diversity indexes · All samples'
        if len(groups)>12:title+=f' · {start//12+1}/{math.ceil(len(groups)/12)}'
        fig.suptitle(title,fontsize=14,fontweight='bold',y=.98)
        save(fig,f'diversity_comparison_{start//12+1:02}',title,kind='diversity_comparison')
        if any(values.get((g['group_id'],'Simpson evenness')) is not None for g in batch):
            fig,ax=pyplot.subplots(figsize=(10,max(3.5,len(batch)*.35+1.6)))
            fig.subplots_adjust(left=.30,right=.95,top=.82,bottom=.15)
            bars(ax,batch,'Simpson evenness')
            title='Simpson evenness · All samples'
            save(fig,f'simpson_evenness_comparison_{start//12+1:02}',title,kind='diversity_comparison')
    metrics=data.get('bcell_metrics',[])
    groups=sorted([g for gid,g in by_group.items() if any(r['group_id']==gid for r in metrics)],key=group_order)
    labels=list(dict.fromkeys(r['isotype'] for r in metrics))
    # Build the complete union once so missing cells and color scales stay
    # comparable across pages. A missing observation is never replaced by zero.
    def iso_order(label):
        import re
        family=0 if label=='IgD/M' else 1 if label.startswith('IgA') else 3 if label.startswith('IgE') else 2
        return family,tuple((1,int(x)) if x.isdigit() else (0,x) for x in re.split(r'(\d+)',label))
    labels.sort(key=iso_order)
    for start in range(0,len(groups),10):
        batch=groups[start:start+10]
        fig,axes=pyplot.subplots(1,2,figsize=(max(11,len(batch)*1.4+5),max(5,len(labels)*.43+2.2)))
        fig.subplots_adjust(left=.09,right=.98,top=.78,bottom=.16,wspace=.30)
        for ax,key,title,color in zip(axes,('mean_shm_percent','shared_cdr3_percent'),
            ('Mean SHM','CDR3s observed as other isotype'),('#315D8B','#4F8F91')):
            lookup={(r['group_id'],r['isotype']):r.get(key) for r in metrics}
            matrix=np.array([[np.nan if lookup.get((g['group_id'],iso)) is None else lookup[g['group_id'],iso]
                              for g in batch] for iso in labels],dtype=float)
            maximum=max([r[key] for r in metrics if r.get(key) is not None]+[1])
            step=2.5 if key=='mean_shm_percent' else 10
            cap=max(step,min(100,math.ceil(maximum/step)*step))
            cmap=LinearSegmentedColormap.from_list(key,['#F5F8FA',color]);cmap.set_bad('#E9EDF0')
            image=ax.imshow(np.ma.masked_invalid(matrix),cmap=cmap,vmin=0,vmax=cap,aspect='auto',interpolation='none')
            ax.set_xticks(range(len(batch)),[wrap(s,16) for s in sample_labels(batch)],fontsize=9)
            ax.xaxis.tick_top();ax.tick_params(axis='x',length=0,pad=8)
            ax.set_yticks(range(len(labels)),labels,fontsize=9);ax.tick_params(axis='y',length=0,pad=7)
            ax.set_title(title,fontsize=11,fontweight='bold',pad=18)
            ax.set_xticks(np.arange(-.5,len(batch),1),minor=True);ax.set_yticks(np.arange(-.5,len(labels),1),minor=True)
            ax.grid(which='minor',color='white',linewidth=1);ax.tick_params(which='minor',length=0)
            for spine in ax.spines.values():spine.set_visible(False)
            for i in range(len(labels)):
                for j in range(len(batch)):
                    value=matrix[i,j]
                    ax.text(j,i,'—' if np.isnan(value) else f'{value:.2f}%',ha='center',va='center',fontsize=8.5,
                            color='#697681' if np.isnan(value) else text_on(to_hex(cmap(value/cap))))
            colorbar=fig.colorbar(image,ax=ax,orientation='horizontal',pad=.08,fraction=.045,aspect=32)
            colorbar.ax.xaxis.set_major_formatter(PercentFormatter(xmax=100));colorbar.ax.tick_params(labelsize=8,length=0)
            colorbar.outline.set_visible(False)
        title='B cell Metrics · All samples'
        if len(groups)>10:title+=f' · {start//10+1}/{math.ceil(len(groups)/10)}'
        fig.suptitle(title,fontsize=14,fontweight='bold',y=.98)
        save(fig,f'bcell_metrics_comparison_{start//10+1:02}',title,kind='bcell_metrics_comparison')


def render_isotype_distribution(data, *, pyplot, save):
    """Migrate single_chain._isotype_bars: IgM/D blue, other isotypes orange.

    Values come from the platform's verified 06 copy/CDR3 summaries rather than
    recalculating the old PDF's input selection or silently taking a first call.
    """
    for group in data.get('usage',{}).get('groups',[]):
        rows=[r for r in data.get('isotype_distribution',[]) if r['group_id']==group['group_id']]
        if not rows:continue
        labels=list(dict.fromkeys(r['isotype'] for r in rows))
        height=max(4,len(labels)*.40+1.8)
        fig,axes=pyplot.subplots(1,2,figsize=(11,height))
        fig.subplots_adjust(left=.10,right=.96,top=.78,bottom=.15,wspace=.38)
        for ax,metric,title in zip(axes,('expression','unique_cdr3'),('% Expression','% Unique CDR3')):
            lookup={r['isotype']:r['percent'] for r in rows if r['metric']==metric}
            values=[lookup.get(label) for label in labels]
            colors=['#4C72B0' if label in {'IgM','IgD'} else '#DD8452' for label in labels]
            ax.set_facecolor('#EAEAF2')
            ax.barh(range(len(labels)),[value or 0 for value in values],color=colors,height=.8)
            ax.set_yticks(range(len(labels)),labels,fontsize=10);ax.invert_yaxis()
            ax.set_xlim(0,max([v for v in values if v is not None]+[1])*1.25)
            ax.set_xlabel(title,fontsize=10);ax.set_title(title,fontsize=11,fontweight='bold',pad=14)
            ax.grid(axis='x',color='white',linewidth=.8);ax.set_axisbelow(True);ax.tick_params(length=0)
            for spine in ax.spines.values():spine.set_visible(False)
            for i,value in enumerate(values):ax.annotate('N/A' if value is None else f'{value:.2f}%',
                (value or 0,i),xytext=(4,0),textcoords='offset points',va='center',fontsize=9)
        title=group['sample']+' / IGH · B cell Isotype Distribution'
        fig.suptitle(title,fontsize=13,fontweight='bold',y=.98)
        save(fig,label_name(group['sample'])+'__'+group['group_id'][:8]+'__isotype_distribution',
             title,group['sample'],'IGH','isotype_distribution')
