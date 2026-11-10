"""Queued Raw-R1 primer checks and verified reuse before full reporting."""
from __future__ import annotations

from collections import defaultdict
from html import escape
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .catalog import assigned_primers, build_catalog, chains_by_sample
from .io import digest, safe_file, version, write_json, write_table
from .primer_metrics import build_raw, raw_source
from .sources import verify

VERSION = 'raw-primer-check-v1'


def fingerprint(root, items, catalog, allowed_roots):
    """Include unavailable sources so restored Raw inputs invalidate a check."""
    sources = []
    for item in items:
        meta = json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else {}
        path, evidence = raw_source(item, root, meta, allowed_roots)
        upstream = meta.get('upstream') or {}
        if path is None and upstream.get('sources', {}).get('raw_R1'):
            folder = Path(item['trace']).parent
            archived = []
            for name, expected in upstream['files'].items():
                checked = safe_file(folder/name, root)
                actual = version(checked)
                if actual != expected:
                    raise ValueError('原始 R1 归档已变化，请重新预检查')
                archived.append((name, actual))
            sources.append((item['id'], 'archived_raw_source', archived))
        else:
            sources.append((item['id'], evidence, str(path) if path else None, version(path) if path else None))
    code = [hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in
            ('primer_check.py', 'primer_metrics.py', 'catalog.py', 'alternatives.py', 'matching.py')]
    return digest([VERSION, str(Path(root).resolve()), sorted(items, key=lambda r: r['id']),
                   catalog['version'], sources, code])


def load(folder, root, items, catalog, allowed_roots):
    folder = Path(folder)
    path = safe_file(folder/'.primer-check.json', folder)
    manifest = json.loads(safe_file(folder/'manifest.json', folder).read_text(encoding='utf-8'))
    if version(path) != manifest.get('primer_check_version'):
        raise ValueError('引物预检查记录已变化，请重新运行预检查')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('fingerprint') != fingerprint(root, items, catalog, allowed_roots):
        raise ValueError('样本、引物配置或原始数据已变化，请重新运行引物预检查')
    for receipt in data['metrics']['sources']:
        if version(receipt['path']) != receipt['version']:
            raise ValueError('原始数据已变化，请重新运行引物预检查')
    return data


def sample_reviews(items, catalog, metrics):
    matching = defaultdict(dict)
    for row in metrics['matching']:
        matching[row['sample']][row['match_class']] = row
    unavailable = {r['sample'] for r in metrics['availability'] if r['status'] not in
                   {'frozen_raw_source', 'current_raw_source', 'archived_raw_source'}}
    result = []
    threshold = float(catalog['config'].get('primer_match_threshold', 50))
    for sample, chains in chains_by_sample(items).items():
        values = matching[sample]
        unique = values.get('unique', {})
        total = unique.get('denominator')
        complete = sample not in unavailable and total is not None and total > 0
        rate = unique.get('percent') if complete else None
        state = 'unavailable' if not complete else 'low' if rate < threshold else 'ok'
        if complete and unique.get('unassessed_count'):
            state = 'review'
        result.append(dict(sample=sample, groups={chain:sorted({p['group'] for p in assigned_primers(catalog, sample, chain)}) for chain in chains},
            total_reads=total if complete else None, unique_percent=rate,
            multiple_percent=values.get('multiple', {}).get('percent') if complete else None,
            unmatched_percent=values.get('unmatched', {}).get('percent') if complete else None,
            unassessed_count=unique.get('unassessed_count', 0), status=state))
    return result


