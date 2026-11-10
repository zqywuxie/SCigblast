"""Separate raw-R1 and mapping-input matching, with query-level mapping rates.

Raw reads are streamed once per source into the private index. A read is linked
only when its canonical ID is unique in both that R1 source and the query archive.
No final read count is substituted for missing historical mapping evidence.
"""
from collections import Counter
import json
from pathlib import Path
import sqlite3

from .aggregate import calls
from .catalog import Matcher, assigned_primers
from .io import canonical_header, digest, safe_file, sequences, table, version
from .batch import tune

STAGES = {'raw_R1': 'Raw', 'mapping': 'Mapping reads'}
COLUMNS = ('sample', 'chain', 'stage', 'primer', 'matched_reads', 'total_reads',
           'match_percent', 'mapped_reads', 'mapping_percent', 'retained_reads',
           'unverified_reads', 'evidence')


def match(sequence, matcher, config):
    if config.get('read_structure') == 'oligo':
        adapter = config.get('adapter', 'ACACTCTTTCCCTACACGACGCTCTTCCGATCT').upper()
        cut = len(adapter)+int(config.get('umi_length',10))+int(config.get('barcode_length',8))
        if not sequence.startswith(adapter) or len(sequence) <= cut:
            return 'read_structure_unavailable', []
        sequence = sequence[cut:]
    names, _ = matcher.match(sequence)
    return ('unique' if len(names)==1 else 'multiple' if names else 'unmatched'), names


def raw_source(item, root, meta, allowed_roots):
    source = meta.get('read_sources', {}).get('raw_R1')
    if not source:
        source = (meta.get('upstream') or {}).get('sources', {}).get('raw_R1')
    if source:
        path = Path(source['path'])
        if not path.is_file():return None, 'raw_source_unavailable'
        for allowed in allowed_roots:
            if path.absolute().is_relative_to(Path(allowed).resolve()):
                path = safe_file(path, allowed)
                if version(path) != source['version']:
                    raise ValueError('原始 R1 已变化，请勿混用不同批次数据')
                return path, 'frozen_raw_source'
        return None, 'raw_source_outside_allowed_roots'
    # Old mapping traces have no raw receipt. Use only the authoritative Match
    # manifest in the matching dataset, explicitly label current-source evidence.
    if item.get('summary_path'):
        parent = Path(item['summary_path']).parent
        if parent.parent.name == '05.igblastn_out':
            manifest = parent.parent.parent/'01.match'/parent.name/'sample_manifest.csv'
            if manifest.is_file():
                matches = [r for r in table(safe_file(manifest,root))
                           if r.get('status')=='OK' and r.get('sample_id')==item['sample_label']
                           and r.get('pair_id','')==item['pair']]
                paths = {r.get('r1_path') for r in matches if r.get('r1_path')}
                if len(paths)==1:
                    path = Path(paths.pop())
                    if not path.is_file():return None, 'raw_source_unavailable'
                    for allowed in allowed_roots:
                        if path.absolute().is_relative_to(Path(allowed).resolve()):
                            return safe_file(path,allowed), 'current_raw_source'
    return None, 'raw_source_unavailable'


def archived_raw(marker, meta, root):
    """Read immutable full-trace R1 even when the original FASTQ is gone."""
    from Bio import bgzf
    upstream=meta['upstream']
    index=safe_file(marker.parent/upstream['index'],root)
    archive=safe_file(marker.parent/upstream['archive'],root)
    conn=sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)
    try:
        with bgzf.BgzfReader(str(archive),'rb') as incoming:
            for title,offset in conn.execute("SELECT title,offset FROM reads WHERE stage='raw_R1' ORDER BY ordinal"):
                incoming.seek(offset)
                yield title,incoming.readline().decode('ascii').strip()
    finally:conn.close()


