"""Atomic, ranked clone tables using the exact record counts used for Treemap."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import shutil
import tempfile

VERSION = 'records-rank-v1'
FIELDS = ('sample_key', 'sample_label', 'chain', 'rank', 'v_call', 'j_call',
          'cdr3_aa', 'record_count', 'frequency_percent', 'cumulative_frequency_percent')


def write_clone_table(clones, total, target, *, sample_key='', sample_label='', chain=''):
    """Preserve raw clone identities; break count ties by V, J, then CDR3 text.

    Sorting is separate from the renderer's frame. Write rows incrementally,
    without allocating additional rank/frequency columns or large IPC payloads.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            cumulative = 0
            ranked = clones.sort_values(['copy', 'V', 'J', 'CDR3(pep)'], ascending=[False, True, True, True])
            for rank, (v_call, j_call, cdr3, count) in enumerate(ranked.itertuples(index=False, name=None), 1):
                count = int(count)
                cumulative += count
                writer.writerow((sample_key, sample_label, chain, rank, v_call, j_call, cdr3, count,
                                 f'{count / total * 100:.8f}', f'{cumulative / total * 100:.8f}'))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def merge_clone_tables(paths, target):
    """Combine completed chain tables with bounded memory and one CSV header."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as output:
            csv.writer(output).writerow(FIELDS)
            for path in paths:
                with Path(path).open(encoding='utf-8-sig', newline='') as source:
                    header = source.readline()
                    if next(csv.reader([header]), []) != list(FIELDS):
                        raise ValueError('Clone Top table header does not match the expected format')
                    shutil.copyfileobj(source, output, length=128 * 1024)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
