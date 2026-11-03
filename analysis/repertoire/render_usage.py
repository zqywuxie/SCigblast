"""Complete reference-aware V/J/C pages and IGH isotype overview PNGs."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
import textwrap
import os

from .io import label_name
from .isotype import MAIN_ORDER
from .usage import connect, rows
from .gene_cdr3 import label as metric_label
from . import FIGURE_VERSION
from .palette import ISOTYPE_COLORS,GENE_SEGMENT_COLORS,constant_color,isotype_color,text_on

STYLE_VERSION = FIGURE_VERSION
GENE_STATE_COLORS = {'observed':'#3C718F','ambiguous_only':'#A67B45','zero_observed':'#84909B'}


def render(data, output, *, figure_audit=None,figure_progress=None,isotype_only=False,skip_isotypes=False,index=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import to_rgb
    import numpy as np

    fonts={f.name for f in font_manager.fontManager.ttflist}
    cjk=next((n for n in ('Microsoft YaHei','Noto Sans CJK SC','Noto Sans CJK JP','WenQuanYi Micro Hei','SimHei') if n in fonts),'DejaVu Sans')
    theme={'font.family':['DejaVu Sans',cjk],'font.size':9,'axes.unicode_minus':False,
        'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,
        'axes.edgecolor':'#B8C1C9','axes.linewidth':.6,'text.color':'#233343',
        'xtick.color':'#53616D','ytick.color':'#354553','legend.frameon':False,'pdf.fonttype':42}
    output=Path(output)
    artifacts=[]
    def wrap(value,width=45):
        return '\n'.join(textwrap.wrap(str(value),width,break_on_hyphens=False))
    def publish(fig,name,title,category,meta):
        defaults=dict(sample='',chain='',primer='',segment='')
        defaults.update(meta)
        folder=output/category/'figures';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(name+'.png')
        try:
            fig.canvas.draw()
            if figure_audit:figure_audit(fig,name,exclude_axes=())
            temporary=path.with_name('.'+path.name+'.'+str(os.getpid())+'.tmp')
            try:
                fig.savefig(temporary,format='png',dpi=300,bbox_inches='tight',pad_inches=.14,facecolor='white')
                os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
        finally:plt.close(fig)
        artifacts.append(dict(defaults,id=name,title=title,category=category,style_version=STYLE_VERSION,png=path.relative_to(output).as_posix()))
        if figure_progress:figure_progress(len(artifacts),expected,title)

    usage=data['usage']
    requested=set(data.get('figure_primers',[]))
    with plt.rc_context(theme),closing(connect(index or output/'details.sqlite')) as connection:
        selected_groups=set() if isotype_only else {g['group_id'] for g in usage['groups'] if not data.get('figure_groups') or g['group_id'] in data['figure_groups']}
        expected=sum((r[3]+24)//25 for r in connection.execute('SELECT group_id,primer,segment,count(*),metric,aa_field FROM gene_usage WHERE unique_count>0 GROUP BY group_id,primer,segment,metric,aa_field')
            if r[0] in selected_groups and (not requested or not r[1] or r[1] in requested))
        for group_id in selected_groups:
            contexts=connection.execute('SELECT DISTINCT metric,aa_field FROM gene_usage WHERE group_id=?',(group_id,)).fetchall()
            for metric,field in contexts:
                primers=[r[0] for r in connection.execute("SELECT DISTINCT primer FROM gene_usage WHERE group_id=? AND primer<>'' AND segment='C' AND metric=? AND aa_field=? AND unique_count>0",(group_id,metric,field)) if not requested or r[0] in requested]
                expected+=(len(primers)+11)//12
        igh_groups=[g for g in usage['groups'] if g['chain']=='IGH']
        if igh_groups:
            fields={field for g in igh_groups for field in g.get('aa_fields',[])}
            levels=1
            expected+=max(1,len(fields))*levels+len(igh_groups)
        all_c=[r[0] for r in connection.execute("SELECT DISTINCT gene FROM gene_usage WHERE segment='C' ORDER BY gene")]
        gene_colors={gene:constant_color(gene) for gene in all_c}
        for group in usage['groups']:
            if isotype_only:continue
            if data.get('figure_groups') and group['group_id'] not in data['figure_groups']:continue
            gid=group['group_id']
            primers=group['primers']
            if requested:primers=[p for p in primers if p in requested]
            for primer in ['']+primers:
                for segment in ('V','J','C'):
                    for metric,aa_field in [('expression','')]+[('unique_cdr3',field) for field in group.get('aa_fields',[])]:
                        total=connection.execute('SELECT count(*) FROM gene_usage WHERE group_id=? AND primer=? AND segment=? AND metric=? AND aa_field=? AND unique_count>0',(gid,primer,segment,metric,aa_field)).fetchone()[0]
                        if not total:
                            continue
                        for start in range(0,total,25):
                            batch=list(rows(connection,group_id=gid,primer=primer,segment=segment,metric=metric,aa_field=aa_field,observed_only=True,offset=start,limit=25))
                            labels=[wrap(r['gene'],32) for r in batch]
                            height=max(2.9,sum(label.count('\n')+1 for label in labels)*.29+1.5)
                            fig,ax=plt.subplots(figsize=(10.2,height))
                            ax.set_yticks(range(len(labels)),labels,fontsize=10)
                            fig.canvas.draw()
                            left=max([x.get_window_extent().width/fig.dpi for x in ax.get_yticklabels()]+[.8])+.3
                            width=max(9.4,left+5.3+2)
                            fig.set_size_inches(width,height)
                            title=f"{group['sample']} / {group['chain']} · {segment} gene usage · {metric_label(metric,aa_field)}"
                            if group.get('reference_scope')=='comparison_current':title+=' · Current reference comparison'
                            if total>25:title+=f' · {start//25+1}/{(total+24)//25}'
                            if primer:title+='\n'+wrap(primer,90)
                            title_lines=title.count('\n')+1
                            top=(.78+.22*(title_lines-1))/height
                            fig.subplots_adjust(left=left/width,right=1-2/width,top=1-top,bottom=.58/height)
                            vals=[r['percent'] or 0 for r in batch]
                            states=['zero_observed' if r['status']=='zero_observed' else
                                'ambiguous_only' if not r['unique_count'] and r['ambiguous_count'] else 'observed' for r in batch]
                            row_colors=[GENE_SEGMENT_COLORS[segment] if state=='observed' else GENE_STATE_COLORS[state] for state in states]
                            maximum=max(vals+[1]);cap=min(100,max(5,5*__import__('math').ceil(maximum/5)))
                            ax.barh(range(len(batch)),vals,height=.56,color=row_colors)
                            for label,color in zip(ax.get_yticklabels(),row_colors):label.set_color(color)
                            ax.set_xlim(0,cap);ax.set_ylim(len(batch)-.65,-.65)
                            ax.tick_params(axis='y',length=0,pad=8)
                            ax.set_xlabel(metric_label(metric,aa_field),labelpad=9)
                            for i,row in enumerate(batch):
                                value='—' if row['percent'] is None else f"{row['percent']:.2f}%"
                                text=value+f"  ·  {row['unique_count']:,}"
                                ax.text(1.025,i,text,transform=ax.get_yaxis_transform(),ha='left',va='center',fontsize=8.5,
                                        color=row_colors[i])
                                if states[i]=='zero_observed':
                                    ax.plot(0,i,'o',ms=4.2,markerfacecolor='white',markeredgecolor=row_colors[i],markeredgewidth=1,clip_on=False)
                                elif states[i]=='ambiguous_only':
                                    ax.plot(0,i,'D',ms=3.8,color=row_colors[i],clip_on=False)
                            fig.text(.5,1-.16/height,title,ha='center',va='top',fontsize=12,fontweight='bold')
                            name=label_name(group['sample'])+'__'+group['chain']+'__'+gid[:8]+('__'+label_name(primer) if primer else '')+f'__{segment}__{metric}'+('__'+aa_field if aa_field else '')+f'__{start//25+1:02}'
                            publish(fig,name,title,'03_primer_genes' if primer else '02_sample_genes',dict(sample=group['sample'],chain=group['chain'],primer=primer,segment=segment,
                                group_id=gid,kind='primer_genes' if primer else 'sample_genes',page=start//25+1,reference_id=group['reference_id'],metric=metric,aa_field=aa_field))

            for metric,aa_field in [('expression','')]+[('unique_cdr3',field) for field in group.get('aa_fields',[])]:
                # Normalize within each primer's classified C genes.
                counts=defaultdict(Counter)
                for row in rows(connection,group_id=gid,segment='C',metric=metric,aa_field=aa_field):
                    if row['primer'] and row['unique_count']:
                        counts[row['primer']][row['gene']]=row['unique_count']
                keys=sorted(p for p in counts if not requested or p in requested)
                genes=sorted({gene for p in keys for gene in counts[p]})
                denominators={r[0]:r[1] for r in connection.execute("SELECT primer,max(denominator) FROM gene_usage WHERE group_id=? AND segment='C' AND metric=? AND aa_field=? GROUP BY primer",(gid,metric,aa_field))}
                for start in range(0,len(keys),12):
                    batch=keys[start:start+12]
                    labels=[wrap(p,18) for p in batch]
                    extra=max(x.count('\n') for x in labels)*.16
                    fig,ax=plt.subplots(figsize=(max(8.8,len(batch)*1.25+1),5.0+extra))
                    fig.subplots_adjust(left=.08,right=.98,top=.74,bottom=(.56+extra)/(5.0+extra))
                    bottom=np.zeros(len(batch))
                    for i,gene in enumerate(genes):
                        values=np.array([counts[p][gene]*100/denominators[p] if denominators[p] else 0 for p in batch])
                        color=gene_colors[gene]
                        ax.bar(range(len(batch)),values,bottom=bottom,width=.62,color=color,label=gene,edgecolor='white',linewidth=.35)
                        for x,v,b in zip(range(len(batch)),values,bottom):
                            if v>=6:ax.text(x,b+v/2,f'{v:.1f}%',ha='center',va='center',fontsize=8,color=text_on(color))
                        bottom+=values
                    ax.set_ylim(0,100);ax.set_yticks([0,25,50,75,100],['0%','25%','50%','75%','100%'])
                    ax.set_xticks(range(len(batch)),labels,fontsize=8);ax.tick_params(axis='x',length=0,pad=8)
                    ax.legend(loc='lower center',bbox_to_anchor=(.5,1.035),ncol=min(5,len(genes)),fontsize=8)
                    title=f"{group['sample']} / {group['chain']} · C gene composition by primer · {metric_label(metric,aa_field)}"
                    fig.text(.5,.96,title,ha='center',va='top',fontsize=12,fontweight='bold')
                    publish(fig,label_name(group['sample'])+'__'+gid[:8]+f'__primer_C_{metric}'+('__'+aa_field if aa_field else '')+f'_composition_{start//12+1}',title,'03_primer_genes',dict(sample=group['sample'],chain=group['chain'],group_id=gid,kind='composition',segment='C',metric=metric,aa_field=aa_field))

        all_igh=[g for g in usage['groups'] if g['chain']=='IGH' and not skip_isotypes]
        iso=usage['isotypes']
        for target_sample,igh in [('',all_igh)]+[(g['sample'],[g]) for g in all_igh]:
            fields=sorted({field for group in igh for field in group.get('aa_fields',[])}) or ['']
            for level in ('main',):
                if not igh:break
                labels=list(MAIN_ORDER)
                if not labels:continue
                colors=ISOTYPE_COLORS
                for field in fields:
                    width=max(11.6,len(igh)*1.35+3)
                    fig,axes=plt.subplots(1,2,figsize=(width,5.1))
                    fig.subplots_adjust(left=.055,right=.985,top=.70,bottom=.18,wspace=.22)
                    for ax,metric,panel_title in zip(axes,('abundance','unique_cdr3'),('% Expression','% uCDR3')):
                        bottom=np.zeros(len(igh))
                        scoped=[r for r in iso if r['level']==level and r['metric']==metric and (metric=='abundance' or r['aa_field']==field)]
                        index={(r['group_id'],r['isotype']):r for r in scoped}
                        for label in labels:
                            values=np.array([index.get((g['group_id'],label),{}).get('percent') or 0 for g in igh])
                            ax.bar(range(len(igh)),values,bottom=bottom,width=.62,color=colors[label],label=label,edgecolor='white',linewidth=.4)
                            for x,v,b in zip(range(len(igh)),values,bottom):
                                if v>=7:ax.text(x,b+v/2,f'{v:.1f}%',ha='center',va='center',fontsize=8,color=text_on(colors[label]))
                            bottom+=values
                        for i,total in enumerate(bottom):
                            if total==0:ax.text(i,45,'N/A',ha='center',va='center',rotation=90,color='#87929D',fontsize=8)
                        repeated=Counter(g['sample'] for g in igh)
                        names=[wrap(g['sample'],15)+('\nRef. '+g['reference_id'][:6] if repeated[g['sample']]>1 else '') for g in igh]
                        ax.set_xticks(range(len(igh)),names,fontsize=8)
                        ax.tick_params(axis='x',length=0,pad=8)
                        ax.set_ylim(0,100);ax.set_yticks([0,25,50,75,100],['0%','25%','50%','75%','100%'])
                        center=(len(igh)-1)/2;span=max(1.2,center+.6)
                        ax.set_xlim(center-span,center+span)
                        ax.set_title(panel_title,pad=12,fontsize=11,fontweight='bold')
                    handles,legend=axes[0].get_legend_handles_labels()
                    fig.legend(handles,legend,loc='upper center',bbox_to_anchor=(.5,.87),ncol=min(8,len(labels)),fontsize=8)
                    title='IGH Isotype · '+(target_sample or 'All samples')
                    if len(fields)>1:title+=' · '+{'cdr3_aa':'CDR3aa','junction_aa':'Junctionaa'}.get(field,field)
                    fig.text(.5,.97,title,ha='center',va='top',fontsize=13,fontweight='bold')
                    publish(fig,('isotype__'+label_name(target_sample)+'__'+igh[0]['group_id'][:8] if target_sample else 'isotype_overview')+'__'+level+'__'+(field or 'unavailable'),title,'01_overview',dict(kind='isotype',level=level,aa_field=field,sample=target_sample,group_id=igh[0]['group_id'] if target_sample else ''))
    return artifacts