def html_report(data):
    def text(value):
        return escape(str(value))

    def number(value):
        return 'N/A' if value is None else f'{value:,}'

    def percent(value):
        return 'N/A' if value is None else f'{value:.2f}%'

    def table(headers, rows, kind='', *, grouped=False):
        def cells(values):
            return '<tr>'+''.join('<td>'+text(value)+'</td>' for value in values)+'</tr>'

        if grouped:
            samples = defaultdict(list)
            for row in rows:
                samples[row[0]].append(row[1:])
            headers = headers[1:]
            blocks = []
            for index, (sample, values) in enumerate(samples.items(), 1):
                heading = '<tr class="sample-divider"><th scope="rowgroup" colspan="'+str(len(headers))+'">'+\
                    '<div class="sample-heading"><span class="sample-index">'+f'{index:02d}'+'</span><strong>'+text(sample)+\
                    '</strong><small>'+str(len(values))+' 项</small></div></th></tr>'
                blocks.append('<tbody class="sample-block" data-sample="'+text(sample)+'">'+heading+''.join(cells(row) for row in values)+'</tbody>')
            body = ''.join(blocks)
        else:
            body = '<tbody>'+''.join(cells(row) for row in rows)+'</tbody>'
        return '<div class="scroll '+kind+'"><table><thead><tr>'+''.join('<th>'+text(h)+'</th>' for h in headers)+'</tr></thead>'+body+'</table></div>'

    labels = {'ok':'达到匹配阈值', 'low':'低于匹配阈值', 'unavailable':'原始数据不可计算', 'review':'部分 reads 无法评估'}
    content = table(['样本', '所选引物组', 'Raw R1 reads', '唯一匹配', '多重匹配', '未匹配', '核对提示'], [
        [r['sample'], ' / '.join(c+' · '+', '.join(g) for c, g in r['groups'].items()), number(r['total_reads']),
         percent(r['unique_percent']), percent(r['multiple_percent']), percent(r['unmatched_percent']), labels[r['status']]] for r in data['checks']], 'overview')
    sample_order = {row['sample']:index for index, row in enumerate(data['checks'])}
    primer_rows = sorted(data['metrics']['rows'], key=lambda row:sample_order.get(row['sample'], len(sample_order)))
    primers = table(['样本', '链', '引物全名', '唯一匹配 reads', '占样本 Raw R1'], [
        [r['sample'], r['chain'], r['primer'], number(r['matched_reads']), percent(r['match_percent'])] for r in primer_rows], 'primers', grouped=True)
    suspected = [r for r in data['review']['comparisons'] if r['status']=='suspected']
    alternatives = table(['样本', '其他引物组', '唯一匹配率', '标记'], [
        [r['sample'], r['tested_group'], percent(r['unique_percent']), 'Suspected'] for r in suspected], 'alternatives', grouped=True)
    alternative_section = '<h2>其他引物组测试</h2>'+alternatives if suspected else ''
    return f'''<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{text(data['task_name'])} · 引物匹配预检查</title>
<style>
body{{font:14px/1.6 system-ui,sans-serif;color:#243e54;background:#f3f6fa;margin:0}}
main{{max-width:1400px;margin:32px auto;padding:24px;background:#fff;border:1px solid #dce4ec;border-radius:10px}}
h1{{font-size:25px}} h2{{font-size:18px;margin-top:28px}} p{{color:#617285}}
.scroll{{overflow:auto;max-height:600px;border:1px solid #dce4ec}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
th,td{{padding:10px 12px;border-bottom:1px solid #dce4ec;text-align:left;white-space:nowrap}}
.overview td{{padding-top:14px;padding-bottom:14px;border-bottom:5px solid #fff;background:#f5f8fc}}
.overview tr:nth-child(even) td{{background:#edf2f8}}
.overview td:first-child{{font-weight:700;color:#244b71;border-left:3px solid #7c9bbd}}
.overview td:nth-child(2){{white-space:normal;min-width:180px;max-width:280px}}
th{{background:#eef3f7;position:sticky;top:0;z-index:1}}
.sample-block td{{background:#fff}} .sample-block:nth-of-type(even) td{{background:#f7f9fc}}
.sample-divider th{{position:static;padding:12px;border-left:3px solid #6c8db2;background:#e7eef6;color:#243e54}}
.sample-block+.sample-block .sample-divider th{{border-top:10px solid #fff}}
.sample-heading{{display:flex;align-items:center;gap:10px}}
.sample-heading strong{{font-size:13px}} .sample-heading small{{margin-left:auto;font-weight:400;color:#617285}}
.sample-index{{font-size:11px;color:#526f8b;border:1px solid #b9c9da;background:#fff;border-radius:4px;padding:1px 6px}}
@media(max-width:600px){{main{{margin:0;padding:16px}}}}
</style></head><body><main>
<h1>{text(data['task_name'])}</h1><h2>引物匹配预检查</h2>
<p>最初始 _j 文件的全部 Raw R1 reads 为分母。5′ 起点全长 IUPAC 匹配。MIX 以各链引物并集统计，共享输入只计一次。</p>
<p>唯一匹配率阈值 {text(data['threshold'])}%。匹配结果供人工核对引物配置。</p>
{content}
<h2>逐引物匹配</h2>{primers}
{alternative_section}
</main></body></html>'''


