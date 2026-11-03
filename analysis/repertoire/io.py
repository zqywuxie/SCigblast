"""Bounded sequence/table readers and immutable evidence helpers."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re

from pipeline_locus_split import atomic_text

csv.field_size_limit(16 * 1024 * 1024)
CHAINS = ('TRA', 'TRB', 'TRD', 'TRG', 'IGH', 'IGK', 'IGL')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def version(path):
    info = Path(path).stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def safe_file(path, root):
    path, root = Path(path).absolute(), Path(root).resolve()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'不支持符号链接：{path.name}')
    path.resolve().relative_to(root)
    if not path.is_file():
        raise ValueError(f'文件不存在：{path.name}')
    return path.resolve()


def open_text(path):
    return gzip.open(path, 'rt', encoding='utf-8-sig', newline='') if str(path).endswith('.gz') else Path(path).open(encoding='utf-8-sig', newline='')


def table(path):
    with open_text(path) as handle:
        yield from csv.DictReader(handle, delimiter='\t' if '.tsv' in Path(path).suffixes else ',')


def selected_reader(handle, fields, *, delimiter=','):
    """Use the same CSV engine, allocating only the columns this pass consumes."""
    reader=csv.reader(handle,delimiter=delimiter)
    header=next(reader,[]);fields=set(fields)
    indices=[(name,i) for i,name in enumerate(header) if name in fields]
    for values in reader:
        if values:
            # As DictReader, later duplicate headers win; short rows yield None.
            yield {name:values[i] if i<len(values) else None for name,i in indices}


def selected_table(path, fields):
    with open_text(path) as handle:
        yield from selected_reader(handle,fields,delimiter='\t' if '.tsv' in Path(path).suffixes else ',')


def sequences(path, *, normalize=True):
    """Yield original title and full sequence; reject truncated/malformed records."""
    with open_text(path) as handle:
        first = handle.readline()
        if not first:
            return
        if first.startswith('@'):
            title = first
            while title:
                seq, plus, quality = handle.readline(), handle.readline(), handle.readline()
                if not title.startswith('@') or not plus.startswith('+') or not quality or len(seq.rstrip('\r\n')) != len(quality.rstrip('\r\n')):
                    raise ValueError(f'FASTQ 记录不完整：{Path(path).name}')
                sequence=seq.rstrip('\r\n')
                yield title[1:].rstrip('\r\n'), sequence.upper() if normalize else sequence
                title = handle.readline()
        elif first.startswith('>'):
            title, parts = first[1:].rstrip('\r\n'), []
            for line in handle:
                if line.startswith('>'):
                    sequence=''.join(parts)
                    yield title, sequence.upper() if normalize else sequence
                    title, parts = line[1:].rstrip('\r\n'), []
                else:
                    parts.append(line.strip())
            sequence=''.join(parts)
            yield title, sequence.upper() if normalize else sequence
        else:
            raise ValueError(f'无法识别序列格式：{Path(path).name}')


def write_json(path, data):
    atomic_text(path, lambda handle: json.dump(data, handle, ensure_ascii=False, indent=2))


def write_table(path, records, columns):
    def write(handle):
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for row in records:
            # A spreadsheet must not execute data-derived names as formulas.
            writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value for key, value in row.items()})
    atomic_text(path, write)


def label_name(value):
    stem = re.sub(r'[^\w.-]', '_', str(value))[:72].strip('.') or 'sample'
    return stem + '_' + digest(str(value))[:8]


def canonical_header(title):
    """Mirror current Base/Pig header cleanup; collisions are checked by callers."""
    token = title.split()[0].split('#')[0]
    token = re.sub(r'/[12]$', '', token)
    fields = token.split(':')
    if len(fields) >= 8:
        fields = fields[:7]
    for index in range(3,len(fields)):
        fields[index]=re.sub('[A-Za-z]','',fields[index]).lstrip('0') or '0'
    # A candidate only: every bridge must verify uniqueness in its own partition.
    return ':'.join(fields)
