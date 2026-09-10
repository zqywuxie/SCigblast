"""Archive exclusive successful job outputs after 14 days; run daily via cron."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tarfile

ARCHIVE = 'results.tar.gz'
MANIFEST = '.results-archive.json'


class NoSavings(Exception):
    pass


def inventory(root):
    entries = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError('Output contains links or special files')
        if path.is_file():
            stat = path.stat()
            entries.append({'name': path.relative_to(root).as_posix(), 'size': stat.st_size,
                            'mtime': stat.st_mtime_ns,
                            'keep': stat.st_size <= 10 * 1024 * 1024 and (
                                path.suffix.lower() in {'.csv', '.tsv', '.json', '.log', '.xlsx', '.txt', '.done'}
                                or '.pipeline_state' in path.parts)})
    return entries


def verify(archive, entries, source=None):
    expected = {entry['name']: entry for entry in entries}
    seen = set()
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError('Unexpected archive member')
            if member.size != expected[member.name]['size']:
                raise ValueError('Archive size mismatch')
            seen.add(member.name)
            original = (source / member.name).open('rb') if source else None
            try:
                with tar.extractfile(member) as handle:
                    while chunk := handle.read(1024 * 1024):
                        if original and chunk != original.read(len(chunk)):
                            raise ValueError('Archive content mismatch')
                    if original and original.read(1):
                        raise ValueError('Source changed during compression')
            finally:
                if original:
                    original.close()
    if seen != set(expected):
        raise ValueError('Incomplete archive')


def package(root, job_id):
    archive, marker = root / ARCHIVE, root / MANIFEST
    if archive.is_symlink() or marker.is_symlink():
        raise ValueError('Archive path contains a link')
    if marker.exists():
        manifest = json.loads(marker.read_text())
        if manifest['job_id'] != job_id:
            raise ValueError('Archive belongs to another job')
        entries = manifest['entries']
        verify(archive, entries)
    else:
        if archive.exists():
            raise ValueError('Unmanaged archive already exists')
        entries = inventory(root)
        temporary = root / '.results.tar.gz.tmp'
        if temporary.exists():
            raise ValueError('Incomplete archive exists; inspect before retrying')
        try:
            with tarfile.open(temporary, 'w:gz', compresslevel=6) as tar:
                for entry in entries:
                    tar.add(root / entry['name'], arcname=entry['name'], recursive=False)
            verify(temporary, entries, root)
            for entry in entries:
                stat = (root / entry['name']).stat()
                if (stat.st_size, stat.st_mtime_ns) != (entry['size'], entry['mtime']):
                    raise ValueError('Source changed during compression')
            if temporary.stat().st_size >= sum(e['size'] for e in entries if not e['keep']):
                raise NoSavings('Compression would not reduce storage; original files retained')
            os.replace(temporary, archive)
            marker.write_text(json.dumps({'job_id': job_id, 'entries': entries}), encoding='utf-8')
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    # Never remove a file that has changed since the verified archive was made.
    for entry in entries:
        path = root / entry['name']
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('Invalid archive cleanup path')
        if not entry['keep'] and path.exists():
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (entry['size'], entry['mtime']):
                raise ValueError('Output changed after archiving; cleanup stopped')
            path.unlink()
    return archive.stat().st_size


def eligible(row, cutoff):
    if row['status'] == 'ARCHIVING':
        return True
    return (row['status'] == 'SUCCEEDED' and row['ended_at']
            and datetime.fromisoformat(row['ended_at']) <= cutoff)


def run(app, dry_run=False, days=14):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with app.db() as connection:
        rows = connection.execute('SELECT * FROM jobs').fetchall()
    failures = 0
    for row in rows:
        if not eligible(row, cutoff):
            continue
        try:
            raw = Path(row['output_root'])
            root = raw.resolve()
            base = app.DEFAULT_OUTPUT_ROOT.resolve()
            if root == base or not root.is_relative_to(base) or not root.is_dir():
                raise ValueError('Output is not an exclusive results subdirectory')
            if any(p.is_symlink() for p in (raw, *raw.parents)):
                raise ValueError('Output path contains a link')
            for other in rows:
                output = Path(other['output_root']).resolve()
                if other['id'] != row['id'] and (root.is_relative_to(output) or output.is_relative_to(root)):
                    raise ValueError('Shared output directory')
                options = json.loads(other['options_json'])
                for value in (other['input_path'], other['submission_path'], other['barcode_csv'], options.get('submission_path')):
                    if value:
                        protected = Path(value).resolve()
                        if root.is_relative_to(protected) or protected.is_relative_to(root):
                            raise ValueError('Output overlaps an input directory')
            if app.STATE_ROOT.resolve().is_relative_to(root) or app.PIPELINE_ROOT.resolve().is_relative_to(root):
                raise ValueError('Output overlaps application files')
            print(f"archive candidate: {row['id']} {root}", flush=True)
            if dry_run:
                continue
            with app.db() as connection:
                updated = connection.execute("UPDATE jobs SET status='ARCHIVING' WHERE id=? AND status IN ('SUCCEEDED','ARCHIVING')", (row['id'],))
                if not updated.rowcount:
                    continue
            size = package(root, row['id'])
            with app.db() as connection:
                connection.execute("UPDATE jobs SET status='ARCHIVED' WHERE id=? AND status='ARCHIVING'", (row['id'],))
                connection.execute('INSERT INTO actions(job_id,operator,action,details,created_at) VALUES(?,?,?,?,?)',
                                   (row['id'], 'system', 'archive', f'{days} days; archive_bytes={size}', app.now()))
            print(f"archived: {row['id']} bytes={size}", flush=True)
        except NoSavings as exc:
            with app.db() as connection:
                connection.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=? AND status='ARCHIVING'", (row['id'],))
            print(f"archive skipped: {row['id']} {exc}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"archive failed: {row['id']} {exc}", file=sys.stderr, flush=True)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    # The maintenance copy in the persistent runtime uses the installed app.
    sys.path.insert(0, '/app')
    import app
    import fcntl
    import inspect
    if not args.dry_run and 'ARCHIVING' not in inspect.getsource(app.rematch):
        print('Archive deferred: deploy the archive-aware Web version first.', flush=True)
        sys.exit(0)
    with (app.STATE_ROOT / 'results-archive.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(0)
        sys.exit(bool(run(app, dry_run=args.dry_run)))
