"""Queued derived Treemap outputs; never changes pipeline results or completion."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import auth
import derived_store
from artifact_download import stream_zip
from process_identity import group_state, signal_group

LOG = logging.getLogger(__name__)
VERSION = 'qr-records-v1'
ACTIVE_SQL = "(status IN ('queued','running','cancelling') OR pid IS NOT NULL)"
CHAINS = ('TRA', 'TRB', 'TRD', 'TRG', 'IGH', 'IGK', 'IGL')


def clone_table_version():
    from analysis.treemap.clone_table import VERSION as table_version
    return table_version


def init_schema(connection):
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS treemap_scans (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires_at INTEGER NOT NULL,
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS derived_runs (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, parent_job_id TEXT,
            attempt_no INTEGER, name TEXT NOT NULL, source_type TEXT NOT NULL,
            dedupe_key TEXT NOT NULL, output_root TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', data_json TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
            error TEXT, created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT,
            pid INTEGER, process_token TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
            UNIQUE(owner_id,dedupe_key)
        );
        CREATE INDEX IF NOT EXISTS idx_derived_due ON derived_runs(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_derived_parent ON derived_runs(parent_job_id,attempt_no);
    ''')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(derived_runs)')}
    for name in ('done', 'total'):
        if name not in columns:
            connection.execute(f'ALTER TABLE derived_runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
    derived_store.init_schema(connection)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def image_relative(sample_key, sample_label, kind, chain=''):
    """Stable, collision-safe layout, also used to package historical manifests."""
    name = re.sub(r'[^\w.-]', '_', sample_label)[:60] or 'sample'
    directory = 'individual_treemaps' if kind == 'chain' else 'multi_chain_treemaps'
    suffix = chain if kind == 'chain' else 'ALL_CHAINS'
    return Path(directory) / (name + '_' + digest(sample_key)[:12]) / f'{name}_{suffix}.png'


def safe_path(raw, root, *, file=False):
    path, base = Path(raw).absolute(), Path(root).resolve()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('路径包含符号链接，无法用于 Treemap')
    resolved = path.resolve()
    if not resolved.is_relative_to(base) or (file and not resolved.is_file()):
        raise ValueError('Treemap 文件不存在或超出来源范围')
    return resolved


def version(path):
    info = Path(path).stat()
    return [info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def worker_budget():
    from mapping.pipeline.resources import detect_resources
    resources = detect_resources()
    requested = max(1, min(32, int(os.environ.get('SCIGBLAST_TREEMAP_MAX_WORKERS', '2'))))
    per_worker = max(128, int(os.environ.get('SCIGBLAST_TREEMAP_MAX_RSS_MB', '2048')))
    configured = max(128, int(os.environ.get('SCIGBLAST_TREEMAP_MEMORY_BUDGET_MB', '4096'))) * 1024**2
    available = resources['memory_available_bytes']
    supervisor = 64 * 1024**2
    usable = configured
    if available is not None:
        usable = min(usable, available - max(256 * 1024**2, math.ceil(available * 0.05)) - supervisor)
    if usable < 128 * 1024**2:
        raise ValueError('可用内存不足，暂时无法生成 Treemap')
    per_worker = min(per_worker, usable // 1024**2)
    workers = min(requested, max(1, math.floor(resources['cpu_count'])), usable // (per_worker * 1024**2))
    return dict(workers=workers, worker_rss_mb=per_worker,
                group_rss_bytes=workers * per_worker * 1024**2 + supervisor)


def group_rss(pgid):
    from analysis.treemap.run import process_rss
    total = 0
    try:
        entries = list(Path('/proc').iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid:
                total += process_rss(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    return total


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def enqueue_auto(connection, job_id, now):
    """Only persist intent inside the existing status transaction; no scanning."""
    row = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row or row['status'] != 'SUCCEEDED' or not json.loads(row['options_json']).get('generate_treemap'):
        return
    run_id = uuid.uuid4().hex
    folder = Path(row['output_root']) / 'analysis' / 'treemap' / f"attempt-{row['attempt_no']}" / f'run-{run_id}'
    data = dict(pending_discovery=True, items=[], files=[], input_root=row['output_root'], metric='records',
                clone_table_version=clone_table_version())
    inserted = connection.execute('INSERT OR IGNORE INTO derived_runs '
                       '(id,owner_id,parent_job_id,attempt_no,name,source_type,dedupe_key,output_root,data_json,created_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (run_id, row['owner_id'], job_id, row['attempt_no'], row['dataset'], 'job',
                        f"auto:{job_id}:{row['attempt_no']}:{VERSION}", str(folder), json.dumps(data), now))
    if inserted.rowcount:
        derived_store.replace(connection, run_id, data)


class ScanRequest(BaseModel):
    job_id: str = ''
    input_path: str = Field(default='', max_length=4096)


class RunRequest(BaseModel):
    scan_id: str
    selected_ids: list[str] = Field(min_length=1, max_length=10000)
    name: str = Field(default='', max_length=160)


class Service:
    def __init__(self, app):
        self.app = app
        self.stopping = threading.Event()
        self.thread = None
        self.process = None
        self.process_run = None

    def owned_job(self, job_id, user=None):
        user = user or auth.require_user()
        row = self.app.get_job_row(job_id)
        if row['owner_id'] != user['id'] and user['role'] != 'admin':
            raise HTTPException(404, '任务不存在或无权访问')
        return row

    def owned_run(self, run_id):
        user = auth.require_user()
        with self.app.db() as connection:
            row = connection.execute('SELECT * FROM derived_runs WHERE id=?', (run_id,)).fetchone()
        if not row or (row['owner_id'] != user['id'] and user['role'] != 'admin'):
            raise HTTPException(404, 'Treemap 记录不存在或无权访问')
        if row['parent_job_id']:
            self.owned_job(row['parent_job_id'], user)
        return row

    def data(self, row, *, work=False):
        with self.app.db() as connection:
            connection.execute('BEGIN')
            current = connection.execute('SELECT * FROM derived_runs WHERE id=?', (row['id'],)).fetchone()
            if current is None:
                raise HTTPException(404, 'Treemap 记录不存在或无权访问')
            return derived_store.load(connection, current, work=work)

    @staticmethod
    def successful(row):
        if row['status'] != 'SUCCEEDED':
            raise HTTPException(409, '请选择分析已完成且未归档的任务')

    def source(self, body):
        user = auth.require_user()
        if body.job_id:
            if body.input_path:
                raise HTTPException(400, '请选择一种数据来源')
            row = self.owned_job(body.job_id)
            self.successful(row)
            return row, Path(row['output_root']).resolve()
        path = Path(body.input_path).expanduser()
        if not body.input_path or not path.is_absolute():
            raise HTTPException(400, '请填写服务器上的绝对目录')
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise HTTPException(400, '来源路径不能包含符号链接')
        path = path.resolve()
        if not path.is_dir():
            raise HTTPException(400, '来源目录不存在')
        with self.app.db() as connection:
            jobs = connection.execute('SELECT * FROM jobs').fetchall()
            runs = connection.execute('SELECT output_root FROM derived_runs').fetchall()
        # Resolve platform outputs before applying ordinary shared-input roots.
        matches = []
        for row in jobs:
            root = Path(row['output_root']).resolve()
            if path.is_relative_to(root) or root.is_relative_to(path):
                if row['owner_id'] != user['id'] and user['role'] != 'admin':
                    raise HTTPException(404, '该目录包含无权访问的任务结果')
                if not path.is_relative_to(root):
                    raise HTTPException(400, '请直接选择一个分析任务或它的结果目录')
                matches.append(row)
        if len(matches) > 1:
            raise HTTPException(400, '目录关联多个任务，请使用已有任务入口')
        if matches:
            self.successful(matches[0])
            return matches[0], Path(matches[0]['output_root']).resolve()
        if any(path.is_relative_to(Path(row['output_root']).resolve()) or Path(row['output_root']).resolve().is_relative_to(path) for row in runs):
            raise HTTPException(400, '请选择分析数据目录，不要选择 Treemap 输出目录')
        roots = self.app.configured_roots('SCIGBLAST_ALLOWED_INPUT_ROOTS', '/colddata/SCigblast/data')
        if not self.app.is_under(path, roots):
            raise HTTPException(400, '来源目录超出允许的输入范围')
        return None, path

    def discover(self, row, root):
        from analysis.treemap.sources import discover_directory, discover_job
        return discover_job(dict(row)) if row is not None else discover_directory(root)

    def scan(self, body):
        row, root = self.source(body)
        try:
            items = self.discover(row, root)
            if len(items) > 10000:
                raise ValueError('样本链条目超过 10000，请缩小来源目录')
            for item in items:
                item['sources'] = [{**source, 'path': str(safe_path(source['path'], root, file=True)),
                                    'version': version(source['path'])} for source in item.get('sources', [])]
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from None
        scan_id = uuid.uuid4().hex
        data = dict(parent_job_id=row['id'] if row is not None else None,
                    attempt_no=row['attempt_no'] if row is not None else None,
                    input_root=str(root), items=items)
        user = auth.require_user()
        with self.app.db() as connection:
            connection.execute('DELETE FROM treemap_scans WHERE expires_at<?', (int(time.time()),))
            previous = connection.execute('SELECT id FROM treemap_scans WHERE owner_id=? ORDER BY expires_at DESC', (user['id'],)).fetchall()
            for old in previous[15:]:
                connection.execute('DELETE FROM treemap_scans WHERE id=?', (old['id'],))
            connection.execute('INSERT INTO treemap_scans VALUES(?,?,?,?)',
                               (scan_id, user['id'], int(time.time()) + 86400, json.dumps(data, ensure_ascii=False)))
        return self.scan_page(scan_id, limit=10000)

    def scan_data(self, scan_id):
        with self.app.db() as connection:
            row = connection.execute('SELECT * FROM treemap_scans WHERE id=? AND owner_id=?',
                                     (scan_id, auth.require_user()['id'])).fetchone()
        if not row or row['expires_at'] < time.time():
            raise HTTPException(400, '扫描已过期或不可访问，请重新扫描')
        return json.loads(row['data_json'])

    def scan_page(self, scan_id, query='', offset=0, limit=100):
        data = self.scan_data(scan_id)
        items = data['items']
        needle = query.casefold()
        filtered = [item for item in items if not needle or needle in ' '.join(str(item.get(k, '')) for k in ('sample_key', 'sample_label', 'chain', 'error')).casefold()]
        rows = [{**{key: value for key, value in item.items() if key != 'sources'},
                 'source_version': digest(item.get('sources', []))}
                for item in filtered[max(0,offset):max(0,offset)+max(1,min(limit,10000))]]
        return dict(id=scan_id, rows=rows, total=len(filtered), valid_count=sum(bool(i.get('valid')) for i in items),
                    parent_job_id=data['parent_job_id'], chains=sorted({i['chain'] for i in items if i.get('valid')}))

    def create(self, body):
        data = self.scan_data(body.scan_id)
        selected = set(body.selected_ids)
        items = [item for item in data['items'] if item['id'] in selected]
        if len(items) != len(selected) or any(not item.get('valid') for item in items):
            raise HTTPException(400, '所选条目已不可用，请重新扫描')
        try:
            self.verify_inputs(items, data['input_root'])
        except (ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from None
        user = auth.require_user()
        parent = self.owned_job(data['parent_job_id']) if data['parent_job_id'] else None
        if parent is not None:
            self.successful(parent)
            if parent['attempt_no'] != data['attempt_no']:
                raise HTTPException(409, '来源任务已重新执行，请重新扫描')
        run_id = uuid.uuid4().hex
        key = digest(dict(version=VERSION, clone_table_version=clone_table_version(),
                          parent=data['parent_job_id'], attempt=data['attempt_no'],
                          input_root=data['input_root'], items=items))
        if parent is not None:
            output = Path(parent['output_root']) / 'analysis' / 'treemap' / f"attempt-{parent['attempt_no']}" / f'run-{run_id}'
        else:
            output = self.app.default_job_output(user, 'treemap') / f'run-{run_id}'
        name = body.name.strip() or (parent['dataset'] if parent is not None else Path(data['input_root']).name)
        owner_id = parent['owner_id'] if parent is not None else user['id']
        payload = dict(items=[dict(item, status='queued') for item in items], files=[], input_root=data['input_root'], metric='records',
                       clone_table_version=clone_table_version())
        with self.app.PROCESS_LOCK, self.app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if parent is not None:
                current = connection.execute('SELECT status,attempt_no FROM jobs WHERE id=?', (parent['id'],)).fetchone()
                if not current or current['status'] != 'SUCCEEDED' or current['attempt_no'] != data['attempt_no']:
                    raise HTTPException(409, '来源任务已变化，请重新扫描')
            inserted = connection.execute('INSERT OR IGNORE INTO derived_runs '
                               '(id,owner_id,parent_job_id,attempt_no,name,source_type,dedupe_key,output_root,data_json,created_at,total) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (run_id, owner_id, data['parent_job_id'], data['attempt_no'], name,
                                'job' if parent is not None else 'directory', key, str(output), json.dumps(payload, ensure_ascii=False), self.app.now(), len(items)))
            if inserted.rowcount:
                derived_store.replace(connection, run_id, payload)
            existing = connection.execute('SELECT id FROM derived_runs WHERE owner_id=? AND dedupe_key=?', (owner_id, key)).fetchone()
        return self.public(self.owned_run(existing['id']))

    def verify_inputs(self, items, root):
        for item in items:
            for source in item.get('sources', []):
                path = safe_path(source['path'], root, file=True)
                if source.get('version') != version(path):
                    raise ValueError('来源文件已变化，请重新扫描数据')

    def public(self, row, *, details=True):
        data = self.data(row) if details else {}
        status = row['status']
        if row['parent_job_id']:
            with self.app.db() as connection:
                parent = connection.execute('SELECT status,attempt_no FROM jobs WHERE id=?', (row['parent_job_id'],)).fetchone()
            if not parent or parent['attempt_no'] != row['attempt_no'] or parent['status'] not in {'SUCCEEDED','ARCHIVING','ARCHIVED'}:
                status = 'stale'
            elif parent['status'] in {'ARCHIVING','ARCHIVED'}:
                status = 'archived'
        items = [{k: v for k, v in item.items() if k not in {'sources', 'path', 'clone_table'}} for item in data.get('items', [])]
        files = []
        if status not in {'stale','archived'}:
            for item in data.get('files', []):
                try:
                    path = safe_path(Path(row['output_root']) / item['relative_path'], row['output_root'], file=True)
                    if version(path) != item['version']:
                        continue
                except (ValueError, OSError):
                    continue
                files.append({k:v for k,v in item.items() if k not in {'relative_path','version'}} | {'url':f"/api/treemap/runs/{row['id']}/files/{item['id']}"})
        return dict(id=row['id'], name=row['name'], status=status, parent_job_id=row['parent_job_id'], attempt_no=row['attempt_no'],
                    created_at=row['created_at'], started_at=row['started_at'], ended_at=row['ended_at'],
                    error=row['error'], items=items, files=files, metric='records',
                    progress=dict(done=row['done'],total=row['total']))

    def list_runs(self, parent_job_id=None):
        user = auth.require_user()
        where, args = (['parent_job_id=?'], [parent_job_id]) if parent_job_id else ([], [])
        if user['role'] != 'admin':
            where.append('owner_id=?'); args.append(user['id'])
        with self.app.db() as connection:
            rows = connection.execute('SELECT id,owner_id,parent_job_id,attempt_no,name,status,created_at,started_at,ended_at,error,done,total FROM derived_runs' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY created_at DESC,id DESC LIMIT 100', args).fetchall()
        return [self.public(row,details=False) for row in rows]

    def retry(self, run_id):
        row = self.owned_run(run_id)
        if row['status'] not in {'failed','partial','empty','interrupted','cancelled'}:
            raise HTTPException(409, '当前状态不需要重试')
        if row['pid']:
            if group_state(row['pid'], row['process_token']) != 'gone':
                raise HTTPException(409, '上次绘图进程尚未确认退出，请先取消或联系管理员')
        data = self.data(row)
        if any(not item.get('valid', True) for item in data.get('items', [])):
            raise HTTPException(409, '存在来源检查失败的条目，请重新扫描数据后生成')
        try:
            self.verify_inputs(data.get('items', []), data['input_root'])
        except (ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from None
        with self.app.PROCESS_LOCK, self.app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if row['parent_job_id']:
                parent = connection.execute('SELECT status,attempt_no FROM jobs WHERE id=?', (row['parent_job_id'],)).fetchone()
                if not parent or parent['status'] != 'SUCCEEDED' or parent['attempt_no'] != row['attempt_no']:
                    raise HTTPException(409, '来源任务已变化，请重新生成')
            repeated = set()
            artifacts = {item['id']:item for item in data.get('files', [])}
            for item in data.get('items', []):
                if item.get('status') != 'succeeded':
                    repeated.add(item['id'])
                else:
                    try:
                        image = artifacts.get(item.get('artifact_id'))
                        if not image:
                            raise ValueError('单链图片缺失')
                        path = safe_path(Path(row['output_root'])/image['relative_path'],row['output_root'],file=True)
                        if version(path)!=image['version']:
                            raise ValueError('单链图片已变化')
                        if data.get('clone_table_version'):
                            self.clone_table_path(row,item)
                    except (ValueError,OSError):
                        repeated.add(item['id'])
            affected = {item['sample_key'] for item in data.get('items', []) if item['id'] in repeated}
            replaced_artifacts = {item.get('artifact_id') for item in data.get('items', []) if item['id'] in repeated}
            data['composed_samples'] = [sample for sample in data.get('composed_samples', []) if sample not in affected]
            data['files'] = [item for item in data.get('files', []) if item['kind'] != 'clone_top' and
                             item['id'] not in replaced_artifacts and not (item['kind'] == 'combined' and item['sample_key'] in affected)]
            data.pop('clone_top_complete',None)
            for item in data.get('items', []):
                if item['id'] in repeated:
                    item.update(status='queued', error='')
                    item.pop('clone_table',None)
                    item.pop('artifact_id',None)
            updated = connection.execute("UPDATE derived_runs SET status='queued',error=NULL,cancel_requested=0,pid=NULL,process_token=NULL,ended_at=NULL WHERE id=? AND status NOT IN ('queued','running','cancelling')",
                                         (run_id,))
            if updated.rowcount:
                derived_store.save(connection, row, data)
        return self.public(self.owned_run(run_id))

    def cancel(self, run_id):
        row = self.owned_run(run_id)
        with self.app.db() as connection:
            connection.execute("UPDATE derived_runs SET cancel_requested=1,status=CASE WHEN pid IS NULL THEN 'cancelled' ELSE 'cancelling' END WHERE id=? AND (status IN ('queued','running','cancelling','interrupted'))", (run_id,))
        if row['pid'] and self.process_run != run_id:
            state = signal_group(row['pid'], row['process_token'], signal.SIGTERM)
            if state == 'gone':
                with self.app.db() as connection:
                    connection.execute("UPDATE derived_runs SET status='cancelled',pid=NULL,process_token=NULL,ended_at=? WHERE id=?", (self.app.now(), run_id))
            elif state == 'unverified':
                raise HTTPException(409, '无法核验旧绘图进程身份，请联系管理员检查')
        return self.public(self.owned_run(run_id))

    def guard_mutation(self, job_id):
        """Called while PROCESS_LOCK prevents a new derived claim for this task."""
        with self.app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("UPDATE derived_runs SET status='cancelled',cancel_requested=1,ended_at=? WHERE parent_job_id=? AND status IN ('queued','running','cancelling','interrupted') AND pid IS NULL", (self.app.now(), job_id))
            # Interrupted children may have exited since the previous request.
            for old in connection.execute('SELECT id,pid,process_token FROM derived_runs WHERE parent_job_id=? AND pid IS NOT NULL', (job_id,)).fetchall():
                if group_state(old['pid'], old['process_token']) == 'gone':
                    connection.execute("UPDATE derived_runs SET status='cancelled',pid=NULL,process_token=NULL WHERE id=?", (old['id'],))
            active = connection.execute(f'SELECT id FROM derived_runs WHERE parent_job_id=? AND {ACTIVE_SQL}', (job_id,)).fetchall()
            for row in active:
                connection.execute("UPDATE derived_runs SET cancel_requested=1,status='cancelling' WHERE id=?", (row['id'],))
        if active:
            for row in active:
                with self.app.db() as connection:
                    old = connection.execute('SELECT pid,process_token FROM derived_runs WHERE id=?', (row['id'],)).fetchone()
                if old and old['pid'] and self.process_run != row['id']:
                    signal_group(old['pid'], old['process_token'], signal.SIGTERM)
            raise HTTPException(409, 'Treemap 正在停止，请稍后重试此操作')

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        with self.app.db() as connection:
            connection.execute("UPDATE derived_runs SET status='interrupted',error='绘图服务重启，请确认旧进程退出后重试' WHERE status IN ('running','cancelling')")
            for row in connection.execute("SELECT id FROM jobs WHERE status='SUCCEEDED'").fetchall():
                enqueue_auto(connection, row['id'], self.app.now())
        self.thread = threading.Thread(target=self.loop, name='scigblast-treemap', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=3)

    def loop(self):
        while not self.stopping.is_set():
            try:
                worked = self.deliver_one()
            except Exception as error:
                LOG.warning('Treemap queue unavailable: %s', type(error).__name__)
                worked = False
            if not worked:
                self.stopping.wait(2)

    def current_valid(self, connection, row):
        current = connection.execute('SELECT status,cancel_requested FROM derived_runs WHERE id=?', (row['id'],)).fetchone()
        if not current or current['cancel_requested'] or current['status'] != 'running':
            return False
        if row['parent_job_id']:
            parent = connection.execute('SELECT status,attempt_no FROM jobs WHERE id=?', (row['parent_job_id'],)).fetchone()
            return bool(parent and parent['status'] == 'SUCCEEDED' and parent['attempt_no'] == row['attempt_no'])
        return True

    def primary_pending(self, connection):
        return connection.execute("SELECT 1 FROM jobs WHERE status IN ('QUEUED','RUNNING','MATCHING','STOPPING') LIMIT 1").fetchone() is not None

    def save(self, row, data):
        with self.app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not self.current_valid(connection,row) or self.stopping.is_set():
                raise ValueError('Treemap 已停止或来源任务已更新')
            derived_store.save(connection, row, data)

    def work_unit(self, row, request):
        """One bounded wave, with one persisted supervisor identity and shared flock."""
        lock = None
        process = None
        req_path = result_path = None
        timeout = max(10, int(os.environ.get('SCIGBLAST_TREEMAP_TIMEOUT_SECONDS', '1800')))
        batch = request['kind'] == 'batch'
        if batch:
            timeout += 10  # Allow the supervisor to publish per-worker timeout errors.
        limit = request.get('group_rss_bytes') if batch else None
        limit = limit or worker_budget()['worker_rss_mb'] * 1024**2
        from mapping.pipeline.resources import detect_resources
        available = detect_resources()['memory_available_bytes']
        if available is not None:
            limit = min(limit, available - max(256 * 1024**2, int(available * 0.05)))
            if limit < 128 * 1024**2:
                raise ValueError('可用内存不足，暂时无法生成 Treemap')
        if os.name == 'posix':
            import fcntl
            lock_path = Path(os.environ.get('SCIGBLAST_RESOURCE_LOCK_FILE', '/colddata/SCigblast/results/.scigblast-resource.lock'))
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock = lock_path.open('a')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                return None
        try:
            with self.app.PROCESS_LOCK, self.app.db() as connection:
                if self.primary_pending(connection) or not self.current_valid(connection, row):
                    return None
                folder = safe_path(row['output_root'], self.app.DEFAULT_OUTPUT_ROOT)
                folder.mkdir(parents=True, exist_ok=True)
                temporary = folder / '.work'
                temporary.mkdir(exist_ok=True)
                if batch:
                    request = dict(request, work_dir=str(temporary))
                token = uuid.uuid4().hex
                req_path, result_path = temporary / f'{token}.request.json', temporary / f'{token}.result.json'
                atomic_json(req_path, request)
                process_env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(self.app.PIPELINE_ROOT),
                               'PYTHONUNBUFFERED':'1', 'PYTHONDONTWRITEBYTECODE':'1', 'SCIGBLAST_WEB_PROCESS_TOKEN':token,
                               'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'}
                if lock is not None:
                    process_env['SCIGBLAST_TREEMAP_LOCK_FD'] = str(lock.fileno())
                for key in ('SYSTEMROOT','WINDIR','TEMP','TMP','LANG','LC_ALL'):
                    if key in os.environ:
                        process_env[key] = os.environ[key]
                log_path = folder / 'treemap.log'
                with log_path.open('ab') as log:
                    process = subprocess.Popen([sys.executable, '-m', 'analysis.treemap.run', '--request', str(req_path), '--output', str(result_path)],
                                               cwd=str(self.app.PIPELINE_ROOT), env=process_env, stdout=log, stderr=log, start_new_session=True,
                                               **({'pass_fds':(lock.fileno(),)} if lock is not None else {}))
                self.process, self.process_run = process, row['id']
                connection.execute('UPDATE derived_runs SET pid=?,process_token=? WHERE id=?', (process.pid, token, row['id']))
            deadline = time.monotonic() + timeout
            error = None
            while process.poll() is None:
                with self.app.db() as connection:
                    valid = self.current_valid(connection, row)
                if self.stopping.is_set() or not valid:
                    error = '绘图已取消或来源任务已变化'
                elif time.monotonic() > deadline:
                    error = '绘图超过时间预算，请缩小数据范围后重试'
                elif os.name == 'posix':
                    from analysis.treemap.run import process_rss
                    if (group_rss(process.pid) if batch else process_rss(process.pid)) > limit:
                        error = '绘图超过内存预算，请缩小数据范围后重试'
                if error:
                    self.stop_group(process, token)
                    raise ValueError(error)
                self.stopping.wait(0.2)
            if process.returncode != 0 or not result_path.is_file():
                raise ValueError('绘图未完成，请查看 Treemap 日志并检查输入文件')
            return json.loads(result_path.read_text(encoding='utf-8'))
        finally:
            try:
                if process is not None:
                    self.stop_group(process, token)
            finally:
                if self.process_run == row['id']:
                    self.process, self.process_run = None, None
                    if os.name != 'posix' or group_state(process.pid,token) == 'gone':
                        with self.app.db() as connection:
                            connection.execute('UPDATE derived_runs SET pid=NULL,process_token=NULL WHERE id=?', (row['id'],))
                if lock is not None:
                    lock.close()
                for path in (req_path, result_path):
                    if path is not None:
                        path.unlink(missing_ok=True)

    @staticmethod
    def stop_group(process, token):
        if os.name != 'posix':
            if process.poll() is None:
                # Windows development only; Linux production uses verified PGIDs.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            process.wait(timeout=5)
            return
        state = signal_group(process.pid, token, signal.SIGTERM)
        if state == 'unverified':
            raise RuntimeError('无法核验绘图进程组身份，保留进程记录待管理员检查')
        deadline = time.monotonic() + 2
        while group_state(process.pid, token) != 'gone' and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.05)
        state = signal_group(process.pid, token, signal.SIGKILL)
        if state == 'unverified':
            raise RuntimeError('无法核验绘图进程组身份，保留进程记录待管理员检查')
        process.wait(timeout=5)
        deadline = time.monotonic() + 3
        while group_state(process.pid, token) != 'gone':
            if time.monotonic() >= deadline:
                raise RuntimeError('绘图子进程尚未确认退出，保留进程记录')
            time.sleep(0.05)

    def artifact(self, row, path, kind, sample_key='', chain=''):
        path = safe_path(path, row['output_root'], file=True)
        return dict(id=uuid.uuid4().hex, label=path.name, relative_path=path.relative_to(row['output_root']).as_posix(),
                    kind=kind, sample_key=sample_key, chain=chain, version=version(path))

    @staticmethod
    def clone_table_path(row,item):
        table = item.get('clone_table') or {}
        if not table.get('relative_path'):
            raise ValueError('Clone Top 中间表缺失，请重试生成')
        path = safe_path(Path(row['output_root'])/table['relative_path'],row['output_root'],file=True)
        if version(path) != table['version']:
            raise ValueError('Clone Top 中间表已变化，请重试生成')
        return path

    def download(self, run_id, sample_key=None):
        row = self.owned_run(run_id)
        if self.public(row, details=False)['status'] in {'stale', 'archived'}:
            raise HTTPException(409, '结果已过期或归档，请从来源任务查看当前结果或完整归档')
        data = self.data(row)
        labels = {item['sample_key']: item['sample_label'] for item in data['items']}
        if sample_key is not None and sample_key not in labels:
            raise HTTPException(404, '未找到所选样本')
        artifacts = [item for item in data['files']
                     if (item['kind'] in {'chain', 'combined'} and
                         (sample_key is None or item['sample_key'] == sample_key)) or
                        (item['kind'] in {'stats','clone_top'} and sample_key is None)]
        if not any(item['kind'] in {'chain', 'combined'} for item in artifacts):
            raise HTTPException(404, '暂无可下载的图片')
        files = []
        for item in artifacts:
            try:
                path = safe_path(Path(row['output_root']) / item['relative_path'], row['output_root'], file=True)
                if version(path) != item['version']:
                    raise ValueError('artifact changed')
            except (ValueError, OSError):
                raise HTTPException(409, '结果文件已变化或不可用，请刷新后重试') from None
            archive_name = ('treemap_summary.csv' if item['kind'] == 'stats' else 'clone_top.csv' if item['kind']=='clone_top' else
                            image_relative(item['sample_key'], labels[item['sample_key']],
                                           item['kind'], item['chain']).as_posix())
            files.append((path, archive_name))
        filename = f"Treemap_{row['id']}{'_sample' if sample_key is not None else ''}.zip"
        return StreamingResponse(stream_zip(files), media_type='application/zip',
                                 headers={'Content-Disposition': f'attachment; filename="{filename}"',
                                          'Cache-Control': 'no-store'})

    def deliver_one(self):
        self.reap_finished_children()
        with self.app.PROCESS_LOCK, self.app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if self.primary_pending(connection):
                return False
            row = connection.execute("SELECT * FROM derived_runs WHERE status IN ('queued','running') ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return False
            if row['parent_job_id']:
                parent = connection.execute('SELECT * FROM jobs WHERE id=?', (row['parent_job_id'],)).fetchone()
                if not parent or parent['status'] != 'SUCCEEDED' or parent['attempt_no'] != row['attempt_no']:
                    connection.execute("UPDATE derived_runs SET status='cancelled',ended_at=? WHERE id=?", (self.app.now(),row['id']))
                    return True
            else:
                parent = None
            connection.execute("UPDATE derived_runs SET status='running',started_at=COALESCE(started_at,?) WHERE id=?", (self.app.now(),row['id']))
        data = self.data(row, work=True)
        item = None
        try:
            if data.get('pending_discovery'):
                items = self.discover(parent, Path(data['input_root']))
                data['items'] = []
                for discovered in items:
                    if not discovered.get('valid'):
                        data['items'].append(dict(discovered,status='failed'))
                        continue
                    for source in discovered['sources']:
                        source['version'] = version(safe_path(source['path'], data['input_root'], file=True))
                    data['items'].append(dict(discovered,status='queued'))
                data['pending_discovery'] = False
                self.save(row,data)
                if not any(source.get('valid') for source in data['items']):
                    raise ValueError('没有适用于 Treemap 的最终样本链结果')
            self.verify_inputs(data['items'], data['input_root'])
            queued = [i for i in data['items'] if i.get('status') == 'queued']
            item = queued[0] if queued else None
            if item is not None:
                budget = worker_budget()
                selected = queued[:budget['workers']]
                requests = [dict(kind='chain', sources=entry['sources'], chain=entry['chain'],
                                 target=str(Path(row['output_root']) / image_relative(entry['sample_key'], entry['sample_label'], 'chain', entry['chain']))) for entry in selected]
                if data.get('clone_table_version'):
                    for entry, request in zip(selected,requests):
                        request.update(clone_table_target=str(Path(row['output_root'])/'.clone_tables'/f"{entry['id']}.csv"),
                                       sample_key=entry['sample_key'],sample_label=entry['sample_label'])
                request = requests[0] if len(requests) == 1 else dict(
                    kind='batch', units=requests, worker_rss_mb=budget['worker_rss_mb'],
                    group_rss_bytes=budget['group_rss_bytes'],
                    timeout_seconds=max(10,int(os.environ.get('SCIGBLAST_TREEMAP_TIMEOUT_SECONDS','1800'))))
                try:
                    result = self.work_unit(row, request)
                except Exception as error:
                    with self.app.db() as connection:
                        current = connection.execute('SELECT pid FROM derived_runs WHERE id=?',(row['id'],)).fetchone()
                    if current and current['pid']:
                        self.end(row,'interrupted',str(error))
                        return True
                    result = {'results': [{'error': str(error)} for _ in selected]}
                if result is None:
                    return False
                with self.app.db() as connection:
                    if not self.current_valid(connection,row) or self.stopping.is_set():
                        self.end(row,'interrupted' if self.stopping.is_set() else 'cancelled')
                        return True
                results = result['results'] if 'results' in result else [{'result':result}]
                if len(results) != len(selected):
                    raise ValueError('绘图批次结果不完整')
                for entry, request, outcome in zip(selected, requests, results):
                    try:
                        if outcome.get('error'):
                            raise ValueError(outcome['error'])
                        self.verify_inputs([entry], data['input_root'])
                        value = outcome['result']
                        if data.get('clone_table_version'):
                            table = safe_path(request['clone_table_target'],row['output_root'],file=True)
                            entry['clone_table'] = dict(relative_path=table.relative_to(row['output_root']).as_posix(),version=version(table))
                        entry.update(status='empty' if value.get('empty') else 'succeeded', error='',
                                     productive_count=value['productive_count'], clone_count=value['clone_count'])
                        if not value.get('empty'):
                            artifact = self.artifact(row, request['target'], 'chain', entry['sample_key'], entry['chain'])
                            entry['artifact_id'] = artifact['id']; data['files'].append(artifact)
                    except Exception as error:
                        entry.update(status='failed',error=str(error)[:500])
                self.save(row,data)
                return True
            # Chain work is complete: compose one sample at a time, releasing the lock.
            completed = set(data.get('composed_samples', []))
            sample = next((i['sample_key'] for i in data['items'] if i['sample_key'] not in completed), None)
            if sample is not None:
                # Validate every registered input image before passing it to
                # the composition worker; input TSV checks alone are not enough.
                for artifact in data['files']:
                    if artifact['kind']=='chain' and artifact['sample_key']==sample:
                        path = safe_path(Path(row['output_root'])/artifact['relative_path'],row['output_root'],file=True)
                        if version(path)!=artifact['version']:
                            raise ValueError('合图来源图片已变化，请重试生成')
                images = {f['chain']:str(Path(row['output_root'])/f['relative_path']) for f in data['files'] if f['kind']=='chain' and f['sample_key']==sample}
                if images:
                    label = next(i['sample_label'] for i in data['items'] if i['sample_key']==sample)
                    target = Path(row['output_root']) / image_relative(sample, label, 'combined')
                    result = self.work_unit(row, dict(kind='combined',images=images,target=str(target)))
                    if result is None:
                        return False
                    data['files'].append(self.artifact(row,target,'combined',sample))
                data['composed_samples'] = sorted(completed|{sample}); self.save(row,data)
                return True
            if data.get('clone_table_version') and not data.get('clone_top_complete'):
                entries = [entry for entry in data['items'] if entry.get('status') in {'succeeded','empty'}]
                paths = [str(self.clone_table_path(row,entry)) for entry in entries]
                target = Path(row['output_root'])/'clone_top.csv'
                result = self.work_unit(row,dict(kind='clone_merge',tables=paths,target=str(target)))
                if result is None:
                    return False
                for entry in entries:
                    self.clone_table_path(row,entry)
                data['files'] = [file for file in data['files'] if file['kind']!='clone_top']+[self.artifact(row,target,'clone_top')]
                data['clone_top_complete'] = True
                self.save(row,data)
                return True
            self.finish(row,data)
        except Exception as error:
            with self.app.db() as connection:
                valid = self.current_valid(connection,row)
            if not valid or self.stopping.is_set():
                self.end(row,'interrupted' if self.stopping.is_set() else 'cancelled',str(error))
            elif item is not None:
                item.update(status='failed',error=str(error)[:500]); self.save(row,data)
            else:
                self.end(row,'failed',str(error))
        return True

    def reap_finished_children(self):
        # A child from a previous Web instance may exit after startup. Clear
        # its stale reservation only when the process group is confirmed gone.
        with self.app.db() as connection:
            rows = connection.execute("SELECT id,pid,process_token FROM derived_runs WHERE status IN ('interrupted','cancelling') AND pid IS NOT NULL").fetchall()
        for old in rows:
            if old['id'] == self.process_run or group_state(old['pid'],old['process_token']) != 'gone':
                continue
            with self.app.db() as connection:
                connection.execute("UPDATE derived_runs SET pid=NULL,process_token=NULL,status=CASE WHEN status='cancelling' THEN 'cancelled' ELSE status END,ended_at=COALESCE(ended_at,?) WHERE id=? AND pid=? AND status IN ('interrupted','cancelling')",
                                   (self.app.now(),old['id'],old['pid']))

    def end(self,row,status,error=None):
        with self.app.db() as connection:
            current = connection.execute('SELECT pid,process_token FROM derived_runs WHERE id=?',(row['id'],)).fetchone()
            live = bool(current and current['pid'] and group_state(current['pid'],current['process_token']) != 'gone')
            connection.execute('UPDATE derived_runs SET status=?,error=?,ended_at=?'+('' if live else ',pid=NULL,process_token=NULL')+' WHERE id=?',
                               ('interrupted' if live else status,error,self.app.now(),row['id']))

    def finish(self,row,data):
        with self.app.PROCESS_LOCK:
            with self.app.db() as connection:
                if not self.current_valid(connection,row):
                    self.end(row,'cancelled')
                    return
            self._finish_locked(row,data)

    def _finish_locked(self,row,data):
        self.verify_inputs(data['items'], data['input_root'])
        root = safe_path(row['output_root'], self.app.DEFAULT_OUTPUT_ROOT)
        root.mkdir(parents=True,exist_ok=True)
        target = root/'treemap_summary.csv'
        temporary = target.with_suffix('.csv.tmp')
        fields = ['sample_key','sample_label','chain','status','productive_count','clone_count','error']
        with temporary.open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(data['items'])
        os.replace(temporary,target)
        data['files']=[f for f in data['files'] if f['kind']!='stats']+[self.artifact(row,target,'stats')]
        states={i['status'] for i in data['items']}
        status='partial' if 'failed' in states and 'succeeded' in states else 'failed' if 'failed' in states else 'empty' if states=={'empty'} else 'succeeded'
        with self.app.PROCESS_LOCK,self.app.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not self.current_valid(connection,row):
                connection.execute("UPDATE derived_runs SET status='cancelled',ended_at=? WHERE id=?",(self.app.now(),row['id']))
                return
            atomic_json(root/'manifest.json',dict(version=VERSION,run_id=row['id'],metric='records',data=derived_store.clean(data)))
            derived_store.save(connection,row,data)
            connection.execute('UPDATE derived_runs SET status=?,ended_at=?,error=? WHERE id=?',
                               (status,self.app.now(),'部分链绘图失败，请检查后重试' if 'failed' in states else None,row['id']))


def router(service):
    routes=APIRouter()

    @routes.get('/api/treemap/tasks')
    def tasks():
        user=auth.require_user()
        with service.app.db() as connection:
            rows=connection.execute("SELECT id,dataset,pipeline FROM jobs WHERE status='SUCCEEDED'"+(' AND owner_id=?' if user['role']!='admin' else '')+' ORDER BY created_at DESC LIMIT 500',([user['id']] if user['role']!='admin' else [])).fetchall()
        return {'tasks':[dict(row,label=service.app.REGISTRY[row['pipeline']]['label']) for row in rows]}

    @routes.post('/api/treemap/scan')
    def scan(body:ScanRequest): return service.scan(body)

    @routes.get('/api/treemap/scans/{scan_id}')
    def scan_page(scan_id:str,query:str='',offset:int=0,limit:int=100): return service.scan_page(scan_id,query,offset,limit)

    @routes.post('/api/treemap/runs')
    def create(body:RunRequest): return service.create(body)

    @routes.get('/api/treemap/runs')
    def runs(): return {'runs':service.list_runs()}

    @routes.get('/api/treemap/runs/{run_id}')
    def run(run_id:str): return service.public(service.owned_run(run_id))

    @routes.get('/api/treemap/runs/{run_id}/download')
    def download(run_id:str,sample_key:str|None=None): return service.download(run_id,sample_key)

    @routes.post('/api/treemap/runs/{run_id}/retry')
    def retry(run_id:str): return service.retry(run_id)

    @routes.post('/api/treemap/runs/{run_id}/cancel')
    def cancel(run_id:str): return service.cancel(run_id)

    @routes.get('/api/treemap/runs/{run_id}/files/{artifact_id}')
    def file(run_id:str,artifact_id:str,download:bool=False):
        row=service.owned_run(run_id)
        with service.app.db() as connection:
            item=derived_store.artifact(connection,row,artifact_id)
        if service.public(row,details=False)['status'] in {'stale','archived'} or not item:
            raise HTTPException(404,'图片已过期、归档或不可访问')
        try:
            path=safe_path(Path(row['output_root'])/item['relative_path'],row['output_root'],file=True)
            if version(path)!=item['version']:
                raise ValueError('artifact changed')
        except (ValueError,OSError):
            raise HTTPException(404,'图片已过期、归档或不可访问') from None
        table = item['kind'] in {'stats','clone_top'}
        return FileResponse(path,media_type='text/csv' if table else 'image/png',filename=path.name if download or table else None)

    @routes.get('/api/jobs/{job_id}/treemap')
    def job_runs(job_id:str):
        row=service.owned_job(job_id)
        return dict(runs=service.list_runs(job_id),enabled=bool(json.loads(row['options_json']).get('generate_treemap')))

    @routes.post('/api/jobs/{job_id}/treemap')
    def job_generate(job_id:str):
        scan=service.scan(ScanRequest(job_id=job_id))
        ids=[i['id'] for i in scan['rows'] if i.get('valid')]
        if not ids: raise HTTPException(400,'没有适用于 Treemap 的有效结果')
        return service.create(RunRequest(scan_id=scan['id'],selected_ids=ids))

    return routes