def execute(request, output):
    root, output = Path(request['input_root']).resolve(), Path(output)
    items = request['items']
    if not items:
        raise ValueError('请选择至少一项样本链结果')
    verify(items, root)
    if output.exists():
        raise ValueError('预检查目录已存在，请创建新版本')
    config = request['primer']
    if not config or not config.get('primer_path'):
        raise ValueError('请先配置引物表与样本引物分配')
    scope = chains_by_sample(items)
    catalog = build_catalog(config.get('submission_path'), config['primer_path'], config.get('columns', {}), sorted(scope), scope)
    allowed_roots = request.get('allowed_input_roots', [str(root)])
    before = fingerprint(root, items, catalog, allowed_roots)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent/('.'+output.name+'.'+uuid.uuid4().hex)
    staging.mkdir()

    def progress(done, total, message):
        if request.get('progress_path'):
            write_json(request['progress_path'], dict(done=done, total=total, message=message, phase='primer_check'))
        print(f'[PRIMER CHECK] {done}/{total} {message}', flush=True)

    try:
        metrics = build_raw(items, root, catalog=catalog, allowed_roots=allowed_roots, progress=progress)
        from .alternatives import compare_raw
        review = compare_raw(items, root, catalog, metrics['matching'], allowed_roots=allowed_roots, progress=progress)
        verify(items, root)
        if before != fingerprint(root, items, catalog, allowed_roots):
            raise ValueError('原始数据在预检查期间发生变化，请重新检查')
        data = dict(analysis_kind='primer_check', task_name=request.get('task_name') or root.name,
                    threshold=float(catalog['config'].get('primer_match_threshold', 50)),
                    checks=sample_reviews(items, catalog, metrics),
                    metrics={k:v for k,v in metrics.items() if k not in {'sources', 'availability'}},
                    review=review, figures=[], exports={})
        for name, rows in [('sample_matching', metrics['matching']), ('chain_matching', metrics['chain_matching']),
                           ('primer_matching', metrics['rows']), ('alternative_groups', review['comparisons'])]:
            if rows:
                filename = name+'.csv'
                write_table(staging/filename, rows, list(rows[0]))
                data['exports'][name] = filename
        write_json(staging/'.primer-check.json', dict(fingerprint=before, metrics=metrics, review=review))
        write_json(staging/'summary.json', data)
        (staging/'report.html').write_text(html_report(data), encoding='utf-8')
        files = ['summary.json', 'report.html', *data['exports'].values()]
        write_json(staging/'manifest.json', dict(version=VERSION, files=files, primer_check_version=version(staging/'.primer-check.json'),
                   file_versions={name:version(staging/name) for name in files}))
        os.replace(staging, output)
        progress(len(scope), len(scope), '引物匹配预检查完成，请核对后生成报告')
        return dict(samples=len(scope))
    finally:
        if staging.exists():
            if staging.is_symlink() or staging.resolve().parent != output.parent.resolve():
                raise ValueError('Unsafe primer-check staging directory')
            shutil.rmtree(staging)
