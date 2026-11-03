"""Independent suspected-primer assignments against unchanged mapping evidence."""
from __future__ import annotations

from collections import Counter,defaultdict
from contextlib import closing
from itertools import zip_longest
import json
from pathlib import Path
import sqlite3

from .catalog import Matcher
from .io import digest,version,table,sequences,safe_file,selected_table
from .primer_metrics import match,raw_source,archived_raw
from .postprocessed_usage import identity,LINEAGE_FIELDS
from .usage import materialize_gene_usage
from .gene_cdr3 import enrich

VERSION='suspected-primer-analysis-v1-existing-mapping'
EXPORTS=('scenario_matching_summary','scenario_primer_mapping_summary',
         'scenario_primer_gene_usage','scenario_primer_reference_gene_observation')


def build(data,items,root,index,output,*,allowed_roots=(),progress=None):
    """Keep the original index read-only; store exact alternative copy shares."""
    result=dict(primer_scenarios=[],scenario_version=VERSION,**{key:[] for key in EXPORTS})
    catalog=data.get('catalog') or {}
    candidates={(r['sample'],r['tested_group']) for r in data.get('primer_group_comparisons',[])
                if r.get('status')=='suspected'}
    groups=[g for g in data.get('usage',{}).get('groups',[]) if g['sample'] in {s for s,_ in candidates}
            and g.get('composition',{}).get('status')=='complete']
    if not groups:return result
    index,output,root=Path(index).resolve(),Path(output),Path(root).resolve()
    receipts={index:version(index)};counts=Counter();totals=Counter();availability={}
    conn=sqlite3.connect(output,uri=True);conn.row_factory=sqlite3.Row
    conn.execute('ATTACH DATABASE ? AS base',(index.as_uri()+'?mode=ro',))
    tables={r[0] for r in conn.execute("SELECT name FROM base.sqlite_master WHERE type='table'")}
    alternative_cache={'alternative_match_records','alternative_match_scopes','alternative_match_sources'}<=tables
    conn.executescript('''PRAGMA temp_store=FILE; PRAGMA cache_size=-65536;
      CREATE TABLE scenarios(scenario_id TEXT PRIMARY KEY,base_group_id TEXT,sample TEXT,chain TEXT,primer_group TEXT);
      CREATE TABLE scenario_matches(scenario_id TEXT,stage TEXT,source_item TEXT,ordinal INTEGER,
        sequence_id TEXT,match_class TEXT,primers TEXT,primer TEXT,mapped INTEGER,retained INTEGER,state TEXT,
        PRIMARY KEY(scenario_id,stage,source_item,ordinal));
      CREATE TABLE lineage(scenario_id TEXT,identity_key TEXT,primer TEXT,n INTEGER,
        PRIMARY KEY(scenario_id,identity_key,primer));
      CREATE TABLE usage_groups(id TEXT PRIMARY KEY,sample TEXT,chain TEXT,reference_id TEXT,reference_json TEXT);
      CREATE TABLE usage_primers(group_id TEXT,primer TEXT,PRIMARY KEY(group_id,primer));
      CREATE TABLE usage_reference(group_id TEXT,segment TEXT,gene TEXT,PRIMARY KEY(group_id,segment,gene));
      CREATE TABLE composition_sources(group_id TEXT PRIMARY KEY,status TEXT);
      CREATE TABLE composition_records(id INTEGER PRIMARY KEY,group_id TEXT,primer TEXT,sample_record INTEGER,
        weight INTEGER,eligible INTEGER,aa_field TEXT,aa_value TEXT,aa_valid INTEGER);
      CREATE TABLE composition_calls(record_id INTEGER,segment TEXT,gene TEXT,assignment TEXT);
      CREATE TABLE allocations(id INTEGER PRIMARY KEY,scenario_id TEXT,base_record_id INTEGER,primer TEXT,weight INTEGER);
      CREATE TABLE source_receipts(path TEXT PRIMARY KEY,version TEXT);
    ''')
    try:
        for number,group in enumerate(groups,1):
            sample,chain,gid=group['sample'],group['chain'],group['group_id']
            selected=[i for i in items if i['sample_label']==sample and i['chain']==chain
                      and i['id'] in {r[0] for r in conn.execute('SELECT id FROM base.usage_sources WHERE group_id=?',(gid,))}]
            profiles=[]
            for _,name in sorted(c for c in candidates if c[0]==sample):
                primers=catalog.get('groups',{}).get(name)
                if not primers:raise ValueError('Suspected primer group unavailable: '+name)
                sid=digest([VERSION,gid,name,primers,catalog['config']])[:24]
                profile=dict(scenario_id=sid,base_group_id=gid,sample=sample,chain=chain,primer_group=name,
                    status='suspected',analysis_basis='existing_mapping',primers=[p['name'] for p in primers],
                    reference_id=group['reference_id'],reference_scope=group['reference_scope'])
                profiles.append((profile,Matcher(primers)));result['primer_scenarios'].append(profile)
                conn.execute('INSERT INTO scenarios VALUES(?,?,?,?,?)',(sid,gid,sample,chain,name))
                conn.execute('INSERT INTO usage_groups SELECT ?,sample,chain,reference_id,reference_json FROM base.usage_groups WHERE id=?',(sid,gid))
                conn.executemany('INSERT INTO usage_primers VALUES(?,?)',[(sid,p['name']) for p in primers])
                conn.execute('INSERT INTO usage_reference SELECT ?,segment,gene FROM base.usage_reference WHERE group_id=?',(sid,gid))
                conn.execute('INSERT INTO composition_sources VALUES(?,?)',(sid,'complete'))

            reusable={}
            for stage in ('mapping','raw_R1'):
                saved=conn.execute('SELECT catalog_version,groups_json FROM base.alternative_match_scopes WHERE sample=? AND stage=?',(sample,stage)).fetchone() if alternative_cache else None
                reusable[stage]=set(json.loads(saved['groups_json'])) if saved and saved['catalog_version']==catalog['version'] else set()

            def add(stage,item,ordinal,sequence,link,cached_names=None):
                for profile,matcher in profiles:
                    sid=profile['scenario_id']
                    if cached_names is not None and profile['primer_group'] in reusable[stage]:
                        names=sorted(set(cached_names).intersection(profile['primers']))
                        category='unique' if len(names)==1 else 'multiple' if names else 'unmatched'
                    else:category,names=match(sequence,matcher,catalog['config'])
                    primer=names[0] if category=='unique' else '[Multiple]' if category=='multiple' else '[Unmatched]' if category=='unmatched' else '[Unassessed]'
                    mapped=link['mapped'];retained=link['retained']
                    conn.execute('INSERT INTO scenario_matches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (sid,stage,item['id'],ordinal,link['mapping_sequence_id'],category,json.dumps(names,ensure_ascii=False),
                         names[0] if category=='unique' else '',mapped,retained,link['state']))
                    totals[(sid,stage)]+=1;counts[(sid,stage,category,'category')]+=1
                    for key,value in [('matched',1),('mapped',int(bool(mapped))),('retained',int(bool(retained))),('unverified',int(mapped is None))]:
                        counts[(sid,stage,primer,key)]+=value

            for item in selected:
                sql='''SELECT o.sequence,o.matching_ordinal,l.mapping_sequence_id,l.mapped,l.retained,l.state
                  '''+(',a.names AS alternative_names' if alternative_cache else ',NULL AS alternative_names')+'''
                  FROM base.observations o JOIN base.primer_mapping_links l
                    ON l.sample_key=o.sample_key AND l.chain=o.chain AND l.stage='mapping'
                    AND l.source=o.matching_source AND l.ordinal=o.matching_ordinal
                  '''+('''LEFT JOIN base.alternative_match_sources s ON s.sample=o.sample AND s.stage='mapping'
                    AND s.source=json_array(o.sample_key,o.matching_source)
                    LEFT JOIN base.alternative_match_records a ON a.source_id=s.id AND a.ordinal=o.matching_ordinal''' if alternative_cache else '')+'''
                  WHERE o.sample_key=? AND o.chain=? ORDER BY o.matching_ordinal'''
                for n,row in enumerate(conn.execute(sql,(item['sample_key'],chain)),1):
                    add('mapping',item,row['matching_ordinal'],row['sequence'] or '',row,
                        json.loads(row['alternative_names']) if row['alternative_names'] is not None else None)
                    if n%10000==0:
                        conn.commit()
                        if progress:progress(number-1,len(groups),f'{sample}: suspected mapping assignments {n:,}')
                conn.commit()
                meta=json.loads(Path(item['trace']).read_text()) if item.get('trace') else {}
                receipts[Path(item['trace'])]=version(item['trace'])
                if (meta.get('upstream') or {}).get('sources',{}).get('raw_R1'):
                    raw=safe_file(Path(item['trace']).parent/meta['upstream']['archive'],root)
                    incoming=archived_raw(Path(item['trace']),meta,root);evidence='archived_raw_source'
                else:
                    raw,evidence=raw_source(item,root,meta,allowed_roots)
                    incoming=sequences(raw) if raw else ()
                if raw is None:
                    availability[item['id']]=evidence;continue
                receipts[raw]=version(raw)
                sources=conn.execute("SELECT DISTINCT source FROM base.primer_mapping_links WHERE sample_key=? AND chain=? AND stage='raw_R1'",(item['sample_key'],chain)).fetchall()
                if len(sources)!=1:raise ValueError('Raw scenario source is not uniquely partitioned')
                cursor=conn.execute('''SELECT l.ordinal,l.mapping_sequence_id,l.mapped,l.retained,l.state,r.sequence_id AS raw_id
                    FROM base.primer_mapping_links l JOIN base.raw_primer_reads r ON r.source=l.source AND r.ordinal=l.ordinal
                    WHERE l.sample_key=? AND l.chain=? AND l.stage='raw_R1' AND l.source=? ORDER BY l.ordinal''',
                    (item['sample_key'],chain,sources[0][0]))
                use_raw_cache=alternative_cache and 'raw_match_sources' in tables and all(p['primer_group'] in reusable['raw_R1'] for p,_ in profiles)
                if use_raw_cache:
                    cache_sql='''SELECT l.*,a.names AS alternative_names FROM base.primer_mapping_links l
                      JOIN base.raw_match_sources s ON s.cache_key=l.source
                      LEFT JOIN base.alternative_match_sources ms ON ms.sample=? AND ms.stage='raw_R1'
                        AND ms.source=json_array('',s.source)
                      LEFT JOIN base.alternative_match_records a ON a.source_id=ms.id AND a.ordinal=l.ordinal
                      WHERE l.sample_key=? AND l.chain=? AND l.stage='raw_R1' AND l.source=?'''
                    cache_args=(sample,item['sample_key'],chain,sources[0][0])
                    expected=conn.execute('SELECT count(*) FROM ('+cache_sql+')',cache_args).fetchone()[0]
                    missing=conn.execute('SELECT count(*) FROM ('+cache_sql+') WHERE alternative_names IS NULL',cache_args).fetchone()[0]
                    link_count=conn.execute("SELECT count(*) FROM base.primer_mapping_links WHERE sample_key=? AND chain=? AND stage='raw_R1' AND source=?",cache_args[1:]).fetchone()[0]
                    use_raw_cache=not missing and expected==link_count
                if use_raw_cache:
                    for n,link in enumerate(conn.execute(cache_sql+' ORDER BY l.ordinal',cache_args),1):
                        add('raw_R1',item,link['ordinal'],'',link,json.loads(link['alternative_names']))
                        if n%10000==0:conn.commit()
                else:
                    for n,(read,link) in enumerate(zip_longest(incoming,cursor),1):
                        if read is None or link is None or link['ordinal']!=n or read[0].split()[0]!=link['raw_id']:
                            raise ValueError('Raw reads do not match the saved ordinal/ID lineage')
                        add('raw_R1',item,n,read[1],link)
                        if n%10000==0:
                            conn.commit()
                            if progress:progress(number-1,len(groups),f'{sample}: suspected Raw assignments {n:,}')
                cursor.close()
                availability[item['id']]=evidence;conn.commit()
            conn.execute('CREATE INDEX IF NOT EXISTS scenario_mapping_id ON scenario_matches(scenario_id,stage,source_item,sequence_id)')
            for item in selected:
                receipts[Path(item['path'])]=version(item['path'])
                if 'final_lineage_sources' in tables and conn.execute('SELECT 1 FROM base.final_lineage_sources WHERE source_item=?',(item['id'],)).fetchone():
                    for profile,_ in profiles:
                        conn.execute('''INSERT INTO lineage
                          SELECT ?,f.identity_key,CASE WHEN m.n=1 THEN m.primer ELSE '' END,count(*)
                          FROM base.final_lineage f LEFT JOIN (
                            SELECT sequence_id,count(*) AS n,min(primer) AS primer FROM scenario_matches
                            WHERE scenario_id=? AND stage='mapping' AND source_item=? GROUP BY sequence_id
                          ) m ON m.sequence_id=f.sequence_id WHERE f.source_item=?
                          GROUP BY f.identity_key,CASE WHEN m.n=1 THEN m.primer ELSE '' END
                          ON CONFLICT(scenario_id,identity_key,primer) DO UPDATE SET n=n+excluded.n''',
                          (profile['scenario_id'],profile['scenario_id'],item['id'],item['id']))
                    conn.commit()
                    continue
                for n,row in enumerate(selected_table(item['path'],LINEAGE_FIELDS),1):
                    key=identity(row)
                    for profile,_ in profiles:
                        links=conn.execute("SELECT primer FROM scenario_matches WHERE scenario_id=? AND stage='mapping' AND source_item=? AND sequence_id=? LIMIT 2",
                            (profile['scenario_id'],item['id'],row.get('sequence_id',''))).fetchall()
                        primer=links[0][0] if len(links)==1 else ''
                        conn.execute('INSERT INTO lineage VALUES(?,?,?,1) ON CONFLICT(scenario_id,identity_key,primer) DO UPDATE SET n=n+1',
                            (profile['scenario_id'],key,primer))
                    if n%10000==0:
                        conn.commit()
                        if progress:progress(number-1,len(groups),f'{sample}: verifying suspected copy lineage {n:,}')
                conn.commit()
            for profile,_ in profiles:
                sid=profile['scenario_id']
                conn.execute('''INSERT INTO allocations(scenario_id,base_record_id,primer,weight)
                  SELECT ?,r.id,l.primer,l.n FROM base.composition_records r JOIN lineage l
                    ON l.identity_key=r.identity_key AND l.scenario_id=?
                  WHERE r.group_id=? AND r.sample_record=1 AND l.primer<>''
                    AND r.weight=(SELECT sum(n) FROM lineage z WHERE z.scenario_id=l.scenario_id AND z.identity_key=r.identity_key)
                    AND 1=(SELECT count(*) FROM base.composition_records z WHERE z.group_id=r.group_id AND z.identity_key=r.identity_key AND z.sample_record=1)''',(sid,sid,gid))
                profile['copies']=group['composition']['copies']
                profile['assigned_copies']=conn.execute('SELECT coalesce(sum(weight),0) FROM allocations WHERE scenario_id=?',(sid,)).fetchone()[0]
                profile['unresolved_copies']=profile['copies']-profile['assigned_copies']
                assert profile['unresolved_copies']>=0
            conn.commit()

        conn.executescript('''INSERT INTO composition_records
            SELECT a.id,a.scenario_id,a.primer,0,a.weight,1,r.aa_field,r.aa_value,r.aa_valid
            FROM allocations a JOIN base.composition_records r ON r.id=a.base_record_id;
          INSERT INTO composition_calls SELECT a.id,c.segment,c.gene,c.assignment
            FROM allocations a JOIN base.composition_calls c ON c.record_id=a.base_record_id;
          CREATE INDEX composition_selection ON composition_records(group_id,sample_record,primer,eligible);
          CREATE INDEX composition_call_lookup ON composition_calls(record_id,segment);''')
        materialize_gene_usage(conn,postprocessed=True);enrich(conn);conn.commit()
        for profile in result['primer_scenarios']:
            sid=profile['scenario_id'];base=dict(scenario_id=sid,base_group_id=profile['base_group_id'],group_id=sid,
                sample=profile['sample'],chain=profile['chain'],primer_group=profile['primer_group'])
            for stage in ('mapping','raw_R1'):
                total=totals[(sid,stage)]
                if not total:continue
                for category in ('unique','multiple','unmatched'):
                    value=counts[(sid,stage,category,'category')]
                    result['scenario_matching_summary'].append(dict(base,stage=stage,match_class=category,count=value,
                        denominator=total,percent=100*value/total))
                oracle=[r for r in data.get('primer_group_comparisons',[]) if r['sample']==profile['sample'] and r['tested_group']==profile['primer_group'] and r.get('stage','mapping')==stage]
                if len(oracle)==1 and sum(g['sample']==profile['sample'] for g in groups)==1:
                    assert total==oracle[0]['denominator']
                    for category in ('unique','multiple','unmatched'):
                        assert counts[(sid,stage,category,'category')]==oracle[0][category+'_count'],(sid,stage,category)
                names=set(profile['primers'])|{k[2] for k in counts if k[:2]==(sid,stage) and k[3]!='category'}
                for primer in sorted(names):
                    matched,mapped,retained,unknown=(counts[(sid,stage,primer,k)] for k in ('matched','mapped','retained','unverified'))
                    result['scenario_primer_mapping_summary'].append(dict(base,stage=stage,primer=primer,
                        matched_reads=matched,total_reads=total,match_percent=100*matched/total,
                        mapped_reads=mapped if not unknown else None,mapping_percent=100*mapped/matched if matched and not unknown else None,
                        retained_reads=retained,unverified_reads=unknown,evidence='existing_mapping_reclassified'))
        profiles={p['scenario_id']:p for p in result['primer_scenarios']}
        for row in conn.execute('SELECT * FROM gene_usage ORDER BY group_id,primer,segment,gene,metric'):
            row=dict(row);profile=profiles[row['group_id']]
            row.update(scenario_id=profile['scenario_id'],base_group_id=profile['base_group_id'],primer_group=profile['primer_group'])
            if row['unique_count']>0:result['scenario_primer_gene_usage'].append(row)
            if row['metric']=='expression':result['scenario_primer_reference_gene_observation'].append(row)
        for row in conn.execute('SELECT group_id,primer,segment,metric,sum(percent),max(denominator) FROM gene_usage GROUP BY group_id,primer,segment,metric'):
            if row[-1]:assert abs(row[-2]-100)<1e-6,row
        for path,signature in receipts.items():
            if version(path)!=signature:raise ValueError('Source changed during suspected group analysis: '+str(path))
            conn.execute('INSERT INTO source_receipts VALUES(?,?)',(str(path),json.dumps(signature)))
        conn.execute('CREATE TABLE scenario_metadata AS SELECT * FROM scenarios');conn.commit()
        result['scenario_availability']=availability
        if progress:progress(len(groups),len(groups),'Suspected primer analyses complete')
        return result
    finally:conn.close()


