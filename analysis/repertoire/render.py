"""Report-ready PNG figures from the aggregates used by the Web interface."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import textwrap
import os

from . import FIGURE_CATEGORIES, FIGURE_VERSION
from .io import label_name
from .palette import MATCH_COLORS,constant_color,text_on

STYLE_VERSION = FIGURE_VERSION


def render(data, output, *, top_n=20, figure_audit=None, figure_progress=None,cache_root=None):
    """Draw supported figures; an optional callback audits final geometry."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.colors import to_rgb
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    import numpy as np

    fonts = {font.name for font in font_manager.fontManager.ttflist}
    preferred = next((n for n in ('Arial', 'DejaVu Sans') if n in fonts), 'sans-serif')
    cjk = next((n for n in ('Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans CJK JP', 'WenQuanYi Micro Hei', 'SimHei') if n in fonts), 'DejaVu Sans')
    theme = {
        'font.family': [preferred, cjk], 'font.sans-serif': [preferred, cjk, 'DejaVu Sans'],
        'font.size': 9.5, 'axes.titlesize': 11, 'axes.labelsize': 9,
        'xtick.labelsize': 8.5, 'ytick.labelsize': 9.5, 'legend.fontsize': 8.5,
        'axes.unicode_minus': False, 'axes.spines.top': False, 'axes.spines.right': False,
        'axes.linewidth': .65, 'axes.edgecolor': '#A6ADB5', 'text.color': '#233343',
        'axes.labelcolor': '#455564', 'xtick.color': '#53616D', 'ytick.color': '#354553',
        'xtick.major.width': .6, 'ytick.major.width': .6,
        'legend.frameon': False, 'savefig.facecolor': 'white',
        'pdf.fonttype': 42, 'svg.fonttype': 'none',
    }
    blue, neutral, amber = MATCH_COLORS['unique'], MATCH_COLORS['unmatched'], MATCH_COLORS['multiple']
    output, artifacts = Path(output), []

    def number(value):
        return '—' if value is None else f'{int(value):,}'

    def wrapped(label, width=42):
        # Line wrapping preserves full identifiers.
        label = {'[多重匹配]':'[Multiple matches]', '[未匹配]':'[Unmatched]',
            '[未评估]':'[Unassessed]', '[多基因候选]':'[Multiple gene calls]',
            '[无注释]':'[Unannotated]', '[未配置 C 参考]':'[C reference unavailable]',
            '[未评估 C 注释]':'[C annotation unassessed]', '[关联不唯一，未评估]':'[Ambiguous lineage]',
            '[缺少输出，未评估]':'[Missing output]', '[最终无记录，原因未知]':'[Absent from final output]',
            '[未进入比对]':'[Not mapped]', '[阶段关联不可用]':'[Stage lineage unavailable]'}.get(label, label)
        return '\n'.join('\n'.join(textwrap.wrap(line, width, break_long_words=True, break_on_hyphens=False)) for line in str(label).split('\n'))

    def heading(fig, title, left=.08, y=.955):
        fig.text(.5, y, wrapped(title, 90), ha='center', va='top', fontsize=12, fontweight='bold')

    def save(fig, name, title, sample='', chain='', kind='', primer='', preparation='', segment='', exclude_axes=()):
        category = FIGURE_CATEGORIES[kind]
        folder = output / category / 'figures'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name + '.png')
        try:
            fig.canvas.draw()
            if figure_audit is not None:
                figure_audit(fig, name, exclude_axes=exclude_axes)
            temporary=path.with_name('.'+path.name+'.'+str(os.getpid())+'.tmp')
            try:
                fig.savefig(temporary,format='png',dpi=300,bbox_inches='tight',pad_inches=.12)
                os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
        finally:
            plt.close(fig)
        artifacts.append(dict(id=name, title=title, sample=sample, chain=chain,
            kind=kind, category=category, primer=primer, preparation=preparation,
            segment=segment, style_version=STYLE_VERSION, png=path.relative_to(output).as_posix()))

    def horizontal(labels, values, title, *, value_labels, xlabel='', colors=None, note='', compact=False):
        """Use a common scale and a separate aligned column for values."""
        labels = [wrapped(label) for label in labels]
        height = max(2.65 if compact else 3.1, sum(label.count('\n')+1 for label in labels)*.31+1.65)
        fig, ax = plt.subplots(figsize=(9, height))
        ax.set_yticks(range(len(labels)), labels)
        fig.canvas.draw()
        left_in = max([label.get_window_extent().width/fig.dpi for label in ax.get_yticklabels()] + [.65]) + .25
        right_in, plot_in = 1.85, 4.65
        width = max(8.2, left_in + plot_in + right_in)
        fig.set_size_inches(width, height)
        bottom_in = (.45 if compact else .67) + (.28 if note else 0)
        top_in = .78 + .22*wrapped(title, 90).count('\n')
        fig.subplots_adjust(left=left_in/width, right=1-right_in/width,
            bottom=bottom_in/height, top=1-top_in/height)
        bars = ax.barh(range(len(labels)), values, height=.56, color=colors or blue, edgecolor='none')
        ax.set_ylim(len(labels)-.55, -.55)
        ax.set_xlim(0, 100)
        ax.tick_params(axis='y', length=0, pad=8)
        ax.spines['left'].set_visible(False)
        for index, label in enumerate(value_labels):
            ax.text(1.035, index, label, transform=ax.get_yaxis_transform(),
                ha='left', va='center', fontsize=9.5, clip_on=False)
        if compact:
            ax.xaxis.set_visible(False)
            ax.spines['bottom'].set_visible(False)
        else:
            ax.set_axisbelow(True)
            ax.set_xticks([0, 25, 50, 75, 100])
            ax.grid(axis='x', color='#E7EBEF', linewidth=.65)
            ax.set_xlabel(xlabel, labelpad=10)
        heading(fig, title, left=left_in/width, y=1-.16/height)
        if note:
            fig.text(left_in/width, .04/height, wrapped(note, 100), va='bottom', fontsize=8, color='#66737F')
        return fig, ax, bars

    with plt.rc_context(theme):
        from .output_metrics import render as render_outputs
        from .presentation import shared_inputs
        render_outputs(data.get('sample_output_summary',[]),pyplot=plt,save=save,inputs=shared_inputs(data))
        from scigblast.stage_metrics import summarize_volume
        volumes = data.get('stage_volumes') or summarize_volume(data['stages'], data.get('samples', []))
        from .render_stages import render_overview
        render_overview(volumes,pyplot=plt,save=save)
        for sample in sorted({r['sample'] for r in volumes}):
            render_overview([r for r in volumes if r['sample']==sample],pyplot=plt,save=save,target_sample=sample)

        from .render_matching import render as render_matching
        if data.get('dual_matching_summary'):
            for stage in ('raw_R1','mapping'):
                scoped=dict(data,matching_totals=[r for r in data['dual_matching_summary'] if r['stage']==stage],
                    primer_group_comparisons=[r for r in data.get('primer_group_comparisons',[]) if r.get('stage','mapping')==stage])
                # Both stages use their own complete, deduplicated input scope.
                scoped['primer_group_comparisons']=[dict(r,unit='reads') for r in scoped['primer_group_comparisons']]
                render_matching(scoped,pyplot=plt,save=save,stage=stage)
                for sample in sorted({r['sample'] for r in scoped['matching_totals']}):
                    selected=dict(scoped,matching_totals=[r for r in scoped['matching_totals'] if r['sample']==sample],
                        primer_group_comparisons=[r for r in scoped['primer_group_comparisons'] if r['sample']==sample])
                    render_matching(selected,pyplot=plt,save=save,stage=stage,target_sample=sample)
        else:
            render_matching(data, pyplot=plt, save=save)

        metrics=data.get('primer_mapping_summary',[])
        for sample,chain,stage in sorted({(r['sample'],r['chain'],r['stage']) for r in metrics}):
            selected=sorted([r for r in metrics if (r['sample'],r['chain'],r['stage'])==(sample,chain,stage) and not r['primer'].startswith('[')],key=lambda r:(-r['matched_reads'],r['primer']))
            stage_label={'raw_R1':'Raw','mapping':'Mapping reads'}[stage]
            for page in range(0,len(selected),20):
                batch=selected[page:page+20]
                for field,kind,label,color in (('match_percent','primers','Unique primer matching','#419A8B'),('mapping_percent','primer_mapping','Mapping rate by primer',blue)):
                    if kind=='primer_mapping' and stage!='mapping':continue
                    title=f'{sample} / {chain} · {label} · {stage_label}'
                    values=[r[field] or 0 for r in batch]
                    texts=[('N/A' if r[field] is None else f'{r[field]:.4f}%')+f" · {r['matched_reads']:,}" if kind=='primers' else
                        ('N/A' if r[field] is None else f'{r[field]:.2f}%')+f" · {number(r['mapped_reads'])} / {r['matched_reads']:,}" for r in batch]
                    fig,_,_=horizontal([r['primer'] for r in batch],values,title,value_labels=texts,xlabel='Unique primer match (%)' if kind=='primers' else 'Mapping rate (%)',colors=color)
                    save(fig,label_name(sample)+f'__{chain}__{stage}__{kind}_{page//20+1}',title,sample,chain,kind,preparation=stage)

        for sample, primers in (data.get('catalog') or {}).get('assignments', {}).items():
            if metrics:break
            for chain in sorted({r['chain'] for r in data['samples'] if r['sample'] == sample}):
                counts = Counter()
                for row in data['primer_counts']:
                    if row['sample'] == sample and row['chain'] == chain and row['subset'] == 'all' and not row['primer'].startswith('['):
                        counts[row['primer']] += row['count']
                total = sum(r['count'] for r in data['match_counts'] if r['sample'] == sample and r['chain'] == chain)
                ranked = sorted(((p['name'], counts[p['name']]) for p in primers), key=lambda row: (-row[1], row[0]))
                for page in range(0, len(ranked), 20):
                    batch = ranked[page:page+20]
                    values = [v*100/total if total else 0 for _, v in batch]
                    title = f'{sample} / {chain} · Unique primer matching'
                    fig, _, _ = horizontal([name for name, _ in batch], values, title,
                        value_labels=[f'{percent:.4f}% · {count:,}' if total else f'— · {count:,}' for (_, count), percent in zip(batch, values)],
                        xlabel='Unique primer match (%)',colors='#419A8B')
                    save(fig, label_name(sample)+f'__{chain}__primers_{page//20+1}', title, sample, chain, 'primers')

        if not data.get('usage'):
            gene_groups, composition_groups = defaultdict(Counter), defaultdict(Counter)
            requested = set((data.get('catalog') or {}).get('config', {}).get('figure_primers', []))
            for row in data['gene_counts']:
                if row['segment'] != 'C' or row['subset'] != 'retained':
                    continue
                state = row.get('trim_state', 'not_recorded')
                preparation = 'trimmed' if state.startswith('trimmed_') else 'untrimmed' if state.endswith('_untrimmed') else 'not_recorded'
                if not requested or row['primer'] in requested:
                    gene_groups[(row['sample'], row['chain'], row['segment'], row['primer'] if requested else '', preparation)][row['gene']] += row['count']
                if not row['primer'].startswith('[') and not row['gene'].startswith('['):
                    composition_groups[(row['sample'], row['chain'], row['segment'], preparation)][(row['primer'], row['gene'])] += row['count']
            group_label = {'trimmed': 'Primer trimmed', 'untrimmed': 'Original sequence retained', 'not_recorded': ''}
            for (sample, chain, segment, primer, preparation), counts in gene_groups.items():
                total = sum(counts.values())
                if not total:
                    continue
                ranked = sorted(counts.items(), key=lambda row: (-row[1], row[0]))
                shown = ranked[:top_n]
                if len(ranked) > top_n:
                    shown.append((f'Other ({len(ranked)-top_n} classes)', sum(v for _, v in ranked[top_n:])))
                values = [v*100/total for _, v in shown]
                show_preparation = bool(group_label[preparation]) and not (primer and preparation == 'trimmed')
                title = f'{sample} / {chain} · C gene distribution'+(' · '+group_label[preparation] if show_preparation else '')
                if primer:
                    title += '\n' + primer
                fig, _, _ = horizontal([label for label, _ in shown], values, title,
                    value_labels=[f'{percent:.2f}% · {count:,}' for (_, count), percent in zip(shown, values)],
                    xlabel='% Expression',
                    colors=[neutral if label.startswith('[') else blue for label, _ in shown])
                name = label_name(sample)+f'__{chain}__{segment}__{preparation}'+('__'+label_name(primer) if primer else '')
                save(fig, name, title, sample, chain, 'genes', primer, preparation, segment=segment)

            # A fixed mapping keeps a C gene the same colour across samples.
            gene_colors = {gene:constant_color(gene) for counts in composition_groups.values() for _,gene in counts}
            for (sample, chain, segment, preparation), counts in composition_groups.items():
                totals = Counter()
                for (_, gene), count in counts.items():
                    totals[gene] += count
                ranked_genes = [gene for gene, _ in totals.most_common()]
                genes = sorted(ranked_genes[:top_n])
                remainder = ranked_genes[top_n:]
                other_label = f'Other ({len(remainder)} classes)'
                shown_genes = genes + ([other_label] if remainder else [])
                primers = sorted({p for p, _ in counts})
                for page in range(0, len(primers), 16):
                    selected = primers[page:page+16]
                    matrix = np.array([[counts[(p, g)] for g in genes] + ([sum(counts[(p, g)] for g in remainder)] if remainder else []) for p in selected], dtype=float)
                    denominator = np.array([sum(v for (name, _), v in counts.items() if name == p) for p in selected])
                    matrix = np.divide(matrix*100, denominator[:, None], out=np.zeros_like(matrix), where=denominator[:, None] != 0)
                    assert np.allclose(matrix.sum(axis=1)[denominator>0], 100), 'C composition must preserve each primer denominator'
                    legend_rows = (len(shown_genes)+4)//5
                    labels = [wrapped(primer, 16) for primer in selected]
                    max_lines = max(label.count('\n')+1 for label in labels)
                    height = 5.1 + max(0, legend_rows-1)*.18 + max(0,max_lines-3)*.15
                    fig, ax = plt.subplots(figsize=(max(8.4, len(selected)*1.25+1), height))
                    fig.subplots_adjust(left=.085, right=.985, bottom=(.44+max_lines*.15)/height, top=1-(.92+legend_rows*.18)/height)
                    positions = np.arange(len(selected))
                    bottom = np.zeros(len(selected))
                    for column, gene in enumerate(shown_genes):
                        color = '#D7DDE0' if gene == other_label else gene_colors[gene]
                        values = matrix[:, column]
                        ax.bar(positions, values, width=.62, bottom=bottom, color=color, label=gene, edgecolor='white', linewidth=.4)
                        text_color = text_on(color)
                        for index, value in enumerate(values):
                            if value >= 6:
                                ax.text(index, bottom[index]+value/2, f'{value:.1f}%', ha='center', va='center', fontsize=9, color=text_color)
                        bottom += values
                    ax.set_ylim(0, 100)
                    ax.set_yticks([0, 25, 50, 75, 100], ['0%', '25%', '50%', '75%', '100%'])
                    ax.set_xticks(positions, labels, ha='center')
                    ax.tick_params(axis='x', length=0, pad=9, labelsize=8.5)
                    ax.spines['left'].set_visible(False)
                    ax.set_axisbelow(True)
                    ax.grid(False)
                    ax.legend(loc='lower center', bbox_to_anchor=(.5, 1.03), ncol=min(5,len(shown_genes)), borderaxespad=0, handlelength=1.2, columnspacing=1.3)
                    title = f'{sample} / {chain} · C gene composition by primer'+(' · '+group_label[preparation] if preparation=='untrimmed' else '')
                    heading(fig, title, left=.085, y=1-.16/height)
                    name = label_name(sample)+f'__{chain}__{segment}__{preparation}_composition_{page//16+1}'
                    save(fig, name, title, sample, chain, 'composition', preparation=preparation, segment=segment)

    if data.get('usage'):
        from .figure_jobs import render_usage
        artifacts.extend(render_usage(data, output, figure_audit=figure_audit,figure_progress=figure_progress,cache_root=cache_root))
    from .repertoire_metrics import render as render_repertoire_metrics
    render_repertoire_metrics(data, pyplot=plt, save=save)
    return artifacts