def build_raw(items, root, output=None, catalog=None, *, allowed_roots=(), progress=None):
    """Match original R1 once per sample/source, independently of annotations."""
    if not catalog:
        return dict(rows=[], matching=[], chain_matching=[], availability=[], sources=[])
    totals, counts, unique = Counter(), Counter(), Counter()
    sample_totals, sample_counts = Counter(), Counter()
    availability, receipts, sources = [], {}, {}
    scopes = {}
    for item in items:
        meta = json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else {}
        path, evidence = raw_source(item, root, meta, allowed_roots)
        archived = False
        if path is None and (meta.get('upstream') or {}).get('sources', {}).get('raw_R1'):
            folder = Path(item['trace']).parent
            for name, expected in meta['upstream']['files'].items():
                checked = safe_file(folder/name, root)
                if version(checked) != expected:
                    raise ValueError('原始 R1 归档已变化')
                receipts[str(checked)] = expected
            path = safe_file(folder/meta['upstream']['archive'], root)
            archived, evidence = True, 'archived_raw_source'
        if path is None:
            availability.append(dict(sample=item['sample_label'],chain=item['chain'],stage='raw_R1',status=evidence))
            continue
        key = (item['sample_label'], str(path.resolve()), archived)
        entry = sources.setdefault(key, dict(path=path, meta=meta, item=item, chains=set(), evidence=evidence))
        entry['chains'].add(item['chain'])
        receipts[str(path)] = version(path)
    for number, ((sample, _, archived), source) in enumerate(sources.items(), 1):
        matcher = Matcher(assigned_primers(catalog, sample))
        allowed = {chain:{p['name'] for p in assigned_primers(catalog, sample, chain)} for chain in source['chains']}
        for chain in allowed:
            scopes.setdefault((sample,chain),set()).add(source['evidence'])
            totals.setdefault((sample,chain),0)
            availability.append(dict(sample=sample,chain=chain,stage='raw_R1',status=source['evidence']))
        sample_totals.setdefault(sample,0)
        incoming = archived_raw(Path(source['item']['trace']), source['meta'], root) if archived else sequences(source['path'])
        for ordinal, (_, sequence) in enumerate(incoming, 1):
            category, names = match(sequence, matcher, catalog['config']) if sequence else ('sequence_unavailable', [])
            sample_totals[sample] += 1
            sample_counts[(sample,category)] += 1
            for chain, primers in allowed.items():
                selected = sorted(set(names) & primers)
                state = category
                if category in {'unique','multiple','unmatched'}:
                    state = 'unique' if len(selected)==1 else 'multiple' if selected else 'unmatched'
                totals[(sample,chain)] += 1
                counts[(sample,chain,state)] += 1
                if state == 'unique':
                    unique[(sample,chain,selected[0])] += 1
            if progress and ordinal % 20000 == 0:
                progress(number-1,len(sources),f'{sample} · Raw R1 primer matching {ordinal:,}')
        if progress:
            progress(number,len(sources),f'{sample} · Raw R1 primer matching complete')
    if any(version(path) != expected for path, expected in receipts.items()):
        raise ValueError('原始 R1 在统计期间发生变化')
    rows, matching, chain_matching = [], [], []
    for (sample,chain), total in sorted(totals.items()):
        for primer in sorted(assigned_primers(catalog,sample,chain),key=lambda p:(-unique[(sample,chain,p['name'])],p['name'])):
            count = unique[(sample,chain,primer['name'])]
            rows.append(dict(sample=sample,chain=chain,stage='raw_R1',primer=primer['name'],matched_reads=count,
                total_reads=total,match_percent=100*count/total if total else None,evidence='|'.join(sorted(scopes[(sample,chain)]))))
        for category in ('unique','multiple','unmatched'):
            count = counts[(sample,chain,category)]
            chain_matching.append(dict(sample=sample,chain=chain,stage='raw_R1',unit='reads',match_class=category,
                count=count,denominator=total,percent=100*count/total if total else None))
    for sample, total in sorted(sample_totals.items()):
        unassessed = total - sum(sample_counts[(sample,c)] for c in ('unique','multiple','unmatched'))
        for category in ('unique','multiple','unmatched'):
            count = sample_counts[(sample,category)]
            matching.append(dict(sample=sample,stage='raw_R1',unit='reads',match_class=category,
                count=count,denominator=total,percent=100*count/total if total else None,unassessed_count=unassessed))
    return dict(rows=rows,matching=matching,chain_matching=chain_matching,availability=availability,
                sources=[dict(path=path,version=expected) for path,expected in sorted(receipts.items())])