def render(data,output,*,index=None,figure_progress=None,cache_root=None):
    """Reuse the regular V/J/C renderer with isolated scenario records."""
    from .figure_jobs import render_usage
    profiles=data.get('primer_scenarios',[])
    if not profiles:return []
    groups=[dict(p,group_id=p['scenario_id'],sample=p['sample']+' / '+p['primer_group']+' (Suspected)',aa_fields=['cdr3_aa']) for p in profiles]
    figures=render_usage(dict(usage=dict(groups=groups,isotypes=[])),output,
        index=index or Path(output)/'scenario_details.sqlite',figure_progress=figure_progress,
        cache_root=cache_root,receipt_name='.scenario_figure_cache.json',skip_isotypes=True)
    by_id={p['scenario_id']:p for p in profiles}
    for f in figures:
        p=by_id[f['group_id']];f.update(sample=p['sample'],scenario_id=p['scenario_id'],primer_group=p['primer_group'])
    import matplotlib.pyplot as plt
    from .io import label_name
    for profile in profiles:
        rows=[r for r in data['scenario_primer_mapping_summary'] if r['scenario_id']==profile['scenario_id'] and not r['primer'].startswith('[')]
        for stage in ('raw_R1','mapping'):
            batch=sorted([r for r in rows if r['stage']==stage],key=lambda r:(-r['matched_reads'],r['primer']))
            if not batch:continue
            for kind,key,title in [('primers','match_percent','Unique primer matching')]+([('primer_mapping','mapping_percent','Mapping rate')] if stage=='mapping' else []):
                fig,ax=plt.subplots(figsize=(10,max(3.5,len(batch)*.38+1.8)));fig.subplots_adjust(left=.36,right=.92,top=.78,bottom=.14)
                values=[r[key] for r in batch];ax.barh(range(len(batch)),[v or 0 for v in values],color='#3979B8',height=.6)
                ax.set_yticks(range(len(batch)),[r['primer'] for r in batch]);ax.invert_yaxis();ax.set_xlim(0,100);ax.set_xlabel('%')
                for i,v in enumerate(values):ax.annotate('N/A' if v is None else f'{v:.2f}%',(v or 0,i),xytext=(4,0),textcoords='offset points',va='center',fontsize=9)
                full=profile['sample']+' / '+profile['primer_group']+' (Suspected) · '+title+' · '+('Raw' if stage=='raw_R1' else 'Mapping reads')
                fig.suptitle(full,fontsize=11,fontweight='bold',y=.97)
                name='suspected__'+profile['scenario_id'][:8]+'__'+stage+'__'+kind;png='03_primer_genes/figures/'+name+'.png';path=Path(output)/png;path.parent.mkdir(parents=True,exist_ok=True)
                try:fig.savefig(path,dpi=300,bbox_inches='tight',facecolor='white')
                finally:plt.close(fig)
                figures.append(dict(id=name,title=full,png=png,kind=kind,sample=profile['sample'],chain=profile['chain'],primer='',segment='',preparation=stage,
                    group_id=profile['scenario_id'],scenario_id=profile['scenario_id'],primer_group=profile['primer_group']))
    return figures
