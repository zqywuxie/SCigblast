"""Copy-weighted composition from published AIRR preprocessing outputs.

Mapping observations remain separate. Primer weights are recovered only when
the selected mapping records account for every copy of a postprocessed row.
"""
from __future__ import annotations

import json
from pathlib import Path

from .aggregate import calls
from .io import digest, open_text, safe_file, table, version, selected_reader, selected_table
from .isotype import AA, classify
from .output_metrics import count, published
from .batch import lookup_rows

LINEAGE_FIELDS=('sequence_id','sequence','locus','v_call','j_call','c_call','cdr3_aa')


def copy_count(row):
    """An explicit count must be a positive integer; never turn bad data into 1."""
    values = [count(row[key]) for key in ('copy', 'umi_counts')
              if key in row and row[key] not in (None, '')]
    if not values or any(value is None or value <= 0 for value in values):
        raise ValueError('AIRR composition requires positive integer copy/umi_counts')
    if len(set(values)) != 1:
        raise ValueError('copy and umi_counts disagree')
    return values[0]


def identity(row):
    # Keep full calls: the lineage bridge must not join different annotations.
    def text(key):
        value = str(row.get(key) or '').strip()
        return '' if value.lower() in {'nan', 'na', 'none', 'null'} else value
    return digest([text(key) for key in
                   ('sequence', 'locus', 'v_call', 'j_call', 'c_call', 'cdr3_aa')])


def resolve(selected, root):
    """Require one published sample/chain output with the exact selected inputs."""
    folders = set()
    for item in selected:
        if not item.get('summary_path'):
            return None, 'preprocessing_source_unavailable'
        parent = Path(item['summary_path']).parent
        if parent.parent.name == '05.igblastn_out':
            folders.add(parent.parent.parent / '06.preprocessing' / parent.name)
        elif parent.name == '05.igblastn_out':
            folders.add(parent.parent / '06.preprocessing')
        else:
            return None, 'preprocessing_source_unavailable'
    if len(folders) != 1:
        return None, 'multiple_preprocessing_sources'
    folder = folders.pop()
    if not (folder / 'processing_manifest.csv').is_file():
        return None, 'preprocessing_unavailable'
    if not (folder / '.preprocessing_state/base_completion.json').is_file():
        return None, 'preprocessing_not_published'
    sample, chain = selected[0]['sample_label'], selected[0]['chain']
    manifests = [row for row in table(safe_file(folder / 'processing_manifest.csv', root))
                 if row.get('sample') == sample and row.get('status') == 'OK']
    if len(manifests) != 1:
        return None, 'preprocessing_sample_unverified'
    manifest = manifests[0]
    sources = (manifest.get('bcr_path', '') if chain.startswith('IG')
               else manifest.get('tcr_path', '')).split(';')
    actual = {str(Path(path).resolve()) for path in sources
              if path and Path(path).stem.upper() == chain}
    expected = {str(Path(item['path']).resolve()) for item in selected}
    if actual != expected or not published(folder, root, expected):
        return None, 'preprocessing_scope_unverified'
    source = folder / 'junction_peps' / Path(manifest['input_root']).name / manifest['relative_sample']
    source = safe_file(source / ('BCR' if chain.startswith('IG') else 'TCR') / (chain + '.csv'), root)
    marker = json.loads(safe_file(folder / '.preprocessing_state/base_completion.json', root).read_text(encoding='utf-8'))
    stat = source.stat()
    if marker.get('outputs', {}).get(str(source)) != [stat.st_size, stat.st_mtime_ns]:
        return None, 'preprocessing_output_unverified'
    return source, 'complete'


