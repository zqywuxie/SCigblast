"""Summary-backed reMapping inventory; only requested output files are inspected."""
from __future__ import annotations

import csv
import json
import stat
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


STAGES = (('01.fasta', 'conversion_summary.csv'),
          ('02.igblastn_out', 'chain_summary.csv'),
          ('03.umi_count', 'umi_count_summary.csv'))
LABELS = {'success': '已完成', 'empty': '结果为空', 'failed': '处理失败',
          'pending': '尚未完成', 'archived': '已归档'}
MAX_JOBS = 32
MAX_RECORDS = 100_000
_cache = OrderedDict()
_lock = threading.RLock()


def _relative(value):
    value = str(value or '')
    path = PurePosixPath(value)
    if not value or '\\' in value or ':' in value or path.is_absolute() or '..' in path.parts:
        return None
    return path if path.suffix.lower() == '.csv' else None


def _safe(path, base):
    try:
        return path.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError, RuntimeError):
        return False


def _version(info):
    return f'{info.st_ino}:{info.st_size}:{info.st_mtime_ns}'


def _summary_version(path, root):
    if not _safe(path, root):
        return None
    try:
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) if stat.S_ISREG(info.st_mode) else None
    except OSError:
        return None


def _read_summary(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return {str(source): record for record in csv.DictReader(handle)
                if (source := _relative(record.get('source_file')))}


def _count(record, field):
    value = str(record.get(field) or '')
    return int(value) if value.isdecimal() else None


def _identity(source, record):
    path = PurePosixPath(source)
    sample, _, chain = path.stem.rpartition('__')
    return {'source_file': source, 'sample_id': record.get('sample_id') or sample,
            'sample_key': record.get('sample_key') or (path.parent / sample).as_posix(),
            'chain': (record.get('chain') or chain).upper(),
            'label': path.with_suffix('').as_posix()}


@dataclass
class Inventory:
    root: Path
    dataset: str
    rows: list
    descriptors: dict
    complete: bool
    weight: int


def _build(row, signatures):
    root, dataset = Path(row['output_root']), row['dataset']
    summaries = []
    for (stage, name), signature in zip(STAGES, signatures):
        try:
            summaries.append(_read_summary(root / stage / dataset / name) if signature else {})
        except (OSError, csv.Error, UnicodeError):
            summaries.append({})
    conversion, alignment, final = summaries
    options = json.loads(row['options_json'])
    expected = options.get('mapping_inputs')
    complete = isinstance(expected, list)
    if complete:
        sources = {str(source): item for item in expected if isinstance(item, dict)
                   and (source := _relative(item.get('source_file')))}
        # Resume still selects by sample/chain. Include real, subsequently
        # discovered inputs from that scope without inventing combinations.
        for summary in summaries:
            for source, record in summary.items():
                sources.setdefault(source, record)
    else:
        sources = {source: record for summary in summaries for source, record in summary.items()}
    selected_chains = set(options.get('mapping_chains') or [])
    selected_samples = options.get('mapping_samples')
    ranks = [{source: number for number, source in enumerate(summary)} for summary in summaries]
    rows, descriptors = [], {}
    for source, original in sorted(sources.items()):
        stages = [summary.get(source, {}) for summary in summaries]
        identity = _identity(source, original)
        selected_identity = _identity(source, {})
        if (any(record.get('status') == 'SKIPPED' for record in stages)
                or (selected_chains and selected_identity['chain'] not in selected_chains)
                or (selected_samples is not None and selected_identity['sample_key'] not in selected_samples)):
            continue
        failed = next((record for record in reversed(stages) if record.get('status') == 'ERROR'), None)
        final_ok = stages[2].get('status') == 'OK' and failed is None
        state = ('failed' if failed else
                 ('empty' if _count(stages[2], 'output_rows') == 0 else 'success') if final_ok else 'pending')
        entry = {**identity, 'state': state, 'state_label': LABELS[state],
                 'error': str(failed.get('error') or '处理失败，摘要未记录原因') if failed else '',
                 'status': 'ERROR' if failed else 'OK' if final_ok else '',
                 'stage': '03.umi_count', 'preview': False, 'kind': None, 'path': None,
                 'size': None, 'file_version': None,
                 'input_rows': _count(stages[0], 'input_rows'),
                 'input_sequences': _count(stages[0], 'input_sequences'),
                 'output_rows': _count(stages[2], 'output_rows') if final_ok else None,
                 'retained_percent': None, '_final': None, '_intermediate': None}
        for index in (1, 2):
            if stages[index].get('status') != 'OK' or any(item.get('status') == 'ERROR' for item in stages[:index + 1]):
                continue
            stage = STAGES[index][0]
            sequences = _count(stages[0], 'input_sequences')
            if sequences is None and index == 1:
                sequences = _count(stages[1], 'input_sequences')
            output_rows = _count(stages[index], 'output_rows')
            descriptor = {**identity, 'stage': stage, 'preview': True, 'status': 'OK',
                          '_rank': ranks[index][source],
                          'input_rows': _count(stages[0], 'input_rows'),
                          'input_sequences': sequences, 'output_rows': output_rows,
                          'retained_percent': round(output_rows / sequences * 100, 2)
                              if sequences and output_rows is not None else None}
            relative = PurePosixPath(stage) / dataset / PurePosixPath(source).with_suffix('.tsv')
            descriptor['_relative'] = relative.as_posix()
            descriptors['mapping:' + relative.as_posix()] = descriptor
            if index == 2:
                legacy = relative.with_suffix('.filtered.tsv').as_posix()
                descriptor['_legacy'] = legacy
                descriptors['mapping:' + legacy] = descriptor
            entry['_final' if index == 2 else '_intermediate'] = descriptor
        if entry['_final']:
            entry.update({key: value for key, value in entry['_final'].items() if not key.startswith('_')})
        rows.append(entry)
    return Inventory(root, dataset, rows, descriptors, complete,
                     len(rows) + sum(len(summary) for summary in summaries) + len(descriptors))


def inventory(row):
    """Cache by summary identity, selection and attempt; never cache file availability."""
    root = Path(row['output_root'])
    with _lock:
        for _ in range(2):
            signatures = tuple(_summary_version(root / stage / row['dataset'] / name, root) for stage, name in STAGES)
            key = (str(root), row['dataset'], row['attempt_no'], row['options_json'],
                   dict(row).get('execution_json'), signatures)
            cached = _cache.get(key)
            if cached:
                _cache.move_to_end(key)
                return cached
            result = _build(row, signatures)
            after = tuple(_summary_version(root / stage / row['dataset'] / name, root) for stage, name in STAGES)
            if after == signatures:
                if result.weight <= MAX_RECORDS:
                    # Keep only the newest version of an output/dataset.
                    for old in [old for old in _cache if old[:2] == key[:2]]:
                        del _cache[old]
                    _cache[key] = result
                    while len(_cache) > MAX_JOBS or sum(item.weight for item in _cache.values()) > MAX_RECORDS:
                        _cache.popitem(last=False)
                return result
        # Never authorize a file from a known-outdated snapshot. While the
        # runner keeps replacing summaries, retain only known pending inputs.
        return _build(row, (None,) * len(STAGES))


def _materialize(index, descriptor, requested=None):
    if descriptor is None:
        return None
    candidates = [requested.removeprefix('mapping:')] if requested else [descriptor['_relative'], descriptor.get('_legacy')]
    base = index.root / descriptor['stage'] / index.dataset
    if not _safe(base, index.root):
        return None
    for relative in candidates:
        if not relative:
            continue
        path = index.root / relative
        if not _safe(path, base):
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            return {**{key: value for key, value in descriptor.items() if not key.startswith('_')},
                    'kind': 'mapping:' + relative, 'path': str(path),
                    'size': info.st_size, 'file_version': _version(info)}
    return None


def lookup(row, kind, index=None):
    if row['status'] == 'ARCHIVED':
        return None
    index = index or inventory(row)
    return _materialize(index, index.descriptors.get(kind), kind)


def files(row):
    """Legacy all-files API; the paged UI and individual access use other entrypoints."""
    if row['status'] == 'ARCHIVED':
        return []
    index = inventory(row)
    return [item for field in ('_final', '_intermediate')
            for descriptor in sorted((record[field] for record in index.rows if record[field]), key=lambda item: item['_rank'])
            if (item := _materialize(index, descriptor))]


def page(row, query='', chain='', state='', offset=0, limit=50):
    index = inventory(row)
    archived = row['status'] == 'ARCHIVED'
    counts = dict.fromkeys(LABELS, 0)
    counts.update(Counter('archived' if archived else item['state'] for item in index.rows))
    counts['total'] = len(index.rows)
    needle = query.strip().casefold()
    filtered = [item for item in index.rows
                if (not chain or item['chain'] == chain.upper())
                and (not state or ('archived' if archived else item['state']) == state)
                and (not needle or needle in ' '.join(str(item.get(key) or '')
                     for key in ('source_file', 'sample_id', 'sample_key', 'chain', 'error')).casefold())]
    offset, limit = max(0, offset), max(1, min(200, limit))
    rows = []
    for item in filtered[offset:offset + limit]:
        entry = {key: value for key, value in item.items() if not key.startswith('_')}
        final = None if archived else _materialize(index, item['_final'])
        intermediate = None if archived else _materialize(index, item['_intermediate'])
        if final:
            entry.update(final)
        entry.update(intermediate=intermediate, available=bool(final), preview=bool(final),
                     availability='archived' if archived else 'available' if final else
                         'missing' if item['_final'] else 'not_ready')
        if archived:
            entry.update(state='archived', state_label=LABELS['archived'])
        elif item['_final'] and not final:
            entry['error'] = '摘要记录已完成，但结果文件不存在或已不可访问'
        rows.append(entry)
    return {'rows': rows, 'total': len(filtered), 'offset': offset, 'limit': limit,
            'counts': counts, 'chains': sorted({item['chain'] for item in index.rows}),
            'inventory_complete': index.complete}
