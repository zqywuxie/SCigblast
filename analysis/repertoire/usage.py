"""Mapped-read gene usage joined to primer lineage and actual reference snapshots.

AIRR files are streamed once, independently of the number of primers. Large
gene/primer tables and CDR3 distinct keys remain in the private SQLite database.
"""
from __future__ import annotations

from contextlib import closing
import csv
import json
import re
from pathlib import Path
import sqlite3
import subprocess

from .aggregate import calls
from .io import digest, open_text, safe_file
from .isotype import AA, MAPPING_VERSION, classify, summarize
from .batch import lookup_rows, prepare_observation_links, tune

USAGE_COLUMNS = ('group_id','sample','chain','reference_id','reference_scope','primer','segment','gene',
                 'unique_count','ambiguous_count','denominator','percent','reference_catalog','status','metric','aa_field')


def connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def current_reference(root,species,chain):
    """Explicit present-day comparison, never asserted as the historical DB."""
    from scigblast.igblast_cache import reference
    version_name,_=reference(species)
    refs,genes={},[]
    for segment in ('V','J','C'):
        prefix=Path(root)/'database_251117'/version_name/chain/f'{species}_gl_{chain}_{segment}'
        if not any(prefix.parent.glob(prefix.name+'.n*')):continue
        result=subprocess.run(['blastdbcmd','-db',str(prefix),'-entry','all','-outfmt','%i\t%s'],capture_output=True,text=True,timeout=120,check=True)
        records=[]
        for line in result.stdout.splitlines():
            name,sequence=line.split('\t',1)
            if len(sequence)<=1:continue
            name=name.removeprefix('lcl|')
            records.append([name,sequence]);genes.append((segment,name.split('*')[0]))
        refs[segment]=dict(available=bool(records),sha256=digest(records))
    return refs,genes