def build(connection, items, root, progress=None,*,lineage_samples=None):
    """Materialize sample copies and independently verified primer copy shares."""
    # This adapter covers Pig/Base 05 -> 06. Other workflows keep their existing
    # representative/UMI contract until their own postprocessing adapter exists.
    if not any('05.igblastn_out' in Path(item['path']).parts for item in items):
        return None
    connection.execute('PRAGMA temp_store=FILE')
    connection.executescript('''
      CREATE TABLE composition_sources(group_id TEXT PRIMARY KEY,status TEXT,source TEXT,
        records INTEGER DEFAULT 0,copies INTEGER DEFAULT 0,primer_copies INTEGER DEFAULT 0,
        unresolved_copies INTEGER DEFAULT 0);
      CREATE TABLE composition_records(id INTEGER PRIMARY KEY,group_id TEXT,source_item TEXT,
        sequence_id TEXT,primer TEXT,sample_record INTEGER,weight INTEGER,eligible INTEGER,
        cdr3_aa TEXT,junction_aa TEXT,aa_field TEXT,aa_value TEXT,aa_valid INTEGER,
        isotype_main TEXT,isotype_subclass TEXT,isotype_state TEXT,identity_key TEXT);
      CREATE TABLE composition_calls(record_id INTEGER,segment TEXT,gene TEXT,assignment TEXT);
      CREATE TEMP TABLE composition_lineage(identity_key TEXT,primer TEXT,n INTEGER,
        PRIMARY KEY(identity_key,primer));
      CREATE TABLE final_lineage(source_item TEXT,ordinal INTEGER,sequence_id TEXT,identity_key TEXT,
        PRIMARY KEY(source_item,ordinal));
      CREATE TABLE final_lineage_sources(source_item TEXT PRIMARY KEY);
    ''')
    by_id = {item['id']: item for item in items}
    groups = connection.execute('SELECT id FROM usage_groups ORDER BY sample,chain,id').fetchall()
    receipts = {}
    for index, group in enumerate(groups, 1):
        gid = group['id']
        selected = [by_id[row[0]] for row in connection.execute(
            'SELECT id FROM usage_sources WHERE group_id=?', (gid,))]
        source, status = resolve(selected, root)
        connection.execute('INSERT INTO composition_sources(group_id,status,source) VALUES(?,?,?)',
                           (gid, status, str(source) if source else ''))
        if source is None:
            continue
        if connection.execute('SELECT 1 FROM composition_sources WHERE source=? AND group_id<>?',
                              (str(source), gid)).fetchone():
            raise ValueError('One postprocessed output cannot represent multiple reference groups')
        receipts[source] = version(source)
        connection.execute('DELETE FROM composition_lineage')
        # Read the actual mapped sequences, not observations.sequence, which may
        # contain the untrimmed input used to establish primer ownership.
        for item in selected:
            keep_lineage=lineage_samples is None or item['sample_label'] in lineage_samples
            path = safe_file(item['path'], root)
            receipts[path] = version(path)
            with open_text(path) as handle:
                reader = selected_reader(handle,LINEAGE_FIELDS,delimiter='\t' if '.tsv' in path.suffixes else ',')
                connection.execute('DROP TABLE IF EXISTS temp.composition_source_links')
                connection.execute('''CREATE TEMP TABLE composition_source_links AS
                    SELECT sequence_id,count(*) AS n,min(primer) AS primer FROM usage_records
                    WHERE source_item=? AND eligible=1 GROUP BY sequence_id''',(item['id'],))
                connection.execute('CREATE INDEX temp.composition_source_id ON composition_source_links(sequence_id)')
                joined=lookup_rows(connection,reader,'''SELECT b.ordinal,l.n,l.primer FROM report_batch b
                    LEFT JOIN composition_source_links l ON l.sequence_id=b.sequence_id ORDER BY b.ordinal''')
                lineage_batch, final_batch = [], []
                for ordinal, (row, linked) in enumerate(joined, 1):
                    # Unknown/multiple ownership remains in the sample but is
                    # not allocated to a named primer.
                    primer = linked['primer'] if linked['n'] == 1 else ''
                    identity_key=identity(row)
                    lineage_batch.append((identity_key,primer))
                    if keep_lineage:final_batch.append((item['id'],ordinal,row.get('sequence_id',''),identity_key))
                    if ordinal % 10000 == 0:
                        connection.executemany('''INSERT INTO composition_lineage VALUES(?,?,1)
                            ON CONFLICT(identity_key,primer) DO UPDATE SET n=n+1''',lineage_batch)
                        connection.executemany('INSERT INTO final_lineage VALUES(?,?,?,?)',final_batch)
                        lineage_batch.clear();final_batch.clear()
                        connection.commit()
                        if progress: progress(index-1, len(groups), f"{item['sample_label']} / {item['chain']}: verifying primer copies {ordinal:,}")
                connection.executemany('''INSERT INTO composition_lineage VALUES(?,?,1)
                    ON CONFLICT(identity_key,primer) DO UPDATE SET n=n+1''',lineage_batch)
                connection.executemany('INSERT INTO final_lineage VALUES(?,?,?,?)',final_batch)
                if keep_lineage:connection.execute('INSERT INTO final_lineage_sources VALUES(?)',(item['id'],))
        records = copies = 0
        for row in selected_table(source,(*LINEAGE_FIELDS,'junction_aa','copy','umi_counts')):
            if (row.get('locus') or '').strip().upper() != selected[0]['chain']:
                raise ValueError('Postprocessed output contains a different locus')
            weight = copy_count(row)
            aa = str(row.get('cdr3_aa') or '').strip().upper()
            main, subtype, state = classify(row.get('c_call'))
            key = identity(row)
            cursor = connection.execute('INSERT INTO composition_records VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (gid, selected[0]['id'], '', '', 1, weight, 1, row.get('cdr3_aa'), row.get('junction_aa'),
                 'cdr3_aa', aa, int(bool(AA.fullmatch(aa))), main, subtype, state, key))
            for segment in ('V', 'J', 'C'):
                genes = calls(row.get(segment.lower() + '_call'))
                assignment = 'unique' if len(genes) == 1 else 'ambiguous' if genes else 'unannotated'
                connection.executemany('INSERT INTO composition_calls VALUES(?,?,?,?)',
                                       [(cursor.lastrowid, segment, gene, assignment) for gene in genes or ['']])
            records += 1
            copies += weight
            if records % 10000 == 0: connection.commit()
        connection.execute('CREATE INDEX IF NOT EXISTS composition_identity ON composition_records(group_id,identity_key,sample_record)')
        connection.execute('CREATE INDEX IF NOT EXISTS composition_call_lookup ON composition_calls(record_id,segment)')
        # Exact accounting only: never spread a collapsed copy count by guessed
        # percentages, and never copy the entire weight into every primer.
        connection.execute('DROP TABLE IF EXISTS temp.composition_allocations')
        connection.execute('''CREATE TEMP TABLE composition_allocations AS
            SELECT r.id,l.primer,l.n FROM composition_records r JOIN composition_lineage l
              ON l.identity_key=r.identity_key
            WHERE r.group_id=? AND r.sample_record=1 AND l.primer<>''
              AND r.weight=(SELECT sum(z.n) FROM composition_lineage z WHERE z.identity_key=r.identity_key)
              AND 1=(SELECT count(*) FROM composition_records z WHERE z.group_id=r.group_id
                     AND z.identity_key=r.identity_key AND z.sample_record=1)''', (gid,))
        allocated = 0
        for record_id, primer, weight in connection.execute('SELECT * FROM composition_allocations'):
            cursor = connection.execute('''INSERT INTO composition_records
                SELECT NULL,group_id,source_item,sequence_id,?,0,?,eligible,cdr3_aa,junction_aa,
                  aa_field,aa_value,aa_valid,isotype_main,isotype_subclass,isotype_state,identity_key
                FROM composition_records WHERE id=?''', (primer, weight, record_id))
            connection.execute('INSERT INTO composition_calls SELECT ?,segment,gene,assignment FROM composition_calls WHERE record_id=?',
                               (cursor.lastrowid, record_id))
            allocated += weight
        connection.execute('UPDATE composition_sources SET records=?,copies=?,primer_copies=?,unresolved_copies=? WHERE group_id=?',
                           (records, copies, allocated, copies-allocated, gid))
        connection.commit()
    connection.executescript('''
      CREATE INDEX composition_selection ON composition_records(group_id,sample_record,primer,eligible);
      CREATE INDEX IF NOT EXISTS composition_call_lookup ON composition_calls(record_id,segment);
      DROP TABLE composition_lineage;
      DROP TABLE IF EXISTS composition_allocations;
    ''')
    if any(version(path) != receipt for path, receipt in receipts.items()):
        raise ValueError('Mapping/preprocessing output changed during composition analysis')
    return [dict(row) for row in connection.execute('SELECT * FROM composition_sources ORDER BY group_id')]
