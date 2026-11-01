"""Archive exclusive successful job outputs after 14 days; run daily via cron."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile

ARCHIVE = 'results.tar.gz'
MANIFEST = '.results-archive.json'
PENDING = '.results-archive.pending.json'


class NoSavings(Exception):
    pass


def archive_roots(app):
    # Archive permissions are separate from the roots allowed for NEW jobs.
    # Historical jobs predate the narrower results/SCigblastWeb default.
    values = [str(app.DEFAULT_OUTPUT_ROOT)]
    values.extend(os.environ.get('SCIGBLAST_ALLOWED_OUTPUT_ROOTS', '').split(os.pathsep))
    values.extend(os.environ.get(
        'SCIGBLAST_ARCHIVE_LEGACY_ROOTS',
        os.pathsep.join(('/colddata/SCigblast/results', '/colddata/zqy/SCigblast/results')),
    ).split(os.pathsep))
    return list(dict.fromkeys(Path(value).resolve() for value in values if value.strip()))


def canonical_output(path, roots):
    resolved = Path(path).resolve()
    # Bind-mount aliases have different paths but refer to the same directory.
    # Compare every job/input using the same spelling to detect shared outputs.
    for index, alias in enumerate(roots):
        if resolved.is_relative_to(alias):
            for canonical in roots[:index]:
                try:
                    if alias.samefile(canonical):
                        return canonical / resolved.relative_to(alias)
                except OSError:
                    continue
    return resolved


def unpublished(root):
    return not any(os.path.lexists(root / name) for name in (ARCHIVE, MANIFEST, PENDING))


def release_failed_archive(app, row, reason):
    # Once publication/cleanup starts, preserve the recovery journal and state.
    # A later archive run must verify the archive before finishing cleanup.
    if not unpublished(Path(row['output_root'])):
        return False
    with app.db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        updated = connection.execute(
            "UPDATE jobs SET status='SUCCEEDED' WHERE id=? AND status='ARCHIVING'",
            (row['id'],),
        )
        if updated.rowcount:
            connection.execute(
                'INSERT INTO actions(job_id,operator,action,details,created_at) VALUES(?,?,?,?,?)',
                (row['id'], 'system', 'archive-failed', str(reason)[:2000], app.now()),
            )
    return bool(updated.rowcount)


def recover_stale(app):
    # The CLI holds the same exclusive flock as the archive worker. Do not
    # launch compression here: this command only releases pre-publication failures.
    with app.db() as connection:
        rows = connection.execute("SELECT * FROM jobs WHERE status='ARCHIVING'").fetchall()
    if not rows:
        print('No stale archiving jobs', flush=True)
        return 0
    destination = app.STATE_ROOT / 'maintenance' / 'archive-recovery-backups'
    destination.mkdir(parents=True, exist_ok=True)
    backup = destination / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    with app.db() as source, sqlite3.connect(backup) as target:
        source.backup(target)
    print(f'Archive recovery database backup: {backup}', flush=True)
    unresolved = 0
    for row in rows:
        if any(row[key] is not None for key in ('pid', 'pgid') if key in row.keys()):
            print(f"Archive recovery deferred: process identity {row['id']}", flush=True)
            unresolved += 1
            continue
        if release_failed_archive(app, row, 'Recovered stale archive status under exclusive archive lock; no published archive or journal'):
            print(f"Archive status recovered: {row['id']} -> SUCCEEDED", flush=True)
        else:
            print(f"Archive recovery deferred: published archive/journal {row['id']}", flush=True)
            unresolved += 1
    return unresolved


def inventory(root):
    entries = []
    for path in sorted(root.rglob('*')):
        if path.parent == root and path.name in {'.results.tar.gz.tmp', '.results-archive.pending.tmp'}:
            if path.is_symlink() or not path.is_file():
                raise ValueError('Invalid archive staging file')
            continue
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


def validate_entries(root, entries):
    names = set()
    for entry in entries:
        name = entry['name']
        rel = Path(name)
        if not name or rel.is_absolute() or '..' in rel.parts or name in names:
            raise ValueError('Invalid archive manifest entry')
        names.add(name)
        path = root / rel
        if not path.resolve().is_relative_to(root) or any(part.is_symlink() for part in (path, *path.parents) if part != root and part.is_relative_to(root)):
            raise ValueError('Archive manifest path contains a link')


def package(root, job_id):
    archive, marker, pending = root / ARCHIVE, root / MANIFEST, root / PENDING
    temporary = root / '.results.tar.gz.tmp'
    pending_temp = root / '.results-archive.pending.tmp'
    if any(path.is_symlink() for path in (archive, marker, pending, temporary, pending_temp)):
        raise ValueError('Archive path contains a link')
    if marker.exists():
        manifest = json.loads(marker.read_text())
        if manifest['job_id'] != job_id:
            raise ValueError('Archive belongs to another job')
        entries = manifest['entries']
        validate_entries(root, entries)
        verify(archive, entries)
    elif pending.exists():
        manifest = json.loads(pending.read_text(encoding='utf-8'))
        if manifest['job_id'] != job_id:
            raise ValueError('Pending archive belongs to another job')
        entries = manifest['entries']
        validate_entries(root, entries)
        candidate = archive if archive.exists() else temporary
        verify(candidate, entries, root)
        for entry in entries:
            stat = (root / entry['name']).stat()
            if (stat.st_size, stat.st_mtime_ns) != (entry['size'], entry['mtime']):
                raise ValueError('Source changed during archive recovery')
        if not archive.exists():
            os.replace(temporary, archive)
        os.replace(pending, marker)
    else:
        if archive.exists():
            raise ValueError('Unmanaged archive already exists')
        entries = inventory(root)
        try:
            if not temporary.exists():
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
            # Publish a durable journal before renaming the verified archive.
            # On restart the pending record proves ownership and supports recovery.
            with pending_temp.open('w', encoding='utf-8') as handle:
                json.dump({'job_id': job_id, 'entries': entries}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending_temp, pending)
            os.replace(temporary, archive)
            os.replace(pending, marker)
        except Exception:
            if not pending.exists():
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
    roots = archive_roots(app)
    for row in rows:
        if not eligible(row, cutoff):
            continue
        try:
            raw = Path(row['output_root'])
            resolved = raw.resolve()
            if resolved in roots or not any(resolved.is_relative_to(base) for base in roots) or not resolved.is_dir():
                raise ValueError('Output is not an exclusive results subdirectory')
            if any(p.is_symlink() for p in (raw, *raw.parents)):
                raise ValueError('Output path contains a link')
            root = canonical_output(raw, roots)
            for other in rows:
                output = canonical_output(other['output_root'], roots)
                if other['id'] != row['id'] and (root.is_relative_to(output) or output.is_relative_to(root)):
                    raise ValueError('Shared output directory')
                options = json.loads(other['options_json'])
                for value in (other['input_path'], other['submission_path'], other['barcode_csv'], options.get('submission_path')):
                    if value:
                        protected = canonical_output(value, roots)
                        if root.is_relative_to(protected) or protected.is_relative_to(root):
                            raise ValueError('Output overlaps an input directory')
            if canonical_output(app.STATE_ROOT, roots).is_relative_to(root) or canonical_output(app.PIPELINE_ROOT, roots).is_relative_to(root):
                raise ValueError('Output overlaps application files')
            print(f"archive candidate: {row['id']} {root}", flush=True)
            if dry_run:
                continue
            with app.db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                # Both services claim through SQLite: an archive process does
                # not share the Web process's in-memory scheduling lock.
                has_derived = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='derived_runs'").fetchone()
                has_reports = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_runs'").fetchone()
                if has_reports and connection.execute("SELECT 1 FROM report_runs WHERE parent_job_id=? AND (status IN ('queued','running','cancelling') OR pid IS NOT NULL) LIMIT 1", (row['id'],)).fetchone():
                    print(f"archive deferred: active report {row['id']}", flush=True)
                    continue
                if has_derived and connection.execute("SELECT 1 FROM derived_runs WHERE parent_job_id=? AND (status IN ('queued','running','cancelling') OR pid IS NOT NULL) LIMIT 1", (row['id'],)).fetchone():
                    print(f"archive deferred: active Treemap {row['id']}", flush=True)
                    continue
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
            if not dry_run:
                release_failed_archive(app, row, exc)
            print(f"archive failed: {row['id']} {exc}", file=sys.stderr, flush=True)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--recover-stale', action='store_true', help='Back up SQLite and release stale pre-publication archive states without compressing results')
    args = parser.parse_args()
    if args.dry_run and args.recover_stale:
        parser.error('--dry-run and --recover-stale cannot be combined')
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
            print('Archive worker is active; no state changed.', flush=True)
            sys.exit(0)
        sys.exit(bool(recover_stale(app) if args.recover_stale else run(app, dry_run=args.dry_run)))
