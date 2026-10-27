"""Treemap API/lifecycle contracts; only tiny synthetic AIRR files are rendered."""
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import smtplib
import subprocess
import threading
import time
import unittest
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import test_workflow as workflow
import postanalysis
import archive_results
import derived_store

web = workflow.web
HEADERS = {'X-SCIGBLAST-Request': '1'}


class TreemapTests(unittest.TestCase):
    def setUp(self):
        workflow.WorkflowTests.setUp(self)
        self.service = web.TREEMAP
        self.out.mkdir(exist_ok=True)
        self.patches.append(patch.object(self.service, 'stopping', threading.Event()))
        self.patches[-1].start()
        self.patches.append(patch.dict(os.environ, {
            'SCIGBLAST_RESOURCE_LOCK_FILE': str(self.root / 'treemap-resource.lock'),
            'SCIGBLAST_TREEMAP_TIMEOUT_SECONDS': '60',
            'SCIGBLAST_TREEMAP_MAX_WORKERS': '1',
        }))
        self.patches[-1].start()

    tearDown = workflow.WorkflowTests.tearDown

    def airr(self, directory=None, sample='A', chain='IGH', empty=False):
        directory = directory or self.raw
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{sample}__{chain}.tsv'
        columns = ['productive', 'v_call', 'j_call', 'junction_aa', 'locus', 'copy', 'umi_count']
        records = [('T', 'V1*01', 'J1*01', 'CAR', chain, 1000, 1000),
                   ('T', 'V1*01', 'J1*01', 'CAR', chain, 8000, 8000),
                   ('T', 'V2*01', 'J1*01', 'CAT', chain, 2000, 2000),
                   ('F', 'V1*01', 'J1*01', 'BAD', chain, 5, 5),
                   ('T', 'V1*01', 'J1*01', '', chain, 9, 9)]
        if empty:
            records = records[3:]
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, delimiter='\t')
            writer.writerow(columns)
            writer.writerows(records)
        return path

    def scan(self, client=None, **body):
        response = (client or self.client).post('/api/treemap/scan', json=body or {'input_path': str(self.raw)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_run(self, scan=None, selected=None, client=None):
        scan = scan or self.scan(client=client)
        response = (client or self.client).post('/api/treemap/runs', json={
            'scan_id': scan['id'],
            'selected_ids': selected if selected is not None else [r['id'] for r in scan['rows'] if r['valid']],
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_info(self, run_id, client=None):
        response = (client or self.client).get(f'/api/treemap/runs/{run_id}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def deliver(self, run_id, maximum=20):
        for _ in range(maximum):
            data = self.run_info(run_id)
            if data['status'] not in {'queued', 'running', 'cancelling'}:
                return data
            self.assertTrue(self.service.deliver_one(), data)
        self.fail('Treemap did not reach a terminal state within bounded work units')

    def job(self, enabled=False, split_stage=False):
        response = self.client.post('/api/jobs', json={
            'pipeline': 'igblast_base', 'operator': 'fixture', 'input_path': str(self.raw),
            'submission_revision': self.view['revision'], 'dataset_label': 'treemap-fixture',
            'generate_treemap': enabled,
        })
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()['id']
        if not split_stage:
            # Most lifecycle cases deliberately exercise existing five-stage
            # results; new six-stage outputs have their own end-to-end case.
            row = web.get_job_row(job_id)
            options = json.loads(row['options_json'])
            options.pop('locus_split_version', None)
            web.update_job(job_id, options_json=json.dumps(options))
        return job_id

    def job_results(self, job_id):
        row = web.get_job_row(job_id)
        stage = Path(row['output_root']) / '05.igblastn_out' / row['dataset']
        source = self.airr(stage / 'A')
        with (stage / 'chain_summary.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['sample_id', 'chain', 'status', 'output_path'])
            writer.writeheader()
            writer.writerow(dict(sample_id='A', chain='IGH', status='OK', output_path=str(source)))
        return source

    def other_client(self):
        with web.db() as connection:
            connection.execute('INSERT INTO users(id,username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',
                               ('other', 'other', 'Other', 'unused-fixture-hash', 'user', int(time.time())))
            connection.execute('INSERT INTO sessions VALUES(?,?,?)',
                               (web.auth.digest('treemap-other'), 'other', int(time.time()) + 3600))
        client = TestClient(web.app, headers=HEADERS)
        client.cookies.set(web.auth.COOKIE, 'treemap-other')
        self.addCleanup(client.close)
        return client

    def test_real_subprocess_selected_only_counts_records_and_preserves_inputs(self):
        source = self.airr()
        omitted = self.airr(sample='B', chain='TRB')
        omitted_chain = self.airr(chain='IGK')
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, omitted, omitted_chain)}
        scan = self.scan()
        selected = [r['id'] for r in scan['rows'] if r['chain'] == 'IGH']
        run = self.create_run(scan, selected)
        self.assertEqual((run['status'], run['metric'], run['progress']['total']), ('queued', 'records', 1))
        result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual((result['items'][0]['productive_count'], result['items'][0]['clone_count']), (3, 2))
        self.assertCountEqual([f['kind'] for f in result['files']], ['chain', 'clone_top', 'stats'])
        self.assertNotIn('clone_table',result['items'][0])
        with web.db() as connection:
            stored = connection.execute('SELECT * FROM derived_runs WHERE id=?', (run['id'],)).fetchone()
        self.assertFalse((Path(stored['output_root']) / 'multi_chain_treemaps').exists())
        for artifact in self.service.data(stored)['files']:
            if artifact['kind'] in {'chain', 'combined'}:
                expected = 'individual_treemaps' if artifact['kind'] == 'chain' else 'multi_chain_treemaps'
                self.assertEqual(Path(artifact['relative_path']).parts[0], expected)
                self.assertEqual(Path(artifact['relative_path']).name, 'A__IGH.png')
        for artifact in result['files']:
            response = self.client.get(artifact['url'])
            self.assertEqual(response.status_code, 200, response.text[:200] if artifact['kind'] == 'stats' else '')
            if artifact['kind'] in {'chain','combined'}:
                with Image.open(io.BytesIO(response.content)) as image:
                    self.assertEqual(image.size, (3000, 3000))
                    self.assertAlmostEqual(image.info['dpi'][0], 300, delta=1)
                    image.verify()
            elif artifact['kind']=='stats':
                rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                self.assertEqual((rows[0]['productive_count'], rows[0]['clone_count']), ('3', '2'))
            else:
                self.assertIn('attachment',response.headers['content-disposition'])
                rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                self.assertEqual([r['record_count'] for r in rows],['2','1'])
                self.assertEqual([r['rank'] for r in rows],['1','2'])
                self.assertEqual([r['cdr3_aa'] for r in rows],['CAR','CAT'])
                self.assertAlmostEqual(float(rows[0]['frequency_percent']),200/3)
                self.assertEqual(rows[-1]['cumulative_frequency_percent'],'100.00000000')
                self.assertEqual({r['chain'] for r in rows},{'IGH'})
                self.assertEqual({r['sample_key'] for r in rows},{result['items'][0]['sample_key']})
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
        self.assertEqual(self.client.get(f"/api/treemap/runs/{run['id']}/files/not-an-artifact").status_code, 404)
        with patch.object(postanalysis, 'version', side_effect=AssertionError('history must not stat every artifact')):
            history = self.client.get('/api/treemap/runs')
        self.assertEqual(history.status_code, 200)
        summary = history.json()['runs'][0]
        self.assertEqual(summary['progress'], {'done': 1, 'total': 1})
        self.assertEqual((summary['items'], summary['files']), ([], []))

    def test_final_mapping_records_partition_for_manual_and_auto_generation(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                job_id = self.job(enabled=automatic)
                source = self.job_results(job_id)
                other = self.airr(source.parent.parent / 'B', sample='B', chain='TRB')
                with (source.parent.parent / 'chain_summary.csv').open('a', encoding='utf-8', newline='') as handle:
                    csv.writer(handle).writerow(['B', 'TRB', 'OK', str(other)])
                with source.open('a', encoding='utf-8', newline='') as handle:
                    writer = csv.writer(handle, delimiter='\t')
                    writer.writerow(['T', 'FOREIGN', 'FOREIGN', 'OTHERCHAIN', 'TRB', 9999, 9999])
                    writer.writerow(['T', 'UNKNOWN', 'UNKNOWN', 'NOCHAIN', '', 9999, 9999])
                with other.open('a', encoding='utf-8', newline='') as handle:
                    csv.writer(handle, delimiter='\t').writerow(['T', 'FOREIGN', 'FOREIGN', 'OTHERCHAIN', 'IGH', 9999, 9999])
                if not automatic:
                    for path in (source, other):
                        path.write_text(path.read_text(encoding='utf-8').replace('junction_aa\tlocus\t', 'junction_aa\tchain\t'), encoding='utf-8')
                before = {path: path.read_bytes() for path in (source, other)}
                web.update_job(job_id, status='SUCCEEDED')
                if automatic:
                    with web.db() as connection:
                        run_id = connection.execute('SELECT id FROM derived_runs WHERE parent_job_id=?', (job_id,)).fetchone()[0]
                else:
                    run_id = self.create_run(self.scan(job_id=job_id))['id']
                with patch.object(self.service, 'work_unit', wraps=self.service.work_unit) as worker:
                    result = self.deliver(run_id)
                self.assertEqual(result['status'], 'succeeded', result)
                self.assertEqual({(item['sample_key'], item['chain']) for item in result['items']}, {('A', 'IGH'), ('B', 'TRB')})
                self.assertTrue(all(item['productive_count']==3 and item['clone_count']==2 for item in result['items']))
                requests = [call.args[1] for call in worker.call_args_list if call.args[1]['kind']=='chain']
                self.assertEqual(len(requests), 2)
                self.assertTrue(all(request['partition_by_chain'] for request in requests))
                self.assertNotIn('combined', {file['kind'] for file in result['files']})
                table = next(file for file in result['files'] if file['kind']=='clone_top')
                rows = list(csv.DictReader(io.StringIO(self.client.get(table['url']).content.decode('utf-8-sig'))))
                self.assertEqual([row['cdr3_aa'] for row in rows], ['CAR', 'CAT', 'CAR', 'CAT'])
                self.assertEqual([row['record_count'] for row in rows], ['2', '1', '2', '1'])
                self.assertEqual({(row['sample_key'], row['chain']) for row in rows}, {('A', 'IGH'), ('B', 'TRB')})
                self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_new_base_job_requires_locus_split_and_renders_its_output(self):
        import pipeline_locus_split
        job_id = self.job(enabled=True, split_stage=True)
        source = self.job_results(job_id)
        # An incidental chain column must not override the actual AIRR locus
        # used by the explicit pipeline split stage.
        text = source.read_text(encoding='utf-8').replace('locus\tcopy', 'locus\tchain\tcopy')
        lines = text.splitlines()
        source.write_text(lines[0]+'\n'+'\n'.join(line.replace('\tIGH\t','\tIGH\tTRB\t') for line in lines[1:])+'\n',encoding='utf-8')
        row = web.get_job_row(job_id)
        stage = Path(row['output_root'])/'06.locus_split'/row['dataset']
        web.update_job(job_id,status='SUCCEEDED')
        scan = self.scan(job_id=job_id)
        self.assertEqual(scan['valid_count'],0)
        self.assertIn('locus split',scan['rows'][0]['error'])
        self.assertTrue(pipeline_locus_split.run(source.parent.parent/'chain_summary.csv',stage))
        with source.open('a',encoding='utf-8') as handle:
            handle.write('T\tWRONG\tWRONG\tWRONG\tTRB\tTRB\t1\t1\n')
        # Changing stage 5 after splitting must not make Treemap read stage 5.
        scan = self.scan(job_id=job_id)
        self.assertEqual(scan['valid_count'],1)
        self.assertEqual(scan['rows'][0]['source_files'],['06.locus_split/'+row['dataset']+'/A/IGH.tsv'])
        with web.db() as connection:
            run_id = connection.execute('SELECT id FROM derived_runs WHERE parent_job_id=?',(job_id,)).fetchone()[0]
        result = self.deliver(run_id)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['items'][0]['productive_count'],3)
        self.assertEqual(result['items'][0]['clone_count'],2)

    def test_existing_run_retains_partition_policy_when_retried(self):
        source = self.airr()
        with source.open('a', encoding='utf-8', newline='') as handle:
            csv.writer(handle, delimiter='\t').writerow(['T', 'V3', 'J3', 'LEGACY', 'TRB', 1, 1])
        run = self.create_run()
        with web.db() as connection:
            row = connection.execute('SELECT * FROM derived_runs WHERE id=?', (run['id'],)).fetchone()
            data = derived_store.load(connection, row)
            data.pop('chain_partition_version')
            derived_store.save(connection, row, data)
        with patch.object(self.service, 'work_unit', wraps=self.service.work_unit) as worker:
            result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['items'][0]['productive_count'], 4)
        self.assertFalse(next(call.args[1] for call in worker.call_args_list if call.args[1]['kind']=='chain')['partition_by_chain'])

    def test_manual_generation_does_not_reuse_previous_partition_policy(self):
        self.airr()
        scan = self.scan()
        previous = self.create_run(scan)
        with web.db() as connection:
            snapshot = json.loads(connection.execute('SELECT data_json FROM treemap_scans WHERE id=?', (scan['id'],)).fetchone()[0])
            old_key = postanalysis.digest(dict(version=postanalysis.VERSION, clone_table_version=postanalysis.clone_table_version(),
                                              composition_version=postanalysis.COMPOSITION_VERSION,
                                              parent=snapshot['parent_job_id'], attempt=snapshot['attempt_no'],
                                              input_root=snapshot['input_root'], items=snapshot['items']))
            connection.execute("UPDATE derived_runs SET dedupe_key=?,status='succeeded' WHERE id=?", (old_key, previous['id']))
        current = self.create_run(scan)
        self.assertNotEqual(current['id'], previous['id'])
        self.assertEqual(self.create_run(scan)['id'], current['id'])

    def test_single_chain_v1_run_finishes_without_composition(self):
        self.airr()
        run = self.create_run()
        with web.db() as connection:
            row = connection.execute('SELECT * FROM derived_runs WHERE id=?', (run['id'],)).fetchone()
            data = derived_store.load(connection, row)
            connection.execute('UPDATE derived_runs SET storage_version=1,data_json=? WHERE id=?', (json.dumps(data), run['id']))
            derived_store.delete(connection, [run['id']])
        with patch.object(self.service, 'work_unit', wraps=self.service.work_unit) as worker:
            result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertNotIn('combined', [call.args[1]['kind'] for call in worker.call_args_list])
        self.assertCountEqual([file['kind'] for file in result['files']], ['chain', 'clone_top', 'stats'])
        with web.db() as connection:
            self.assertEqual(connection.execute('SELECT storage_version FROM derived_runs WHERE id=?', (run['id'],)).fetchone()[0], 1)

    def test_empty_second_chain_does_not_create_single_image_composite(self):
        self.airr(chain='IGH')
        self.airr(chain='IGK', empty=True)
        run = self.create_run()
        with patch.object(self.service, 'work_unit', wraps=self.service.work_unit) as worker:
            result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual({item['chain']: item['status'] for item in result['items']}, {'IGH': 'succeeded', 'IGK': 'empty'})
        self.assertNotIn('combined', [call.args[1]['kind'] for call in worker.call_args_list])
        self.assertCountEqual([file['kind'] for file in result['files']], ['chain', 'clone_top', 'stats'])

    def test_manual_generation_does_not_reuse_old_composition_policy(self):
        self.airr()
        scan = self.scan()
        legacy = self.create_run(scan)
        with web.db() as connection:
            snapshot = json.loads(connection.execute('SELECT data_json FROM treemap_scans WHERE id=?', (scan['id'],)).fetchone()[0])
            old_key = postanalysis.digest(dict(version=postanalysis.VERSION, clone_table_version=postanalysis.clone_table_version(),
                                              parent=snapshot['parent_job_id'], attempt=snapshot['attempt_no'],
                                              input_root=snapshot['input_root'], items=snapshot['items']))
            connection.execute("UPDATE derived_runs SET dedupe_key=?,status='succeeded' WHERE id=?", (old_key, legacy['id']))
        current = self.create_run(scan)
        self.assertNotEqual(current['id'], legacy['id'])
        self.assertEqual(self.create_run(scan)['id'], current['id'])
        self.assertEqual(self.run_info(legacy['id'])['status'], 'succeeded')

    def test_run_folders_use_beijing_time_and_avoid_database_and_disk_collisions(self):
        self.airr(chain='IGH'); self.airr(chain='IGK')
        for associated in (False, True):
            with self.subTest(associated=associated):
                if associated:
                    job_id = self.job()
                    source = self.job_results(job_id)
                    other = self.airr(source.parent, chain='IGK')
                    with (source.parent.parent/'chain_summary.csv').open('a',encoding='utf-8',newline='') as handle:
                        csv.writer(handle).writerow(['A','IGK','OK',str(other)])
                    web.update_job(job_id,status='SUCCEEDED')
                    scan = self.scan(job_id=job_id)
                else:
                    scan = self.scan()
                selected = {item['chain']:item['id'] for item in scan['rows'] if item['valid']}
                with patch.object(web,'now',return_value='2026-10-26T08:00:00+00:00'):
                    first = self.create_run(scan,[selected['IGH']])
                    self.assertEqual(self.create_run(scan,[selected['IGH']])['id'],first['id'])
                    second = self.create_run(scan,[selected['IGK']])
                    with web.db() as connection:
                        roots = {item['id']:Path(item['output_root']) for item in connection.execute('SELECT id,output_root FROM derived_runs')}
                    base = roots[first['id']].parent
                    (base/'20261026_160000_03').mkdir(parents=True)
                    third = self.create_run(scan,list(selected.values()))
                with web.db() as connection:
                    third_root = Path(connection.execute('SELECT output_root FROM derived_runs WHERE id=?',(third['id'],)).fetchone()[0])
                self.assertEqual(roots[first['id']].name,'20261026_160000')
                self.assertEqual(roots[second['id']],base/'20261026_160000_02')
                self.assertEqual(third_root,base/'20261026_160000_04')
                self.assertEqual(base.name,'treemap')
                if associated:
                    self.assertEqual(base,Path(web.get_job_row(job_id)['output_root'])/'treemap')
                else:
                    self.assertEqual(base.parent.parent,self.out)

    def test_auto_attempts_use_separate_flat_output_folders(self):
        job_id = self.job(enabled=True)
        with patch.object(web,'now',return_value='2026-10-26T08:00:00+00:00'):
            web.update_job(job_id,status='SUCCEEDED',attempt_no=1)
            web.update_job(job_id,status='FAILED')
            web.update_job(job_id,status='SUCCEEDED',attempt_no=2)
            web.update_job(job_id,status='SUCCEEDED')
        with web.db() as connection:
            rows = connection.execute('SELECT * FROM derived_runs WHERE parent_job_id=? ORDER BY attempt_no',(job_id,)).fetchall()
        self.assertEqual(len(rows),2)
        base = Path(web.get_job_row(job_id)['output_root'])/'treemap'
        self.assertEqual(Path(rows[0]['output_root']),base/'20261026_160000')
        self.assertEqual(Path(rows[1]['output_root']),base/'20261026_160000_02')
        self.assertEqual([row['attempt_no'] for row in rows],[1,2])

    def zip_run(self, *, parent=False, legacy_layout=False):
        if parent:
            job_id = self.job()
            self.job_results(job_id)
            web.update_job(job_id, status='SUCCEEDED')
            run = self.create_run(self.scan(job_id=job_id))
        else:
            self.airr(self.raw / 'one', sample='Same', chain='IGH')
            self.airr(self.raw / 'one', sample='Same', chain='IGK')
            self.airr(self.raw / 'two', sample='Same', chain='TRB')
            run = self.create_run()
        if legacy_layout:
            old_root = Path(web.get_job_row(job_id)['output_root'])/'analysis'/'treemap'/'attempt-0'/f"run-{run['id']}"
            with web.db() as connection:
                connection.execute('UPDATE derived_runs SET output_root=? WHERE id=?',(str(old_root),run['id']))
        def small_png(row, request):
            if request['kind']=='clone_merge':
                from analysis.treemap.clone_table import merge_clone_tables
                merge_clone_tables(request['tables'],request['target'])
                return {'path':request['target']}
            path = Path(request['target'])
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (4, 4), (50, 80, 120)).save(path)
            if request.get('clone_table_target'):
                from analysis.treemap.aggregate import load_clones
                from analysis.treemap.clone_table import write_clone_table
                clones,total=load_clones(request['sources'],request['chain'])
                write_clone_table(clones,total,request['clone_table_target'],sample_key=request['sample_key'],
                                  sample_label=request['sample_label'],chain=request['chain'])
            return {'productive_count': 3, 'clone_count': 2}
        with patch.object(self.service, 'work_unit', side_effect=small_png):
            result = self.deliver(run['id'])
        with web.db() as connection:
            row = connection.execute('SELECT * FROM derived_runs WHERE id=?', (run['id'],)).fetchone()
        return result, row, self.service.data(row)

    def test_legacy_run_folder_download_and_retry_stay_at_recorded_location(self):
        result,row,_ = self.zip_run(parent=True,legacy_layout=True)
        original = row['output_root']
        self.assertEqual(Path(original).parts[-4:],('analysis','treemap','attempt-0',f"run-{row['id']}"))
        self.assertEqual(self.client.get(next(file['url'] for file in result['files'] if file['kind']=='chain')).status_code,200)
        self.assertEqual(self.client.get(f"/api/treemap/runs/{row['id']}/download").status_code,200)
        with web.db() as connection:
            connection.execute("UPDATE derived_runs SET status='partial' WHERE id=?",(row['id'],))
        self.assertEqual(self.client.post(f"/api/treemap/runs/{row['id']}/retry",json={}).status_code,200)
        self.assertEqual(self.deliver(row['id'])['status'],'succeeded')
        with web.db() as connection:
            self.assertEqual(connection.execute('SELECT output_root FROM derived_runs WHERE id=?',(row['id'],)).fetchone()[0],original)

    def test_zip_all_and_sample_keep_image_bytes_and_collision_safe_directories(self):
        result, row, data = self.zip_run()
        endpoint = f"/api/treemap/runs/{row['id']}/download"
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else '')
        self.assertEqual(response.headers['content-type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 6)
            self.assertIn('treemap_summary.csv', archive.namelist())
            self.assertIn('clone_top.csv', archive.namelist())
            self.assertEqual({p.split('/')[0] for p in archive.namelist() if p.endswith('.png')},
                             {'individual_treemaps', 'multi_chain_treemaps'})
            # Equal sample labels in separate input directories must never collide.
            self.assertEqual(len({p.split('/')[1] for p in archive.namelist() if p.endswith('.png')}), 2)
            self.assertEqual({Path(p).name for p in archive.namelist() if p.endswith('.png')},
                             {'Same__IGH.png', 'Same__IGK.png', 'Same__TRB.png', 'Same.png'})
            for artifact in data['files']:
                if artifact['kind'] == 'stats':
                    name = 'treemap_summary.csv'
                elif artifact['kind']=='clone_top':
                    name = 'clone_top.csv'
                else:
                    label = next(i['sample_label'] for i in data['items'] if i['sample_key'] == artifact['sample_key'])
                    name = postanalysis.image_relative(artifact['sample_key'], label, artifact['kind'], artifact['chain']).as_posix()
                    expected = f"{label}__{artifact['chain']}.png" if artifact['kind']=='chain' else f'{label}.png'
                    self.assertEqual(Path(artifact['relative_path']).name,expected)
                self.assertEqual(archive.read(name), (Path(row['output_root']) / artifact['relative_path']).read_bytes())
        sample = next(item['sample_key'] for item in result['items'] if item['chain'] == 'IGH')
        selected = self.client.get(endpoint, params={'sample_key': sample})
        with zipfile.ZipFile(io.BytesIO(selected.content)) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            self.assertTrue(all(name.endswith('.png') for name in archive.namelist()))
        single_sample = next(item['sample_key'] for item in result['items'] if item['chain'] == 'TRB')
        single = self.client.get(endpoint, params={'sample_key': single_sample})
        with zipfile.ZipFile(io.BytesIO(single.content)) as archive:
            self.assertEqual(len(archive.namelist()), 1)
            self.assertTrue(archive.namelist()[0].startswith('individual_treemaps/'))
        self.assertEqual(len([file for file in result['files'] if file['kind'] == 'combined']), 1)
        self.assertEqual(self.client.get(endpoint, params={'sample_key': '../unknown'}).status_code, 404)
        self.assertEqual(self.other_client().get(endpoint).status_code, 404)
        self.assertEqual(list(Path(row['output_root']).rglob('*.zip')), [])

    def test_zip_normalizes_historical_layout_without_moving_existing_files(self):
        result, row, data = self.zip_run()
        for artifact in data['files']:
            if artifact['kind'] in {'stats','clone_top'}:
                continue
            path = Path(row['output_root']) / artifact['relative_path']
            suffix = artifact['chain'] if artifact['kind']=='chain' else 'ALL_CHAINS'
            legacy = Path(row['output_root']) / 'samples' / artifact['sample_key'] / f'Same_{suffix}.png'
            legacy.parent.mkdir(parents=True, exist_ok=True)
            path.replace(legacy)
            artifact.update(relative_path=legacy.relative_to(row['output_root']).as_posix(), version=postanalysis.version(legacy))
        with web.db() as connection:
            connection.execute('UPDATE derived_runs SET storage_version=1,data_json=? WHERE id=?', (json.dumps(data), row['id']))
            derived_store.delete(connection,[row['id']])
        response = self.client.get(f"/api/treemap/runs/{row['id']}/download")
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertFalse(any(name.startswith('samples/') for name in archive.namelist()))
            self.assertEqual(len(archive.namelist()), 6)
            self.assertEqual({Path(name).name for name in archive.namelist() if name.endswith('.png')},
                             {'Same__IGH.png', 'Same__IGK.png', 'Same__TRB.png', 'Same.png'})
        for artifact in result['files']:
            self.assertEqual(self.client.get(artifact['url']).status_code, 200)

    def test_zip_rejects_changed_or_missing_artifacts_without_partial_archive(self):
        _, row, data = self.zip_run()
        image = next(item for item in data['files'] if item['kind'] == 'chain')
        path = Path(row['output_root']) / image['relative_path']
        path.write_bytes(b'changed')
        endpoint = f"/api/treemap/runs/{row['id']}/download"
        self.assertEqual(self.client.get(endpoint).status_code, 409)
        path.unlink()
        self.assertEqual(self.client.get(endpoint).status_code, 409)

    def test_zip_rejects_stale_archived_and_empty_runs(self):
        _, row, _ = self.zip_run(parent=True)
        endpoint = f"/api/treemap/runs/{row['id']}/download"
        web.update_job(row['parent_job_id'], status='ARCHIVED')
        self.assertEqual(self.client.get(endpoint).status_code, 409)
        web.update_job(row['parent_job_id'], status='SUCCEEDED', attempt_no=row['attempt_no'] + 1)
        self.assertEqual(self.client.get(endpoint).status_code, 409)
        self.airr(empty=True)
        empty = self.deliver(self.create_run()['id'])
        self.assertEqual(self.client.get(f"/api/treemap/runs/{empty['id']}/download").status_code, 404)

    def test_worker_budget_respects_cpu_quota_memory_and_explicit_serial_mode(self):
        gib = 1024**3
        resources = {'cpu_count': 8, 'memory_available_bytes': 8*gib, 'memory_limit_bytes': 8*gib}
        with patch('mapping.pipeline.resources.detect_resources', return_value=resources), patch.dict(os.environ, {
            'SCIGBLAST_TREEMAP_MAX_WORKERS': '4', 'SCIGBLAST_TREEMAP_MAX_RSS_MB': '1024',
            'SCIGBLAST_TREEMAP_MEMORY_BUDGET_MB': '3072',
        }):
            self.assertEqual(postanalysis.worker_budget()['workers'], 3)
            resources['cpu_count'] = 1.5
            self.assertEqual(postanalysis.worker_budget()['workers'], 1)
            resources.update(cpu_count=8, memory_available_bytes=2*gib)
            self.assertEqual(postanalysis.worker_budget()['workers'], 1)
            resources['memory_available_bytes'] = 128*1024**2
            with self.assertRaisesRegex(ValueError, 'Treemap'):
                postanalysis.worker_budget()
            resources['memory_available_bytes'] = 8*gib
            with patch.dict(os.environ, {'SCIGBLAST_TREEMAP_MAX_WORKERS': '1'}):
                self.assertEqual(postanalysis.worker_budget()['workers'], 1)

    def test_unverified_supervisor_is_preserved_and_blocks_further_dispatch(self):
        self.airr(chain='IGH')
        self.airr(chain='TRB')
        run = self.create_run()
        def uncertain(row, request):
            with web.db() as connection:
                connection.execute('UPDATE derived_runs SET pid=12345,process_token=? WHERE id=?', ('unknown-supervisor',row['id']))
            raise RuntimeError('unverified process group')
        with patch.object(self.service,'work_unit',side_effect=uncertain), patch.object(postanalysis,'group_state',return_value='unverified'):
            self.assertTrue(self.service.deliver_one())
            self.assertFalse(self.service.deliver_one())
        self.assertEqual(self.run_info(run['id'])['status'],'interrupted')
        with web.db() as connection:
            self.assertEqual(connection.execute('SELECT pid FROM derived_runs WHERE id=?',(run['id'],)).fetchone()['pid'],12345)

    def test_parallel_workers_isolate_failure_then_retry_without_changing_successful_image(self):
        self.airr(chain='IGH')
        self.airr(chain='TRB')
        run = self.create_run()
        actual = self.service.work_unit
        batch_results = []
        def fail_one(row, request):
            if request['kind'] == 'batch':
                self.assertEqual(len(request['units']), 2)
                # Make only one worker fail after source snapshot validation.
                request['units'][0] = dict(request['units'][0], sources=[{'path': str(self.raw / 'missing.tsv')}])
                result = actual(row, request)
                batch_results.extend(result['results'])
                return result
            return actual(row, request)
        resources = {'cpu_count': 4, 'memory_available_bytes': 8*1024**3, 'memory_limit_bytes': 8*1024**3}
        with patch('mapping.pipeline.resources.detect_resources', return_value=resources), patch.dict(os.environ, {
            'SCIGBLAST_TREEMAP_MAX_WORKERS': '2',
        }), patch.object(self.service, 'work_unit', side_effect=fail_one):
            partial = self.deliver(run['id'])
        self.assertEqual(partial['status'], 'partial', partial)
        self.assertEqual(len({entry['pid'] for entry in batch_results}), 2)
        self.assertEqual(sum('error' in entry for entry in batch_results), 1)
        good = next(file for file in partial['files'] if file['kind'] == 'chain')
        before = self.client.get(good['url']).content
        self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry", json={}).status_code, 200)
        complete = self.deliver(run['id'])
        self.assertEqual(complete['status'], 'succeeded', complete)
        self.assertEqual(self.client.get(good['url']).content, before)
        self.assertEqual(len([f for f in complete['files'] if f['kind'] == 'chain']), 2)

    @unittest.skipUnless(os.name == 'posix', 'Linux process groups and shared flock')
    def test_cancel_parallel_group_reaps_workers_and_releases_shared_lock(self):
        import fcntl
        self.airr(chain='IGH')
        self.airr(chain='TRB')
        run = self.create_run()
        marker = self.root / 'parallel-pids.json'
        script = self.root / 'slow-supervisor.py'
        script.write_text(
            'import json,os,subprocess,sys,time\n'
            'from pathlib import Path\n'
            "fd=int(os.environ['SCIGBLAST_TREEMAP_LOCK_FD'])\n"
            "children=[subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],pass_fds=(fd,)) for _ in range(2)]\n"
            'Path(sys.argv[1]).write_text(json.dumps([os.getpid()]+[p.pid for p in children]))\n'
            'time.sleep(60)\n', encoding='utf-8')
        original = subprocess.Popen
        def launch(args, **kwargs):
            return original([args[0], str(script), str(marker)], **kwargs)
        resources = {'cpu_count': 4, 'memory_available_bytes': 8*1024**3, 'memory_limit_bytes': 8*1024**3}
        with patch('mapping.pipeline.resources.detect_resources', return_value=resources), patch.dict(os.environ, {
            'SCIGBLAST_TREEMAP_MAX_WORKERS': '2',
        }), patch.object(postanalysis.subprocess, 'Popen', side_effect=launch), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.service.deliver_one)
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.is_file())
            pids = json.loads(marker.read_text())
            self.assertEqual(len(pids), 3)
            with Path(os.environ['SCIGBLAST_RESOURCE_LOCK_FILE']).open('a') as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            response = self.client.post(f"/api/treemap/runs/{run['id']}/cancel", json={})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(future.result(timeout=15))
        self.assertEqual(self.run_info(run['id'])['status'], 'cancelled')
        self.assertEqual(postanalysis.group_state(pids[0], None), 'gone')
        for pid in pids:
            try:
                status = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
                self.assertEqual(status, 'Z')
            except FileNotFoundError:
                pass
        with Path(os.environ['SCIGBLAST_RESOURCE_LOCK_FILE']).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_scan_paging_does_not_change_selection_and_invalid_ids_rejected(self):
        self.airr(sample='first', chain='IGH')
        self.airr(sample='second', chain='TRB')
        scan = self.scan()
        filtered = self.client.get(f"/api/treemap/scans/{scan['id']}", params={'query': 'second', 'limit': 1}).json()
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['rows'][0]['chain'], 'TRB')
        run = self.create_run(scan)
        self.assertEqual(run['progress']['total'], 2)
        bad = self.client.post('/api/treemap/runs', json={'scan_id': scan['id'], 'selected_ids': ['forged-id']})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.post('/api/treemap/runs', json={'scan_id': scan['id'], 'selected_ids': []}).status_code, 422)

    def test_scan_and_run_are_owner_bound_and_csrf_protected(self):
        self.airr()
        scan = self.scan()
        run = self.create_run(scan)
        other = self.other_client()
        self.assertEqual(other.get(f"/api/treemap/scans/{scan['id']}").status_code, 400)
        self.assertEqual(other.post('/api/treemap/runs', json={'scan_id': scan['id'], 'selected_ids': [scan['rows'][0]['id']]}).status_code, 400)
        for suffix in ['', '/cancel', '/retry']:
            response = other.post(f"/api/treemap/runs/{run['id']}{suffix}", json={}) if suffix else other.get(f"/api/treemap/runs/{run['id']}")
            self.assertEqual(response.status_code, 404)
        self.assertEqual(other.get('/api/treemap/runs').json()['runs'], [])
        anonymous = TestClient(web.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get('/api/treemap/runs').status_code, 401)
        self.assertEqual(self.client.post('/api/treemap/scan', json={'input_path': str(self.raw)}, headers={'X-SCIGBLAST-Request': ''}).status_code, 403)

    def test_concurrent_duplicate_create_returns_same_run(self):
        self.airr()
        scan = self.scan()
        body = {'scan_id': scan['id'], 'selected_ids': [r['id'] for r in scan['rows']]}
        def submit():
            client = TestClient(web.app, headers=HEADERS)
            client.cookies.set(web.auth.COOKIE, 'fixture-session')
            try:
                return client.post('/api/treemap/runs', json=body)
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual([r.status_code for r in responses], [200, 200])
        self.assertEqual(responses[0].json()['id'], responses[1].json()['id'])
        with web.db() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM derived_runs').fetchone()[0], 1)

    def test_changed_after_scan_is_rejected_and_changed_after_queue_fails_cleanly(self):
        source = self.airr()
        scan = self.scan()
        source.write_text(source.read_text() + '\n', encoding='utf-8')
        response = self.client.post('/api/treemap/runs', json={'scan_id': scan['id'], 'selected_ids': [r['id'] for r in scan['rows']]})
        self.assertEqual(response.status_code, 409)
        run = self.create_run()
        source.write_text(source.read_text() + '\n', encoding='utf-8')
        with patch.object(self.service, 'work_unit') as worker:
            result = self.deliver(run['id'])
        worker.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('变化', result['error'])

    def test_cancel_before_work_never_starts_process(self):
        self.airr()
        run = self.create_run()
        response = self.client.post(f"/api/treemap/runs/{run['id']}/cancel", json={})
        self.assertEqual(response.json()['status'], 'cancelled')
        with patch.object(self.service, 'work_unit') as worker:
            self.assertFalse(self.service.deliver_one())
        worker.assert_not_called()
        self.assertEqual(self.run_info(run['id'])['files'], [])

    def test_pending_primary_has_priority_over_derived_work(self):
        self.airr()
        run = self.create_run()
        job_id = self.job()
        with patch.object(self.service, 'work_unit') as worker:
            self.assertFalse(self.service.deliver_one())
        worker.assert_not_called()
        self.assertEqual(self.run_info(run['id'])['status'], 'queued')
        web.update_job(job_id, status='FAILED')
        self.assertEqual(self.deliver(run['id'])['status'], 'succeeded')

    def test_auto_only_enabled_success_dedupes_and_uses_owner_without_render_or_mail(self):
        job_id = self.job(enabled=True)
        self.other_client()
        web.update_job(job_id, owner_id='other')
        disabled_id = self.job(enabled=False)
        with patch.object(self.service, 'discover') as discover, patch.object(postanalysis.subprocess, 'Popen') as launch, patch.object(smtplib, 'SMTP') as smtp, patch.object(smtplib, 'SMTP_SSL') as ssl:
            for status in ('WAITING_REVIEW', 'FAILED', 'STOPPED', 'COMPLETED_WITHOUT_MARKER'):
                web.update_job(job_id, status=status)
                with web.db() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM derived_runs').fetchone()[0], 0)
            web.update_job(disabled_id, status='SUCCEEDED')
            web.update_job(job_id, status='SUCCEEDED')
            web.update_job(job_id, status='SUCCEEDED', progress=100)
            discover.assert_not_called(); launch.assert_not_called(); smtp.assert_not_called(); ssl.assert_not_called()
        with web.db() as connection:
            rows = connection.execute('SELECT * FROM derived_runs').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['owner_id'], rows[0]['parent_job_id'], rows[0]['status']), ('other', job_id, 'queued'))
        self.assertTrue(json.loads(rows[0]['data_json'])['pending_discovery'])
        self.assertEqual(web.get_job_row(job_id)['status'], 'SUCCEEDED')

    def test_parent_attempt_change_and_archive_hide_files_and_prevent_work(self):
        for parent_status, expected in [('ARCHIVED', 'archived'), ('SUCCEEDED', 'stale')]:
            with self.subTest(parent_status=parent_status):
                job_id = self.job()
                self.job_results(job_id)
                web.update_job(job_id, status='SUCCEEDED', attempt_no=1)
                run = self.create_run(self.scan(job_id=job_id))
                web.update_job(job_id, status=parent_status, attempt_no=2 if expected == 'stale' else 1)
                with patch.object(self.service, 'work_unit') as worker:
                    self.assertTrue(self.service.deliver_one())
                worker.assert_not_called()
                result = self.run_info(run['id'])
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['files'], [])
                self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry", json={}).status_code, 409)

    def test_one_chain_failure_isolated_and_retry_rebuilds_composite(self):
        self.airr(chain='IGH')
        self.airr(chain='TRB')
        run = self.create_run()
        actual = self.service.work_unit
        composites = []
        def fail_one(row, request):
            if request['kind'] == 'chain' and request['chain'] == 'IGH':
                raise ValueError('synthetic chain failure')
            if request['kind'] == 'combined':
                composites.append(set(request['images']))
            return actual(row, request)
        with patch.object(self.service, 'work_unit', side_effect=fail_one):
            partial = self.deliver(run['id'])
        self.assertEqual(partial['status'], 'partial', partial)
        self.assertEqual(composites, [])
        partial_table=next(file for file in partial['files'] if file['kind']=='clone_top')
        partial_rows=list(csv.DictReader(io.StringIO(self.client.get(partial_table['url']).content.decode('utf-8-sig'))))
        self.assertEqual({row['chain'] for row in partial_rows},{'TRB'})
        self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry", json={}).status_code, 200)
        self.assertEqual(self.client.get(partial_table['url']).status_code,404)
        def capture(row, request):
            if request['kind'] == 'combined':
                composites.append(set(request['images']))
            return actual(row, request)
        with patch.object(self.service, 'work_unit', side_effect=capture):
            complete = self.deliver(run['id'])
        self.assertEqual(complete['status'], 'succeeded', complete)
        self.assertEqual(composites[-1], {'IGH', 'TRB'})
        self.assertEqual(len([f for f in complete['files'] if f['kind'] == 'combined']), 1)
        table=next(file for file in complete['files'] if file['kind']=='clone_top')
        rows=list(csv.DictReader(io.StringIO(self.client.get(table['url']).content.decode('utf-8-sig'))))
        self.assertEqual({row['chain'] for row in rows},{'IGH','TRB'})
        self.assertEqual(len(rows),4)
        self.assertEqual(sum(int(row['record_count']) for row in rows),6)

    def test_retry_repairs_missing_clone_table_without_rerunning_other_chains(self):
        self.airr(chain='IGH');self.airr(chain='TRB')
        run=self.create_run()
        actual=self.service.work_unit
        def fail_merge(row,request):
            if request['kind']=='clone_merge':
                raise ValueError('synthetic table publication failure')
            return actual(row,request)
        with patch.object(self.service,'work_unit',side_effect=fail_merge):
            failed=self.deliver(run['id'])
        self.assertEqual(failed['status'],'failed')
        with web.db() as connection:
            row=connection.execute('SELECT * FROM derived_runs WHERE id=?',(run['id'],)).fetchone()
        data=self.service.data(row)
        lost=data['items'][0]
        (Path(row['output_root'])/lost['clone_table']['relative_path']).unlink()
        kept=next(file for file in failed['files'] if file['kind']=='chain' and file['chain']!=lost['chain'])
        before=self.client.get(kept['url']).content
        self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry",json={}).status_code,200)
        calls=[]
        def capture(row,request):
            if request['kind']=='chain':calls.append(request['chain'])
            return actual(row,request)
        with patch.object(self.service,'work_unit',side_effect=capture):
            complete=self.deliver(run['id'])
        self.assertEqual(complete['status'],'succeeded',complete)
        self.assertEqual(calls,[lost['chain']])
        self.assertEqual(self.client.get(kept['url']).content,before)
        table=next(file for file in complete['files'] if file['kind']=='clone_top')
        self.assertEqual(len(list(csv.DictReader(io.StringIO(self.client.get(table['url']).content.decode('utf-8-sig'))))),4)

    def test_new_table_format_does_not_reuse_legacy_manual_run_or_auto_regenerate_old_run(self):
        self.airr()
        scan=self.scan();legacy=self.create_run(scan)
        with web.db() as connection:
            snapshot=json.loads(connection.execute('SELECT data_json FROM treemap_scans WHERE id=?',(scan['id'],)).fetchone()['data_json'])
            data=derived_store.load(connection,connection.execute('SELECT * FROM derived_runs WHERE id=?',(legacy['id'],)).fetchone())
            data.pop('clone_table_version')
            key=postanalysis.digest(dict(version=postanalysis.VERSION,parent=snapshot['parent_job_id'],attempt=snapshot['attempt_no'],
                                        input_root=snapshot['input_root'],items=snapshot['items']))
            connection.execute('UPDATE derived_runs SET storage_version=1,dedupe_key=?,data_json=? WHERE id=?',(key,json.dumps(data),legacy['id']))
            derived_store.delete(connection,[legacy['id']])
        self.assertNotEqual(self.create_run(scan)['id'],legacy['id'])
        job_id=self.job(enabled=True)
        web.update_job(job_id,status='SUCCEEDED')
        with web.db() as connection:
            old=connection.execute('SELECT * FROM derived_runs WHERE parent_job_id=?',(job_id,)).fetchone()
            old_data=derived_store.load(connection,old);old_data.pop('clone_table_version')
            connection.execute("UPDATE derived_runs SET status='succeeded',storage_version=1,data_json=? WHERE id=?",(json.dumps(old_data),old['id']))
            derived_store.delete(connection,[old['id']])
            postanalysis.enqueue_auto(connection,job_id,web.now())
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM derived_runs WHERE parent_job_id=?',(job_id,)).fetchone()[0],1)

    def test_empty_data_is_successful_no_data_result_without_blank_png(self):
        self.airr(empty=True)
        run = self.create_run()
        result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'empty', result)
        self.assertEqual((result['items'][0]['productive_count'], result['items'][0]['clone_count']), (0, 0))
        self.assertCountEqual([f['kind'] for f in result['files']], ['clone_top','stats'])
        table = next(f for f in result['files'] if f['kind']=='clone_top')
        rows = list(csv.DictReader(io.StringIO(self.client.get(table['url']).content.decode('utf-8-sig'))))
        self.assertEqual(rows,[])

    def test_v1_queued_run_completes_and_retries_without_automatic_storage_migration(self):
        self.airr(empty=True)
        run=self.create_run()
        with web.db() as connection:
            row=connection.execute('SELECT * FROM derived_runs WHERE id=?',(run['id'],)).fetchone()
            data=derived_store.load(connection,row)
            connection.execute('UPDATE derived_runs SET storage_version=1,data_json=? WHERE id=?',(json.dumps(data),run['id']))
            derived_store.delete(connection,[run['id']])
        result=self.deliver(run['id'])
        self.assertEqual(result['status'],'empty')
        self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry",json={}).status_code,200)
        self.assertEqual(self.deliver(run['id'])['status'],'empty')
        with web.db() as connection:
            row=connection.execute('SELECT * FROM derived_runs WHERE id=?',(run['id'],)).fetchone()
            self.assertEqual(row['storage_version'],1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM derived_items WHERE run_id=?',(run['id'],)).fetchone()[0],0)
        self.assertCountEqual([file['kind'] for file in result['files']],['clone_top','stats'])

    def test_composition_rejects_changed_image_and_retry_repairs_it(self):
        self.airr()
        run=self.create_run()
        self.assertTrue(self.service.deliver_one())
        with web.db() as connection:
            row=connection.execute('SELECT * FROM derived_runs WHERE id=?',(run['id'],)).fetchone()
            data=derived_store.load(connection,row)
        artifact=data['files'][0]
        (Path(row['output_root'])/artifact['relative_path']).unlink()
        self.assertTrue(self.service.deliver_one())
        self.assertEqual(self.run_info(run['id'])['status'],'failed')
        self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry",json={}).status_code,200)
        self.assertEqual(self.deliver(run['id'])['status'],'succeeded')
        self.assertEqual(self.client.get(f"/api/treemap/runs/{run['id']}/files/{artifact['id']}").status_code,404)

    def test_automatic_discovery_failure_does_not_change_primary_success(self):
        job_id = self.job(enabled=True)
        web.update_job(job_id, status='SUCCEEDED', progress=100, exit_code=0)
        run = self.client.get(f'/api/jobs/{job_id}/treemap').json()['runs'][0]
        before = dict(web.get_job_row(job_id))
        result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'failed')
        after = dict(web.get_job_row(job_id))
        for field in ('status', 'progress', 'attempt_no', 'exit_code', 'env_json', 'options_json'):
            self.assertEqual(after[field], before[field], field)

    def test_directory_lookup_cannot_bypass_job_ownership_or_archive(self):
        job_id = self.job()
        source = self.job_results(job_id)
        web.update_job(job_id, status='SUCCEEDED')
        other = self.other_client()
        self.assertEqual(other.get('/api/treemap/tasks').json()['tasks'], [])
        for body in ({'job_id': job_id}, {'input_path': str(source.parent)}):
            self.assertEqual(other.post('/api/treemap/scan', json=body).status_code, 404)
        scan = self.scan(input_path=str(source.parent))
        web.update_job(job_id, status='ARCHIVING')
        self.assertEqual(self.client.post('/api/treemap/runs', json={
            'scan_id': scan['id'], 'selected_ids': [r['id'] for r in scan['rows'] if r['valid']]}).status_code, 409)
        self.assertEqual(self.client.post('/api/treemap/scan', json={'input_path': str(source.parent)}).status_code, 409)

    def test_archive_waits_for_derived_run_and_claims_after_cancel(self):
        job_id = self.job()
        self.job_results(job_id)
        web.update_job(job_id, status='SUCCEEDED', ended_at='2020-01-01T00:00:00+00:00')
        run = self.create_run(self.scan(job_id=job_id))
        with patch.object(archive_results, 'package', return_value=123) as package:
            self.assertEqual(archive_results.run(web, days=0), 0)
            package.assert_not_called()
            self.assertEqual(web.get_job_row(job_id)['status'], 'SUCCEEDED')
            self.client.post(f"/api/treemap/runs/{run['id']}/cancel", json={})
            self.assertEqual(archive_results.run(web, days=0), 0)
            package.assert_called_once()
        self.assertEqual(web.get_job_row(job_id)['status'], 'ARCHIVED')

    def test_mutation_guard_cancels_between_chain_and_composite(self):
        job_id = self.job()
        self.job_results(job_id)
        web.update_job(job_id, status='SUCCEEDED')
        run = self.create_run(self.scan(job_id=job_id))
        self.assertTrue(self.service.deliver_one())
        checkpoint = self.run_info(run['id'])
        self.assertEqual((checkpoint['status'], checkpoint['progress']['done']), ('running', 1))
        with web.PROCESS_LOCK:
            self.service.guard_mutation(job_id)
        self.assertEqual(self.run_info(run['id'])['status'], 'cancelled')
        with patch.object(self.service, 'work_unit') as worker:
            self.assertFalse(self.service.deliver_one())
        worker.assert_not_called()

    def test_restart_child_reservation_clears_only_after_confirmed_exit(self):
        self.airr()
        run = self.create_run(self.scan())
        with web.db() as connection:
            connection.execute("UPDATE derived_runs SET status='interrupted',pid=12345,process_token='previous-instance' WHERE id=?",(run['id'],))
        with patch.object(postanalysis,'group_state',return_value='unverified'):
            self.service.reap_finished_children()
        with web.db() as connection:
            self.assertEqual(connection.execute('SELECT pid FROM derived_runs WHERE id=?',(run['id'],)).fetchone()['pid'],12345)
        with patch.object(postanalysis,'group_state',return_value='gone'):
            self.service.reap_finished_children()
        with web.db() as connection:
            row = connection.execute('SELECT * FROM derived_runs WHERE id=?',(run['id'],)).fetchone()
        self.assertIsNone(row['pid'])
        self.assertEqual(row['status'],'interrupted')

    def test_auto_preserves_missing_chain_diagnostic_in_partial_result(self):
        job_id = self.job(enabled=True)
        source = self.job_results(job_id)
        summary = source.parent.parent / 'chain_summary.csv'
        # One valid chain and one missing final file must not report full success.
        with summary.open('a',encoding='utf-8',newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['A','TRB','OK',str(source.parent/'missing_TRB.tsv')])
        web.update_job(job_id,status='SUCCEEDED')
        runs = self.client.get(f'/api/jobs/{job_id}/treemap').json()['runs']
        result = self.deliver(runs[0]['id'])
        self.assertEqual(result['status'],'partial',result)
        missing = next(item for item in result['items'] if item['chain']=='TRB')
        self.assertEqual(missing['status'],'failed')
        self.assertTrue(missing['error'])
        self.assertEqual(self.client.post(f"/api/treemap/runs/{result['id']}/retry",json={}).status_code,409)


if __name__ == '__main__':
    unittest.main()
