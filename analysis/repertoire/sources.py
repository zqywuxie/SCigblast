"""Read-only discovery of final chain results and available historical evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .io import CHAINS, digest, safe_file, table, version


def files_under(root, names=None):
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in {'analysis', 'treemap', 'logs'} and not (Path(current)/d).is_symlink())
        for name in sorted(files):
            if names is None or name in names:
                candidate = Path(current)/name
                if not candidate.is_symlink():
                    yield candidate


def discover(root):
    root = Path(root).resolve()
    items, seen = [], set()
    # Prefer one authoritative summary per stage, never count its CSV/TSV twins.
    summaries = {}
    for path in files_under(root, {'igblast_summary.tsv', 'chain_summary.csv'}):
        if path.parent not in summaries or path.name == 'igblast_summary.tsv':
            summaries[path.parent] = path
    for parent, summary in sorted(summaries.items()):
        for row in table(summary):
            if row.get('status', '').upper() != 'OK' or row.get('chain') not in CHAINS:
                continue
            raw = row.get('output_path', '')
            if not raw:
                continue
            path = safe_file(Path(raw) if Path(raw).is_absolute() else parent / raw, root)
            sample, pair = row.get('sample_id') or path.parent.name, row.get('pair_id', '')
            dataset = str(parent.relative_to(root))
            key = digest([dataset, sample, pair])[:24]
            if str(path) in seen:
                continue
            seen.add(str(path))
            items.append(dict(id=digest([key, row['chain'], str(path)]), sample_key=key, sample_label=sample,
                pair=pair, dataset=dataset, chain=row['chain'], path=str(path), version=version(path), summary=row))
            items[-1]['summary_path']=str(summary)
    if not summaries:
        for path in files_under(root):
            match = re.fullmatch(r'(?:(.+)__)?(TRA|TRB|TRD|TRG|IGH|IGK|IGL)\.(tsv|csv)(\.gz)?', path.name, re.I)
            if not match:
                continue
            path = safe_file(path, root)
            first = next(table(path), None)
            if first is None or not {'sequence_id', 'v_call'} <= first.keys():
                continue
            sample = match[1] or path.parent.name
            key = digest([str(path.parent.relative_to(root)), sample])[:24]
            items.append(dict(id=digest([key, match[2], str(path)]), sample_key=key, sample_label=sample,
                pair='', dataset='', chain=match[2].upper(), path=str(path), version=version(path), summary={}))
    if len(items) > 10000:
        raise ValueError('结果超过 10000 项，请缩小输入目录')
    trace = root / '.analysis' / 'trace'
    trace_paths = []
    if trace.is_dir() and not trace.is_symlink():
        for directory in trace.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                trace_paths.extend(directory.glob('*.json'))
    by_output = {}
    for marker in trace_paths:
        try:
            data = json.loads(safe_file(marker, root).read_text(encoding='utf-8'))
            if data.get('version') in {'mapping-trace-v1', 'mapping-trace-v2'} and data.get('output_path'):
                annotation = safe_file(marker.parent / data['annotation'], root)
                result = safe_file(data['output_path'], root)
                if version(annotation) == data['annotation_version'] and version(result) == data.get('output_version'):
                    by_output[str(Path(data['output_path']).resolve())] = str(marker)
        except (OSError, ValueError, KeyError):
            continue
    for item in items:
        item['trace'] = by_output.get(item['path'])
        item['trace_version'] = version(item['trace']) if item['trace'] else None
        item['merged']=None
        if item.get('summary_path'):
            parent=Path(item['summary_path']).parent
            final=Path(item['path'])
            if parent.parent.name=='05.igblastn_out' and final.is_relative_to(parent):
                base=parent.parent.parent/'04.pandaseq'/parent.name/final.parent.relative_to(parent)
                stems={re.sub(r'[^A-Za-z0-9._-]+','_',item['pair']).strip('_'),
                       re.sub(r'[^A-Za-z0-9._-]+','_',Path(item['pair']).name).strip('_')}
                candidates=[base/(stem+'_merged.fasta') for stem in stems if stem and (base/(stem+'_merged.fasta')).is_file()]
                if len(candidates)==1:
                    merged=safe_file(candidates[0],root)
                    item['merged']=dict(path=str(merged),version=version(merged))
        full=False
        if item['trace']:
            full=bool(json.loads(Path(item['trace']).read_text(encoding='utf-8')).get('upstream'))
        item['capabilities'] = dict(final_genes=True, prefilter_annotations=bool(item['trace']),
            primer_sequence='完整比对输入' if item['trace'] or item['merged'] else '仅最终 TSV 的序列字段（存在时）', upstream_lineage=False,
            reference_catalog=bool(item['trace']))
        item['capabilities']['upstream_lineage']=full
    return sorted(items, key=lambda item: (item['sample_label'], item['pair'], item['chain']))


def verify(items, root):
    for item in items:
        path = safe_file(item['path'], root)
        if version(path) != item['version']:
            raise ValueError('来源结果已变化，请重新扫描')
        if item.get('trace'):
            trace=safe_file(item['trace'], root)
            if version(trace)!=item.get('trace_version'):
                raise ValueError('追溯版本已变化，请重新扫描')
            evidence=json.loads(trace.read_text(encoding='utf-8'))
            dependencies={evidence['annotation']:evidence['annotation_version'],**evidence.get('query_files',{}),**(evidence.get('upstream') or {}).get('files',{})}
            for name,expected in dependencies.items():
                if version(safe_file(trace.parent/name,root))!=expected:
                    raise ValueError('追溯文件已变化，请重新生成证据或选择可用的历史结果范围')
            preparation=evidence.get('primer_preparation')
            if preparation:
                for name,expected in preparation['files'].items():
                    if version(safe_file(Path(preparation['folder'])/name,root))!=expected:raise ValueError('primer 归属/剪切文件已变化')
        if item.get('merged'):
            merged=safe_file(item['merged']['path'],root)
            if version(merged)!=item['merged']['version']:raise ValueError('合并序列已变化，请重新扫描')
