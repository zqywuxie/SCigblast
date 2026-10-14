"""User-owned scan snapshots and immutable explicit input manifests."""
import json
from pathlib import Path
import re
import time
import uuid
from pipeline_input_manifest import digest, verify


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
    (folder / f'{token}.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return token


def get_scan(root, token, user, pipeline, input_path):
    if not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('请先扫描输入目录')
    try:
        value = json.loads((Path(root) / 'input_scans' / f'{token}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('扫描不存在，请重新扫描') from exc
    if value['owner_id'] != user or value['pipeline'] != pipeline or value['input_path'] != str(input_path) or value['expires'] < time.time():
        raise ValueError('扫描已过期或不属于当前用户/流程/目录，请重新扫描')
    return value


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
