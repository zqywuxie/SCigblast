"""Capture unfiltered chain annotations without changing public AIRR outputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import uuid

from analysis.repertoire.io import canonical_header, digest, sequences, version, write_json

VERSION = 'mapping-trace-v2'
_LOCK = threading.Lock()
_ARCHIVE_LOCKS = {}
FIELDS = ('sequence_id', 'locus', 'v_call', 'd_call', 'j_call', 'c_call', 'productive',
          'stop_codon', 'vj_in_frame', 'v_frameshift', 'v_identity', 'j_identity', 'c_identity',
          'cdr3_aa', 'junction_aa')


def archive_lock(folder):
    with _LOCK:
        return _ARCHIVE_LOCKS.setdefault(str(Path(folder).resolve()),threading.Lock())


def trace_folder(output_root, dataset, sample, pair, fasta):
    key = digest([dataset, sample, pair, str(Path(fasta).resolve())])[:24]
    return Path(output_root) / '.analysis' / 'trace' / key


def capture_queries(folder, fasta):
    """One immutable query archive shared by the chain workers of a pair."""
    from Bio import bgzf
    folder = Path(folder)
    identity = version(fasta)
    with archive_lock(folder):
        marker = folder / 'queries.json'
        try:
            data = json.loads(marker.read_text(encoding='utf-8'))
            if data.get('schema') == 2 and data['source_version'] == identity and all(version(folder / name) == info for name, info in data['files'].items()):
                return data
        except (OSError, ValueError, KeyError):
            pass
        folder.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        archive, index = folder / f'queries-{token}.bgz', folder / f'queries-{token}.sqlite'
        connection = sqlite3.connect(index)
        try:
            connection.execute('CREATE TABLE queries (ordinal INTEGER PRIMARY KEY, sequence_id TEXT NOT NULL, title TEXT NOT NULL, offset INTEGER NOT NULL, canonical TEXT NOT NULL)')
            sha, count = hashlib.sha256(), 0
            with bgzf.BgzfWriter(str(archive), 'wb') as outgoing:
                for count, (title, seq) in enumerate(sequences(fasta), 1):
                    body = (seq + '\n').encode('ascii')
                    offset = outgoing.tell()
                    outgoing.write(body)
                    sha.update((title + '\n' + seq + '\n').encode())
                    connection.execute('INSERT INTO queries VALUES(?,?,?,?,?)', (count, title.split()[0], title, offset, canonical_header(title)))
                    if count % 20000 == 0:
                        connection.commit()
            connection.execute('CREATE INDEX query_sequence_id ON queries(sequence_id)')
            connection.execute('CREATE INDEX query_canonical ON queries(canonical)')
            connection.commit()
            if version(fasta) != identity:
                raise ValueError('query 输入在归档时发生变化')
        except BaseException:
            connection.close()
            index.unlink(missing_ok=True)
            archive.unlink(missing_ok=True)
            raise
        finally:
            connection.close()
        data = dict(version=VERSION, schema=2, source=str(fasta), source_version=identity, count=count,
                    sequence_sha256=sha.hexdigest(), archive=archive.name, index=index.name,
                    files={name.name: version(name) for name in (archive, index)})
        write_json(marker, data)
        return data


class Capture:
    def __init__(self, context, chain):
        self.folder = trace_folder(context['output_root'], context['dataset'], context['sample'], context['pair'], context['fasta'])
        self.queries = capture_queries(self.folder, context['fasta'])
        # Cheap receipts let later reports read R1 without archiving every upstream
        # sequence during mapping. Reports still enforce their input allowlist.
        self.read_sources = {key: dict(path=str(Path(path).resolve()), version=version(path))
                             for key, path in context.get('reads', {}).items()
                             if path and Path(path).is_file()}
        self.upstream = capture_upstream(self.folder, context.get('reads', {})) if os.environ.get('SCIGBLAST_TRACE_MODE') == 'full' else None
        self.chain, self.context = chain, context
        self.path = self.folder / f'{chain}-{uuid.uuid4().hex}.sqlite'
        self.connection = sqlite3.connect(self.path)
        columns = ','.join(f'{key} TEXT' for key in FIELDS)
        self.connection.execute(f'CREATE TABLE annotations (ordinal INTEGER PRIMARY KEY,{columns},filter_reason TEXT NOT NULL)')
        self.count = 0
        self.available_fields = set()

    def record(self, row, reason):
        self.available_fields.update(set(row) & set(FIELDS))
        self.count += 1
        values = [self.count, *[row.get(key) for key in FIELDS], reason]
        self.connection.execute('INSERT INTO annotations VALUES(' + ','.join('?' for _ in values) + ')', values)
        if self.count % 20000 == 0:
            self.connection.commit()

    def publish(self, report):
        self.connection.execute('CREATE INDEX annotation_id ON annotations(sequence_id)')
        self.connection.execute('CREATE TABLE reference_genes (segment TEXT, allele TEXT, gene TEXT, sequence_sha256 TEXT, PRIMARY KEY(segment,allele))')
        reference = {}
        for segment, prefix in self.context.get('databases', {}).items():
            reference[segment] = dict(prefix=str(prefix), available=False)
            # Read the actual BLAST index, not a possibly stale neighboring FASTA.
            try:
                completed = subprocess.run(['blastdbcmd', '-db', str(prefix), '-entry', 'all', '-outfmt', '%i\t%s'], capture_output=True, text=True, timeout=120, check=True)
                sha = hashlib.sha256()
                for line in completed.stdout.splitlines():
                    allele, sequence = line.split('\t', 1)
                    if len(sequence) <= 1:
                        continue
                    allele = allele.removeprefix('lcl|')
                    sha.update((allele + '\t' + sequence + '\n').encode())
                    self.connection.execute('INSERT OR IGNORE INTO reference_genes VALUES(?,?,?,?)', (segment, allele, allele.split('*')[0], hashlib.sha256(sequence.encode()).hexdigest()))
                reference[segment].update(available=True, sha256=sha.hexdigest())
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                reference[segment]['error'] = str(error)[:500]
        self.connection.commit()
        self.connection.close()
        write_json(self.folder / f'{self.chain}.json', dict(version=VERSION, chain=self.chain,
            sample=self.context['sample'], pair=self.context['pair'], dataset=self.context['dataset'],
            output_path=self.context.get('output_path'),
            output_version=version(self.context['output_path']),
            mapping_source=self.context.get('mapping_fasta',self.context['fasta']),
            primer_preparation=self.context.get('primer_preparation'),
            query_version=self.queries['sequence_sha256'], query_index=self.queries['index'], query_archive=self.queries['archive'],
            query_files=self.queries['files'],
            annotation=self.path.name, annotation_version=version(self.path), count=self.count,
            upstream=self.upstream,
            read_sources=self.read_sources,
            annotation_fields=sorted(self.available_fields),
            report=report, reference=reference, dependency=self.context.get('dependency')))

    def abort(self):
        self.connection.close()
        self.path.unlink(missing_ok=True)


def begin(context, chain):
    return Capture(context, chain) if context and os.environ.get('SCIGBLAST_TRACE_MODE', 'mapping') != 'off' else None


def capture_upstream(folder, sources):
    """Optional full input sequences + stage membership; no quality or ID rewriting."""
    from Bio import bgzf
    if not sources or not sources.get('raw_R1') or not sources.get('raw_R2'):
        raise ValueError('全流程追溯缺少原始 R1/R2 来源')
    folder=Path(folder)
    versions={key:dict(path=str(path),version=version(path)) for key,path in sources.items() if path and Path(path).is_file()}
    with archive_lock(folder):
        marker=folder/'upstream.json'
        try:
            data=json.loads(marker.read_text(encoding='utf-8'))
            if data['sources']==versions and all(version(folder/name)==info for name,info in data['files'].items()):return data
        except (OSError,ValueError,KeyError):pass
        token=uuid.uuid4().hex
        index,archive=folder/f'upstream-{token}.sqlite',folder/f'upstream-{token}.bgz'
        conn=sqlite3.connect(index)
        counts,hashes={},{}
        try:
            conn.execute('CREATE TABLE reads(stage TEXT,ordinal INTEGER,title TEXT,canonical TEXT,offset INTEGER,PRIMARY KEY(stage,ordinal))')
            with bgzf.BgzfWriter(str(archive),'wb') as outgoing:
                for stage,source in versions.items():
                    sequence_sha=hashlib.sha256()
                    total=0
                    for ordinal,(title,seq) in enumerate(sequences(source['path']),1):
                        total=ordinal
                        sequence_sha.update((title+'\n'+seq+'\n').encode())
                        offset=outgoing.tell() if stage.startswith('raw_') else None
                        if offset is not None:outgoing.write((seq+'\n').encode('ascii'))
                        conn.execute('INSERT INTO reads VALUES(?,?,?,?,?)',(stage,ordinal,title,canonical_header(title),offset))
                        if ordinal%20000==0:conn.commit()
                    if version(source['path'])!=source['version']:raise ValueError('上游序列在追溯时发生变化')
                    counts[stage]=total
                    hashes[stage]=sequence_sha.hexdigest()
            conn.execute('CREATE INDEX read_canonical ON reads(stage,canonical)')
            conn.commit()
        except BaseException:
            conn.close();index.unlink(missing_ok=True);archive.unlink(missing_ok=True);raise
        finally:conn.close()
        data=dict(sources=versions,index=index.name,archive=archive.name,
            counts=counts,sequence_sha256=hashes,
            files={p.name:version(p) for p in (index,archive)})
        write_json(marker,data)
        return data
