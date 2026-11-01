"""Final-result gene usage joined to primer lineage and actual reference snapshots.

AIRR files are streamed once, independently of the number of primers. Large
gene/primer tables and CDR3 distinct keys remain in the private SQLite database.
"""
from __future__ import annotations

from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
import subprocess

from .aggregate import calls
from .io import digest, open_text, safe_file
from .isotype import AA, MAPPING_VERSION, classify, summarize

USAGE_COLUMNS = ('group_id','sample','chain','reference_id','reference_scope','primer','segment','gene',
                 'unique_count','ambiguous_count','denominator','percent','reference_catalog','status')


def connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def current_reference(root,species,chain):
    """Explicit present-day comparison, never asserted as the historical DB."""
    from pipeline_igblast_cache import reference
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


def build(items, root, output, catalog=None, progress=None,comparison=None):
    database = Path(output)/'details.sqlite'
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript('''
        CREATE TABLE usage_groups(id TEXT PRIMARY KEY,sample TEXT,chain TEXT,reference_id TEXT,reference_json TEXT);
        CREATE TABLE usage_records(id INTEGER PRIMARY KEY,group_id TEXT,source_item TEXT,sequence_id TEXT,
            primer TEXT,match_class TEXT,trim_state TEXT,eligible INTEGER,cdr3_aa TEXT,junction_aa TEXT,
            aa_field TEXT,aa_value TEXT,aa_valid INTEGER,isotype_main TEXT,isotype_subclass TEXT,isotype_state TEXT);
        CREATE TABLE usage_calls(record_id INTEGER,segment TEXT,gene TEXT,assignment TEXT);
        CREATE TABLE usage_reference(group_id TEXT,segment TEXT,gene TEXT,PRIMARY KEY(group_id,segment,gene));
        CREATE TABLE usage_primers(group_id TEXT,primer TEXT,PRIMARY KEY(group_id,primer));
        CREATE TABLE usage_sources(id TEXT PRIMARY KEY,group_id TEXT,sample_key TEXT,chain TEXT);
    ''')
    comparisons={}
    try:
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
            with open_text(path) as handle:
                reader = csv.DictReader(handle,delimiter='\t' if '.tsv' in path.suffixes else ',')
                fields = reader.fieldnames or []
                aa_field = 'cdr3_aa' if 'cdr3_aa' in fields else 'junction_aa' if 'junction_aa' in fields else ''
                for ordinal, row in enumerate(reader,1):
                    sequence_id = row.get('sequence_id','')
                    # Index lookup is per source partition; duplicate IDs are not guessed.
                    linked = connection.execute('''SELECT match_class,primers,trim_state FROM observations
                        WHERE sample_key=? AND chain=? AND mapping_sequence_id=? LIMIT 2''',
                        (item['sample_key'],item['chain'],sequence_id)).fetchall()
                    match_class, primer, trim_state = 'not_assessed', '', 'not_recorded'
                    if len(linked) == 1:
                        match_class, names, trim_state = linked[0]
                        names = json.loads(names)
                        if match_class == 'unique' and len(names)==1:
                            primer = names[0]
                    if primer:
                        connection.execute('INSERT OR IGNORE INTO usage_primers VALUES(?,?)',(group_id,primer))
                    locus = row.get('locus',row.get('chain'))
                    eligible = int(bool(sequence_id) and (locus is None or locus.strip().upper()==item['chain'])
                                   and ('productive' not in fields or row.get('productive')=='T'))
                    main, subtype, state = classify(row.get('c_call'))
                    aa_value = (row.get(aa_field) or '').strip().upper() if aa_field else ''
                    cursor = connection.execute('INSERT INTO usage_records VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (group_id,item['id'],sequence_id,primer,match_class,trim_state or 'not_recorded',eligible,
                         row.get('cdr3_aa'),row.get('junction_aa'),aa_field,aa_value,int(bool(AA.fullmatch(aa_value))),main,subtype,state))
                    for segment in ('V','J','C'):
                        genes = calls(row.get(segment.lower()+'_call'))
                        assignment = 'unique' if len(genes)==1 else 'ambiguous' if genes else 'unannotated'
                        connection.executemany('INSERT INTO usage_calls VALUES(?,?,?,?)',
                            [(cursor.lastrowid,segment,gene,assignment) for gene in genes or ['']])
                    if ordinal % 10000 == 0:
                        connection.commit()
                        if progress: progress(index-1,len(items),f"{item['sample_label']} / {item['chain']}：汇总最终结果 {ordinal:,} 条")
            connection.commit()
        connection.executescript('''
            CREATE INDEX usage_source_id ON usage_records(source_item,sequence_id);
            UPDATE usage_records SET eligible=0,primer='',match_class='ambiguous_lineage'
            WHERE (source_item,sequence_id) IN
                (SELECT source_item,sequence_id FROM usage_records GROUP BY source_item,sequence_id HAVING count(*)>1);
            CREATE INDEX usage_group_primer ON usage_records(group_id,primer,eligible);
            CREATE INDEX usage_cdr3 ON usage_records(group_id,eligible,aa_field,aa_valid,isotype_main,isotype_subclass,aa_value);
            CREATE INDEX usage_record_calls ON usage_calls(record_id,segment);
            CREATE TABLE usage_counts AS
              SELECT r.group_id,'' AS primer,c.segment,c.gene,c.assignment,count(*) AS count
                FROM usage_records r JOIN usage_calls c ON c.record_id=r.id WHERE r.eligible=1
                GROUP BY r.group_id,c.segment,c.gene,c.assignment
              UNION ALL
              SELECT r.group_id,r.primer,c.segment,c.gene,c.assignment,count(*) AS count
                FROM usage_records r JOIN usage_calls c ON c.record_id=r.id WHERE r.eligible=1 AND r.primer<>''
                GROUP BY r.group_id,r.primer,c.segment,c.gene,c.assignment;
            CREATE INDEX usage_counts_key ON usage_counts(group_id,primer,segment,gene);
            CREATE VIEW gene_usage AS
              WITH keys AS (
                SELECT p.group_id,p.primer,r.segment,r.gene FROM usage_primers p
                  JOIN usage_reference r ON p.group_id=r.group_id
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
        connection.commit()
        groups=[]
        for group in connection.execute('SELECT * FROM usage_groups ORDER BY sample,chain,id').fetchall():
            record=dict(group)
            record['reference']=json.loads(record.pop('reference_json'))
            record['reference_scope']=record['reference'].pop('_scope','unavailable')
            record['group_id']=record.pop('id')
            record['primers']=[r[0] for r in connection.execute("SELECT primer FROM usage_primers WHERE group_id=? AND primer<>'' ORDER BY primer",(record['group_id'],))]
            record['record_count']=connection.execute('SELECT count(*) FROM usage_records WHERE group_id=? AND eligible=1',(record['group_id'],)).fetchone()[0]
            record['excluded_count']=connection.execute('SELECT count(*) FROM usage_records WHERE group_id=? AND eligible=0',(record['group_id'],)).fetchone()[0]
            record['aa_fields']=[r[0] for r in connection.execute("SELECT DISTINCT aa_field FROM usage_records WHERE group_id=? AND aa_field<>'' ORDER BY aa_field",(record['group_id'],))]
            record['isotype_states']={r[0]:r[1] for r in connection.execute('SELECT isotype_state,count(*) FROM usage_records WHERE group_id=? AND eligible=1 GROUP BY isotype_state',(record['group_id'],))}
            record['aa_coverage']=[dict(field=r[0],records=r[1],valid_records=r[2],isotype_valid_records=r[3]) for r in connection.execute("SELECT aa_field,count(*),sum(aa_valid),sum(CASE WHEN isotype_main<>'' THEN aa_valid ELSE 0 END) FROM usage_records WHERE group_id=? AND eligible=1 GROUP BY aa_field",(record['group_id'],))]
            matching_where='EXISTS(SELECT 1 FROM usage_sources s WHERE s.group_id=? AND s.sample_key=observations.sample_key AND s.chain=observations.chain)'
            record['matching_records']=connection.execute('SELECT count(*) FROM observations WHERE '+matching_where,(record['group_id'],)).fetchone()[0]
            record['primer_observations']={r[0]:r[1] for r in connection.execute("SELECT json_extract(primers,'$[0]'),count(*) FROM observations WHERE match_class='unique' AND "+matching_where+" GROUP BY json_extract(primers,'$[0]')",(record['group_id'],))}
            record['segments']={}
            for segment in ('V','J','C'):
                counts={r[0]:r[1] for r in connection.execute('''SELECT c.assignment,count(DISTINCT r.id)
                    FROM usage_records r JOIN usage_calls c ON c.record_id=r.id
                    WHERE r.group_id=? AND r.eligible=1 AND c.segment=? GROUP BY c.assignment''',(record['group_id'],segment))}
                counts['reference_available']=bool(record['reference'].get(segment,{}).get('available'))
                record['segments'][segment]=counts
            groups.append(record)
        return dict(version='final-usage-v1',groups=groups,isotypes=summarize(connection),isotype_mapping=MAPPING_VERSION,
            rules=dict(subset='retained',abundance='one eligible final AIRR record = one count',
                genes='single gene after allele collapsing; ambiguous candidates counted separately',
                primer='unique primer assignment only; never multiply multi-primer records',
                cdr3='file-level cdr3_aa preferred; otherwise separate junction_aa scope; exact canonical AA',
                unique_denominator='sum of within-isotype distinct AA counts; main classes re-deduplicated',
                reference='actual mapping snapshot; unavailable historical reference never replaced silently'))
    finally:
        connection.close()


def rows(connection, *, group_id='', primer=None, segment='', zero_only=False, observed_only=False, observation_state='', offset=0, limit=None):
    where,args=[],[]
    for key,value in (('group_id',group_id),('segment',segment)):
        if value: where.append(key+'=?');args.append(value)
    if primer is not None: where.append('primer=?');args.append(primer)
    if zero_only: where.append("status='zero_observed'")
    if observed_only: where.append('unique_count>0')
    if observation_state:
        if observation_state not in {'zero_observed','ambiguous_only','outside_reference'}:raise ValueError('unknown observation state')
        where.append('status=?');args.append(observation_state)
    sql='SELECT '+','.join(USAGE_COLUMNS)+' FROM gene_usage'+(' WHERE '+' AND '.join(where) if where else '')
    sql+=' ORDER BY sample,chain,group_id,primer,segment,unique_count DESC,ambiguous_count DESC,gene'
    if limit is not None:sql+=' LIMIT ? OFFSET ?';args.extend([limit,max(0,offset)])
    yield from (dict(row) for row in connection.execute(sql,args))