def build(items, root, output, catalog=None, progress=None,comparison=None,lineage_samples=None):
    database = Path(output)/'details.sqlite'
    connection = sqlite3.connect(database,uri=True)
    tune(connection)
    connection.row_factory = sqlite3.Row
    connection.executescript('''
        CREATE TABLE usage_groups(id TEXT PRIMARY KEY,sample TEXT,chain TEXT,reference_id TEXT,reference_json TEXT);
        CREATE TABLE usage_records(id INTEGER PRIMARY KEY,group_id TEXT,source_item TEXT,sequence_id TEXT,
            primer TEXT,match_class TEXT,trim_state TEXT,eligible INTEGER,cdr3_aa TEXT,junction_aa TEXT,
            aa_field TEXT,aa_value TEXT,aa_valid INTEGER,isotype_main TEXT,isotype_subclass TEXT,isotype_state TEXT);
        CREATE TABLE usage_calls(record_id INTEGER,segment TEXT,gene TEXT,assignment TEXT);
        CREATE TABLE usage_candidates(record_id INTEGER,segment TEXT,call TEXT,candidate_count INTEGER,status TEXT);
        CREATE TABLE usage_reference(group_id TEXT,segment TEXT,gene TEXT,PRIMARY KEY(group_id,segment,gene));
        CREATE TABLE usage_primers(group_id TEXT,primer TEXT,PRIMARY KEY(group_id,primer));
        CREATE TABLE usage_sources(id TEXT PRIMARY KEY,group_id TEXT,sample_key TEXT,chain TEXT);
        CREATE INDEX usage_source_scope ON usage_sources(group_id,sample_key,chain);
    ''')
    comparisons={}
    try:
        prepare_observation_links(connection)
        next_id = 1
        for index, item in enumerate(items, 1):
            meta = json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else {}
            references = meta.get('reference', {})
            comparison_genes=[]
            reference_scope='mapping_snapshot' if references else 'unavailable'
            if not references and comparison:
                key=(comparison['species'],item['chain'])
                if key not in comparisons:comparisons[key]=current_reference(comparison['root'],*key)
                references,comparison_genes=comparisons[key]
                reference_scope='comparison_current'
            identity = {segment: {'available': bool(info.get('available')), 'sha256': info.get('sha256')}
                        for segment, info in references.items() if segment in {'V','J','C'}}
            reference_id = digest([reference_scope,identity])[:16] if identity else 'unavailable'
            identity['_scope']=reference_scope
            group_id = digest([item.get('dataset',''),item['sample_label'],item['chain'],reference_id])[:24]
            connection.execute('INSERT OR IGNORE INTO usage_groups VALUES(?,?,?,?,?)',
                (group_id,item['sample_label'],item['chain'],reference_id,json.dumps(identity)))
            connection.execute('INSERT OR IGNORE INTO usage_primers VALUES(?,?)',(group_id,''))
            connection.execute('INSERT INTO usage_sources VALUES(?,?,?,?)',(item['id'],group_id,item['sample_key'],item['chain']))
            for primer in (catalog or {}).get('assignments',{}).get(item['sample_label'],[]):
                connection.execute('INSERT OR IGNORE INTO usage_primers VALUES(?,?)',(group_id,primer['name']))
            if meta:
                refpath = safe_file(Path(item['trace']).parent/meta['annotation'],root)
                with closing(connect(refpath)) as ref:
                    for segment, gene in ref.execute("SELECT DISTINCT segment,gene FROM reference_genes WHERE segment IN ('V','J','C')"):
                        if references.get(segment,{}).get('available'):
                            connection.execute('INSERT OR IGNORE INTO usage_reference VALUES(?,?,?)',(group_id,segment,gene))
            for segment,gene in comparison_genes:
                connection.execute('INSERT OR IGNORE INTO usage_reference VALUES(?,?,?)',(group_id,segment,gene))
            path = safe_file(item['path'],root)
            # With trace evidence, include mapped nonproductive queries as well;
            # the public pipeline TSV remains filtered and unchanged.
            with closing(connect(safe_file(Path(item['trace']).parent/meta['annotation'],root))) if meta else open_text(path) as handle:
                reader = handle.execute('SELECT * FROM annotations ORDER BY ordinal') if meta else csv.DictReader(handle,delimiter='\t' if '.tsv' in path.suffixes else ',')
                fields = meta.get('annotation_fields', []) if meta else reader.fieldnames or []
                aa_field = 'cdr3_aa' if 'cdr3_aa' in fields else 'junction_aa' if 'junction_aa' in fields else ''
                if meta:
                    connection.execute('ATTACH DATABASE ? AS source_annotations',
                        (safe_file(Path(item['trace']).parent/meta['annotation'],root).as_uri()+'?mode=ro',))
                    reader=connection.execute('''SELECT a.*,l.n AS __n,o.match_class AS __match_class,
                        o.primers AS __primers,o.trim_state AS __trim_state FROM source_annotations.annotations a
                        LEFT JOIN observation_links l ON l.sample_key=? AND l.chain=? AND l.mapping_sequence_id=a.sequence_id
                        LEFT JOIN observations o ON l.n=1 AND o.id=l.first_id
                        ORDER BY a.ordinal''',(item['sample_key'],item['chain']))
                    joined=((dict(row),row) for row in reader)
                else:
                    connection.execute('DROP TABLE IF EXISTS temp.report_source_links')
                    connection.execute('''CREATE TEMP TABLE report_source_links AS SELECT l.mapping_sequence_id,l.n,
                        o.match_class,o.primers,o.trim_state FROM observation_links l
                        LEFT JOIN observations o ON l.n=1 AND o.id=l.first_id
                        WHERE l.sample_key=? AND l.chain=?''',(item['sample_key'],item['chain']))
                    connection.execute('CREATE INDEX temp.report_source_link_id ON report_source_links(mapping_sequence_id)')
                    joined = lookup_rows(connection, (dict(row) for row in reader), '''SELECT b.ordinal,
                        l.n AS __n,l.match_class AS __match_class,l.primers AS __primers,l.trim_state AS __trim_state
                        FROM report_batch b LEFT JOIN report_source_links l ON l.mapping_sequence_id=b.sequence_id ORDER BY b.ordinal''')
                record_batch, call_batch, primer_batch = [], [], set()
                for ordinal, (row, linked) in enumerate(joined,1):
                    sequence_id = row.get('sequence_id','')
                    match_class, primer, trim_state = 'not_assessed', '', 'not_recorded'
                    if linked['__n'] == 1:
                        match_class, names, trim_state = linked['__match_class'],linked['__primers'],linked['__trim_state']
                        names = json.loads(names)
                        if match_class == 'unique' and len(names)==1:
                            primer = names[0]
                    if primer:
                        primer_batch.add((group_id,primer))
                    locus = row.get('locus',row.get('chain'))
                    eligible = int(bool(sequence_id) and bool(calls(row.get('v_call')))
                                   and ((not meta and locus is None) or (locus or '').strip().upper()==item['chain']))
                    if meta and (linked['__n']!=1 or linked['__match_class']=='ambiguous_lineage'):
                        eligible=0
                    main, subtype, state = classify(row.get('c_call'))
                    aa_value = (row.get(aa_field) or '').strip().upper() if aa_field else ''
                    record_batch.append((next_id,group_id,item['id'],sequence_id,primer,match_class,trim_state or 'not_recorded',eligible,
                         row.get('cdr3_aa'),row.get('junction_aa'),aa_field,aa_value,int(bool(AA.fullmatch(aa_value))),main,subtype,state))
                    for segment in ('V','J','C'):
                        genes = calls(row.get(segment.lower()+'_call'))
                        assignment = 'unique' if len(genes)==1 else 'ambiguous' if genes else 'unannotated'
                        call_batch.extend((next_id,segment,gene,assignment) for gene in genes or [''])
                    next_id += 1
                    if ordinal % 10000 == 0:
                        connection.executemany('INSERT INTO usage_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',record_batch)
                        connection.executemany('INSERT INTO usage_calls VALUES(?,?,?,?)',call_batch)
                        connection.executemany('INSERT OR IGNORE INTO usage_primers VALUES(?,?)',primer_batch)
                        record_batch.clear(); call_batch.clear(); primer_batch.clear()
                        connection.commit()
                        if progress: progress(index-1,len(items),f"{item['sample_label']} / {item['chain']}：汇总最终结果 {ordinal:,} 条")
                connection.executemany('INSERT INTO usage_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',record_batch)
                connection.executemany('INSERT INTO usage_calls VALUES(?,?,?,?)',call_batch)
                connection.executemany('INSERT OR IGNORE INTO usage_primers VALUES(?,?)',primer_batch)
            connection.commit()
            if meta:connection.execute('DETACH DATABASE source_annotations')
        if progress:progress(len(items),len(items),'正在建立基因与 CDR3 汇总索引')
        connection.executescript('''
            CREATE INDEX usage_source_id ON usage_records(source_item,sequence_id);
            UPDATE usage_records SET eligible=0,primer='',match_class='ambiguous_lineage'
            WHERE (source_item,sequence_id) IN
                (SELECT source_item,sequence_id FROM usage_records GROUP BY source_item,sequence_id HAVING count(*)>1);
            CREATE INDEX usage_group_primer ON usage_records(group_id,primer,eligible);
            CREATE INDEX usage_cdr3 ON usage_records(group_id,eligible,aa_field,aa_valid,isotype_main,isotype_subclass,aa_value);
            CREATE INDEX usage_record_calls ON usage_calls(record_id,segment);
            CREATE INDEX usage_candidate_records ON usage_candidates(record_id,segment);
            CREATE TABLE candidate_gene_summary AS
              SELECT g.sample,g.chain,r.group_id,r.primer,c.segment,c.call,c.candidate_count,c.status,count(*) AS count
              FROM usage_candidates c JOIN usage_records r ON r.id=c.record_id
              JOIN usage_groups g ON g.id=r.group_id WHERE r.eligible=1
              GROUP BY r.group_id,r.primer,c.segment,c.call,c.candidate_count,c.status;
            CREATE INDEX candidate_gene_selection ON candidate_gene_summary(group_id,segment,primer,count DESC);
        ''')
        from .postprocessed_usage import build as build_composition
        composition_sources = build_composition(connection, items, root, progress,lineage_samples=lineage_samples)
        postprocessed = composition_sources is not None
        materialize_gene_usage(connection,postprocessed=postprocessed)
        from .gene_cdr3 import enrich
        if progress:progress(len(items),len(items),'正在去重汇总 CDR3 基因组成')
        enrich(connection)
        connection.commit()
        groups=[]
        group_rows=connection.execute('SELECT * FROM usage_groups ORDER BY sample,chain,id').fetchall()
        for number,group in enumerate(group_rows,1):
            record=dict(group)
            record['reference']=json.loads(record.pop('reference_json'))
            record['reference_scope']=record['reference'].pop('_scope','unavailable')
            record['group_id']=record.pop('id')
            record['primers']=[r[0] for r in connection.execute("SELECT primer FROM usage_primers WHERE group_id=? AND primer<>'' ORDER BY primer",(record['group_id'],))]
            record['record_count']=connection.execute('SELECT count(*) FROM usage_records WHERE group_id=? AND eligible=1',(record['group_id'],)).fetchone()[0]
            source_ids={r[0] for r in connection.execute('SELECT id FROM usage_sources WHERE group_id=?',(record['group_id'],))}
            record['count_basis']='mapped_reads' if all(i.get('trace') for i in items if i['id'] in source_ids) else 'available_result_reads'
            if postprocessed:
                record['composition'] = next(source for source in composition_sources if source['group_id']==record['group_id'])
            record['excluded_count']=connection.execute('SELECT count(*) FROM usage_records WHERE group_id=? AND eligible=0',(record['group_id'],)).fetchone()[0]
            record['aa_fields']=[r[0] for r in connection.execute("SELECT DISTINCT aa_field FROM usage_records WHERE group_id=? AND aa_field='cdr3_aa'",(record['group_id'],))]
            record['isotype_states']={r[0]:r[1] for r in connection.execute('SELECT isotype_state,count(*) FROM usage_records WHERE group_id=? AND eligible=1 GROUP BY isotype_state',(record['group_id'],))}
            record['aa_coverage']=[dict(field=r[0],records=r[1],valid_records=r[2],isotype_valid_records=r[3]) for r in connection.execute("SELECT aa_field,count(*),sum(aa_valid),sum(CASE WHEN isotype_main<>'' THEN aa_valid ELSE 0 END) FROM usage_records WHERE group_id=? AND eligible=1 GROUP BY aa_field",(record['group_id'],))]
            if postprocessed:
                record['isotype_states']={r[0]:r[1] for r in connection.execute('SELECT isotype_state,sum(weight) FROM composition_records WHERE group_id=? AND sample_record=1 GROUP BY isotype_state',(record['group_id'],))}
                record['aa_coverage']=[dict(field=r[0],records=r[1],valid_records=r[2],isotype_valid_records=r[3]) for r in connection.execute("SELECT aa_field,sum(weight),sum(weight*aa_valid),sum(CASE WHEN isotype_main<>'' THEN weight*aa_valid ELSE 0 END) FROM composition_records WHERE group_id=? AND sample_record=1 GROUP BY aa_field",(record['group_id'],))]
            # Drive from the small distinct source scope. A correlated EXISTS
            # over observations rescans every sample (including large sequence
            # payloads for primer JSON) once per group.
            matching_from='''FROM (SELECT DISTINCT sample_key,chain FROM usage_sources WHERE group_id=?) s
                CROSS JOIN observations o ON o.sample_key=s.sample_key AND o.chain=s.chain'''
            record['matching_records']=connection.execute('SELECT count(*) '+matching_from,(record['group_id'],)).fetchone()[0]
            record['primer_observations']={r[0]:r[1] for r in connection.execute("SELECT json_extract(o.primers,'$[0]'),count(*) "+matching_from+" WHERE o.match_class='unique' GROUP BY json_extract(o.primers,'$[0]')",(record['group_id'],))}
            record['segments']={}
            record['candidate_counts']={r[0]:r[1] for r in connection.execute('''SELECT segment,sum(count)
                FROM candidate_gene_summary WHERE group_id=? GROUP BY segment''',(record['group_id'],))}
            for segment in ('V','J','C'):
                counts={r[0]:r[1] for r in connection.execute('''SELECT c.assignment,count(DISTINCT r.id)
                    FROM usage_records r JOIN usage_calls c ON c.record_id=r.id
                    WHERE r.group_id=? AND r.eligible=1 AND c.segment=? GROUP BY c.assignment''',(record['group_id'],segment))}
                counts['reference_available']=bool(record['reference'].get(segment,{}).get('available'))
                record['segments'][segment]=counts
            groups.append(record)
            if progress:progress(number,len(group_rows),f"{record['sample']} / {record['chain']}：基因汇总元数据完成")
        return dict(version='final-usage-v3-postprocessed-copy',groups=groups,isotypes=summarize(connection),isotype_mapping=MAPPING_VERSION,
            rules=dict(subset='postprocessed' if postprocessed else 'mapped',
                abundance='published AIRR preprocessing copy/umi_counts; exact primer lineage shares only' if postprocessed else 'one eligible AIRR query = one read',
                composition_source='published_airr_preprocessing' if postprocessed else 'mapping',
                expression_weight='copy_or_umi_counts' if postprocessed else 'one_query',
                gene_denominator='uniquely classified gene reads within each sample/chain, primer and segment',
                gene_expression_denominator='classified_gene_reads',
                isotype_expression_denominator='classified_c_reads',
                genes='single gene after allele collapsing; ambiguous candidates counted separately',
                primer='unique primer assignment only; never multiply multi-primer records',
                cdr3='valid canonical cdr3_aa for metrics; junction_aa preserved for lineage only, no Unique Junction output',
                unique_denominator='sum of within-isotype distinct AA counts; main classes re-deduplicated',
                gene_unique_denominator='sum of distinct canonical cdr3_aa memberships within uniquely classified genes only; same AA across genes counted separately',
                reference='actual mapping snapshot; unavailable historical reference never replaced silently'))
    finally:
        connection.close()


