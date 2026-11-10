"""Owned, queued post-pipeline QC/primer analyses and report figure exports."""
from __future__ import annotations

import json
from contextlib import closing, contextmanager
import csv
import io
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

import auth
from artifact_download import stream_zip, file_response
from process_identity import group_state

LOG=logging.getLogger(__name__)
ACTIVE="(status IN ('queued','running','cancelling') OR pid IS NOT NULL)"


def init_schema(connection):
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS report_uploads(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_scans(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,expires REAL NOT NULL,data_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_plans(job_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,config_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_runs(
            id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,parent_job_id TEXT,attempt_no INTEGER,
            source_json TEXT NOT NULL,config_json TEXT NOT NULL,output_root TEXT NOT NULL,
            dedupe_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',created_at TEXT NOT NULL,
            started_at TEXT,ended_at TEXT,error TEXT,pid INTEGER,process_token TEXT,
            UNIQUE(owner_id,dedupe_key));
        CREATE INDEX IF NOT EXISTS report_parent ON report_runs(parent_job_id,attempt_no);
        CREATE INDEX IF NOT EXISTS report_due ON report_runs(status,created_at);
        CREATE TABLE IF NOT EXISTS report_read_leases(id TEXT PRIMARY KEY,run_id TEXT,parent_job_id TEXT,expires REAL);
        CREATE INDEX IF NOT EXISTS report_read_parent ON report_read_leases(parent_job_id,expires);
    ''')


def enqueue_auto(connection,job_id,now):
    from analysis.repertoire.io import digest
    row=connection.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row or row['status']!='SUCCEEDED' or row['pipeline'] not in {'pig_igblast','igblast_base'}:
        return
    options=json.loads(row['options_json'])
    plan=connection.execute('SELECT config_json FROM report_plans WHERE job_id=?',(job_id,)).fetchone()
    if not options.get('generate_report') and not plan:
        return
    config=json.loads(plan['config_json']) if plan else options.get('primer_analysis') or {}
    from analysis.repertoire import VERSION,FIGURE_VERSION
    from analysis.repertoire.workbook import VERSION as WORKBOOK_VERSION
    key=digest(['auto-report',VERSION,FIGURE_VERSION,WORKBOOK_VERSION,job_id,row['attempt_no'],config])
    run_id=uuid.uuid4().hex
    source=dict(input_root=row['output_root'],items=None,task_name=row['dataset'])
    folder=Path(row['output_root'])/'analysis'/'reports'/run_id[:12]
    connection.execute('INSERT OR IGNORE INTO report_runs(id,owner_id,parent_job_id,attempt_no,source_json,config_json,output_root,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (run_id,row['owner_id'],job_id,row['attempt_no'],json.dumps(source),json.dumps(config),str(folder),key,now))


class Scan(BaseModel):
    job_id:str=''
    input_path:str=Field(default='',max_length=4096)


class Create(BaseModel):
    scan_id:str
    selected_ids:list[str]=Field(min_length=1,max_length=10000)
    config:dict=Field(default_factory=dict)
    comparison_species:str=''
    primer_check_id:str=Field(default='',max_length=64)


class Service:
    def __init__(self,app):
        self.app=app
        self.stopping=threading.Event()
        self.thread=None

    def owned(self,run_id):
        user=auth.require_user()
        with self.app.db() as conn:
            row=conn.execute('SELECT * FROM report_runs WHERE id=?',(run_id,)).fetchone()
        if not row or (row['owner_id']!=user['id'] and user['role']!='admin'):
            raise HTTPException(404,'分析记录不存在或无权访问')
        if row['parent_job_id']:
            self.app.TREEMAP.owned_job(row['parent_job_id'],user)
        return row

    def guard_mutation(self,job_id):
        with self.app.db() as conn:
            active=conn.execute('SELECT 1 FROM report_runs WHERE parent_job_id=? AND '+ACTIVE+' LIMIT 1',(job_id,)).fetchone()
            reading=conn.execute('SELECT 1 FROM report_read_leases WHERE parent_job_id=? AND expires>? LIMIT 1',(job_id,time.time())).fetchone()
        if active:
            raise HTTPException(409,'后续分析尚在排队或运行，请先取消后再修改/删除来源任务')
        if reading:
            raise HTTPException(409,'报告正在导出，请稍后再修改或删除来源任务')

    @contextmanager
    def read_lease(self,row):
        token=uuid.uuid4().hex;stop=threading.Event()
        with self.app.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=conn.execute('SELECT status,output_root FROM report_runs WHERE id=?',(row['id'],)).fetchone()
            if not current or current['status']!='succeeded' or current['output_root']!=row['output_root']:
                raise ValueError('Report changed before HTML export')
            if row['parent_job_id']:
                parent=conn.execute('SELECT status FROM jobs WHERE id=?',(row['parent_job_id'],)).fetchone()
                if not parent or parent['status']!='SUCCEEDED':raise ValueError('Report source is not available for HTML export')
            conn.execute('DELETE FROM report_read_leases WHERE expires<=?',(time.time(),))
            conn.execute('INSERT INTO report_read_leases VALUES(?,?,?,?)',(token,row['id'],row['parent_job_id'],time.time()+90))
        def heartbeat():
            while not stop.wait(20):
                try:
                    with self.app.db() as conn:conn.execute('UPDATE report_read_leases SET expires=? WHERE id=?',(time.time()+90,token))
                except Exception:LOG.warning('Cannot renew report read lease %s',row['id'])
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:yield
        finally:
            stop.set();thread.join(timeout=2)
            with self.app.db() as conn:conn.execute('DELETE FROM report_read_leases WHERE id=?',(token,))

    def purge_html_cache(self,rows):
        from analysis.repertoire.html_cache import purge
        base=(self.app.STATE_ROOT/'report_html_cache').resolve()
        for row in rows:
            try:
                target=base/row['owner_id']/row['id']
                if target.resolve()==base or not target.resolve().is_relative_to(base):
                    raise ValueError('Unsafe HTML cache identity')
                purge(target)
            except (OSError,ValueError):LOG.warning('Cannot purge derived HTML cache for %s',row['id'])
            try:
                # Invoked after archive/delete has checked active reports/read leases.
                # Keep analysis.log and all other report_work diagnostics.
                from analysis.repertoire.private_cache import locked
                from analysis.repertoire.statistics_checkpoint import Checkpoint
                work=(Path(row['output_root']).parent/'.report_checkpoints').resolve()
                checkpoint=work/row['id']
                if (checkpoint.resolve()==work or not checkpoint.resolve().is_relative_to(work)
                    or not self.app.is_under(checkpoint,[self.app.DEFAULT_OUTPUT_ROOT])):
                    raise ValueError('Unsafe statistics checkpoint identity')
                if checkpoint.is_dir():
                    with locked(checkpoint) as owned:Checkpoint(owned,'',{}).clear()
            except (OSError,ValueError):LOG.warning('Cannot purge report statistics checkpoint for %s',row['id'])

    def freeze_mapping(self,row,env,destination):
        """Freeze the selected primers for this analysis attempt, before stage 5."""
        for key in ('SCIGBLAST_PRIMER_MAPPING_CONFIG','SCIGBLAST_PRIMER_MAPPING_HASH'):
            env.pop(key,None)
        if row['pipeline'] not in {'igblast_base','pig_igblast'} or env.get('SCIGBLAST_WEB_MATCH_ONLY')=='1':return None
        options=json.loads(row['options_json'])
        with self.app.db() as conn:
            plan=conn.execute('SELECT config_json FROM report_plans WHERE job_id=?',(row['id'],)).fetchone()
        config=json.loads(plan['config_json']) if plan else options.get('primer_analysis') or {}
        if not config or not config.get('columns',{}).get('trim_before_mapping',True):return None
        from analysis.repertoire.catalog import build_catalog,chains_by_sample
        from analysis.repertoire.io import safe_file,table,write_json,digest
        from scigblast.primer import frozen_payload
        import hashlib
        manifest=safe_file(Path(row['output_root'])/'01.match'/row['dataset']/'sample_manifest.csv',row['output_root'])
        scope=chains_by_sample([record for record in table(manifest) if record.get('status')=='OK'])
        samples=sorted(scope)
        if not samples:raise ValueError('没有可用于引物归属的已确认样本')
        catalog=build_catalog(config.get('submission_path'),config['primer_path'],config['columns'],samples,scope)
        payload=frozen_payload(catalog)
        path=Path(destination)/('primer-mapping-'+digest(payload)[:20]+'.json')
        if not path.exists():write_json(path,payload)
        content=path.read_bytes()
        if json.loads(content)!=payload:raise ValueError('冻结的引物配置已损坏，请重新保存引物计划')
        env['SCIGBLAST_PRIMER_MAPPING_CONFIG']=str(path)
        env['SCIGBLAST_PRIMER_MAPPING_HASH']=hashlib.sha256(content).hexdigest()
        return dict(version=payload['version'],catalog_version=catalog['version'],config_hash=env['SCIGBLAST_PRIMER_MAPPING_HASH'],unassigned_policy='keep_original')

    def upload_path(self,token):
        with self.app.db() as conn:
            row=conn.execute('SELECT * FROM report_uploads WHERE id=? AND owner_id=?',(token,auth.require_user()['id'])).fetchone()
        if not row:
            raise HTTPException(404,'工作簿不可用或无权访问')
        from analysis.repertoire.io import safe_file
        return str(safe_file(row['path'],self.app.STATE_ROOT/'report_uploads'))

    def config(self,body):
        if not body or not body.get('submission_id') and not body.get('primer_id'):
            return {}
        if len(json.dumps(body))>100000:
            raise HTTPException(400,'配置过大')
        if not body.get('primer_id'):
            raise HTTPException(400,'请上传引物表')
        return dict(submission_path=self.upload_path(body['submission_id']) if body.get('submission_id') else None,
            primer_path=self.upload_path(body.get('primer_id')),columns=dict(body.get('columns',{}),observation_stage='mapping'))

    def sample_chains(self,row):
        from analysis.repertoire.catalog import chains_by_sample
        from analysis.repertoire.io import safe_file,table
        manifest=Path(row['output_root'])/'01.match'/row['dataset']/'sample_manifest.csv'
        if manifest.is_file():
            records=[r for r in table(safe_file(manifest,row['output_root'])) if r.get('status')=='OK']
        else:
            records=json.loads(row['options_json']).get('input_assignments',[])
        return chains_by_sample(records)

    def scan(self,body):
        from analysis.repertoire.sources import discover
        from analysis.repertoire.io import version
        # Reuse existing ownership/path/root rules, including task paths typed manually.
        parent,root=self.app.TREEMAP.source(body)
        if parent is not None and parent['pipeline'] not in {'igblast_base','pig_igblast'}:
            raise HTTPException(400,'首版支持 Pig IgBLAST / IgBLAST base')
        try:
            items=discover(root)
        except (OSError,ValueError) as error:
            raise HTTPException(400,str(error)) from None
        if not items:
            raise HTTPException(400,'未找到可用的单链 AIRR 结果')
        token=uuid.uuid4().hex
        data=dict(input_root=str(root),items=items,parent_job_id=parent['id'] if parent is not None else None,
            attempt_no=parent['attempt_no'] if parent is not None else None)
        with self.app.db() as conn:
            conn.execute('DELETE FROM report_scans WHERE expires<?',(time.time(),))
            conn.execute('INSERT INTO report_scans VALUES(?,?,?,?)',(token,auth.require_user()['id'],time.time()+86400,json.dumps(data)))
        return dict(scan_id=token,items=[{k:v for k,v in row.items() if k not in {'path','trace','summary'}} for row in items])

    def scan_data(self,token):
        with self.app.db() as conn:
            row=conn.execute('SELECT * FROM report_scans WHERE id=? AND owner_id=?',(token,auth.require_user()['id'])).fetchone()
        if not row or row['expires']<time.time():
            raise HTTPException(400,'扫描已过期，请重新扫描')
        return json.loads(row['data_json'])

    def preview(self,body):
        from analysis.repertoire.catalog import build_catalog,chains_by_sample
        data=self.scan_data(body.scan_id)
        items=[row for row in data['items'] if row['id'] in set(body.selected_ids)]
        if len(items)!=len(set(body.selected_ids)):
            raise HTTPException(400,'所选样本结果已变化')
        config=self.config(body.config)
        if not config:
            return dict(assignments={},samples=sorted({row['sample_label'] for row in items}))
        try:
            scope=chains_by_sample(items)
            return build_catalog(config.get('submission_path'),config['primer_path'],config['columns'],sorted(scope),scope)
        except (OSError,ValueError,KeyError) as error:
            raise HTTPException(400,str(error)) from None

    def create(self,body,*,primer_only=False):
        from analysis.repertoire.io import digest
        from analysis.repertoire.sources import verify
        data=self.scan_data(body.scan_id)
        selected=set(body.selected_ids)
        items=[row for row in data['items'] if row['id'] in selected]
        if len(items)!=len(selected):
            raise HTTPException(400,'所选结果不可用')
        catalog=self.preview(body)
        config=self.config(body.config)
        if primer_only and not config:
            raise HTTPException(400,'请先上传引物表并完成引物分配')
        try:verify(items,data['input_root'])
        except (OSError,ValueError) as error:raise HTTPException(409,str(error)) from None
        user=auth.require_user()
        parent=self.app.TREEMAP.owned_job(data['parent_job_id']) if data['parent_job_id'] else None
        allowed_roots=[str(p) for p in self.app.configured_roots('SCIGBLAST_ALLOWED_INPUT_ROOTS','/colddata/SCigblast/data')]
        checked=None
        check_folder=None
        if config and not primer_only:
            if not body.primer_check_id:
                raise HTTPException(409,'请先运行引物匹配预检查，并核对结果')
            check=self.owned(body.primer_check_id)
            if check['status']!='succeeded' or json.loads(check['source_json']).get('analysis_kind')!='primer_check':
                raise HTTPException(409,'引物预检查尚未完成')
            if check['parent_job_id']!=data['parent_job_id'] or check['attempt_no']!=data['attempt_no']:
                raise HTTPException(409,'引物预检查与当前来源任务不一致')
            from analysis.repertoire.primer_check import load as load_check
            try:
                checked=load_check(check['output_root'],data['input_root'],items,catalog,allowed_roots)
            except (OSError,ValueError,KeyError) as error:
                raise HTTPException(409,str(error)) from None
            check_folder=check['output_root']
        from analysis.repertoire import VERSION,FIGURE_VERSION
        from analysis.repertoire.workbook import VERSION as WORKBOOK_VERSION
        comparison=None
        if body.comparison_species and not primer_only:
            if body.comparison_species not in {'human','pig','cat','dog'}:raise HTTPException(400,'不支持该比较物种')
            comparison=dict(species=body.comparison_species,root=os.environ.get('SCIGBLAST_REFERENCE_ROOT','/opt/scigblast/igblast'))
        if primer_only:
            from analysis.repertoire.primer_check import fingerprint
            try:key=fingerprint(data['input_root'],items,catalog,allowed_roots)
            except (OSError,ValueError,KeyError) as error:raise HTTPException(409,str(error)) from None
        else:
            key=digest(['report',VERSION,FIGURE_VERSION,WORKBOOK_VERSION,data['input_root'],items,config,data['attempt_no'],comparison,
                        checked['fingerprint'] if checked else None])
        run_id=uuid.uuid4().hex
        category='primer_checks' if primer_only else 'reports'
        base=Path(parent['output_root'])/'analysis'/category if parent is not None else self.app.default_job_output(user,category).parent
        folder=base/run_id[:12]
        source=dict(input_root=data['input_root'],items=items,comparison_reference=comparison)
        if primer_only:source['analysis_kind']='primer_check'
        if check_folder:source['primer_check_root']=check_folder
        with self.app.PROCESS_LOCK,self.app.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if parent is not None:
                current=conn.execute('SELECT status,attempt_no FROM jobs WHERE id=?',(parent['id'],)).fetchone()
                if not current or current['status']!='SUCCEEDED' or current['attempt_no']!=data['attempt_no']:
                    raise HTTPException(409,'来源任务已变化，请重新扫描')
            owner=parent['owner_id'] if parent is not None else user['id']
            conn.execute('INSERT OR IGNORE INTO report_runs(id,owner_id,parent_job_id,attempt_no,source_json,config_json,output_root,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (run_id,owner,data['parent_job_id'],data['attempt_no'],json.dumps(source),json.dumps(config),str(folder),key,self.app.now()))
            existing=conn.execute('SELECT id FROM report_runs WHERE owner_id=? AND dedupe_key=?',(owner,key)).fetchone()[0]
        return self.public(self.owned(existing))

    def public(self,row):
        result={k:row[k] for k in ('id','parent_job_id','attempt_no','status','created_at','started_at','ended_at','error')}
        result['analysis_kind']=json.loads(row['source_json']).get('analysis_kind','report')
        result['archived']=False
        if row['parent_job_id']:
            with self.app.db() as conn:
                parent=conn.execute('SELECT status FROM jobs WHERE id=?',(row['parent_job_id'],)).fetchone()
            result['archived']=bool(parent and parent['status'] in {'ARCHIVING','ARCHIVED'})
        work=self.app.STATE_ROOT/'report_work'/row['id']
        try:result['progress']=json.loads((work/'progress.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):result['progress']=None
        return result

    def summary(self,row,compact=False):
        from analysis.repertoire.io import safe_file
        if row['status']!='succeeded':raise HTTPException(409,'汇总尚未生成')
        try:
            name='overview.json' if compact and (Path(row['output_root'])/'overview.json').is_file() else 'summary.json'
            path=safe_file(Path(row['output_root'])/name,row['output_root'])
        except (ValueError,OSError):raise HTTPException(409,'汇总文件已归档或不可用，请下载任务归档包或重新生成') from None
        return json.loads(path.read_text(encoding='utf-8'))

    def artifact(self,row,name):
        from analysis.repertoire.io import safe_file,version
        root=Path(row['output_root'])
        manifest=json.loads(safe_file(root/'manifest.json',root).read_text(encoding='utf-8'))
        path=safe_file(root/name,root)
        expected=manifest.get('file_versions',{}).get(name)
        if expected is not None and version(path)!=expected:
            raise HTTPException(409,'导出文件已变化，请重新生成分析')
        return path

    def start(self):
        self.stopping.clear()
        with self.app.db() as conn:
            for row in conn.execute("SELECT * FROM report_runs WHERE status IN ('running','cancelling')").fetchall():
                if not row['pid'] or group_state(row['pid'],row['process_token'])=='gone':
                    conn.execute("UPDATE report_runs SET status='interrupted',pid=NULL,process_token=NULL,error='服务重启，分析中断，可重试' WHERE id=?",(row['id'],))
                else:
                    conn.execute("UPDATE report_runs SET status='interrupted',error='旧进程仍存在，保留运行记录等待退出' WHERE id=?",(row['id'],))
        self.thread=threading.Thread(target=self.loop,daemon=True,name='repertoire-reports')
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:self.thread.join(timeout=10)

    def loop(self):
        while not self.stopping.is_set():
            try:self.deliver()
            except Exception:LOG.exception('Report dispatch failed')
            self.stopping.wait(1)

    def deliver(self):
        from analysis.repertoire.io import write_json,safe_file
        from analysis.repertoire.sources import discover,verify
        # An unverified persisted process blocks dispatch until it is known gone.
        with self.app.db() as conn:
            live=conn.execute('SELECT * FROM report_runs WHERE pid IS NOT NULL').fetchall()
            for old in live:
                if group_state(old['pid'],old['process_token'])=='gone':
                    conn.execute('UPDATE report_runs SET pid=NULL,process_token=NULL WHERE id=?',(old['id'],))
                else:return
            if self.app.TREEMAP.primary_pending(conn):return
            row=conn.execute("SELECT * FROM report_runs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row:return
        lock=None
        if os.name=='posix':
            import fcntl
            path=Path(os.environ.get('SCIGBLAST_RESOURCE_LOCK_FILE','/colddata/SCigblast/results/.scigblast-resource.lock'))
            path.parent.mkdir(parents=True,exist_ok=True)
            lock=path.open('a')
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close();return
        process=None
        token=uuid.uuid4().hex
        status,error='failed',None
        try:
            with self.app.PROCESS_LOCK,self.app.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current=conn.execute('SELECT * FROM report_runs WHERE id=?',(row['id'],)).fetchone()
                if current['status']!='queued' or self.app.TREEMAP.primary_pending(conn):return
                if row['parent_job_id']:
                    parent=conn.execute('SELECT * FROM jobs WHERE id=?',(row['parent_job_id'],)).fetchone()
                    if not parent or parent['status']!='SUCCEEDED' or parent['attempt_no']!=row['attempt_no']:
                        conn.execute("UPDATE report_runs SET status='cancelled',error='来源任务已变化',ended_at=? WHERE id=?",(self.app.now(),row['id']));return
                conn.execute("UPDATE report_runs SET status='running',started_at=? WHERE id=?",(self.app.now(),row['id']))
            source=json.loads(row['source_json'])
            items=source.get('items') or discover(source['input_root'])
            verify(items,source['input_root'])
            folder=Path(row['output_root'])
            if not self.app.is_under(folder,[self.app.DEFAULT_OUTPUT_ROOT]):raise ValueError('分析输出超出允许目录')
            if any(p.is_symlink() for p in (folder,*folder.parents)):raise ValueError('输出路径含符号链接')
            work=self.app.STATE_ROOT/'report_work'/row['id']
            work.mkdir(parents=True,exist_ok=True)
            request=dict(source,items=items,primer=json.loads(row['config_json']) or None,run_id=row['id'],progress_path=str(work/'progress.json'))
            request['performance_path']=str(work/'performance.json')
            # Keep rename-based checkpoints on the result filesystem. STATE_ROOT
            # may be a different Docker bind mount and cannot be atomically renamed.
            request['checkpoint_root']=str(folder.parent/'.report_checkpoints'/row['id'])
            request['task_name']=parent['dataset'] if row['parent_job_id'] else source.get('task_name') or Path(source['input_root']).name
            # Trusted server configuration, never a browser-supplied file allowlist.
            request['allowed_input_roots']=[str(p) for p in self.app.configured_roots('SCIGBLAST_ALLOWED_INPUT_ROOTS','/colddata/SCigblast/data')]
            write_json(work/'request.json',request)
            from mapping.pipeline.resources import detect_resources
            available=detect_resources()['memory_available_bytes']
            budget=max(256,int(os.environ.get('SCIGBLAST_REPORT_MAX_RSS_MB','2048')))*1024**2
            if available is not None:
                budget=min(budget,available-max(256*1024**2,int(available*.05)))
            if budget<256*1024**2:raise ValueError('当前可用内存不足，请稍后重试后续分析')
            env={k:os.environ[k] for k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','LANG','LC_ALL','HOME','USERPROFILE') if k in os.environ}
            env.update(PYTHONPATH=str(self.app.PIPELINE_ROOT),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',
                PYTHONIOENCODING='utf-8',MPLCONFIGDIR=str(work/'matplotlib'),
                SCIGBLAST_WEB_PROCESS_TOKEN=token,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
            env['SCIGBLAST_REPORT_MEMORY_BUDGET_MB']=str(int(budget/1024**2))
            workers=os.environ.get('SCIGBLAST_REPORT_FIGURE_WORKERS','auto')
            env['SCIGBLAST_REPORT_FIGURE_WORKERS']='auto' if workers=='auto' else str(max(1,min(2,int(workers))))
            env['SCIGBLAST_REPORT_FIGURE_WORKER_MB']=str(max(256,int(os.environ.get('SCIGBLAST_REPORT_FIGURE_WORKER_MB','640'))))
            compute=os.environ.get('SCIGBLAST_REPORT_COMPUTE_WORKERS','1')
            env['SCIGBLAST_REPORT_COMPUTE_WORKERS']='auto' if compute=='auto' else str(max(1,min(2,int(compute))))
            env['SCIGBLAST_REPORT_COMPUTE_WORKER_MB']=str(max(256,int(os.environ.get('SCIGBLAST_REPORT_COMPUTE_WORKER_MB','640'))))
            env['SCIGBLAST_REPORT_STATISTICS_CACHE_MB']=str(max(0,int(os.environ.get('SCIGBLAST_REPORT_STATISTICS_CACHE_MB','0'))))
            with (work/'analysis.log').open('ab') as log:
                process=subprocess.Popen([sys.executable,'-m','analysis.repertoire.run','--request',str(work/'request.json'),'--output',str(folder)],
                    cwd=self.app.PIPELINE_ROOT,env=env,stdout=log,stderr=log,start_new_session=True,
                    **({'pass_fds':(lock.fileno(),)} if lock else {}))
            with self.app.db() as conn:
                conn.execute('UPDATE report_runs SET pid=?,process_token=? WHERE id=?',(process.pid,token,row['id']))
            deadline=time.monotonic()+max(60,int(os.environ.get('SCIGBLAST_REPORT_TIMEOUT_SECONDS','14400')))
            peak_rss=0
            while process.poll() is None:
                with self.app.db() as conn:
                    current=conn.execute('SELECT status FROM report_runs WHERE id=?',(row['id'],)).fetchone()
                if self.stopping.is_set() or current['status']=='cancelling':
                    status='cancelled';raise ValueError('分析已取消或服务停止')
                if time.monotonic()>deadline:raise ValueError('分析超过时长预算，可缩小样本范围重试')
                if os.name=='posix':
                    from postanalysis import group_rss
                    rss=group_rss(process.pid);peak_rss=max(peak_rss,rss)
                    if rss>budget:raise ValueError('分析超过内存预算，可缩小样本范围重试')
                self.stopping.wait(.25)
            write_json(work/'resource_usage.json',dict(peak_process_tree_rss_bytes=peak_rss,memory_budget_bytes=budget))
            if process.returncode or not (folder/'manifest.json').is_file():
                raise ValueError('分析未完成，请查看分析日志并重试')
            verify(items,source['input_root'])
            status='succeeded'
        except Exception as exc:
            error=str(exc)
            LOG.warning('Report %s: %s',row['id'],error)
        finally:
            gone=True
            if process is not None:
                try:self.app.TREEMAP.stop_group(process,token)
                except Exception:
                    gone=False;status='interrupted';error='无法确认进程已退出，保留进程记录'
            if lock:lock.close()
            with self.app.db() as conn:
                conn.execute('UPDATE report_runs SET status=?,error=?,ended_at=?'+(',pid=NULL,process_token=NULL' if gone else '')+' WHERE id=? AND status IN (\'running\',\'cancelling\')',
                    (status,error,self.app.now(),row['id']))


def router(service):
    router=APIRouter(prefix='/api/reports')

    @router.post('/upload')
    async def upload(request:Request,filename:str='primers.xlsx',header_row:int=Query(default=1,ge=1,le=500000)):
        from analysis.repertoire.catalog import workbook,workbook_preview
        suffix=Path(filename).suffix.lower()
        if suffix not in {'.xls','.xlsx'}:raise HTTPException(400,'仅支持 XLS/XLSX')
        token=uuid.uuid4().hex
        folder=service.app.STATE_ROOT/'report_uploads'/auth.require_user()['id']/token
        folder.mkdir(parents=True,exist_ok=False)
        path=folder/('workbook'+suffix)
        try:
            total=0
            with path.open('wb') as handle:
                async for chunk in request.stream():
                    total+=len(chunk)
                    if total>20*1024**2:raise HTTPException(413,'文件超过 20 MiB')
                    handle.write(chunk)
            book=workbook(path)
            with service.app.db() as conn:
                conn.execute('INSERT INTO report_uploads VALUES(?,?,?,?,?)',(token,auth.require_user()['id'],str(path),Path(filename).name,service.app.now()))
            return dict(id=token,**workbook_preview(book,header_row))
        except Exception as error:
            path.unlink(missing_ok=True)
            if isinstance(error,HTTPException):raise
            raise HTTPException(400,str(error)) from None

    @router.get('/workbook/{token}')
    def inspect(token:str,sheet:str='',header_row:int=Query(default=1,ge=1,le=500000)):
        from analysis.repertoire.catalog import workbook,workbook_preview
        try:
            book=workbook(service.upload_path(token),sheet or None)
            return workbook_preview(book,header_row)
        except (OSError,ValueError,KeyError) as error:raise HTTPException(400,str(error)) from None

    @router.post('/scan')
    def scan(body:Scan):return service.scan(body)

    @router.post('/primer-options')
    def primer_options(body:dict):
        from analysis.repertoire.catalog import build_catalog
        config=service.config(dict(body,submission_id=None))
        if not config:return dict(groups=[],primers=[])
        try:
            catalog=build_catalog(None,config['primer_path'],config['columns'],[])
            return dict(groups=sorted(catalog['groups']),primers=sorted({item['name'] for items in catalog['groups'].values() for item in items}))
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise HTTPException(400,str(error)) from None

    @router.get('/plans/{job_id}/samples')
    def plan_samples(job_id:str):
        parent=service.app.TREEMAP.owned_job(job_id)
        scope=service.sample_chains(parent)
        return dict(samples=sorted(scope),items=[dict(sample=sample,chain=chain) for sample,chains in scope.items() for chain in chains])

    @router.post('/preview')
    def preview(body:Create):return service.preview(body)

    @router.post('/primer-checks')
    def primer_check(body:Create):return service.create(body,primer_only=True)

    @router.post('')
    def create(body:Create):return service.create(body)

    @router.put('/plans/{job_id}')
    def plan(job_id:str,body:dict):
        parent=service.app.TREEMAP.owned_job(job_id)
        if parent['pipeline'] not in {'pig_igblast','igblast_base'}:raise HTTPException(400,'该流程暂不支持引物计划')
        config=service.config(body)
        from analysis.repertoire.catalog import workbook
        if config:
            for name in ('submission_path','primer_path'):
                if config.get(name):workbook(config[name])
            preview_plan(job_id,body)
        with service.app.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=conn.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if current['status'] in {'ARCHIVING','ARCHIVED','DELETING'}:raise HTTPException(409,'任务正在归档或删除')
            conn.execute('INSERT OR REPLACE INTO report_plans VALUES(?,?,?)',(job_id,parent['owner_id'],json.dumps(config)))
            enqueue_auto(conn,job_id,service.app.now())
        return dict(saved=True)

    @router.post('/plans/{job_id}/preview')
    def preview_plan(job_id:str,body:dict):
        from analysis.repertoire.catalog import build_catalog,column,value,workbook
        parent=service.app.TREEMAP.owned_job(job_id)
        config=service.config(body)
        if not config:return dict(assignments={})
        try:
            scope=service.sample_chains(parent)
            if scope:
                samples=sorted(scope)
            elif config.get('submission_path'):
                book=workbook(config['submission_path'],config['columns'].get('submission_sheet'))
                index=column(config['columns'].get('sample_column','B'))
                samples=sorted({value(book,r,index) for r in range(int(config['columns'].get('submission_header_row',1)),len(book['rows'])) if value(book,r,index)})
            else:
                raise ValueError('请先完成 Match，再按样本和链分配引物')
            return build_catalog(config.get('submission_path'),config['primer_path'],config['columns'],samples,scope)
        except (OSError,ValueError,KeyError) as error:raise HTTPException(400,str(error)) from None

    @router.get('')
    def runs(job_id:str=''):
        user=auth.require_user();where=["COALESCE(json_extract(source_json,'$.analysis_kind'),'report')='report'"];args=[]
        if job_id:service.app.TREEMAP.owned_job(job_id);where.append('parent_job_id=?');args.append(job_id)
        if user['role']!='admin':where.append('owner_id=?');args.append(user['id'])
        with service.app.db() as conn:
            rows=conn.execute('SELECT * FROM report_runs'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY created_at DESC,id DESC LIMIT 100',args).fetchall()
        return [service.public(row) for row in rows]

    @router.get('/{run_id}')
    def detail(run_id:str):return service.public(service.owned(run_id))

    @router.get('/{run_id}/summary')
    def summary(run_id:str,compact:bool=False):return service.summary(service.owned(run_id),compact=compact)

    @router.get('/{run_id}/usage')
    def usage(run_id:str,group_id:str,primer:str='',segment:str='V',metric:str='expression',aa_field:str='cdr3_aa',zero_only:bool=False,observed_only:bool=False,reference_only:bool=False,observation_state:str='',
              offset:int=Query(default=0,ge=0),limit:int=Query(default=25,ge=1,le=100),format:str='json',gene_query:str=Query(default='',max_length=120),definite_only:bool=False):
        from analysis.repertoire.io import safe_file
        from analysis.repertoire.usage import connect,rows,USAGE_COLUMNS
        row=service.owned(run_id)
        if row['status']!='succeeded':raise HTTPException(409,'分析尚未完成')
        if segment not in {'V','J','C'} or format not in {'json','csv'}:raise HTTPException(400,'未知基因区或输出格式')
        if metric not in {'expression','unique_cdr3'} or aa_field not in {'cdr3_aa','junction_aa'}:raise HTTPException(400,'未知基因统计指标或 AA 字段')
        if metric=='unique_cdr3' and aa_field!='cdr3_aa':raise HTTPException(400,'暂不提供 Unique Junction 指标')
        if observation_state not in {'','zero_observed','ambiguous_only','outside_reference'}:raise HTTPException(400,'未知参考观察状态')
        if observed_only and (zero_only or observation_state):raise HTTPException(400,'已匹配分布与参考观察清单须分别查询')
        index=safe_file(Path(row['output_root'])/'details.sqlite',row['output_root'])
        with closing(connect(index)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='gene_usage'").fetchone():raise HTTPException(409,'该历史分析尚无 V/J/C 汇总，请重新生成')
            if not conn.execute('SELECT 1 FROM usage_primers WHERE group_id=? AND primer=?',(group_id,primer)).fetchone():raise HTTPException(404,'当前样本或引物不存在')
            modern='metric' in {r[1] for r in conn.execute('PRAGMA table_info(gene_usage)')}
            if not modern and metric!='expression':raise HTTPException(409,'该历史分析尚无 Unique CDR3 基因统计，请重新生成后续分析')
            condition='group_id=? AND primer=? AND segment=?'+(" AND status='zero_observed'" if zero_only else '')+(' AND unique_count>0' if observed_only else '')+(' AND reference_catalog=1' if reference_only else '')+(' AND status=?' if observation_state else '')
            args=[group_id,primer,segment]+([observation_state] if observation_state else [])
            if definite_only:condition+=" AND (unique_count>0 OR (ambiguous_count=0 AND status='zero_observed'))"
            if gene_query.strip():condition+=' AND instr(lower(gene),lower(?))>0';args.append(gene_query.strip())
            if modern:
                condition+=' AND metric=?';args.append(metric)
                if metric=='unique_cdr3':condition+=' AND aa_field=?';args.append(aa_field)
            total=conn.execute('SELECT count(*) FROM gene_usage WHERE '+condition,args).fetchone()[0]
            from analysis.repertoire.api_summary import summary as query_summary
            summary=query_summary(row['owner_id'],index,group_id,primer,segment,metric,aa_field,modern)
            if format=='json':
                result=list(rows(conn,group_id=group_id,primer=primer,segment=segment,metric=metric,aa_field=aa_field,zero_only=zero_only,observed_only=observed_only,reference_only=reference_only,definite_only=definite_only,observation_state=observation_state,gene_query=gene_query,offset=offset,limit=limit))
                return dict(rows=result,total=total,offset=offset,limit=limit,has_more=offset+len(result)<total,summary=summary)
        def stream():
            buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=USAGE_COLUMNS,lineterminator='\n')
            writer.writeheader();yield buffer.getvalue();buffer.seek(0);buffer.truncate(0)
            with closing(connect(index)) as conn:
                for record in rows(conn,group_id=group_id,primer=primer,segment=segment,metric=metric,aa_field=aa_field,zero_only=zero_only,observed_only=observed_only,reference_only=reference_only,definite_only=definite_only,observation_state=observation_state,gene_query=gene_query):
                    writer.writerow({k:"'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for k,v in record.items()})
                    yield buffer.getvalue();buffer.seek(0);buffer.truncate(0)
        filename=f"gene_usage_{segment}_{metric}"+('_'+aa_field if metric=='unique_cdr3' else '')+'.csv'
        return StreamingResponse(stream(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{filename}"'})

    @router.get('/{run_id}/gene-candidates')
    def gene_candidates(run_id:str,group_id:str,primer:str='',segment:str='',
                        offset:int=Query(default=0,ge=0),limit:int=Query(default=25,ge=1,le=100)):
        from analysis.repertoire.io import safe_file
        from analysis.repertoire.usage import connect
        row=service.owned(run_id)
        if row['status']!='succeeded':raise HTTPException(409,'分析尚未完成')
        if segment not in {'','V','D','J','C'}:raise HTTPException(400,'未知基因区')
        index=safe_file(Path(row['output_root'])/'details.sqlite',row['output_root'])
        with closing(connect(index)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='candidate_gene_summary'").fetchone():
                raise HTTPException(409,'该历史报告未整理候选基因，请重新生成分析；旧 mapping 不能补回第二候选')
            if not conn.execute('SELECT 1 FROM usage_groups WHERE id=?',(group_id,)).fetchone():raise HTTPException(404,'样本不存在')
            conditions=['group_id=?'];args=[group_id]
            if primer:conditions.append('primer=?');args.append(primer)
            if segment:conditions.append('segment=?');args.append(segment)
            where=' AND '.join(conditions)
            total=conn.execute('SELECT count(*) FROM candidate_gene_summary WHERE '+where,args).fetchone()[0]
            result=[dict(r) for r in conn.execute('SELECT * FROM candidate_gene_summary WHERE '+where+
                ' ORDER BY segment,count DESC,primer,call LIMIT ? OFFSET ?',[*args,limit,offset])]
        return dict(rows=result,total=total,offset=offset,limit=limit,has_more=offset+len(result)<total)

    @router.post('/{run_id}/cancel')
    def cancel(run_id:str):
        row=service.owned(run_id)
        with service.app.db() as conn:
            conn.execute("UPDATE report_runs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END WHERE id=? AND status IN ('queued','running')",(run_id,))
        return service.public(service.owned(run_id))

    @router.post('/{run_id}/retry')
    def retry(run_id:str):
        row=service.owned(run_id)
        if row['parent_job_id']:
            parent=service.app.TREEMAP.owned_job(row['parent_job_id'])
            if parent['status']!='SUCCEEDED' or parent['attempt_no']!=row['attempt_no']:raise HTTPException(409,'来源任务已变化，请重新扫描')
        with service.app.db() as conn:
            # New folder ensures that a cancelled/failed publish is never reused.
            folder=str(Path(row['output_root']).with_name(uuid.uuid4().hex[:12]))
            conn.execute("UPDATE report_runs SET status='queued',output_root=?,error=NULL,started_at=NULL,ended_at=NULL WHERE id=? AND pid IS NULL AND status IN ('failed','interrupted','cancelled')",(folder,run_id))
        return service.public(service.owned(run_id))

    @router.post('/{run_id}/figures')
    def figures(run_id:str,body:dict):
        from analysis.repertoire.io import digest,safe_file,version
        row=service.owned(run_id);summary=service.summary(row,compact=True)
        primer=str(body.get('primer',''))
        samples={name for name,values in (summary.get('catalog') or {}).get('assignments',{}).items() if any(p['name']==primer for p in values)}
        samples.update(g['sample'] for g in (summary.get('usage') or {}).get('groups',[]) if primer in g['primers'])
        if not samples:raise HTTPException(400,'请选择一个具体引物名称')
        source=json.loads(row['source_json'])
        if summary.get('usage'):
            folder=Path(row['output_root'])
            manifest=json.loads(safe_file(folder/'manifest.json',folder).read_text(encoding='utf-8'))
            index_path=safe_file(folder/'details.sqlite',folder)
            if version(index_path)!=manifest.get('statistics_version'):
                raise HTTPException(409,'统计索引已变化，请重新生成分析')
            summary_path=service.artifact(row,'summary.json')
            source['statistics_source']=dict(root=str(folder),
                summary_version=version(summary_path),index_version=version(index_path))
            source['figure_primers']=[primer]
            source['figure_groups']=[g['group_id'] for g in summary['usage']['groups']
                if primer in g['primers'] and (not body.get('sample') or g['sample']==body['sample']) and (not body.get('chain') or g['chain']==body['chain'])]
            if not source['figure_groups']:raise HTTPException(400,'当前筛选下没有该引物的样本链结果')
        if not source.get('items'):
            from analysis.repertoire.sources import discover
            source['items']=discover(source['input_root'])
        source['items']=[item for item in source['items'] if item['sample_label'] in samples and (not body.get('sample') or item['sample_label']==body['sample']) and (not body.get('chain') or item['chain']==body['chain'])]
        if not source['items']:raise HTTPException(400,'当前筛选下没有该引物的样本链结果')
        config=json.loads(row['config_json'])
        if config:config['columns']['figure_primers']=[primer]
        else:source['figure_primers']=[primer]
        from analysis.repertoire import VERSION,FIGURE_VERSION
        from analysis.repertoire.workbook import VERSION as WORKBOOK_VERSION
        new_id=uuid.uuid4().hex
        key=digest(['primer-figures',VERSION,FIGURE_VERSION,WORKBOOK_VERSION,row['id'],source,config])
        with service.app.PROCESS_LOCK,service.app.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if row['parent_job_id']:
                parent=conn.execute('SELECT status,attempt_no FROM jobs WHERE id=?',(row['parent_job_id'],)).fetchone()
                if not parent or parent['status']!='SUCCEEDED' or parent['attempt_no']!=row['attempt_no']:raise HTTPException(409,'来源任务已变化，请重新扫描')
            output=str(Path(row['output_root']).with_name(new_id[:12]))
            conn.execute('INSERT OR IGNORE INTO report_runs(id,owner_id,parent_job_id,attempt_no,source_json,config_json,output_root,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (new_id,row['owner_id'],row['parent_job_id'],row['attempt_no'],json.dumps(source),json.dumps(config),output,key,service.app.now()))
            existing=conn.execute('SELECT id FROM report_runs WHERE owner_id=? AND dedupe_key=?',(row['owner_id'],key)).fetchone()[0]
        return service.public(service.owned(existing))

    @router.get('/{run_id}/file')
    def file(run_id:str,name:str,download:bool=False):
        from analysis.repertoire.io import safe_file
        row=service.owned(run_id)
        if name=='analysis.log':
            path=safe_file(service.app.STATE_ROOT/'report_work'/run_id/'analysis.log',service.app.STATE_ROOT/'report_work'/run_id)
            return file_response(path,filename='analysis.log')
        data=service.summary(row,compact=True)
        from analysis.repertoire import TABLE_PATHS
        allowed={'report.html','report.xlsx','stage_summary.csv','sample_summary.csv','primer_counts.csv','primer_gene_counts.csv','gene_coverage.csv','matching_summary.csv','sequence_family_matching.csv','filter_summary.csv','primer_preparation_summary.csv','manifest.json'}
        allowed.update(TABLE_PATHS.values())
        allowed.update(data.get('exports',{}).values())
        allowed.update(name+'.csv' for name in TABLE_PATHS)
        allowed.update(figure['png'] for figure in data['figures'])
        if name not in allowed:raise HTTPException(404,'文件不存在')
        name=data.get('exports',{}).get(Path(name).stem,name) if '/' not in name and name.endswith('.csv') else name
        path=service.artifact(row,name)
        response=file_response(path,download=download)
        if name=='report.html':
            from analysis.repertoire.report_html import response_headers
            response.headers.update(response_headers(download=download))
        return response

    @router.get('/{run_id}/report.html')
    def html_report(run_id:str,download:bool=True):
        from analysis.repertoire import report_html
        from analysis.repertoire.io import safe_file
        row=service.owned(run_id)
        data=service.summary(row,compact=True)
        if data.get('analysis_kind')=='primer_check':
            def check_content():
                with service.read_lease(row):
                    with service.artifact(row,'report.html').open('rb') as handle:
                        while chunk:=handle.read(128*1024):yield chunk
            incoming=check_content()
            try:first=next(incoming)
            except (OSError,ValueError,StopIteration) as error:
                incoming.close()
                raise HTTPException(409,'引物匹配报告不可用，请重新运行预检查') from error
            def check_body():
                try:
                    yield first
                    yield from incoming
                finally:incoming.close()
            headers=report_html.response_headers(download=download)
            headers['Content-Disposition']=('attachment' if download else 'inline')+'; filename="primer_matching_report.html"'
            return StreamingResponse(check_body(),media_type='text/html',headers=headers,background=BackgroundTask(incoming.close))
        root=Path(row['output_root'])
        try:
            manifest=json.loads(safe_file(root/'manifest.json',root).read_text(encoding='utf-8'))
            files=set(manifest['files'])
            if (data.get('html_report',{}).get('version')==report_html.VERSION
                and data.get('html_report',{}).get('viewer_signature')==report_html.viewer_signature() and 'report.html' in files):
                response=file_response(service.artifact(row,'report.html'),filename='SCIGBLAST_report.html',download=download)
                response.headers.update(report_html.response_headers(download=download))
                return response
            def resolve(name):
                if name not in files:raise ValueError('报告引用了未登记的文件')
                return service.artifact(row,name)
            assets=report_html.prepare(data,root,resolve=resolve)
        except (OSError,ValueError,KeyError) as error:
            raise HTTPException(409,'HTML 报告材料不可用，请恢复归档或重新生成分析') from error
        # Old reports are exported from their existing counts, without modifying
        # the report directory, manifest or private SQLite index.
        def stream():
            from analysis.repertoire import html_cache
            with service.read_lease(row):
                cache=service.app.STATE_ROOT/'report_html_cache'/row['owner_id']/row['id']
                yield from html_cache.stream(data,manifest,resolve,cache,run_id=run_id,
                    budget_mb=int(os.environ.get('SCIGBLAST_REPORT_HTML_CACHE_MB','128')),assets=assets)
        incoming=stream()
        try:first=next(incoming)
        except (OSError,ValueError,KeyError,StopIteration) as error:
            incoming.close()
            raise HTTPException(409,'HTML 报告材料不可用，请恢复归档或重新生成分析') from error
        def body():
            try:
                yield first
                yield from incoming
            finally:incoming.close()
        # Start/validate the source lease before headers; close even if a client
        # disconnects before consuming the first body chunk.
        return StreamingResponse(body(),media_type='text/html',
            headers=report_html.response_headers(download=download),background=BackgroundTask(incoming.close))

    @router.get('/{run_id}/download')
    def download(run_id:str,images_only:bool=False,package:str='full'):
        from analysis.repertoire.io import safe_file
        row=service.owned(run_id);data=service.summary(row,compact=True)
        manifest=json.loads(safe_file(Path(row['output_root'])/'manifest.json',row['output_root']).read_text(encoding='utf-8'))
        if package not in {'full','check'}:raise HTTPException(400,'不支持的下载类型')
        if package=='check':
            from analysis.repertoire import report_html,html_cache
            from artifact_download import stream_zip_members
            root=Path(row['output_root']);listed=set(manifest['files'])
            def resolve(name):
                if name not in listed:raise ValueError('报告引用了未登记的文件')
                return service.artifact(row,name)
            try:
                assets=report_html.prepare(data,root,resolve=resolve)
                workbook=resolve('report.xlsx')
            except (OSError,ValueError,KeyError) as error:raise HTTPException(409,'报告材料不可用') from error
            def check_stream():
                with service.read_lease(row):
                    incoming=html_cache.stream(data,manifest,resolve,service.app.STATE_ROOT/'report_html_cache'/row['owner_id']/row['id'],
                        run_id=run_id,budget_mb=int(os.environ.get('SCIGBLAST_REPORT_HTML_CACHE_MB','128')),assets=assets)
                    try:
                        first=next(incoming)
                        def html():
                            yield first;yield from incoming
                        def excel():
                            with workbook.open('rb') as handle:
                                while block:=handle.read(128*1024):yield block
                        yield from stream_zip_members([('report.html',html()),('report.xlsx',excel())])
                    finally:incoming.close()
            incoming=check_stream()
            try:first=next(incoming)
            except (OSError,ValueError,KeyError,StopIteration) as error:
                incoming.close();raise HTTPException(409,'无法生成实验核查包') from error
            def body():
                try:yield first;yield from incoming
                finally:incoming.close()
            return StreamingResponse(body(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="report_{run_id}_check.zip"'},background=BackgroundTask(incoming.close))
        names=[name for name in manifest['files'] if name.endswith('.png')] if images_only else [*manifest['files'],'manifest.json']
        files=[(service.artifact(row,name),name) for name in names]
        filename=f"report_{run_id}"+('_png' if images_only else '')+'.zip'
        return StreamingResponse(stream_zip(files),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{filename}"'})

    @router.get('/{run_id}/records')
    def records(run_id:str,sample_key:str='',sample:str='',chain:str='',primer:str='',gene:str='',segment:str='C',match_class:str='',reason:str='',trim_state:str='',trim_group:str='',after:int=0,limit:int=50,group_id:str=''):
        from analysis.repertoire.io import safe_file
        row=service.owned(run_id)
        if row['status']!='succeeded':raise HTTPException(409,'分析尚未完成')
        index=safe_file(Path(row['output_root'])/'details.sqlite',row['output_root'])
        where=['id>?'];args=[max(0,after)]
        for name,value in (('sample_key',sample_key),('sample',sample),('chain',chain),('match_class',match_class),('filter_reason',reason),('trim_state',trim_state)):
            if value:where.append(name+'=?');args.append(value)
        if group_id:
            where.append('EXISTS(SELECT 1 FROM usage_sources s WHERE s.group_id=? AND s.sample_key=observations.sample_key AND s.chain=observations.chain)');args.append(group_id)
        if trim_group=='trimmed':where.append("trim_state LIKE 'trimmed_%'")
        elif trim_group=='untrimmed':where.append("trim_state LIKE '%_untrimmed'")
        elif trim_group=='not_recorded':where.append("(trim_state IS NULL OR trim_state='not_recorded')")
        if primer:where.append('EXISTS(SELECT 1 FROM json_each(primers) WHERE value=?)');args.append(primer)
        if gene:
            if segment not in {'V','J','C'}:raise HTTPException(400,'未知基因区')
            where.append('gene_has('+segment.lower()+'_call,?)');args.append(gene)
        count=max(1,min(100,limit))
        with closing(sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            from analysis.repertoire.aggregate import calls
            conn.create_function('gene_has',2,lambda call,selected:int(selected in calls(call)))
            result=[dict(r) for r in conn.execute('SELECT id,sample_key,sample,pair,chain,scope,sequence_id,match_class,primers,mapping_primers,trim_state,trim_length,filter_reason,v_call,j_call,c_call FROM observations WHERE '+' AND '.join(where)+' ORDER BY id LIMIT ?',[*args,count+1])]
        return dict(rows=result[:count],has_more=len(result)>count,next_cursor=result[count-1]['id'] if len(result)>count else None)

    @router.get('/{run_id}/sequences')
    def sequences(run_id:str,sample:str='',chain:str='',primer:str='',gene:str='',segment:str='C',match_class:str='',reason:str='',trim_state:str='',trim_group:str='',format:str='fasta',group_id:str=''):
        from analysis.repertoire.io import safe_file
        from analysis.repertoire.aggregate import calls
        row=service.owned(run_id)
        if row['status']!='succeeded':raise HTTPException(409,'分析尚未完成')
        if format not in {'fasta','tsv'} or segment not in {'V','J','C'}:raise HTTPException(400,'未知导出格式或基因区')
        index=safe_file(Path(row['output_root'])/'details.sqlite',row['output_root'])
        where=[];args=[]
        for key,value in (('sample',sample),('chain',chain),('match_class',match_class),('filter_reason',reason),('trim_state',trim_state)):
            if value:where.append(key+'=?');args.append(value)
        if group_id:
            where.append('EXISTS(SELECT 1 FROM usage_sources s WHERE s.group_id=? AND s.sample_key=observations.sample_key AND s.chain=observations.chain)');args.append(group_id)
        if trim_group=='trimmed':where.append("trim_state LIKE 'trimmed_%'")
        elif trim_group=='untrimmed':where.append("trim_state LIKE '%_untrimmed'")
        elif trim_group=='not_recorded':where.append("(trim_state IS NULL OR trim_state='not_recorded')")
        if primer:where.append('EXISTS(SELECT 1 FROM json_each(primers) WHERE value=?)');args.append(primer)
        if gene:where.append('gene_has('+segment.lower()+'_call,?)');args.append(gene)
        def stream():
            with closing(sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)) as conn:
                conn.row_factory=sqlite3.Row
                conn.create_function('gene_has',2,lambda call,selected:int(selected in calls(call)))
                columns=['id','sample','pair','chain','scope','sequence_id','match_class','primers','mapping_primers','trim_state','trim_length','filter_reason','v_call','j_call','c_call','sequence']
                buffer=io.StringIO();writer=csv.writer(buffer,delimiter='\t',lineterminator='\n')
                if format=='tsv':writer.writerow(columns);yield buffer.getvalue();buffer.seek(0);buffer.truncate(0)
                for record in conn.execute('SELECT '+','.join(columns)+' FROM observations'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY id',args):
                    if format=='fasta':
                        if record['sequence']:
                            title=str(record['sequence_id']).replace('\n',' ').replace('\r',' ')[:500]
                            yield f">record_{record['id']} {title}\n{record['sequence']}\n"
                    else:
                        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in record])
                        yield buffer.getvalue();buffer.seek(0);buffer.truncate(0)
        return StreamingResponse(stream(),media_type='text/plain',headers={'Content-Disposition':f'attachment; filename="records_{run_id}.{format}"'})

    return router
