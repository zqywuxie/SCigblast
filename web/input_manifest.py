"""User-owned scan snapshots and immutable explicit input manifests."""
import json
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import re
import threading
import time
import uuid
from pipeline_input_manifest import digest, verify

# Private cached objects never escape: callers receive copies of their page or
# manifest. Budgets include an allowance for Python object/string overhead.
MAX_SCANS = 8
MAX_ROWS = 20_000
MAX_BYTES = 32 * 1024**2
_cache = OrderedDict()
_lock = threading.RLock()


def _file_version(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_scan(path):
    for _ in range(2):
        before = _file_version(path)
        with _lock:
            cached = _cache.get(path)
            if cached and cached[0] == before:
                _cache.move_to_end(path)
                return cached[1]
            _cache.pop(path, None)
        raw = path.read_text(encoding='utf-8')
        value = json.loads(raw)
        if _file_version(path) != before:
            continue
        rows, weight = len(value['rows']), len(raw.encode('utf-8')) * 6 + len(value['rows']) * 512
        if rows <= MAX_ROWS and weight <= MAX_BYTES:
            with _lock:
                _cache[path] = (before, value, rows, weight)
                while (len(_cache) > MAX_SCANS or sum(v[2] for v in _cache.values()) > MAX_ROWS
                       or sum(v[3] for v in _cache.values()) > MAX_BYTES):
                    _cache.popitem(last=False)
        return value
    raise ValueError('扫描文件已变化，请重新扫描')


def save_scan(root, user, pipeline, input_path, rows):
    folder = Path(root) / 'input_scans'
    folder.mkdir(parents=True, exist_ok=True)
    # Bound per-user retained scans and expire unused snapshots.
    own = []
    for path in folder.glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value['expires'] < time.time():
                path.unlink()
            elif value['owner_id'] == user:
                own.append(path)
        except (OSError, ValueError, KeyError):
            continue
    for path in sorted(own, key=lambda p: p.stat().st_mtime)[:-15]:
        path.unlink(missing_ok=True)
    token = uuid.uuid4().hex
    value = {'owner_id': user, 'pipeline': pipeline, 'input_path': str(input_path), 'rows': rows, 'expires': time.time() + 86400}
    path = folder / f'{token}.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)
    return token


def _authorized_scan(root, token, user, pipeline, input_path):
    if not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('请先扫描输入目录')
    path = (Path(root) / 'input_scans' / f'{token}.json').resolve()
    try:
        value = _read_scan(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        with _lock:
            _cache.pop(path, None)
        raise ValueError('扫描不存在，请重新扫描') from exc
    if value['owner_id'] != user or value['pipeline'] != pipeline or value['input_path'] != str(input_path) or value['expires'] < time.time():
        raise ValueError('扫描已过期或不属于当前用户/流程/目录，请重新扫描')
    return value


def get_scan(root, token, user, pipeline, input_path):
    return deepcopy(_authorized_scan(root, token, user, pipeline, input_path))


def scan_page(root, token, user, pipeline, input_path, offset=0, limit=50):
    value = _authorized_scan(root, token, user, pipeline, input_path)
    offset, limit = max(0, offset), min(200, max(1, limit))
    return {'total': len(value['rows']), 'rows': deepcopy(value['rows'][offset:offset + limit])}


def publish(root, value):
    folder = Path(root) / 'input_manifests'
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    path = folder / f'{token}.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)
    return str(path), digest(value)


def load(root, path, expected_hash):
    path = Path(path)
    if path.parent.resolve() != (Path(root) / 'input_manifests').resolve() or not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
        raise ValueError('无效的任务输入清单')
    value = json.loads(path.read_text(encoding='utf-8'))
    if digest(value) != expected_hash:
        raise ValueError('任务输入清单已改变')
    verify(value)
    return value