def gene_family(gene, segment):
    """Group canonical gene names for display; never infer an unknown accession."""
    import re
    name=str(gene or '').split('*',1)[0].upper()
    if segment=='C':
        match=re.match(r'^(IGH[AMDEG]|IGKC|IGLC|TR[ABDG]C)',name)
    elif segment=='V':
        match=re.match(r'^((?:IGH|IGK|IGL|TRA|TRB|TRD|TRG)V\d+)',name)
    elif segment=='J':
        match=re.match(r'^((?:IGH|IGK|IGL|TRA|TRB|TRD|TRG)J\d+)-',name)
        if not match:match=re.match(r'^((?:IGH|IGK|IGL|TRA|TRB|TRD|TRG)J)',name)
    else:match=None
    return match.group(1) if match else name


def rows(connection, *, group_id='', primer=None, segment='', metric='expression',aa_field='cdr3_aa',zero_only=False, observed_only=False, reference_only=False, definite_only=False, observation_state='', gene_query='', offset=0, limit=None):
    where,args=[],[]
    for key,value in (('group_id',group_id),('segment',segment)):
        if value: where.append(key+'=?');args.append(value)
    if primer is not None: where.append('primer=?');args.append(primer)
    modern='metric' in {row[1] for row in connection.execute('PRAGMA table_info(gene_usage)')}
    if metric not in {'expression','unique_cdr3'}:raise ValueError('unknown gene metric')
    if modern:
        where.append('metric=?');args.append(metric)
        if metric=='unique_cdr3':
            where.append('aa_field=?');args.append(aa_field)
    elif metric!='expression':raise ValueError('历史分析尚无 Unique CDR3 基因统计，请重新生成')
    if zero_only: where.append("status='zero_observed'")
    if observed_only: where.append('unique_count>0')
    if reference_only: where.append('reference_catalog=1')
    if definite_only: where.append("(unique_count>0 OR (ambiguous_count=0 AND status='zero_observed'))")
    if gene_query.strip(): where.append('instr(lower(gene),lower(?))>0');args.append(gene_query.strip())
    if observation_state:
        if observation_state not in {'zero_observed','ambiguous_only','outside_reference'}:raise ValueError('unknown observation state')
        where.append('status=?');args.append(observation_state)
    columns=','.join(USAGE_COLUMNS) if modern else ','.join(USAGE_COLUMNS[:-2])+",'expression' AS metric,'' AS aa_field"
    sql='SELECT '+columns+' FROM gene_usage'+(' WHERE '+' AND '.join(where) if where else '')
    if reference_only:
        import re
        connection.create_function('scigblast_gene_family',2,gene_family,deterministic=True)
        connection.create_function('scigblast_gene_order',1,lambda name:re.sub(r'\d+',lambda m:m.group().zfill(12),str(name or '').upper()),deterministic=True)
    sql+=' ORDER BY sample,chain,group_id,primer,segment,'+('scigblast_gene_order(scigblast_gene_family(gene,segment)),scigblast_gene_order(gene)' if reference_only else 'unique_count DESC,ambiguous_count DESC,gene')
    if limit is not None:sql+=' LIMIT ? OFFSET ?';args.extend([limit,max(0,offset)])
    yield from (dict(row) for row in connection.execute(sql,args))


