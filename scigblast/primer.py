"""Persist primer attribution on original merged queries, then trim for IgBLAST."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from analysis.repertoire.catalog import Matcher
from analysis.repertoire.io import digest, sequences, version, write_json

VERSION='primer-attribution-before-trim-v1'
CODE_HASH=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_GUARD=threading.Lock()
_LOCKS={}


def frozen_payload(catalog):
    """No read is discarded. Only a shared, definite prefix length is removed."""
    return dict(version=VERSION,catalog=catalog,unassigned_policy='keep_original',
        read_structure=catalog['config'].get('mapping_read_structure',catalog['config'].get('read_structure','window')))


def runtime_config():
    path=os.environ.get('SCIGBLAST_PRIMER_MAPPING_CONFIG','')
    if not path:return None
    content=Path(path).read_bytes()
    expected=os.environ.get('SCIGBLAST_PRIMER_MAPPING_HASH','')
    if not expected or hashlib.sha256(content).hexdigest()!=expected:
        raise ValueError('引物比对配置校验失败，不能复用旧配置')
    value=json.loads(content)
    if value.get('version')!=VERSION or value.get('unassigned_policy')!='keep_original':
        raise ValueError('不支持的引物剪切规则')
    return value


def prepare(fasta,output_root,dataset,sample,pair,*,payload=None):
    payload=payload if payload is not None else runtime_config()
    if not payload:return None
    fasta=Path(fasta).resolve()
    catalog=payload['catalog']
    if sample not in catalog['assignments']:
        raise ValueError(f'{sample} 没有明确的 primer 配置，停止比对准备')
    matcher=Matcher(catalog['assignments'][sample])
    signature=version(fasta)
    key=digest([VERSION,CODE_HASH,str(fasta),signature,payload,sample,pair])
    parent=Path(output_root)/'.analysis'/'primer_inputs'/digest([dataset,sample,pair,str(fasta)])[:24]
    with _GUARD:lock=_LOCKS.setdefault(str(parent),threading.Lock())
    with lock:
        target=parent/key[:24]
        try:
            data=json.loads((target/'manifest.json').read_text(encoding='utf-8'))
            if data['fingerprint']==key and all(version(target/name)==value for name,value in data['files'].items()):
                return dict(data,folder=str(target),manifest=str(target/'manifest.json'),path=str(target/data['mapping_fasta']))
        except (OSError,ValueError,KeyError):pass
        parent.mkdir(parents=True,exist_ok=True)
        if target.exists():target=parent/(key[:24]+'_'+uuid.uuid4().hex[:8])
        staging=parent/('.'+uuid.uuid4().hex)
        staging.mkdir()
        conn=sqlite3.connect(staging/'assignments.sqlite')
        counters={state:0 for state in ('trimmed_unique','trimmed_multi_same_length','unmatched_untrimmed',
            'ambiguous_length_untrimmed','structure_untrimmed','primer_only_untrimmed')}
        count=0;source_sha=hashlib.sha256()
        try:
            conn.execute('''CREATE TABLE assignments(ordinal INTEGER PRIMARY KEY,sequence_id TEXT,names TEXT,
                families TEXT,state TEXT,trim_length INTEGER,primer_length INTEGER,original_length INTEGER,mapped_length INTEGER)''')
            options=catalog['config']
            adapter=options.get('adapter','ACACTCTTTCCCTACACGACGCTCTTCCGATCT').upper()
            prefix_length=len(adapter)+int(options.get('umi_length',10))+int(options.get('barcode_length',8))
            with (staging/'mapping.fasta').open('w',encoding='utf-8',newline='') as outgoing:
                for count,(title,sequence) in enumerate(sequences(fasta,normalize=False),1):
                    source_sha.update((title+'\n'+sequence+'\n').encode())
                    prefix=0;window=sequence
                    structure_ok=True
                    if payload['read_structure']=='oligo':
                        structure_ok=sequence.upper().startswith(adapter) and len(sequence)>prefix_length
                        prefix=prefix_length
                        window=sequence[prefix:] if structure_ok else ''
                    elif payload['read_structure']!='window':raise ValueError('未知 mapping 引物窗口结构')
                    names,families=matcher.match(window)
                    lengths={len(seq) for seq in families}
                    cut=primer_length=0
                    state='unmatched_untrimmed'
                    if not structure_ok:state='structure_untrimmed'
                    elif len(lengths)>1:state='ambiguous_length_untrimmed'
                    elif lengths:
                        primer_length=next(iter(lengths))
                        candidate=prefix+primer_length
                        if candidate>=len(sequence):state='primer_only_untrimmed'
                        else:
                            cut=candidate
                            state='trimmed_unique' if len(names)==1 else 'trimmed_multi_same_length'
                    counters[state]+=1
                    # Attribution is recorded before any bases are removed.
                    conn.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?,?)',
                        (count,title.split()[0],json.dumps(names,ensure_ascii=False),json.dumps(families),state,cut,
                         primer_length,len(sequence),len(sequence)-cut))
                    outgoing.write('>'+title+'\n'+sequence[cut:]+'\n')
                    if count%20000==0:conn.commit()
            conn.execute('CREATE INDEX assignment_id ON assignments(sequence_id)');conn.commit();conn.close()
            if version(fasta)!=signature:raise ValueError('合并序列在引物归属/剪切期间发生变化')
            data=dict(version=VERSION,fingerprint=key,source=str(fasta),source_version=signature,
                source_sequence_sha256=source_sha.hexdigest(),catalog=catalog,catalog_version=catalog['version'],
                read_structure=payload['read_structure'],unassigned_policy='keep_original',
                original_queries=count,submitted_queries=count,counts=counters,
                assignments='assignments.sqlite',mapping_fasta='mapping.fasta',
                files={name:version(staging/name) for name in ('assignments.sqlite','mapping.fasta')})
            write_json(staging/'manifest.json',data)
            os.replace(staging,target)
            return dict(data,folder=str(target),manifest=str(target/'manifest.json'),path=str(target/'mapping.fasta'))
        except BaseException:
            conn.close()
            import shutil
            if staging.exists():shutil.rmtree(staging)
            raise
