"""Stream final Base/Pig AIRR task outputs into selected, locus-matched TSVs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

CHAINS = frozenset(('TRA', 'TRB', 'TRD', 'TRG', 'IGH', 'IGK', 'IGL'))
FIELDS = ('sample_id', 'pair_id', 'sample_key', 'chain', 'input_rows', 'output_rows',
          'excluded_locus_rows', 'unknown_locus_rows', 'status', 'error', 'output_path')
SUMMARY = 'locus_split_summary.csv'
VERSION = 'selected-locus-v1'
MAPPING_FILTER_VERSION = 'productive-valid-v-selected-locus-v1'
BAD_V = frozenset(('', '*', '-', 'NA', 'N/A', 'NONE', 'NULL', 'NO_HIT', 'UNMAPPED', 'NOT_FOUND'))


def filter_mapping_airr(source, target, chain):
    """Filter one completed mapping task in one pass, before its DONE marker."""
    chain = str(chain).strip().upper()
    if chain not in CHAINS:
        raise ValueError(f'unknown target chain: {chain}')
    report = dict(raw_rows=0, mapped_rows=0, productive_rows=0, output_rows=0,
                  locus_input_rows=0, excluded_locus_rows=0, unknown_locus_rows=0,
                  locus_filter_version=1)

    def write(handle):
        header = None
        with Path(source).open(encoding='utf-8-sig', newline='') as incoming:
            writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
            for record in csv.reader(incoming, delimiter='\t'):
                if not record or record[0].startswith('#'):
                    continue
                if header is None:
                    if 'sequence_id' not in record or 'v_call' not in record:
                        continue
                    header = record
                    if 'locus' not in header:
                        raise ValueError(f'missing locus column: {source}')
                    vi, li = header.index('v_call'), header.index('locus')
                    pi = header.index('productive') if 'productive' in header else -1
                    writer.writerow(header)
                    continue
                if len(record) != len(header):
                    raise ValueError(f'malformed AIRR record: {source}')
                report['raw_rows'] += 1
                good_v = record[vi].strip().upper() not in BAD_V
                good_p = pi >= 0 and record[pi].strip().upper() in {'T', 'TRUE'}
                report['mapped_rows'] += int(good_v)
                report['productive_rows'] += int(good_p)
                if not good_v or not good_p:
                    continue
                report['locus_input_rows'] += 1
                locus = record[li].strip().upper()
                if locus == chain:
                    writer.writerow(record)
                    report['output_rows'] += 1
                else:
                    report['excluded_locus_rows'] += 1
                    report['unknown_locus_rows'] += int(locus not in CHAINS)
        if header is None:
            raise ValueError(f'malformed IgBLAST AIRR TSV: {source}')

    atomic_text(target, write)
    return report


def signature(path):
    stat = Path(path).stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle, delimiter='\t' if Path(path).suffix == '.tsv' else ',')


def confined(path, root):
    if root.is_symlink():
        raise ValueError(f'symlink is not allowed: {root}')
    path = Path(os.path.abspath(path))
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f'symlink is not allowed: {current}')
    path.resolve().relative_to(root)
    return path


def plan(summary, output, manifest=None):
    summary, output = Path(summary).resolve(), Path(output).absolute()
    root = summary.parent
    selected = None
    if manifest is not None:
        selected = set()
        for row in rows(manifest):
            if row.get('status', '').upper() != 'OK':
                continue
            chains = set(filter(None, re.split(r'[,;\s]+', row.get('igblast_chains', '').upper())))
            if not chains or chains - CHAINS:
                raise ValueError(f"invalid selected chains: {row.get('sample_id')}")
            selected.update((row.get('sample_id', ''), row.get('pair_id', ''), chain) for chain in chains)
    grouped = {}
    for row in rows(summary):
        key = (row.get('sample_id', ''), row.get('pair_id', ''), row.get('chain', '').strip().upper())
        if selected is not None and key not in selected:
            continue  # Stale/unselected tasks must not broaden a sample's selection.
        if not key[0] or key[2] not in CHAINS or row.get('status', '').upper() != 'OK':
            raise ValueError(f'invalid or failed mapping result: {key}')
        raw = row.get('output_path', '')
        if not raw:
            raise ValueError(f'mapping result has no output_path: {key}')
        path = confined(Path(raw) if Path(raw).is_absolute() else root / raw, root)
        if not path.is_file():
            raise ValueError(f'mapping result is missing: {path}')
        grouped.setdefault(key, set()).add(str(path))
    if not grouped or selected is not None and selected - grouped.keys():
        raise ValueError('selected sample/chain results are missing from the mapping summary')
    # Keep the common case readable; distinguish same-named samples/pairs and
    # sanitized-name collisions without repeating the same name in directories.
    labels = {}
    for sample, pair, _ in grouped:
        name = re.sub(r'[^\w.-]', '_', sample)[:80].strip('.') or 'sample'
        labels.setdefault(name.casefold(), set()).add((sample, pair))
    implementation = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    tasks = []
    for (sample, pair, chain), paths in sorted(grouped.items()):
        name = re.sub(r'[^\w.-]', '_', sample)[:80].strip('.') or 'sample'
        identity = digest([sample, pair, chain])
        if len(labels[name.casefold()]) > 1:
            name += '_' + digest([sample, pair])[:12]
        target = confined(output / name / f'{chain}.tsv', output)
        sources = [{'path': path, 'version': signature(path)} for path in sorted(paths)]
        tasks.append(dict(id=identity, sources=sources, target=str(target),
                          fingerprint=digest([VERSION, implementation, sample, pair, chain, sources]),
                          sample_id=sample, pair_id=pair,
                          sample_key=f'{pair}/{sample}' if pair else sample, chain=chain))
    return tasks


def cached(task, output):
    try:
        marker = confined(Path(output) / '.checkpoints' / f"{task['id']}.json", Path(output).absolute())
        data = json.loads(marker.read_text(encoding='utf-8'))
        if (data['fingerprint'] == task['fingerprint'] and
                data['output_version'] == signature(task['target']) and
                data['report']['output_path'] == task['target']):
            return data['report']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def atomic_text(path, write):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f'.{path.name}.{uuid.uuid4().hex}.tmp'
    # O_EXCL keeps creation safe while 0666 lets the kernel apply umask/default
    # ACL, matching ordinary result files. mkstemp's 0600 masks inherited ACLs.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            write(handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def split_task(task, output):
    report = {key: task[key] for key in ('sample_id', 'pair_id', 'sample_key', 'chain')}
    report.update(input_rows=0, output_rows=0, excluded_locus_rows=0, unknown_locus_rows=0,
                  status='OK', error='', output_path=task['target'])

    def write(handle):
        columns = None
        writer = None
        for source in task['sources']:
            with Path(source['path']).open(encoding='utf-8-sig', newline='') as incoming:
                reader = csv.reader(incoming, delimiter='\t')
                header = next(reader, None)
                if not header or 'locus' not in header:
                    raise ValueError(f"missing locus column: {source['path']}")
                if columns is not None and header != columns:
                    raise ValueError('inconsistent AIRR headers for the same sample/chain')
                if writer is None:
                    columns = header
                    writer = csv.writer(handle, delimiter='\t', lineterminator='\n')
                    writer.writerow(header)
                locus_index = header.index('locus')
                for record in reader:
                    if not record:
                        continue
                    if len(record) != len(header):
                        raise ValueError(f"malformed AIRR record: {source['path']} line {reader.line_num}")
                    report['input_rows'] += 1
                    locus = record[locus_index].strip().upper()
                    if locus == task['chain']:
                        writer.writerow(record)
                        report['output_rows'] += 1
                    else:
                        report['excluded_locus_rows'] += 1
                        report['unknown_locus_rows'] += int(locus not in CHAINS)
            if signature(source['path']) != source['version']:
                raise ValueError('mapping input changed during locus splitting')
        if any(signature(source['path']) != source['version'] for source in task['sources']):
            raise ValueError('mapping input changed during locus splitting')

    atomic_text(task['target'], write)
    marker = Path(output) / '.checkpoints' / f"{task['id']}.json"
    atomic_text(marker, lambda handle: json.dump(dict(fingerprint=task['fingerprint'], report=report,
                                                    output_version=signature(task['target'])), handle))
    return report


def ready(summary, output, manifest=None):
    try:
        tasks = plan(summary, output, manifest)
        actual = list(rows(Path(output) / SUMMARY))
        expected = [cached(task, output) for task in tasks]
        return (all(expected) and actual == [{key: str(row[key]) for key in FIELDS} for row in expected])
    except (OSError, ValueError, KeyError, TypeError, csv.Error):
        return False


def run(summary, output, manifest=None, *, force=False):
    output = Path(output).absolute()
    tasks = plan(summary, output, manifest)
    # Invalidate the published summary first, so a partial/restarted split can
    # never be mistaken for a complete new set of final results.
    (output / SUMMARY).unlink(missing_ok=True)
    reports = []
    for index, task in enumerate(tasks, 1):
        report = None if force else cached(task, output)
        reused = report is not None
        try:
            report = report or split_task(task, output)
        except (OSError, ValueError, csv.Error) as error:
            report = {key: task.get(key, '') for key in FIELDS}
            report.update(status='ERROR', error=str(error), output_path='')
        reports.append(report)
        print(f"[LOCUS_SPLIT] progress={index}/{len(tasks)} percent={index*100//len(tasks)} "
              f"sample={task['sample_id']} chain={task['chain']} status={report['status']} reused={int(reused)}", flush=True)
    def write(handle):
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(reports)
    atomic_text(output / SUMMARY, write)
    return all(row['status'] == 'OK' for row in reports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    try:
        success = ready(args.summary, args.output, args.manifest) if args.check else run(
            args.summary, args.output, args.manifest, force=args.force)
    except (OSError, ValueError, csv.Error) as error:
        print(f'[LOCUS_SPLIT] {error}', flush=True)
        success = False
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
