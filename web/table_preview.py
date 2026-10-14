"""Bounded CSV/TSV preview, independent of Match review semantics."""
import base64
import csv
import hashlib
import hmac
import json
import secrets
from collections import OrderedDict

from fastapi import HTTPException


SCAN_ROWS = 10000
SCAN_BYTES = 4 * 1024 * 1024
_SECRET = secrets.token_bytes(32)
_TOTALS = OrderedDict()


def _encode(value):
    payload = json.dumps(value, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(hmac.digest(_SECRET, payload, 'sha256') + payload).decode()


def _decode(token):
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        if not hmac.compare_digest(raw[:32], hmac.digest(_SECRET, raw[32:], 'sha256')):
            raise ValueError('signature')
        return json.loads(raw[32:])
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(409, '预览游标已失效，请刷新结果') from None


def read_preview(path, offset=0, limit=50, query='', errors_only=False, cursor=''):
    stat = path.stat()
    version = hashlib.sha256(json.dumps([str(path.resolve()), stat.st_ino,
        stat.st_mtime_ns, stat.st_size, query, errors_only]).encode()).hexdigest()
    state = {'version': version, 'position': 0, 'index': 0, 'seen': 0,
             'matched': 0, 'ok': 0, 'error': 0, 'skip': max(0, offset)}
    if cursor:
        state = _decode(cursor)
        if state.get('version') != version:
            raise HTTPException(409, '结果或筛选已变化，请从第一页重新加载')
    limit = min(200, max(1, limit))
    rows = []
    with path.open(encoding='utf-8-sig', newline='') as handle:
        # readline iteration keeps tell() available, including quoted multiline cells.
        reader = csv.DictReader(iter(handle.readline, ''), delimiter='\t' if path.suffix == '.tsv' else ',')
        columns = reader.fieldnames or []
        if cursor:
            handle.seek(state['position'])
        start = handle.tell()
        consumed = 0
        while consumed < SCAN_ROWS and handle.tell() - start < SCAN_BYTES and len(rows) < limit:
            item = next(reader, None)
            if item is None:
                break
            index = state['index']
            state['index'] += 1
            consumed += 1
            if not any(str(value or '').strip() for value in item.values()):
                continue
            state['seen'] += 1
            status = str(item.get('status', '')).upper()
            if status == 'OK':
                state['ok'] += 1
            elif status == 'ERROR':
                state['error'] += 1
            if errors_only and status != 'ERROR':
                continue
            if query and query.casefold() not in ' '.join(str(v or '') for v in item.values()).casefold():
                continue
            state['matched'] += 1
            if state['matched'] <= state['skip']:
                continue
            rows.append({**item, '_row_key': str(index)})
        state['position'] = handle.tell()
    current = path.stat()
    if (current.st_ino, current.st_size, current.st_mtime_ns) != (stat.st_ino, stat.st_size, stat.st_mtime_ns):
        raise HTTPException(409, '结果在读取时已更新，请从第一页重新加载')
    complete = state['position'] >= stat.st_size
    counts = {'total': state['seen'] if complete else None}
    if 'status' in columns:
        counts.update(ok=state['ok'] if complete else None, error=state['error'] if complete else None)
    if complete:
        _TOTALS[version] = (state['matched'], counts)
        if len(_TOTALS) > 128:
            _TOTALS.popitem(last=False)
    total, counts = _TOTALS.get(version, (None, counts))
    return {'path': str(path), 'columns': columns, 'rows': rows, 'counts': counts,
            'total': total, 'offset': max(0, offset),
            'has_status': 'status' in columns, 'has_more': not complete,
            'scan_complete': complete, 'scanned_rows': state['seen'],
            'matched_rows': state['matched'], 'next_cursor': _encode(state) if not complete else None}
