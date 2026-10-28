"""Best-effort, attempt-scoped observation of IgBLAST work.

This store never determines pipeline success and contains no sequence data.
"""
from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import sqlite3
import subprocess
import threading
import re
from collections import deque
import time
from contextlib import closing
from functools import lru_cache
from pathlib import Path


def task_id(identity):
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def inherited_resource_fds():
    if os.name != 'posix':
        return ()
    fds = set()
    for name in ('SCIGBLAST_RESOURCE_LOCK_FD', 'SCIGBLAST_RESOURCE_SLOT_FD', 'SCIGBLAST_RESOURCE_BUDGET_FD'):
        try:
            fd = int(os.environ.get(name, ''))
            if fd >= 3:
                os.fstat(fd)
                fds.add(fd)
        except (ValueError, OSError):
            continue
    return tuple(sorted(fds))


@lru_cache(maxsize=16)
def verified_estimate(binary, species, chain):
    validated = {'pig': {'TRA', 'TRB', 'IGH', 'IGK', 'IGL'}, 'human': {'IGH', 'TRB'}}
    if chain not in validated.get(species, set()):
        return False
    try:
        result = subprocess.run([str(binary), '-version'], capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and bool(re.search(r'^igblastn:\s*1\.22\.0\b', result.stdout, re.M))
    except (OSError, subprocess.TimeoutExpired):
        return False


class Progress:
    def __init__(self, stage, jobs, workers, *, path=None, unit='chain'):
        self.path = str(path) if path is not None else os.environ.get('SCIGBLAST_PROGRESS_DB', '')
        self.stage = stage
        self.enabled = False
        if not self.path:
            return
        try:
            with closing(self.connect()) as db, db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, sample TEXT NOT NULL, pair TEXT NOT NULL, chain TEXT NOT NULL, phase TEXT NOT NULL, started REAL, updated REAL NOT NULL, input_count INTEGER, observed INTEGER, observation TEXT NOT NULL DEFAULT "waiting", verified INTEGER NOT NULL DEFAULT 0, eta_low INTEGER, eta_high INTEGER, error TEXT NOT NULL DEFAULT "")')
                db.execute('CREATE INDEX IF NOT EXISTS task_phase ON tasks(phase, ordinal)')
                db.execute('DELETE FROM tasks')
                db.execute('DELETE FROM meta')
                db.executemany('INSERT INTO meta VALUES(?,?)', [('stage', stage), ('started', str(time.time())), ('workers', str(workers)), ('version', '1'), ('unit', unit)])
                now = time.time()
                db.executemany('INSERT INTO tasks(id,ordinal,sample,pair,chain,phase,updated) VALUES(?,?,?,?,?,?,?)',
                               ((task_id(identity), i, str(sample), str(pair), str(chain), 'queued', now)
                                for i, (identity, sample, pair, chain) in enumerate(jobs)))
            self.enabled = True
        except (OSError, sqlite3.Error, ValueError):
            self.enabled = False

    def connect(self):
        return sqlite3.connect(self.path, timeout=1)

    def update(self, identity, phase, *, input_count=None, error=''):
        if not self.enabled:
            return
        try:
            timestamp = time.time()
            with closing(self.connect()) as db, db:
                db.execute('UPDATE tasks SET phase=?, updated=?, started=CASE WHEN started IS NULL AND ? != "queued" THEN ? ELSE started END, input_count=COALESCE(?, input_count), error=? WHERE id=?',
                           (phase, timestamp, phase, timestamp, input_count, str(error)[:500], task_id(identity)))
        except (OSError, sqlite3.Error, ValueError):
            # Telemetry must never change the outcome of analysis.
            pass

    def observe(self, identity, count, state, eta_low=None, eta_high=None):
        if not self.enabled:
            return
        try:
            with closing(self.connect()) as db, db:
                db.execute("UPDATE tasks SET observed=?, observation=?, eta_low=?, eta_high=?, updated=? WHERE id=? AND phase='aligning'",
                           (count, state, eta_low, eta_high, time.time(), task_id(identity)))
        except (OSError, sqlite3.Error, ValueError):
            pass

    def set_verified(self, identity, verified):
        if not self.enabled:
            return
        try:
            with closing(self.connect()) as db, db:
                db.execute("UPDATE tasks SET verified=? WHERE id=? AND phase='aligning'", (int(bool(verified)), task_id(identity)))
        except (OSError, sqlite3.Error, ValueError):
            pass

    def batch_event(self, identity, ordinal, total, sample, batch, chain, phase):
        if not self.enabled:
            return
        try:
            timestamp = time.time()
            with closing(self.connect()) as db, db:
                db.execute("INSERT INTO meta(key,value) VALUES('declared_total',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(total),))
                db.execute("""INSERT INTO tasks(id,ordinal,sample,pair,chain,phase,started,updated)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    phase=CASE WHEN tasks.phase IN ('aligned','failed') THEN tasks.phase ELSE excluded.phase END,
                    updated=excluded.updated""", (task_id(identity), ordinal, sample, batch, chain, phase, timestamp, timestamp))
        except (OSError, sqlite3.Error, ValueError):
            pass


class BatchLogProgress:
    """Incrementally observe the existing IR/10X batch log for one attempt."""
    pattern = re.compile(r'\[task (\d+)/(\d+)\] \[(?:BCR|TCR)\s+(IGH|IGK|IGL|TRA|TRB|TRD|TRG)\] (.*?)/batch_(\d+)')

    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.pending = b''
        self.identity = None
        self.discard_line = False
        self.progress = None
        self.workers = 4

    def poll(self, log_path, boundary=0):
        try:
            with Path(log_path).open('rb') as handle:
                stat = os.fstat(handle.fileno())
                identity = (stat.st_dev, stat.st_ino)
                if self.identity != identity or self.offset < boundary or stat.st_size < self.offset:
                    self.identity, self.offset, self.pending = identity, boundary, b''
                    self.discard_line = False
                handle.seek(self.offset)
                chunk = handle.read(256 * 1024)
                self.offset += len(chunk)
            if not chunk:
                return
            if self.discard_line:
                _, sep, chunk = chunk.partition(b'\n')
                if not sep:
                    return
                self.discard_line = False
            lines = (self.pending + chunk).split(b'\n')
            self.pending = lines.pop()
            if len(self.pending) > 1024 * 1024:
                self.pending = b''
                self.discard_line = True
            for raw in lines:
                line = raw.decode('utf-8', errors='replace')
                worker_match = re.search(r'(?:max_parallel=|max parallel:\s*)(\d+)', line)
                if worker_match:
                    self.workers = max(1, min(4, int(worker_match.group(1))))
                match = self.pattern.search(line)
                if not match:
                    continue
                if 'worker_start' in line or line.rstrip().endswith(' started'):
                    phase = 'aligning'
                elif 'worker_done' in line:
                    phase = 'failed' if 'status=FAILED' in line else 'aligned' if 'status=OK' in line else None
                elif ' failed with exit code ' in line:
                    phase = 'failed'
                elif ' finished in ' in line:
                    phase = 'aligned'
                else:
                    continue
                if phase is None:
                    continue
                ordinal, total, chain, sample, batch = match.groups()
                if self.progress is None:
                    self.progress = Progress('07.igblast', [], self.workers, path=self.path, unit='batch')
                self.progress.batch_event([sample, chain, batch], int(ordinal)-1, int(total), sample, 'batch_'+batch, chain, phase)
        except (OSError, ValueError):
            pass


def run_igblast(args, log, raw, progress, identity, input_count=None, estimate=False):
    """Run the unchanged command; observe only newly written complete AIRR rows."""
    inherited = inherited_resource_fds()
    if not progress.enabled:
        return subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, pass_fds=inherited).returncode
    progress.set_verified(identity, estimate)
    # A previous interrupted attempt may have left an old raw output behind.
    try:
        raw.unlink(missing_ok=True)
    except OSError:
        return subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, pass_fds=inherited).returncode
    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, pass_fds=inherited)
    stop = threading.Event()

    def observe():
        offset = count = 0
        pending = b''
        file_identity = None
        columns = None
        previous = None
        samples = deque(maxlen=40)
        try:
            while not stop.is_set():
                if raw.is_file():
                    with raw.open('rb') as handle:
                        stat = os.fstat(handle.fileno())
                        identity_now = (stat.st_dev, stat.st_ino)
                        if file_identity is not None and (identity_now != file_identity or stat.st_size < offset):
                            raise ValueError('output changed')
                        file_identity = identity_now
                        handle.seek(offset)
                        chunk = handle.read(1024 * 1024)
                        offset += len(chunk)
                        pending += chunk
                        if len(pending) > 2 * 1024 * 1024:
                            raise ValueError('record too large')
                        lines = pending.split(b'\n')
                        pending = lines.pop()
                        for line in lines:
                            fields = next(csv.reader(io.StringIO(line.decode('utf-8').rstrip('\r')), delimiter='\t', strict=True))
                            if columns is None:
                                if 'sequence_id' not in fields:
                                    raise ValueError('unexpected header')
                                columns = len(fields)
                            elif len(fields) != columns:
                                raise ValueError('incomplete row')
                            else:
                                count += 1
                        state = 'catching_up' if stat.st_size > offset else 'observed'
                        if input_count is not None and count > input_count:
                            raise ValueError('observed rows exceed input')
                        eta_low = eta_high = None
                        if estimate and input_count and columns is not None and state == 'observed':
                            samples.append((time.monotonic(), count))
                            if len(samples) >= 4 and samples[-1][0] - samples[0][0] >= 20:
                                first, middle, last = samples[0], samples[len(samples)//2], samples[-1]
                                rates = [(middle[1]-first[1])/(middle[0]-first[0]), (last[1]-middle[1])/(last[0]-middle[0])]
                                if min(rates)>0 and max(rates)/min(rates)<=4 and last[1]-first[1]>=50 and count < input_count:
                                    eta_low = int((input_count-count)/max(rates))
                                    eta_high = int((input_count-count)/min(rates))
                        else:
                            samples.clear()
                        value = (count if columns is not None else None, state, eta_low, eta_high)
                        if value != previous:
                            progress.observe(identity, *value)
                            previous = value
                stop.wait(2)
        except (OSError, ValueError, UnicodeError, csv.Error, StopIteration):
            progress.observe(identity, None, 'unavailable')

    observer = threading.Thread(target=observe, name='igblast-observer', daemon=True)
    try:
        observer.start()
    except RuntimeError:
        return process.wait()
    try:
        return process.wait()
    finally:
        stop.set()
        observer.join(timeout=0.1)


def read_progress(path):
    """Return only bounded display data from the current attempt's store."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            meta = dict(db.execute('SELECT key,value FROM meta'))
            if meta.get('version') != '1':
                return None
            counts = dict(db.execute('SELECT phase,COUNT(*) FROM tasks GROUP BY phase'))
            updated = db.execute('SELECT MAX(updated) FROM tasks').fetchone()[0]
            active = [dict(row) for row in db.execute(
                "SELECT sample,pair,chain,phase,started,updated,input_count,observed,observation,verified,eta_low,eta_high FROM tasks WHERE phase IN ('preparing','aligning','filtering','publishing') ORDER BY ordinal LIMIT 4")]
            failed = [dict(row) for row in db.execute(
                "SELECT sample,pair,chain,error FROM tasks WHERE phase='failed' ORDER BY ordinal LIMIT 3")]
            total = max(sum(counts.values()), int(meta.get('declared_total', '0')))
            if meta.get('unit') == 'batch':
                counts['queued'] = max(0, total - sum(counts.values()))
            return {'stage': meta['stage'], 'started': float(meta['started']), 'workers': int(meta['workers']), 'unit': meta.get('unit', 'chain'),
                    'counts': counts, 'total': total, 'active': active, 'failed': failed,
                    'updated': updated, 'eta_seconds': None}
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError):
        return None


def read_page(path, offset=0, query='', state='all'):
    path = Path(path)
    if not path.is_file():
        return None
    phases = {'queued': ('queued',), 'active': ('preparing', 'aligning', 'filtering', 'publishing'),
              'done': ('succeeded', 'cached', 'aligned'), 'failed': ('failed',)}
    clauses, args = [], []
    if state in phases:
        clauses.append('phase IN (' + ','.join('?' for _ in phases[state]) + ')')
        args.extend(phases[state])
    if query:
        clauses.append('instr(lower(sample || " " || pair || " " || chain), lower(?)) > 0')
        args.append(query[:160])
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            total = db.execute('SELECT COUNT(*) FROM tasks' + where, args).fetchone()[0]
            rows = [dict(row) for row in db.execute('SELECT sample,pair,chain,phase,input_count,error FROM tasks' + where + ' ORDER BY ordinal LIMIT 20 OFFSET ?', (*args, max(0, offset)))]
            return {'rows': rows, 'total': total, 'offset': max(0, offset), 'limit': 20}
    except (OSError, sqlite3.Error, ValueError):
        return None
