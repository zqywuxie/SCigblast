"""One viewer for authenticated reports and self-contained offline exports."""
from __future__ import annotations
import base64
from datetime import datetime,timedelta,timezone
from html import escape
import hashlib,json,re,secrets
from pathlib import Path
from functools import lru_cache
from . import chart_data
from .io import safe_file
from .report_rules import for_report
from .workbook_overview import sample_rows,sample_sort_key

VERSION='standalone-report-v36-research-workspace'
ASSETS=Path(__file__).with_name('assets')
FIGURE_VIEWS={'diversity_comparison':'metrics','bcell_metrics_comparison':'metrics','isotype_distribution':'overview','diversity':'metrics','bcell_metrics':'metrics','sample_outputs':'outputs','matching':'overview','isotype':'overview','stage_overview':'stages','sample_genes':'sample','genes':'sample','primers':'primer','primer_mapping':'primer','primer_genes':'primer','composition':'primer','heatmap':'primer'}

@lru_cache(maxsize=1)
def viewer_signature():
    result=hashlib.sha256()
    for path in [Path(__file__).with_name(name) for name in ('report_html.py','chart_data.py','report_rules.py','palette.py','workbook_overview.py','presentation.py')]+sorted(ASSETS.glob('*')):
        if path.is_file():result.update(path.name.encode());result.update(path.read_bytes())
    return result.hexdigest()

