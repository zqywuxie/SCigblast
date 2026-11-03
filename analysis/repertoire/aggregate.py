"""Streaming query/primer/gene summaries backed by a private detail index."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .catalog import Matcher
from .matching import summarize_matching
from .io import digest, safe_file, table, version
from .batch import tune

BAD = {'', '*', '-', 'NA', 'N/A', 'NONE', 'NULL', 'NO_HIT', 'UNMAPPED', 'NOT_FOUND'}


def calls(value):
    return sorted({v.strip().split('*')[0] for v in re.split(r'[,;]', value or '') if v.strip().upper() not in BAD})


def attach_primers(conn,meta,root):
    preparation=meta.get('primer_preparation')
    if not preparation:return False
    folder=Path(preparation['folder'])
    index=safe_file(folder/preparation['assignments'],root)
    if version(index)!=preparation['files'][preparation['assignments']]:raise ValueError('mapping 前的 primer 归属索引已变化')
    conn.execute('ATTACH DATABASE ? AS primers',(index.as_uri()+'?mode=ro',))
    return True


def mapping_observations(item, root,archive_root=None):
    """One observation per query; ambiguous IDs/duplicate AIRR rows are explicit."""
    if item.get('trace'):
        marker = safe_file(item['trace'], root)
        meta = json.loads(marker.read_text(encoding='utf-8'))
        folder = marker.parent
        annotation = safe_file(folder/meta['annotation'], root)
        if version(annotation) != meta['annotation_version']:
            raise ValueError('注释证据已变化')
        queries = safe_file(folder/meta['query_index'], root)
        archive = safe_file(folder/meta['query_archive'], root)
        from Bio import bgzf
        conn = sqlite3.connect(annotation.as_uri()+'?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute('ATTACH DATABASE ? AS q', (queries.as_uri()+'?mode=ro',))
        try:
            conn.executescript('''PRAGMA temp_store=FILE;
                CREATE TEMP TABLE query_counts AS SELECT sequence_id,count(*) AS n FROM q.queries GROUP BY sequence_id;
                CREATE INDEX temp.query_count_id ON query_counts(sequence_id);
                CREATE TEMP TABLE annotation_counts AS SELECT sequence_id,count(*) AS n,min(ordinal) AS ordinal
                    FROM annotations GROUP BY sequence_id;
                CREATE INDEX temp.annotation_count_id ON annotation_counts(sequence_id);''')
            has_primers=attach_primers(conn,meta,root)
            extra=(',p.names AS assigned_primers,p.families AS assigned_families,p.state AS trim_state,p.trim_length AS trim_length'
                   if has_primers else ',NULL AS assigned_primers,NULL AS assigned_families,NULL AS trim_state,0 AS trim_length')
            sql = '''SELECT q.ordinal AS query_ordinal,q.sequence_id AS query_id,q.offset,
                coalesce(qc.n,0) AS query_duplicates,coalesce(ac.n,0) AS annotation_count,
                a.*'''+extra+''' FROM q.queries q LEFT JOIN query_counts qc ON qc.sequence_id=q.sequence_id
                LEFT JOIN annotation_counts ac ON ac.sequence_id=q.sequence_id
                LEFT JOIN annotations a ON a.ordinal=ac.ordinal'''+(
                ' LEFT JOIN primers.assignments p ON p.ordinal=q.ordinal' if has_primers else '')+' ORDER BY q.ordinal'
            with bgzf.BgzfReader(str(archive), 'rb') as incoming:
                for record in conn.execute(sql):
                    row = dict(record)
                    incoming.seek(row['offset'])
                    seq = incoming.readline().decode('ascii').strip()
                    if row['query_duplicates'] != 1:
                        reason = 'ambiguous_lineage'
                    elif row['annotation_count'] == 0:
                        reason = 'missing_airr_row'
                    elif row['annotation_count'] > 1:
                        reason = 'duplicate_airr_rows'
                    else:
                        reason = row['filter_reason']
                    if reason in {'ambiguous_lineage', 'duplicate_airr_rows', 'missing_airr_row'}:
                        row.update(v_call=None, j_call=None, c_call=None, locus=None)
                    row.update(sequence_id=row['query_id'], sequence=seq, filter_reason=reason,
                        matching_source=meta['query_version'], matching_ordinal=row['query_ordinal'], matching_unit='queries')
                    yield row, 'all_queries', meta
        finally:
            conn.close()
    elif item.get('merged') and archive_root is not None:
        from pipeline_trace import capture_queries
        from Bio import bgzf
        folder=Path(archive_root)/digest(item['merged'])[:24]
        query_meta=capture_queries(folder,item['merged']['path'])
        index=folder/('historical-'+uuid.uuid4().hex+'.sqlite')
        conn=sqlite3.connect(index,uri=True)
        conn.row_factory=sqlite3.Row
        try:
            conn.execute('CREATE TABLE final_rows(ordinal INTEGER PRIMARY KEY,sequence_id TEXT,data TEXT)')
            for ordinal,row in enumerate(table(item['path']),1):
                conn.execute('INSERT INTO final_rows VALUES(?,?,?)',(ordinal,row.get('sequence_id',''),json.dumps(row)))
                if ordinal%20000==0:conn.commit()
            conn.execute('CREATE INDEX final_id ON final_rows(sequence_id)');conn.commit()
            conn.execute('ATTACH DATABASE ? AS q',((folder/query_meta['index']).as_uri()+'?mode=ro',))
            sql='''SELECT q.ordinal,q.sequence_id,q.offset,f.data,
                (SELECT count(*) FROM q.queries z WHERE z.sequence_id=q.sequence_id) AS qcount,
                (SELECT count(*) FROM final_rows z WHERE z.sequence_id=q.sequence_id) AS acount
                FROM q.queries q LEFT JOIN final_rows f ON f.sequence_id=q.sequence_id
                AND f.ordinal=(SELECT min(ordinal) FROM final_rows z WHERE z.sequence_id=q.sequence_id)
                ORDER BY q.ordinal'''
            with bgzf.BgzfReader(str(folder/query_meta['archive']),'rb') as incoming:
                for record in conn.execute(sql):
                    row=json.loads(record['data']) if record['data'] and record['qcount']==1 and record['acount']==1 else {}
                    reason='ambiguous_lineage' if record['qcount']!=1 else 'duplicate_airr_rows' if record['acount']>1 else 'retained_history' if record['acount']==1 else 'not_in_final_reason_unknown'
                    incoming.seek(record['offset'])
                    row.update(sequence_id=record['sequence_id'],sequence=incoming.readline().decode('ascii').strip(),filter_reason=reason,
                        matching_source=query_meta['sequence_sha256'],matching_ordinal=record['ordinal'],matching_unit='queries')
                    yield row,'all_queries_final_annotations_only',None
        finally:conn.close()
    else:
        for ordinal, row in enumerate(table(item['path']), 1):
            row['filter_reason'] = 'retained_history' if item.get('summary') else 'imported_result'
            row.update(matching_source='result:'+item['id'],matching_ordinal=ordinal,matching_unit='AIRR rows')
            yield row, 'retained_only', None


def observations(item,root,stage='mapping',archive_root=None):
    if stage in {'mapping','retained'}:
        for row,scope,meta in mapping_observations(item,root,archive_root):
            if stage=='retained':
                if row['filter_reason'] not in {'retained','retained_history','imported_result'}:continue
                scope='retained_only'
            yield row,scope,meta
        return
    if stage not in {'raw_R1','raw_R2'}:
        raise ValueError('未知引物匹配阶段')
    if not item.get('trace'):
        raise ValueError('该结果没有全流程追溯，不能分析原始读端')
    marker=safe_file(item['trace'],root)
    meta=json.loads(marker.read_text(encoding='utf-8'))
    upstream=meta.get('upstream')
    if not upstream:
        raise ValueError('该结果仅保存了比对输入；请选择可用的数据范围')
    folder=marker.parent
    for name,signature in upstream['files'].items():
        if version(safe_file(folder/name,root))!=signature:raise ValueError('上游追溯归档已变化')
    query_index=safe_file(folder/meta['query_index'],root)
    annotation=safe_file(folder/meta['annotation'],root)
    if version(annotation)!=meta['annotation_version']:raise ValueError('注释证据已变化')
    conn=sqlite3.connect(safe_file(folder/upstream['index'],root).as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    conn.execute('ATTACH DATABASE ? AS q',(query_index.as_uri()+'?mode=ro',))
    conn.execute('ATTACH DATABASE ? AS a',(annotation.as_uri()+'?mode=ro',))
    has_primers=attach_primers(conn,meta,root)
    mate=stage[-2:]
    from Bio import bgzf
    matching_source=digest(meta['upstream']['sources'][stage])
    try:
        with bgzf.BgzfReader(str(safe_file(folder/upstream['archive'],root)),'rb') as incoming:
            for read in conn.execute('SELECT * FROM reads WHERE stage=? ORDER BY ordinal',(stage,)):
                other='R2' if mate=='R1' else 'R1'
                counts={s:conn.execute('SELECT count(*) FROM reads WHERE stage=? AND canonical=?',(s,read['canonical'])).fetchone()[0]
                        for s in (stage,'fastp_'+mate,'clean_'+mate,'raw_'+other,'fastp_'+other,'clean_'+other)}
                queries=conn.execute('SELECT sequence_id,ordinal FROM q.queries WHERE canonical=?',(read['canonical'],)).fetchmany(2)
                annotated=[]
                if len(queries)==1:
                    annotated=conn.execute('SELECT * FROM a.annotations WHERE sequence_id=?',(queries[0][0],)).fetchmany(2)
                if counts[stage]!=1 or any(v>1 for v in counts.values()) or len(queries)>1:
                    reason='ambiguous_lineage'
                elif counts['raw_'+other]!=1:reason='pair_identity_unverified'
                elif all('fastp_'+m in upstream['sources'] for m in (mate,other)) and counts['fastp_'+mate]!=counts['fastp_'+other]:reason='pair_identity_unverified'
                elif all('clean_'+m in upstream['sources'] for m in (mate,other)) and counts['clean_'+mate]!=counts['clean_'+other]:reason='pair_identity_unverified'
                elif not counts['fastp_'+mate]:
                    reason='not_seen_after_fastp' if 'fastp_'+mate in upstream['sources'] else 'fastp_membership_unavailable'
                elif not counts['clean_'+mate]:reason='clean_membership_unavailable'
                elif not queries:reason='not_merged'
                elif not annotated:reason='missing_airr_row'
                elif len(annotated)>1:reason='duplicate_airr_rows'
                else:reason=annotated[0]['filter_reason']
                row=dict(annotated[0]) if len(annotated)==1 and reason not in {'ambiguous_lineage','pair_identity_unverified','not_seen_after_fastp','fastp_membership_unavailable','clean_membership_unavailable','not_merged'} else {}
                if has_primers and len(queries)==1:
                    prepared=conn.execute('SELECT names,families,state,trim_length FROM primers.assignments WHERE ordinal=?',(queries[0]['ordinal'],)).fetchone()
                    if prepared:row.update(assigned_primers=prepared['names'],assigned_families=prepared['families'],trim_state=prepared['state'],trim_length=prepared['trim_length'])
                incoming.seek(read['offset'])
                row.update(mapping_sequence_id=row.get('sequence_id'),
                    sequence=incoming.readline().decode('ascii').strip(),sequence_id=read['title'].split()[0],filter_reason=reason,
                    matching_source=matching_source,matching_ordinal=read['ordinal'],matching_unit='read ends')
                yield row,stage,meta
    finally:conn.close()


def aggregate(items, root, output, catalog=None, progress=None):
    output = Path(output)
    stage=(catalog or {}).get('config',{}).get('observation_stage','mapping')
    coverage_scopes={}
    datasets={}
    for item in items:
        datasets.setdefault(item['sample_label'],set()).add(item.get('dataset',''))
        scope=stage if stage!='mapping' else 'all_queries' if item.get('trace') else 'partial_queries' if item.get('merged') else 'final_rows'
        coverage_scopes.setdefault((item['sample_label'],item['chain']),set()).add(scope)
    if any(len(scopes)>1 for scopes in coverage_scopes.values()):
        raise ValueError('同一样本链的结果覆盖范围不同，请分开选择这些结果，或选择“仅最终保留结果”后分析，避免混合分母')
    if any(len(values)>1 for values in datasets.values()):
        raise ValueError('存在跨数据集同名样本，请按数据集分别选择结果后分析，避免混合样本身份')
    conn = sqlite3.connect(output/'details.sqlite')
    tune(conn)
    conn.execute('''CREATE TABLE observations (
        id INTEGER PRIMARY KEY, sample_key TEXT,sample TEXT,pair TEXT,chain TEXT,scope TEXT,
        sequence_id TEXT,match_class TEXT,primers TEXT,families INTEGER,filter_reason TEXT,
        v_call TEXT,j_call TEXT,c_call TEXT,sequence TEXT,
        mapping_primers TEXT,trim_state TEXT,trim_length INTEGER,
        matching_source TEXT,matching_ordinal INTEGER,matching_unit TEXT,mapping_sequence_id TEXT)''')
    match_counts, gene_counts, reason_counts, primer_counts, coverage = (Counter() for _ in range(5))
    family_counts=Counter()
    trim_counts=Counter()
    primer_state_counts=Counter()
    summaries, genes_seen = [], {}
    pending = []
    try:
        for number, item in enumerate(items, 1):
            sample, chain = item['sample_label'], item['chain']
            matcher = Matcher(catalog['assignments'][sample]) if catalog else None
            config=catalog['config'] if catalog else {}
            selected_stage=config.get('observation_stage','mapping')
            scope=selected_stage if selected_stage.startswith('raw_') else 'retained_only' if selected_stage=='retained' else 'all_queries' if item.get('trace') else 'all_queries_final_annotations_only' if item.get('merged') else 'retained_only'
            refs=json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else None
            count, sequence_available = 0, 0
            for row, scope, meta in observations(item, root,selected_stage,output/'.inputs'):
                count += 1
                refs = meta
                seq = row.get('sequence', '') or ''
                sequence_available += bool(seq)
                match_seq=seq
                structure_ok=True
                if config.get('read_structure')=='oligo' and seq:
                    adapter=config.get('adapter','ACACTCTTTCCCTACACGACGCTCTTCCGATCT').upper()
                    cut=len(adapter)+int(config.get('umi_length',10))+int(config.get('barcode_length',8))
                    structure_ok=seq.startswith(adapter) and len(seq)>cut
                    match_seq=seq[cut:] if structure_ok else ''
                frozen_names=json.loads(row['assigned_primers']) if row.get('assigned_primers') is not None else None
                names, families = (matcher.match(match_seq) if match_seq else ([],[])) if matcher else (frozen_names or [],json.loads(row.get('assigned_families') or '[]'))
                category = ('not_configured' if not matcher and frozen_names is None else 'sequence_unavailable' if not seq else
                            'read_structure_unavailable' if not structure_ok else
                            'unique' if len(names)==1 else 'multiple' if names else 'unmatched')
                reason = row['filter_reason']
                key = (sample, item['sample_key'], chain, scope)
                match_counts[key+(category,)] += 1
                family_counts[key+('not_assessed' if (not matcher and frozen_names is None) or not seq or not structure_ok else 'unique_family' if len(families)==1 else 'multiple_families' if families else 'unmatched',)] += 1
                reason_counts[key+(reason,)] += 1
                trim_counts[key+(row.get('trim_state') or 'not_recorded',)] += 1
                if category == 'unique':
                    primer = names[0]
                elif category == 'multiple':
                    primer = '[多重匹配]'
                elif category == 'unmatched':
                    primer = '[未匹配]'
                else:
                    primer = '[未评估]'
                subsets = ('all', 'retained') if reason in {'retained', 'retained_history', 'imported_result'} else ('all',)
                for subset in subsets:
                    primer_counts[key+(subset, primer)] += 1
                    trim_state=row.get('trim_state') or 'not_recorded'
                    primer_state_counts[key+(subset,primer,trim_state)]+=1
                    for segment in ('V','J','C'):
                        gene_set = calls(row.get(segment.lower()+'_call'))
                        label = gene_set[0] if len(gene_set)==1 else '[多基因候选]' if gene_set else '[无注释]'
                        if not gene_set and segment=='C':
                            if meta and 'C' not in meta.get('reference',{}):label='[未配置 C 参考]'
                            elif row.get('c_call') is None:label='[未评估 C 注释]'
                        if not gene_set:
                            if reason in {'ambiguous_lineage','pair_identity_unverified','duplicate_airr_rows'}:label='[关联不唯一，未评估]'
                            elif reason=='missing_airr_row':label='[缺少输出，未评估]'
                            elif reason=='not_in_final_reason_unknown':label='[最终无记录，原因未知]'
                            elif reason in {'not_seen_after_fastp','not_merged'}:label='[未进入比对]'
                            elif reason in {'fastp_membership_unavailable','clean_membership_unavailable'}:label='[阶段关联不可用]'
                        gene_counts[key+(subset, primer, trim_state, segment, label)] += 1
                        # Reference coverage is query-based, including ambiguous candidates.
                        for gene in gene_set:
                            coverage[key+(subset, segment, gene, 'unique' if len(gene_set)==1 else 'ambiguous')] += 1
                pending.append((item['sample_key'], sample, item['pair'], chain, scope, row.get('sequence_id',''), category,
                     json.dumps(names,ensure_ascii=False), len(families), reason, row.get('v_call'), row.get('j_call'), row.get('c_call'), seq,
                      json.dumps(frozen_names,ensure_ascii=False) if frozen_names is not None else None,row.get('trim_state'),row.get('trim_length',0),
                      row['matching_source'],row['matching_ordinal'],row['matching_unit'],row.get('mapping_sequence_id',row.get('sequence_id'))))
                if count % 10000 == 0:
                    conn.executemany('INSERT INTO observations VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',pending)
                    pending.clear()
                    conn.commit()
                    if progress:
                        progress(number-1, len(items), f'{sample} / {chain}：已统计 {count:,} 条')
            conn.executemany('INSERT INTO observations VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',pending)
            pending.clear()
            conn.commit()
            if catalog:
                key=(sample,item['sample_key'],chain,scope)
                for subset in ('all','retained'):
                    for primer in catalog['assignments'][sample]:
                        primer_counts.setdefault(key+(subset,primer['name']),0)
                for category in ('unique','multiple','unmatched'):
                    match_counts.setdefault(key+(category,),0)
            if refs:
                refdb = safe_file(Path(item['trace']).parent/refs['annotation'], root)
                with closing(sqlite3.connect(refdb.as_uri()+'?mode=ro',uri=True)) as reference:
                    for segment, gene in reference.execute("SELECT DISTINCT segment,gene FROM reference_genes WHERE segment IN ('V','J','C')"):
                        genes_seen[(sample,item['sample_key'],chain,scope,segment,gene)] = refs['reference'].get(segment,{}).get('available',False)
            summaries.append(dict(id=item['id'], sample=sample, sample_key=item['sample_key'], pair=item['pair'], chain=chain,
                scope=selected_stage if selected_stage.startswith('raw_') else 'retained_only' if selected_stage=='retained' else 'all_queries' if item.get('trace') else 'all_queries_final_annotations_only' if item.get('merged') else 'retained_only', observations=count, sequence_available=sequence_available,
                unit='read ends' if selected_stage.startswith('raw_') else 'queries' if item.get('trace') or item.get('merged') else 'AIRR rows',
                reference=refs.get('reference',{}) if refs else {}, note='' if item.get('trace') else '比对输入完整；未出现在最终结果的原因未知' if item.get('merged') else '仅当前结果记录；过滤前与未输出记录不可还原'))
            summaries[-1]['mapping_preparation']={key:refs['primer_preparation'].get(key) for key in ('version','catalog_version','counts','unassigned_policy')} if refs and refs.get('primer_preparation') else None
            if progress:
                progress(number, len(items), f'{sample} / {chain} 完成')
        if progress:progress(len(items),len(items),'正在建立逐记录索引与跨链去重汇总')
        conn.execute('CREATE INDEX observations_view ON observations(sample_key,chain,match_class,filter_reason,id)')
        conn.execute('CREATE INDEX observations_mapping_id ON observations(sample_key,chain,mapping_sequence_id)')
        conn.commit()
        # Shared inputs are counted once across chains by archived source + ordinal,
        # including duplicate sequence IDs. Final-only legacy rows stay chain-specific.
        total_rows=conn.execute('''WITH unique_records AS (
            SELECT sample,sample_key,
                CASE WHEN MIN(scope)=MAX(scope) THEN MIN(scope) ELSE 'mixed_annotation_evidence' END AS scope,
                matching_source,matching_ordinal,matching_unit AS unit,
                CASE WHEN MIN(match_class)=MAX(match_class) THEN MIN(match_class)
                    ELSE 'conflicting_assessment' END AS match_class,
                group_concat(DISTINCT chain) AS chains
            FROM observations GROUP BY sample_key,matching_source,matching_ordinal
        ) SELECT sample,sample_key,scope,unit,chains,match_class,count(*) AS count
          FROM unique_records GROUP BY sample,sample_key,scope,unit,chains,match_class
          ORDER BY sample,sample_key,scope,unit,chains,match_class''')
        total_columns=[column[0] for column in total_rows.description]
        matching_totals=[dict(zip(total_columns,row)) for row in total_rows]
    finally:
        conn.close()
    base = ('sample','sample_key','chain','scope')
    def records(counter, extra):
        return [dict(zip(base+extra,key), count=value) for key,value in sorted(counter.items())]
    primer_rows = records(primer_counts, ('subset','primer'))
    denominators = {tuple(row[k] for k in base+('subset','primer')): row['count'] for row in primer_rows}
    gene_rows = records(gene_counts, ('subset','primer','trim_state','segment','gene'))
    for row in gene_rows:
        row['denominator'] = denominators[tuple(row[k] for k in base+('subset','primer'))]
        row['percent'] = row['count']*100/row['denominator'] if row['denominator'] else None
        row['state_denominator']=primer_state_counts[tuple(row[k] for k in base+('subset','primer','trim_state'))]
        row['state_percent']=row['count']*100/row['state_denominator'] if row['state_denominator'] else None
    coverage_rows = []
    observed = {key[:4]+(key[5],key[6]) for key in coverage}
    for key in sorted(set(genes_seen)|observed):
        sample, sk, chain, scope, segment, gene = key
        for subset in ('all', 'retained'):
            ck = (sample,sk,chain,scope,subset,segment,gene)
            unique, ambiguous = coverage[ck+('unique',)], coverage[ck+('ambiguous',)]
            coverage_rows.append(dict(sample=sample,sample_key=sk,chain=chain,scope=scope,subset=subset,segment=segment,gene=gene,
                unique_count=unique,ambiguous_count=ambiguous,reference_catalog=genes_seen.get(key,False),
                status='observed' if unique or ambiguous else 'zero_observed'))
    matching_total_summary=summarize_matching(matching_totals)
    return dict(samples=summaries,match_counts=records(match_counts,('match_class',)),matching_totals=matching_totals,
        matching_total_summary=matching_total_summary,
        family_counts=records(family_counts,('match_class',)),
        trim_counts=records(trim_counts,('trim_state',)),
        primer_counts=primer_rows,gene_counts=gene_rows,filter_counts=records(reason_counts,('reason',)),gene_coverage=coverage_rows)
