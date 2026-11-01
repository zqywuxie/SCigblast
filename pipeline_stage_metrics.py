"""Stage metrics with explicit units and provenance, including historical reports."""
from __future__ import annotations

import json
from pathlib import Path

from analysis.repertoire.io import table, version
from analysis.repertoire.sources import files_under


def collect(root, items):
    root = Path(root).resolve()
    rows = []
    def namespace(path):
        parts=Path(path).relative_to(root).parts
        stages={'01.match','02.fastp','03.clean_data','04.pandaseq','05.igblastn_out'}
        terminal={'report','data','sample_manifest.csv','igblast_summary.tsv','chain_summary.csv','clean_summary.csv','clean_counts.csv','pandaseq_summary.csv'}
        for i,part in enumerate(parts):
            if part in stages:
                return parts[i+1] if i+1<len(parts) and parts[i+1] not in terminal else ''
        return ''
    selected={(item['sample_label'],item['pair']) for item in items}
    selected_scopes={(item['sample_label'],item['pair'],namespace(item['path'])) for item in items}
    identities=set(selected)
    for summary in files_under(root,{'sample_manifest.csv','igblast_summary.tsv'}):
        for record in table(summary):
            if record.get('sample_id') is not None:identities.add((record['sample_id'],record.get('pair_id','')))
    def identity(sample,pair):
        if (sample,pair) in identities:return sample,pair
        candidates={(s,p) for s,p in identities if p==pair or Path(str(p).replace('\\','/')).name==pair}
        return next(iter(candidates)) if len(candidates)==1 else None
    def add(sample, pair, stage, metric, count, unit, source, denominator=None, note='', chain='',scope=None):
        if selected:
            resolved=identity(sample,pair)
            if resolved is None or resolved not in selected:return
            sample,pair=resolved
            if (sample,pair,namespace(source) if scope is None else scope) not in selected_scopes:return
        if count is not None:
            count = int(count)
            if count < 0:
                count = None
        rows.append(dict(sample=sample, pair=pair, chain=chain, stage=stage, metric=metric,
            count=count, unit=unit, denominator=denominator,
            percent=count*100/denominator if count is not None and denominator else None,
            source=str(Path(source).relative_to(root)), source_version=version(source), note=note))
    labels = {}
    prepared_seen=set()
    for item in items:
        if item.get('trace'):
            evidence=json.loads(Path(item['trace']).read_text(encoding='utf-8'))
            preparation=evidence.get('primer_preparation')
            if preparation and preparation['manifest'] not in prepared_seen:
                prepared_seen.add(preparation['manifest'])
                source=Path(preparation['manifest'])
                n=preparation['original_queries']
                trimmed=sum(value for name,value in preparation['counts'].items() if name.startswith('trimmed_'))
                for metric,count in (('before_primer_trim',n),('trimmed_queries',trimmed),('untrimmed_queries',n-trimmed),('submitted_after_primer_trim',preparation['submitted_queries'])):
                    add(item['sample_label'],item['pair'],'primer',metric,count,'queries',source,n,scope=namespace(item['path']))
    for item in items:
        labels.setdefault(item['pair'], set()).add(item['sample_label'])
    for path in files_under(root, {'fastp_summary.csv'}):
        for row in table(path):
            if row.get('status', 'OK').upper() != 'OK':
                continue
            report = None
            if row.get('json_path'):
                candidate = path.parent / row['json_path']
                if candidate.is_file() and candidate.resolve().is_relative_to(root) and not candidate.is_symlink():
                    report = candidate
            if report is None and row.get('r1_output'):
                # Match through the exact stage-relative path, not basename search.
                out = Path(row['r1_output'])
                try:
                    relative = out.relative_to(path.parent.parent / 'data')
                    candidate = path.parent / relative.parent / (out.name.removesuffix('_R1.fq.gz') + '.json')
                    if candidate.is_file() and candidate.resolve().is_relative_to(root) and not candidate.is_symlink():
                        report = candidate
                except ValueError:
                    pass
            pair = row.get('pair_id') or row.get('sample') or ''
            candidates = labels.get(pair, set())
            sample = next(iter(candidates)) if len(candidates) == 1 else row.get('sample_id') or pair
            if report:
                data = json.loads(report.read_text(encoding='utf-8'))
                before, after = (data.get('summary', {}).get(k, {}) for k in ('before_filtering', 'after_filtering'))
                add(sample, pair, 'raw', 'total_reads', before.get('total_reads'), 'read ends', report)
                add(sample, pair, 'fastp', 'total_reads', after.get('total_reads'), 'read ends', report, before.get('total_reads'))
                for reason, count in data.get('filtering_result', {}).items():
                    if reason.endswith('_reads'):
                        add(sample, pair, 'fastp_filter', reason, count, 'read ends', report, before.get('total_reads'))
            elif row.get('before_reads') is not None:
                add(sample, pair, 'raw', 'total_reads', row.get('before_reads'), 'read ends', path, note='历史汇总；未核验 FASTQ')
                add(sample, pair, 'fastp', 'total_reads', row.get('after_reads'), 'read ends', path, note='历史汇总；未核验 FASTQ')
            else:
                add(sample, pair, 'fastp', 'total_reads', None, 'read ends', path, note='旧 Pig R1/R2 摘要口径不确定，缺少 JSON')
    for path in files_under(root, {'clean_summary.csv','clean_counts.csv'}):
        if path.name=='clean_summary.csv' and (path.parent/'clean_counts.csv').is_file():continue
        for row in table(path):
            if row.get('status', 'OK').upper() != 'OK':
                continue
            values=[row.get(mate+'_reads','') for mate in ('r1','r2')]
            total=sum(map(int,values)) if all(str(v).isdigit() for v in values) else None
            add(row.get('sample_id',''),row.get('pair_id',''),'clean','total_reads',total,'read ends',path,
                note='R1 + R2 阶段观测计数' if total is not None else '历史摘要无实际计数；不推定 100%')
    for path in files_under(root, {'pandaseq_summary.csv'}):
        for row in table(path):
            if row.get('status', 'OK').upper() != 'OK':
                continue
            sample, pair = row.get('sample_id', ''), row.get('pair_id', '')
            before = int(row['input_pairs']) if row.get('input_pairs') else None
            after = int(row['merged_sequences']) if row.get('merged_sequences') else None
            add(sample, pair, 'clean', 'merge_input', before, 'read pairs', path, note='PANDAseq 输入阶段计数；历史配对 ID 未核验')
            add(sample, pair, 'merge', 'merged_sequences', after, 'queries', path, before, note='分母为输入 read pairs')
    for item in items:
        row = item['summary']
        source = Path(item['summary_path']) if item.get('summary_path') else None
        if source:
            for metric, key in (('input_queries','input_sequences'), ('valid_v_rows','mapped_rows'), ('productive_rows','productive_rows'), ('retained_rows','output_rows')):
                add(item['sample_label'], item['pair'], 'mapping', metric, row.get(key), 'queries' if metric=='input_queries' else 'AIRR rows', source, chain=item['chain'], note='行数与唯一 query 数分开；有效 V 与 productive 为交叉统计')
    # Emit explicit unknown rows rather than presenting missing stages as zero.
    for item in items:
        for stage in ('raw', 'fastp', 'clean', 'merge'):
            if not any(r['sample']==item['sample_label'] and r['pair']==item['pair'] and r['stage']==stage for r in rows):
                add(item['sample_label'], item['pair'], stage, 'unavailable', None, '', item['path'], note='未找到可核验的阶段报告')
    unique = {json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in rows}
    return list(unique.values())