def response_headers(*,download=True):
    return {'Content-Disposition':('attachment' if download else 'inline')+'; filename="SCIGBLAST_report.html"','X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"sandbox allow-scripts allow-downloads; default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'"}

def json_text(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')

def figure_page(figure):
    if figure.get('page'):return max(1,int(figure['page']))
    match=re.search(r' · (\d+)/\d+$',str(figure.get('title','')))
    if not match:match=re.search(r'(?:_composition_|__primers_|__primer_mapping_|__stages_|_stages_overview_|_comparison_|__mapping_|__raw_R1_|primer_matching_total_)(\d+)$',str(figure.get('id','')))
    return max(1,int(match[1])) if match else 1

def prepare(data,root,*,resolve=None):
    root=Path(root);resolve=resolve or (lambda name:safe_file(root/name,root));figures=[]
    for figure in data.get('figures',[]):
        if figure.get('kind') not in FIGURE_VIEWS or figure.get('aa_field')=='junction_aa':continue
        if figure.get('kind')=='isotype' and figure.get('level')=='subclass':continue
        if figure.get('kind')=='primer_mapping' and figure.get('preparation')!='mapping':continue
        path=resolve(figure['png'])
        with path.open('rb') as handle:
            if path.suffix.lower()!='.png' or handle.read(8)!=b'\x89PNG\r\n\x1a\n':raise ValueError('Invalid report PNG')
        figures.append((figure,path))
    return figures,chart_data.prepare(data,resolve)

def stream(data,assets,*,run_id=''):
    figures,tables=assets;task_name=str(data.get('task_name') or '免疫组库分析报告')
    samples=sample_rows(data.get('samples',[]) or data.get('sample_overview',[]),data.get('catalog'))
    if data.get('sample_overview'):
        old={(r['sample'],r['chain']):r for r in data['sample_overview']}
        samples=[old.get((r['sample'],r['chain']),r) for r in samples]
    table_ids={t['id']:set(t['columns']) for t in tables}
    public_figures=[dict(id=f'figure-{i}',title=f.get('title',''),filename=path.name,view=FIGURE_VIEWS[f['kind']],kind=f['kind'],page=figure_page(f),preparation=f.get('preparation',''),group_id=f.get('group_id',''),metric_keys=f.get('metric_keys',[]),scope='single' if f.get('sample') else 'comparison',metric=f.get('metric','expression' if f['kind'] in {'sample_genes','primer_genes','genes','composition'} else ''),stage=f.get('preparation','') if f['kind'] in {'matching','primers','primer_mapping'} else '',interactive=chart_data.supports(f,table_ids),**{k:f.get(k,'') for k in ('sample','chain','primer','segment','aa_field','scenario_id','primer_group')}) for i,(f,path) in enumerate(figures)]
    public_figures.extend(chart_data.virtual_figures(tables,public_figures))
    public_figures=chart_data.viewer_figures(tables,public_figures)
    sources=[dict(group_id=g['group_id'],sample=g['sample'],chain=g['chain'],status=g['composition']['status'],copies=g['composition'].get('copies'),unresolved_copies=g['composition'].get('unresolved_copies')) for g in data.get('usage',{}).get('groups',[]) if g.get('composition')]
    public=dict(task_name=task_name,samples=samples,figures=public_figures,statistic_rules=for_report(data,language='zh'),composition_sources=sources,
                statistics_created_at=data.get('statistics_created_at',''),**chart_data.public_metadata(data))
    public['raw_matching_missing']=any(f.get('kind')=='matching' for f in data.get('figures',[])) and not any(f['kind']=='matching' for f in public_figures)
    from .palette import constant_color
    constants=set()
    for source in tables:
        if source['id'] in {'reference_gene_observation','sample_gene_usage'}:
            for row in chart_data.rows(source):
                item=dict(zip(source['columns'],row))
                if item.get('segment')=='C':constants.add(item['gene'])
    public['colors']['constant']={gene:constant_color(gene) for gene in sorted(constants)}
    display_id=str(data.get('report_display_id') or (hashlib.sha256(str(run_id).encode()).hexdigest()[:8] if run_id else secrets.token_hex(4)))
    date=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M +08:00')
    yield '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    yield '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: blob:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; connect-src \'none\'; base-uri \'none\'; form-action \'none\'">'
    yield '<title>'+escape(task_name)+' · 分析报告</title><style>'+(ASSETS/'report.css').read_text(encoding='utf-8')+'</style></head><body><main>'
    public['report_id']=display_id
    yield '<header><div><span class="eyebrow">SCIGBLAST / ANALYSIS REPORT</span><h1>'+escape(task_name)+'</h1></div><div class="meta"><span>报告编号 '+escape(display_id)+'</span> <button id="share-html">下载 HTML</button> <button id="theme" aria-label="切换深浅主题">◐</button><details class="report-info"><summary>报告信息</summary><dl><dt>HTML 导出时间</dt><dd>'+escape(date)+'</dd><dt>统计生成时间</dt><dd>'+escape(str(data.get('statistics_created_at') or '历史材料未记录'))+'</dd><dt>参考版本</dt><dd>'+escape(', '.join(sorted({g.get('reference_id','') for g in public['groups'] if g.get('reference_id')})))+'</dd></dl></details></div></header>'
    yield '<div class="report-layout"><div class="report-nav"><nav role="tablist" aria-label="报告分类">'
    for key,title in (('overview','总体概览'),('outputs','样本比较'),('primer','引物分析'),('sample','样本分析'),('reference','基因匹配')):yield f'<button role="tab" id="tab-{key}" aria-controls="report-workspace" data-main-view="{key}" aria-selected="false" tabindex="-1">{title}</button>'
    yield '</nav></div><div class="report-content"><section class="panel samples" id="sample-info"><details open><summary>样本信息</summary><div class="table-wrap sample-table-scroll" role="region" aria-label="样本信息，可滚动或拖动查看" tabindex="0"><table><thead><tr><th>类别</th><th>完整样本名</th><th>链</th><th>引物组 / 对应方式</th></tr></thead><tbody>'
    previous_sample=None;sample_block=-1
    for row in samples:
        category=row['category'] if row['category'] in {'Heavy','Light','TCR'} else ''
        boundary=previous_sample!=row['sample']
        if boundary:sample_block+=1
        classes=('sample-start ' if boundary else 'sample-continuation ')+('sample-shade' if sample_block%2 else 'sample-white')
        yield '<tr class="'+classes+'" data-sample="'+escape(str(row['sample']),quote=True)+'"><td><span class="category '+category+'">'+escape(row['category'])+'</span></td><td class="sample-name">'+escape(str(row['sample']))+'</td><td><span class="sample-chain">'+escape(str(row['chain']))+'</span></td><td>'+escape(str(row.get('primer_groups','')))+'</td></tr>'
        previous_sample=row['sample']
    yield '</tbody></table></div></details></section><section class="panel report-workspace" id="report-workspace" role="tabpanel" tabindex="-1"><div class="section-head"><h2 id="view-title" tabindex="-1"></h2><div class="report-head-actions"><button id="overview-isotype-shortcut" hidden>查看单样本 Isotype ↓</button><button id="reset-filters">重置本模块</button></div></div><div id="section-tabs" class="primer-categories" role="tablist" aria-label="样本分析分类" hidden></div>'
    yield '<div id="primer-categories" class="primer-categories" role="tablist" aria-label="引物分析分类" hidden>'+''.join(f'<button role="tab" data-primer-category="{key}" aria-selected="false">{title}</button>' for key,title in [('matching','引物匹配'),('mapping','Mapping 效率'),('genes','逐引物基因组成'),('ccompare','引物间 C 基因对比')])+'</div><details id="module-rules" class="note" hidden></details><p id="category-note" class="caption" hidden></p>'
    yield '<div class="filters"><label>样本<select id="sample"></select></label><label>链<select id="chain"></select></label><label id="scenario-filter" hidden>引物组<select id="scenario"><option value="">原组</option></select></label><label>引物<select id="primer"></select></label><label id="stage-filter" hidden>数据阶段<select id="stage"><option value="">两个阶段</option><option value="raw_R1">Raw</option><option value="mapping">Mapping reads</option></select></label><label id="reference-scope-filter" hidden>统计对象<select id="reference-scope"><option value="sample">整个样本</option><option value="primer">单条引物</option></select></label><label>基因区<select id="segment"><option value="">全部基因区</option><option>V</option><option>J</option><option>C</option></select></label></div><p id="filter-summary" class="filter-summary" role="status"></p>'
    yield '<div id="reference-tools" class="reference-tools" hidden><div class="reference-toolbar"><div class="reference-mode" role="group" aria-label="基因统计对象"><button data-reference-scope="sample" aria-pressed="true">按样本</button><button data-reference-scope="primer" aria-pressed="false">按引物</button></div><label class="reference-search"><span class="sr-only">搜索基因</span><input id="reference-query" type="search" maxlength="120" placeholder="搜索基因，如 IGHG、TRAV"></label><label><span class="sr-only">匹配状态</span><select id="reference-state" aria-label="匹配状态"><option value="">全部状态</option><option value="matched">已匹配</option><option value="unmatched">未匹配</option></select></label><button id="reference-download" disabled>下载总表 CSV ↓</button></div><div class="reference-segments" role="tablist" aria-label="基因区">'+''.join('<button role="tab" data-reference-segment="'+key+'" aria-controls="reference-list" aria-selected="false">'+label+'</button>' for key,label in [('V','V · 可变区'),('J','J · 连接区'),('C','C · 恒定区')])+'</div><span id="reference-export-status" role="status" class="sr-only"></span></div><section id="reference-list" hidden></section>'

    yield '<div class="analysis-tools" id="analysis-tools" hidden><label id="output-metric-filter" hidden>产出指标<select id="output-metric"><option value="">全部指标</option><option value="total_reads">Total Reads</option><option value="total_receptor_rna">Total Receptor RNA</option><option value="total_unique_cdr3s">Total Unique CDR3s</option></select></label><label id="gene-compare-filter" hidden>比较方式<select id="gene-compare"><option value="">各样本独立排序</option><option value="aligned">同链基因对齐</option></select></label><span id="comparison-note" class="caption" role="status"></span></div>'
    yield '<div class="pager" id="gallery-pager"><span id="fig-count" role="status"></span><button id="fig-prev" aria-label="上一页图表">上一页</button><button id="fig-next" aria-label="下一页图表">下一页</button></div><div id="gallery" class="gallery" tabindex="-1"></div><p id="fig-empty" class="empty" hidden>当前范围暂无可用数据。</p><div class="pager bottom-pager" id="gallery-bottom" hidden><button id="return-filters">返回筛选 ↑</button><span id="bottom-count"></span><button id="bottom-prev" aria-label="上一页图表">上一页</button><button id="bottom-next" aria-label="下一页图表">下一页</button></div></section></div></div><noscript><p>启用 JavaScript 后可查看交互图表。样本信息已在上方列出。</p></noscript></main>'
    yield '<dialog id="figure-dialog" aria-labelledby="figure-dialog-title"><div class="figure-dialog-head"><h2 id="figure-dialog-title">图表查看</h2><button id="figure-dialog-close" aria-label="关闭放大图表">关闭 ×</button></div><div id="figure-dialog-content"></div></dialog>'
    yield '<script id="report-data" type="application/json">'+json_text(public)+'</script>'
    for source in tables:
        yield '<script id="table-'+source['id']+'" type="application/json">{"columns":'+json_text(source['columns'])+',"rows":['
        first=True
        for row in chart_data.rows(source):
            if not first:yield ','
            first=False;yield json_text(row)
        yield ']}</script>'
    visible_ids={f['id'] for f in public_figures if not f['interactive']}
    for i,(figure,path) in enumerate(figures):
        if f'figure-{i}' not in visible_ids:continue
        yield f'<template id="figure-{i}"><img alt="'+escape(str(figure.get('title','')),quote=True)+'" src="data:image/png;base64,'
        with path.open('rb') as handle:
            while block:=handle.read(48*1024):yield base64.b64encode(block).decode('ascii')
        yield '"></template>'
    for name in ('charts.min.js','charts.js','report.js'):yield '<script>'+(ASSETS/name).read_text(encoding='utf-8').replace('</script','<\\/script')+'</script>'
    yield '</body></html>'

def write(destination,data,root,*,run_id=''):
    from scigblast.locus_split import atomic_text
    data.setdefault('report_display_id',secrets.token_hex(4));assets=prepare(data,root)
    def output(handle):
        for chunk in stream(data,assets,run_id=run_id):handle.write(chunk)
    atomic_text(destination,output)
    return dict(version=VERSION,viewer_signature=viewer_signature(),file=Path(destination).name,figures=len(assets[0]),tables=len(assets[1]),chart_schema=chart_data.VERSION)