def build(items, root, output, catalog, *, allowed_roots=(), progress=None):
    if not catalog:
        return dict(rows=[], matching=[], availability=[])
    conn = sqlite3.connect(Path(output)/'details.sqlite', uri=True)
    tune(conn)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
      CREATE TABLE raw_primer_reads(source TEXT,ordinal INTEGER,canonical TEXT,sequence_id TEXT,
        match_class TEXT,primers TEXT,PRIMARY KEY(source,ordinal));
      CREATE INDEX raw_primer_canonical ON raw_primer_reads(source,canonical);
      CREATE TABLE raw_match_sources(cache_key TEXT PRIMARY KEY,source TEXT);
      CREATE TABLE primer_mapping_links(sample_key TEXT,chain TEXT,stage TEXT,source TEXT,ordinal INTEGER,
        mapping_sequence_id TEXT,match_class TEXT,primers TEXT,mapped INTEGER,retained INTEGER,state TEXT);
      CREATE INDEX primer_mapping_link_lookup ON primer_mapping_links(sample_key,chain,stage,source,ordinal);
      CREATE TABLE matching_stage_keys(sample TEXT,stage TEXT,source TEXT,ordinal INTEGER,category TEXT,
        PRIMARY KEY(sample,stage,source,ordinal));
    ''')
    result, matching, availability, scanned, scope_evidence = [], [], [], {}, {}
    counts, totals = Counter(), Counter()
    links, stage_keys = [], []
    def flush():
        conn.executemany('INSERT OR IGNORE INTO matching_stage_keys VALUES(?,?,?,?,?)',stage_keys)
        conn.executemany('INSERT INTO primer_mapping_links VALUES(?,?,?,?,?,?,?,?,?,?,?)',links)
        stage_keys.clear();links.clear()
    try:
        for index, item in enumerate(items,1):
            sample, chain = item['sample_label'], item['chain']
            meta = json.loads(Path(item['trace']).read_text(encoding='utf-8')) if item.get('trace') else {}
            evidence = 'mapping_trace' if meta else 'final_results_only'
            scope_evidence.setdefault((sample,chain,'mapping'),set()).add(evidence)
            totals.setdefault((sample,chain,'mapping'),0)
            if meta:
                conn.execute('ATTACH DATABASE ? AS annotations', (safe_file(Path(item['trace']).parent/meta['annotation'],root).as_uri()+'?mode=ro',))
                conn.execute('DROP TABLE IF EXISTS temp.annotation_first')
                conn.execute('''CREATE TEMP TABLE annotation_first AS SELECT sequence_id,count(*) AS n,min(ordinal) AS ordinal
                    FROM annotations.annotations GROUP BY sequence_id''')
                conn.execute('CREATE INDEX temp.annotation_first_id ON annotation_first(sequence_id)')
            sql = '''SELECT o.*,a.locus AS mapped_locus FROM observations o
                LEFT JOIN annotation_first f ON f.sequence_id=o.mapping_sequence_id
                LEFT JOIN annotations.annotations a ON a.ordinal=f.ordinal
                WHERE o.sample_key=? AND o.chain=?''' if meta else 'SELECT *,NULL AS mapped_locus FROM observations WHERE sample_key=? AND chain=?'
            for row in conn.execute(sql, (item['sample_key'],chain)):
                verified = bool(meta) and row['filter_reason'] not in {'ambiguous_lineage','duplicate_airr_rows','missing_airr_row'}
                # Locus comes from the immutable annotations, not the requested
                # chain label attached to this observation.
                mapped = None
                if verified:
                    mapped = bool((row['mapped_locus'] or '').strip().upper()==chain and calls(row['v_call']))
                names = json.loads(row['primers'])
                self_key = (sample,chain,'mapping')
                totals[self_key] += 1
                stage_keys.append((sample,'mapping',row['matching_source'],row['matching_ordinal'],row['sample_match_class']))
                primer = names[0] if row['match_class']=='unique' else '[Multiple]' if row['match_class']=='multiple' else '[Unmatched]' if row['match_class']=='unmatched' else '[Unassessed]'
                key = self_key+(primer,)
                counts[key+('matched',)] += 1
                counts[key+('mapped',)] += int(bool(mapped))
                counts[key+('unverified',)] += int(mapped is None)
                retained = row['filter_reason'] in {'retained','retained_history','imported_result'}
                counts[key+('retained',)] += int(retained)
                links.append((item['sample_key'],chain,'mapping',row['matching_source'],row['matching_ordinal'],row['mapping_sequence_id'],row['match_class'],row['primers'],int(mapped) if mapped is not None else None,int(retained),'verified' if verified else evidence))
                if len(links)>=10000:flush()
            flush()
            conn.commit()
            if meta:conn.execute('DETACH DATABASE annotations')
            if not meta:
                availability.append(dict(sample=sample,chain=chain,stage='raw_R1',status='mapping_trace_unavailable'))
                continue
            upstream=meta.get('upstream') or {}
            if upstream.get('sources',{}).get('raw_R1'):
                path=safe_file(Path(item['trace']).parent/upstream['archive'],root)
                signature=version(path)
                source=digest(upstream['sources']['raw_R1'])
                raw_evidence='archived_raw_source'
                incoming=archived_raw(Path(item['trace']),meta,root)
            else:
                path, raw_evidence = raw_source(item,root,meta,allowed_roots)
                if path is None:
                    availability.append(dict(sample=sample,chain=chain,stage='raw_R1',status=raw_evidence))
                    continue
                signature = version(path)
                source = digest(dict(path=str(path),version=signature))
                incoming=sequences(path)
            matcher = Matcher(catalog['assignments'][sample])
            cache_key = digest([source,catalog['assignments'][sample],catalog['config']])
            conn.execute('INSERT OR IGNORE INTO raw_match_sources VALUES(?,?)',(cache_key,source))
            if cache_key not in scanned:
                raw_batch=[]
                for ordinal,(title,sequence) in enumerate(incoming,1):
                    category,names = match(sequence,matcher,catalog['config'])
                    raw_batch.append((cache_key,ordinal,canonical_header(title),title.split()[0],category,json.dumps(names,ensure_ascii=False)))
                    if ordinal%10000==0:
                        conn.executemany('INSERT INTO raw_primer_reads VALUES(?,?,?,?,?,?)',raw_batch)
                        raw_batch.clear()
                        conn.commit()
                        if progress: progress(index-1,len(items),f'{sample} · Raw primer matching {ordinal:,}')
                conn.executemany('INSERT INTO raw_primer_reads VALUES(?,?,?,?,?,?)',raw_batch)
                if version(path)!=signature:
                    raise ValueError('原始 R1 在统计期间发生变化')
                conn.commit()
                scanned[cache_key] = (path,signature)
            conn.execute('ATTACH DATABASE ? AS queries', (safe_file(Path(item['trace']).parent/meta['query_index'],root).as_uri()+'?mode=ro',))
            conn.execute('ATTACH DATABASE ? AS annotations', (safe_file(Path(item['trace']).parent/meta['annotation'],root).as_uri()+'?mode=ro',))
            conn.execute('DROP TABLE IF EXISTS temp.raw_counts')
            conn.execute('''CREATE TEMP TABLE raw_counts AS SELECT canonical,count(*) AS n FROM raw_primer_reads
                WHERE source=? GROUP BY canonical''',(cache_key,))
            conn.execute('CREATE INDEX temp.raw_count_id ON raw_counts(canonical)')
            conn.execute('DROP TABLE IF EXISTS temp.query_first')
            conn.execute('''CREATE TEMP TABLE query_first AS SELECT canonical,count(*) AS n,min(ordinal) AS ordinal
                FROM queries.queries GROUP BY canonical''')
            conn.execute('CREATE INDEX temp.query_first_id ON query_first(canonical)')
            sql = '''SELECT r.*,
                coalesce(rc.n,0) AS rn,coalesce(qc.n,0) AS qn,
                q.sequence_id AS mapping_id,
                coalesce(ac.n,0) AS an,
                a.locus,a.v_call,a.filter_reason
                FROM raw_primer_reads r LEFT JOIN raw_counts rc ON rc.canonical=r.canonical
                LEFT JOIN query_first qc ON qc.canonical=r.canonical
                LEFT JOIN queries.queries q ON q.ordinal=qc.ordinal
                LEFT JOIN annotation_first ac ON ac.sequence_id=q.sequence_id
                LEFT JOIN annotations.annotations a ON a.ordinal=ac.ordinal
                WHERE r.source=? ORDER BY r.ordinal'''
            totals.setdefault((sample,chain,'raw_R1'),0)
            allowed = {p['name'] for p in assigned_primers(catalog,sample,chain)}
            for row in conn.execute(sql,(cache_key,)):
                verified = row['rn']==1 and row['qn']<=1 and (row['qn']==0 or row['an']==1)
                mapped = bool(verified and (row['locus'] or '').strip().upper()==chain and calls(row['v_call']))
                retained = bool(verified and row['filter_reason']=='retained')
                names = sorted(set(json.loads(row['primers'])) & allowed)
                category = row['match_class']
                if category in {'unique','multiple','unmatched'}:
                    category = 'unique' if len(names)==1 else 'multiple' if names else 'unmatched'
                self_key = (sample,chain,'raw_R1')
                totals[self_key] += 1
                stage_keys.append((sample,'raw_R1',source,row['ordinal'],row['match_class']))
                primer = names[0] if category=='unique' else '[Multiple]' if category=='multiple' else '[Unmatched]' if category=='unmatched' else '[Unassessed]'
                key = self_key+(primer,)
                counts[key+('matched',)] += 1
                counts[key+('mapped',)] += int(mapped)
                counts[key+('unverified',)] += int(not verified)
                counts[key+('retained',)] += int(retained)
                links.append((item['sample_key'],chain,'raw_R1',cache_key,row['ordinal'],row['mapping_id'] if verified else None,category,json.dumps(names,ensure_ascii=False),int(mapped) if verified else None,int(retained),'verified' if verified else 'ambiguous_lineage'))
                if len(links)>=10000:flush()
            flush()
            conn.commit()
            conn.execute('DETACH DATABASE queries');conn.execute('DETACH DATABASE annotations')
            availability.append(dict(sample=sample,chain=chain,stage='raw_R1',status=raw_evidence))
            scope_evidence.setdefault((sample,chain,'raw_R1'),set()).add(raw_evidence)
        for (sample,chain,stage), total in sorted(totals.items()):
            primers = {key[3] for key in counts if key[:3]==(sample,chain,stage)}
            primers.update(p['name'] for p in assigned_primers(catalog,sample,chain))
            for primer in sorted(primers):
                key=(sample,chain,stage,primer)
                matched,mapped,unknown,retained=(counts[key+(metric,)] for metric in ('matched','mapped','unverified','retained'))
                result.append(dict(sample=sample,chain=chain,stage=stage,primer=primer,
                    matched_reads=matched,total_reads=total,match_percent=100*matched/total if total else None,
                    mapped_reads=mapped if not unknown else None,
                    mapping_percent=100*mapped/matched if matched and not unknown else None,
                    retained_reads=retained,unverified_reads=unknown,
                    evidence='unverified_lineage' if unknown else '|'.join(sorted(scope_evidence[(sample,chain,stage)]))))
        # Deduplicate the same R1/query across selected chains, never sum chain
        # totals into an apparent sample-wide matching rate.
        summary = Counter({(r[0],r[1],r[2]):r[3] for r in conn.execute(
            'SELECT sample,stage,category,count(*) FROM matching_stage_keys GROUP BY sample,stage,category')})
        for sample,stage in sorted({key[:2] for key in summary}):
            denominator=sum(summary[(sample,stage,c)] for c in ('unique','multiple','unmatched'))
            for category in ('unique','multiple','unmatched'):
                count=summary[(sample,stage,category)]
                matching.append(dict(sample=sample,stage=stage,unit='reads',match_class=category,
                    count=count,denominator=denominator,percent=100*count/denominator if denominator else None,
                    unassessed_count=sum(v for (s,t,c),v in summary.items() if s==sample and t==stage and c not in {'unique','multiple','unmatched'})))
        for path,signature in scanned.values():
            if version(path)!=signature:raise ValueError('原始 R1 在统计期间发生变化')
        conn.commit()
        return dict(rows=result,matching=matching,availability=availability)
    finally:
        conn.close()
