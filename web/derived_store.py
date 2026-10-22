"""Versioned Treemap state. V1 JSON remains readable; V2 persists bounded waves."""
import json


def init_schema(connection):
    columns = {row[1] for row in connection.execute('PRAGMA table_info(derived_runs)')}
    if 'storage_version' not in columns:
        connection.execute('ALTER TABLE derived_runs ADD COLUMN storage_version INTEGER NOT NULL DEFAULT 1')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS derived_items (
            run_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            sample_key TEXT NOT NULL, status TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(run_id,item_id));
        CREATE INDEX IF NOT EXISTS idx_derived_items_due ON derived_items(run_id,status,ordinal);
        CREATE INDEX IF NOT EXISTS idx_derived_items_sample ON derived_items(run_id,sample_key,ordinal);
        CREATE TABLE IF NOT EXISTS derived_artifacts (
            run_id TEXT NOT NULL, artifact_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            sample_key TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(run_id,artifact_id));
        CREATE INDEX IF NOT EXISTS idx_derived_artifacts_sample ON derived_artifacts(run_id,sample_key,ordinal);
        CREATE INDEX IF NOT EXISTS idx_derived_artifacts_order ON derived_artifacts(run_id,ordinal);
        CREATE TABLE IF NOT EXISTS derived_samples (
            run_id TEXT NOT NULL, sample_key TEXT NOT NULL, ordinal INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(run_id,sample_key));
        CREATE INDEX IF NOT EXISTS idx_derived_samples_due ON derived_samples(run_id,completed,ordinal);
    ''')


def v2(row):
    return dict(row).get('storage_version', 1) == 2


def metadata(data):
    return {key: value for key, value in data.items() if key not in {'items', 'files', 'composed_samples', '_partial'}}


def clean(data):
    return {key: value for key, value in data.items() if key != '_partial'}


def encoded(data):
    return json.dumps(data, ensure_ascii=False)


def delete(connection, run_ids):
    # Do not depend on SQLite's per-connection foreign_keys setting.
    for run_id in run_ids:
        for table in ('derived_items', 'derived_artifacts', 'derived_samples'):
            connection.execute(f'DELETE FROM {table} WHERE run_id=?', (run_id,))


def replace(connection, run_id, data):
    """One transaction for creation/discovery/retry/final publication, never per wave."""
    delete(connection, [run_id])
    items, files = data.get('items', []), data.get('files', [])
    connection.executemany('INSERT INTO derived_items VALUES(?,?,?,?,?,?)',
        ((run_id, item['id'], order, item['sample_key'], item['status'], encoded(item)) for order, item in enumerate(items)))
    connection.executemany('INSERT INTO derived_artifacts VALUES(?,?,?,?,?)',
        ((run_id, item['id'], order, item.get('sample_key', ''), encoded(item)) for order, item in enumerate(files)))
    samples = dict.fromkeys(item['sample_key'] for item in items)
    completed = set(data.get('composed_samples', []))
    connection.executemany('INSERT INTO derived_samples VALUES(?,?,?,?)',
        ((run_id, sample, order, int(sample in completed)) for order, sample in enumerate(samples)))
    connection.execute('UPDATE derived_runs SET storage_version=2,data_json=?,done=?,total=? WHERE id=?',
        (encoded(metadata(data)), sum(terminal(i['status']) for i in items), len(items), run_id))


def terminal(status):
    return status not in {'queued', 'running'}


def load(connection, row, *, work=False):
    data = json.loads(row['data_json'])
    if not v2(row):
        return data
    run_id = row['id']
    data.update(items=[], files=[])
    if data.get('pending_discovery'):
        return data
    if work:
        queued = connection.execute("SELECT data_json FROM derived_items WHERE run_id=? AND status='queued' ORDER BY ordinal LIMIT 32", (run_id,)).fetchall()
        if queued:
            data.update(items=[json.loads(item[0]) for item in queued], _partial=True)
            return data
        sample = connection.execute('SELECT sample_key FROM derived_samples WHERE run_id=? AND completed=0 ORDER BY ordinal LIMIT 1', (run_id,)).fetchone()
        if sample:
            data.update(items=[json.loads(item[0]) for item in connection.execute('SELECT data_json FROM derived_items WHERE run_id=? AND sample_key=? ORDER BY ordinal', (run_id, sample[0]))],
                        files=[json.loads(item[0]) for item in connection.execute('SELECT data_json FROM derived_artifacts WHERE run_id=? AND sample_key=? ORDER BY ordinal', (run_id, sample[0]))],
                        composed_samples=[], _partial=True)
            return data
    data['items'] = [json.loads(item[0]) for item in connection.execute('SELECT data_json FROM derived_items WHERE run_id=? ORDER BY ordinal', (run_id,))]
    data['files'] = [json.loads(item[0]) for item in connection.execute('SELECT data_json FROM derived_artifacts WHERE run_id=? ORDER BY ordinal', (run_id,))]
    completed = [item[0] for item in connection.execute('SELECT sample_key FROM derived_samples WHERE run_id=? AND completed=1 ORDER BY sample_key', (run_id,))]
    if completed:
        data['composed_samples'] = completed
    return data


def save(connection, row, data):
    if not v2(row):
        items = data.get('items', [])
        connection.execute('UPDATE derived_runs SET data_json=?,done=?,total=? WHERE id=?',
            (encoded(data), sum(terminal(i.get('status')) for i in items), len(items), row['id']))
        return
    if not data.get('_partial'):
        replace(connection, row['id'], data)
        return
    run_id, delta = row['id'], 0
    for item in data['items']:
        before = connection.execute('SELECT status,data_json FROM derived_items WHERE run_id=? AND item_id=?', (run_id, item['id'])).fetchone()
        if before is None:
            raise ValueError('Treemap item disappeared')
        delta += int(terminal(item['status'])) - int(terminal(before[0]))
        payload = encoded(item)
        if before[1] != payload:
            connection.execute('UPDATE derived_items SET status=?,data_json=? WHERE run_id=? AND item_id=?',
                               (item['status'], payload, run_id, item['id']))
    order = connection.execute('SELECT COALESCE(MAX(ordinal),-1)+1 FROM derived_artifacts WHERE run_id=?', (run_id,)).fetchone()[0]
    for artifact in data['files']:
        # Existing sample images are read for composition, but never rewritten.
        exists = connection.execute('SELECT 1 FROM derived_artifacts WHERE run_id=? AND artifact_id=?', (run_id, artifact['id'])).fetchone()
        if not exists:
            connection.execute('INSERT INTO derived_artifacts VALUES(?,?,?,?,?)',
                               (run_id, artifact['id'], order, artifact.get('sample_key', ''), encoded(artifact)))
            order += 1
    for sample in data.get('composed_samples', []):
        connection.execute('UPDATE derived_samples SET completed=1 WHERE run_id=? AND sample_key=?', (run_id, sample))
    connection.execute('UPDATE derived_runs SET data_json=?,done=done+? WHERE id=?', (encoded(metadata(data)), delta, run_id))


def artifact(connection, row, artifact_id):
    if v2(row):
        result = connection.execute('SELECT data_json FROM derived_artifacts WHERE run_id=? AND artifact_id=?', (row['id'], artifact_id)).fetchone()
        return json.loads(result[0]) if result else None
    return next((item for item in json.loads(row['data_json'])['files'] if item['id'] == artifact_id), None)
