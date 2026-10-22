"""Synthetic state-only benchmarks; never opens platform data or launches tools."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import input_manifest
import derived_store


def measured(call):
    start = time.perf_counter()
    result = call()
    return result, round((time.perf_counter() - start) * 1000, 3)


def snapshots(root, count):
    rows = [dict(id=str(i), pair_id=f'sample-{i}', sample_id=f'sample-{i}', valid=True,
                 r1=f'/synthetic/{i}_R1.fastq', r2=f'/synthetic/{i}_R2.fastq',
                 versions={'r1': [100, 1], 'r2': [100, 1]}) for i in range(count)]
    token = input_manifest.save_scan(root, 'synthetic', 'igblast_base', '/synthetic', rows)
    path = root / 'input_scans' / f'{token}.json'
    pages = range(0, count, 200)
    _, uncached = measured(lambda: [json.loads(path.read_text(encoding='utf-8'))['rows'][n:n+200] for n in pages])
    input_manifest._cache.clear()
    _, cached = measured(lambda: [input_manifest.scan_page(root, token, 'synthetic', 'igblast_base', '/synthetic', n, 200) for n in pages])
    return dict(items=count, pages=len(pages), uncached_ms=uncached, cached_ms=cached)


def treemap(count):
    items = [dict(id=str(i), sample_key=f'sample-{i}', sample_label=f'sample-{i}', chain='IGH',
                  valid=True, status='queued', sources=[dict(path=f'/synthetic/{i}.tsv', version=[1, 100, 1, 1])])
             for i in range(count)]
    data = dict(items=items, files=[], input_root='/synthetic', metric='records')
    def sample():
        return len(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    size, elapsed = measured(lambda: [sample() for _ in range(10)])
    # A conservative projection: initial JSON size only, before artifacts grow.
    return dict(items=count, json_bytes=size[0], sampled_serializations=10,
                sample_ms=elapsed, projected_chain_wave_bytes=size[0]*((count+1)//2))


def queries():
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY, owner_id TEXT, status TEXT, created_at TEXT)')
    connection.execute('CREATE INDEX idx_jobs_created ON jobs(created_at DESC)')
    connection.executemany('INSERT INTO jobs VALUES(?,?,?,?)',
        ((f'{i:08}', f'user-{i%100}', ('SUCCEEDED','RUNNING','FAILED')[i%3], f'{i//10:08}') for i in range(50000)))
    query = 'SELECT id FROM jobs WHERE owner_id=? ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET 200'
    def run():
        return [connection.execute(query, ('user-42',)).fetchall() for _ in range(20)]
    before_plan = connection.execute('EXPLAIN QUERY PLAN '+query, ('user-42',)).fetchall()
    _, before = measured(run)
    connection.execute('CREATE INDEX idx_jobs_owner_created ON jobs(owner_id,created_at DESC,id DESC)')
    after_plan = connection.execute('EXPLAIN QUERY PLAN '+query, ('user-42',)).fetchall()
    _, after = measured(run)
    connection.close()
    return dict(rows=50000, repeats=20, before_ms=before, after_ms=after,
                before_plan=before_plan, after_plan=after_plan)


def incremental(count):
    connection=sqlite3.connect(':memory:')
    connection.row_factory=sqlite3.Row
    connection.execute('CREATE TABLE derived_runs(id TEXT PRIMARY KEY,data_json TEXT,done INTEGER,total INTEGER)')
    derived_store.init_schema(connection)
    connection.execute("INSERT INTO derived_runs VALUES('run','{}',0,0,1)")
    data=dict(input_root='/synthetic',metric='records',items=[dict(id=str(i),sample_key=f'sample-{i}',
        sample_label=f'sample-{i}',chain='IGH',valid=True,status='queued',
        sources=[dict(path=f'/synthetic/{i}.tsv',version=[1,100,1,1])]) for i in range(count)],files=[])
    serialized=0
    original=derived_store.encoded
    def encoded(value):
        nonlocal serialized
        result=original(value)
        serialized+=len(result.encode('utf-8'))
        return result
    derived_store.encoded=encoded
    start=time.perf_counter()
    try:
        derived_store.replace(connection,'run',data)
        for _ in range((count+1)//2):
            row=connection.execute("SELECT * FROM derived_runs WHERE id='run'").fetchone()
            work=derived_store.load(connection,row,work=True)
            for item in work['items'][:2]:
                item['status']='succeeded'
            derived_store.save(connection,row,work)
        row=connection.execute("SELECT * FROM derived_runs WHERE id='run'").fetchone()
        assert row['done']==count
        return dict(items=count,chain_waves=(count+1)//2,serialized_bytes=serialized,
                    run_metadata_bytes=len(row['data_json'].encode('utf-8')),
                    elapsed_ms=round((time.perf_counter()-start)*1000,3))
    finally:
        derived_store.encoded=original
        connection.close()


def archive(root, count):
    from archive_results import inventory
    folder = root / 'archive'
    folder.mkdir()
    for i in range(count):
        (folder / f'{i:05}.png').write_bytes(b'synthetic' * 128)
    tracemalloc.start()
    entries, elapsed = measured(lambda: inventory(folder))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return dict(files=len(entries), inventory_ms=elapsed, python_peak_bytes=peak)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 1000, 10000])
    parser.add_argument('--archive-files', type=int, default=0)
    args = parser.parse_args()
    if any(n < 1 or n > 10000 for n in args.sizes) or not 0 <= args.archive_files <= 10000:
        parser.error('Synthetic sizes must be 1..10000; archive files 0..10000')
    with tempfile.TemporaryDirectory(prefix='scigblast-benchmark-') as temporary:
        root = Path(temporary)
        result = dict(scan=[snapshots(root, n) for n in args.sizes],
                      treemap=[treemap(n) for n in args.sizes],
                      incremental=[incremental(n) for n in args.sizes],query=queries())
        if args.archive_files:
            result['archive'] = archive(root, args.archive_files)
        print(json.dumps(result, ensure_ascii=True, indent=2))
