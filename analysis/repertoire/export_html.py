"""Build standalone HTML from a completed report's public tables and PNGs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from .io import safe_file, version
from .report_html import write


def export(source, destination, *, replace=False, task_name=None):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError('HTML output must be outside the source report directory')
    if destination.exists() and not replace:
        raise ValueError('Output exists; use --replace to update it')
    started = time.perf_counter()
    manifest_path = safe_file(source / 'manifest.json', source)
    summary_path = safe_file(source / 'summary.json', source)
    signatures = {manifest_path: version(manifest_path), summary_path: version(summary_path)}
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    data = json.loads(summary_path.read_text(encoding='utf-8'))
    listed = set(manifest.get('files', []))
    if 'summary.json' not in listed or not data.get('exports'):
        raise ValueError('Source is not a completed public report')
    # Portable ZIP exports have different device/inode/time values. Their file
    # sizes can still be checked against the original public artifact receipt.
    names = {'summary.json', *data['exports'].values()}
    names.update(f['png'] for f in data.get('figures', []) if f.get('png'))
    for name in sorted(names):
        if name not in listed:
            raise ValueError('Artifact is absent from completion manifest: ' + name)
        path = safe_file(source / name, source)
        signature = version(path)
        recorded = manifest.get('file_versions', {}).get(name)
        if recorded and recorded[2] != signature[2]:
            raise ValueError('Public artifact size changed: ' + name)
        signatures[path] = signature
    if task_name is not None:
        data['task_name'] = task_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.report-html-', suffix='.tmp', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        result = write(temporary, data, source, run_id=str(manifest.get('run_id') or ''))
        for path, signature in signatures.items():
            if version(path) != signature:
                raise ValueError('Report materials changed during export: ' + path.name)
        if destination.exists() and not replace:
            raise ValueError('Output was created by another export')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(mode='html-only', output=str(destination), seconds=round(time.perf_counter()-started, 3),
                figures=result['figures'], tables=result['tables'], statistics_recomputed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Completed report directory, or extracted public ZIP')
    parser.add_argument('--output', required=True, type=Path, help='HTML path outside the source directory')
    parser.add_argument('--replace', action='store_true', help='Replace an existing destination HTML')
    parser.add_argument('--task-name', help='Override display title only')
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.output, replace=args.replace, task_name=args.task_name), ensure_ascii=False))


if __name__ == '__main__':
    main()
