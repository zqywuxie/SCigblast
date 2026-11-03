"""Bounded source lookups and bulk inserts, owned by one SQLite writer."""
from itertools import islice
import os

BATCH_SIZE = 10000


def tune(connection):
    # SQLite's default page cache is small for indexed read/lineage batches.
    # Reserve only a bounded fraction of the existing report memory budget;
    # sorting remains disk-backed and source files are never reconfigured.
    budget=max(128,int(os.environ.get('SCIGBLAST_REPORT_MEMORY_BUDGET_MB','2048')))
    connection.execute('PRAGMA temp_store=FILE')
    connection.execute('PRAGMA cache_size='+str(-1024*max(8,min(64,budget//16))))


def batches(records, size=BATCH_SIZE):
    records = iter(records)
    while batch := list(islice(records, size)):
        yield batch


def prepare_observation_links(connection):
    # Count via the existing covering ID index. Do not fetch large sequence
    # payloads in ID-sorted order just to aggregate three small text fields.
    connection.executescript('''
      CREATE TABLE observation_links AS
        SELECT sample_key,chain,mapping_sequence_id,count(*) AS n,
          min(id) AS first_id
        FROM observations GROUP BY sample_key,chain,mapping_sequence_id;
      CREATE INDEX observation_link_key ON observation_links(sample_key,chain,mapping_sequence_id);
    ''')


def lookup_rows(connection, records, sql):
    """Join a batch of AIRR rows without changing duplicate/null ID semantics."""
    connection.execute('CREATE TEMP TABLE IF NOT EXISTS report_batch(ordinal INTEGER PRIMARY KEY,sequence_id TEXT)')
    for batch in batches(records):
        connection.execute('DELETE FROM report_batch')
        connection.executemany('INSERT INTO report_batch VALUES(?,?)',
                               ((i, row.get('sequence_id', '')) for i, row in enumerate(batch)))
        linked = connection.execute(sql).fetchall()
        if len(linked) != len(batch):
            raise ValueError('Batch lineage did not preserve one record per input')
        for row, link in zip(batch, linked):
            yield row, link
