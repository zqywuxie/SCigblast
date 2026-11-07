"""Read-only SQLite storage inventory; optional per-object scan, never VACUUM."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def inspect(path, *, objects=False):
    path=Path(path).resolve()
    stat=path.stat()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        page_size=connection.execute('PRAGMA page_size').fetchone()[0]
        pages=connection.execute('PRAGMA page_count').fetchone()[0]
        free=connection.execute('PRAGMA freelist_count').fetchone()[0]
        result=dict(file=path.name,file_bytes=stat.st_size,page_size=page_size,pages=pages,
                    free_pages=free,free_bytes=free*page_size,
                    objects=[dict(type=kind,name=name) for kind,name in connection.execute(
                        "SELECT type,name FROM sqlite_master WHERE type IN ('table','index') ORDER BY type,name")])
        if objects:
            # dbstat visits database pages; opt-in only for large live indexes.
            result['object_bytes']=[dict(name=name,bytes=size) for name,size in connection.execute(
                'SELECT name,sum(pgsize) FROM dbstat GROUP BY name ORDER BY sum(pgsize) DESC')]
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('indexes',nargs='+',type=Path)
    parser.add_argument('--objects',action='store_true',help='Scan dbstat pages for per-table/index allocation')
    args=parser.parse_args()
    print(json.dumps([inspect(path,objects=args.objects) for path in args.indexes],indent=2))


if __name__=='__main__':main()