def summarize_volume(stages, samples):
    """Sample-level volume changes, counting each selected pair once upstream."""
    grouped={}
    for row in stages:
        grouped.setdefault(row['sample'],[]).append(row)
    for item in samples:
        grouped.setdefault(item['sample'],[])
    result=[]
    for sample,rows in sorted(grouped.items()):
        selected=[item for item in samples if item['sample']==sample]
        pairs={item['pair'] for item in selected} or {row['pair'] for row in rows}
        if not pairs:continue
        def total(stage,metric,unit,scope=None,chain=''):
            values=[]
            for pair in sorted(pairs if scope is None else scope):
                counts={row['count'] for row in rows if row['pair']==pair and row['stage']==stage and row['metric']==metric and row['unit']==unit and row['chain']==chain}
                if len(counts)!=1 or None in counts:return None
                values.append(next(iter(counts)))
            return sum(values) if values else None
        def add(stage,label,count,unit,*,chain='',scope=None,previous=None,relation='',note=''):
            scope=pairs if scope is None else scope
            before=previous['count'] if previous else None
            comparable=previous is not None and (previous['unit']==unit or relation=='merge')
            row=dict(sample=sample,stage=stage,label=label,chain=chain,count=count,unit=unit,
                     pair_count=len(scope),pair_scope=json.dumps(sorted(scope),ensure_ascii=False),
                     previous_stage=previous['stage'] if previous else '',previous_count=before,
                     previous_unit=previous['unit'] if previous else '',
                     reduction=before-count if comparable and before is not None and count is not None else None,
                     reduction_unit=previous['unit'] if comparable else '',
                     retention_percent=count*100/before if comparable and before and count is not None else None,
                     rate_basis=relation or ('same_unit' if comparable else ''),note=note)
            result.append(row)
            return row
        raw=add('raw','原始输入',total('raw','total_reads','read ends'),'read ends',note='R1 + R2 reads；仅汇总本次选择的配对')
        fastp=add('fastp','Fastp 后',total('fastp','total_reads','read ends'),'read ends',previous=raw)
        add('clean','Header 清理后',total('clean','total_reads','read ends'),'read ends',previous=fastp)
        merge_input=add('merge_input','合并输入',total('clean','merge_input','read pairs'),'read pairs',note='实际输入配对数；reads 转为配对数不计算损失')
        add('merged','双端合并后',total('merge','merged_sequences','queries'),'queries',previous=merge_input,relation='merge',note='合并率 = 合并序列数 / 输入配对数')
        chains=sorted({item['chain'] for item in selected} or {row['chain'] for row in rows if row['stage']=='mapping' and row['chain']})
        for chain in chains:
            scope={item['pair'] for item in selected if item['chain']==chain} or {row['pair'] for row in rows if row['stage']=='mapping' and row['chain']==chain}
            merged=dict(stage='merged',count=total('merge','merged_sequences','queries',scope),unit='queries')
            mapping=add('mapping_input','IgBLAST 输入',total('mapping','input_queries','queries',scope,chain),'queries',chain=chain,scope=scope,previous=merged,note='各链输入独立计数，不跨链相加')
            retained=total('mapping','retained_rows','AIRR rows',scope,chain)
            if retained is None:
                final=[item for item in selected if item['chain']==chain]
                if final and all(item['unit']=='AIRR rows' for item in final):
                    retained=sum(item['observations'] for item in final)
            add('mapping_output','最终保留结果',retained,'AIRR rows',chain=chain,scope=scope,previous=mapping,note='结果按 AIRR 行数计，与输入序列数分别展示')
    return result