def materialize_gene_usage(connection, *, postprocessed):
    """One abundance/denominator contract for original and independent scenarios."""
    records = 'composition_records' if postprocessed else 'usage_records'
    call_table = 'composition_calls' if postprocessed else 'usage_calls'
    abundance = 'sum(r.weight)' if postprocessed else 'count(*)'
    sample_scope = ' AND r.sample_record=1' if postprocessed else ''
    primer_scope = ' AND r.sample_record=0' if postprocessed else ''
    available_scope = "JOIN composition_sources s ON s.group_id=p.group_id AND s.status='complete'" if postprocessed else ''
    connection.executescript(f'''
        CREATE TABLE usage_counts AS
          SELECT r.group_id,'' AS primer,c.segment,c.gene,c.assignment,{abundance} AS count
            FROM {records} r JOIN {call_table} c ON c.record_id=r.id
            WHERE r.eligible=1{sample_scope}
            GROUP BY r.group_id,c.segment,c.gene,c.assignment
          UNION ALL
          SELECT r.group_id,r.primer,c.segment,c.gene,c.assignment,{abundance} AS count
            FROM {records} r JOIN {call_table} c ON c.record_id=r.id
            WHERE r.eligible=1 AND r.primer<>''{primer_scope}
            GROUP BY r.group_id,r.primer,c.segment,c.gene,c.assignment;
        CREATE INDEX usage_counts_key ON usage_counts(group_id,primer,segment,gene);
        CREATE VIEW gene_usage AS
          WITH keys AS (
            SELECT p.group_id,p.primer,r.segment,r.gene FROM usage_primers p
              JOIN usage_reference r ON p.group_id=r.group_id
              {available_scope}
            UNION SELECT group_id,primer,segment,gene FROM usage_counts WHERE gene<>''
          ), denominators AS (
            SELECT group_id,primer,segment,sum(count) AS total FROM usage_counts
              WHERE assignment='unique' GROUP BY group_id,primer,segment
          ), counts AS (
            SELECT group_id,primer,segment,gene,
              sum(CASE WHEN assignment='unique' THEN count ELSE 0 END) AS unique_count,
              sum(CASE WHEN assignment='ambiguous' THEN count ELSE 0 END) AS ambiguous_count
              FROM usage_counts GROUP BY group_id,primer,segment,gene
          )
          SELECT k.group_id,g.sample,g.chain,g.reference_id,
            json_extract(g.reference_json,'$._scope') AS reference_scope,k.primer,k.segment,k.gene,
            coalesce(c.unique_count,0) AS unique_count,coalesce(c.ambiguous_count,0) AS ambiguous_count,
            coalesce(d.total,0) AS denominator,
            CASE WHEN d.total>0 THEN 100.0*coalesce(c.unique_count,0)/d.total END AS percent,
            CASE WHEN r.gene IS NULL THEN 0 ELSE 1 END AS reference_catalog,
            CASE WHEN r.gene IS NULL AND json_extract(g.reference_json,'$.'||k.segment||'.available')=1
                      AND coalesce(c.unique_count,0)+coalesce(c.ambiguous_count,0)>0 THEN 'outside_reference'
                 WHEN coalesce(c.unique_count,0)>0 THEN 'observed'
                 WHEN coalesce(c.ambiguous_count,0)>0 THEN 'ambiguous_only'
                 ELSE 'zero_observed' END AS status
          FROM keys k JOIN usage_groups g ON g.id=k.group_id
            LEFT JOIN counts c USING(group_id,primer,segment,gene)
            LEFT JOIN denominators d USING(group_id,primer,segment)
            LEFT JOIN usage_reference r USING(group_id,segment,gene);
        CREATE TABLE materialized_gene_usage AS SELECT * FROM gene_usage;
        DROP VIEW gene_usage;
        ALTER TABLE materialized_gene_usage RENAME TO gene_usage;
        CREATE INDEX gene_usage_selection ON gene_usage(group_id,primer,segment,unique_count DESC,gene);
    ''')
