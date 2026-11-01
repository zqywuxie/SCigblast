"""Read-only deployment readiness checks, also usable in an older Web image.

deploy.sh supplies this file on stdin so the first upgrade does not depend on
the running image already containing the new health endpoint fields.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request
from contextlib import closing


def snapshot(connection):
    tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'jobs' not in tables:
        raise ValueError('任务数据库缺少 jobs 表，无法确认部署状态')
    columns={row[1] for row in connection.execute('PRAGMA table_info(jobs)')}
    if 'status' not in columns:
        raise ValueError('任务数据库结构未知，无法确认部署状态')
    def count(where):
        return connection.execute('SELECT count(*) FROM jobs WHERE '+where).fetchone()[0]
    counts={
        'pipeline_active':count("status IN ('RUNNING','MATCHING','STOPPING')"),
        'pipeline_queued':count("status='QUEUED'"),
        'archiving':count("status='ARCHIVING'"),
        'deleting':count("status='DELETING'"),
        'pipeline_recovering':count("status='INTERRUPTED' AND pgid IS NOT NULL") if 'pgid' in columns else 0,
    }
    for table,key in (('derived_runs','treemap_active'),('report_runs','reports_active')):
        if table not in tables:
            counts[key]=0
            continue
        fields={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
        if 'status' not in fields:raise ValueError('后分析数据库结构未知：'+table)
        predicate="status IN ('queued','running','cancelling')"+(' OR pid IS NOT NULL' if 'pid' in fields else '')
        counts[key]=connection.execute('SELECT count(*) FROM '+table+' WHERE '+predicate).fetchone()[0]
    return dict(ready=not any(counts.values()),counts=counts)


def main():
    path=Path(os.environ.get('SCIGBLAST_STATE_DIR','/var/lib/scigblast-web'))/'scigblast.sqlite3'
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
            connection.execute('BEGIN')
            result=snapshot(connection)
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as response:
            health=json.load(response)
        if health.get('status')!='ok':raise ValueError('Web 健康状态未知')
        # Keep the live process check as well as the persisted task-state check.
        for key in ('active_jobs','queued_jobs'):
            value=health.get(key)
            if not isinstance(value,int) or value<0:raise ValueError('Web 返回的任务状态未知')
            result['counts']['runtime_'+key]=value
        result['ready']=result['ready'] and not health['active_jobs'] and not health['queued_jobs']
        names={'pipeline_active':'运行中流程','pipeline_queued':'排队流程','archiving':'归档中任务',
            'deleting':'删除中任务','pipeline_recovering':'待确认退出的流程','treemap_active':'树图任务',
            'reports_active':'后续分析任务','runtime_active_jobs':'存活流程','runtime_queued_jobs':'运行队列'}
        busy=[f'{names[key]} {value}' for key,value in result['counts'].items() if value]
        print('部署检查：'+('；'.join(busy) if busy else '主分析、后分析、树图及归档均空闲'),flush=True)
        return 0 if result['ready'] else 1
    except Exception as error:
        print('无法确认部署状态：'+str(error),file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
