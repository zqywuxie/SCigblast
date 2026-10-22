"""Treemap API/lifecycle contracts; only tiny synthetic AIRR files are rendered."""
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import smtplib
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import test_workflow as workflow
import postanalysis
import archive_results

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

    def job(self, enabled=False):
        response = self.client.post('/api/jobs', json={
            'pipeline': 'igblast_base', 'operator': 'fixture', 'input_path': str(self.raw),
            'submission_revision': self.view['revision'], 'dataset_label': 'treemap-fixture',
            'generate_treemap': enabled,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

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
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, omitted)}
        scan = self.scan()
        selected = [r['id'] for r in scan['rows'] if r['chain'] == 'IGH']
        run = self.create_run(scan, selected)
        self.assertEqual((run['status'], run['metric'], run['progress']['total']), ('queued', 'records', 1))
        result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual((result['items'][0]['productive_count'], result['items'][0]['clone_count']), (3, 2))
        self.assertCountEqual([f['kind'] for f in result['files']], ['chain', 'combined', 'stats'])
        for artifact in result['files']:
            response = self.client.get(artifact['url'])
            self.assertEqual(response.status_code, 200, response.text[:200] if artifact['kind'] == 'stats' else '')
            if artifact['kind'] != 'stats':
                with Image.open(io.BytesIO(response.content)) as image:
                    self.assertEqual(image.size, (3000, 3000))
                    self.assertAlmostEqual(image.info['dpi'][0], 300, delta=1)
                    image.verify()
            else:
                rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                self.assertEqual((rows[0]['productive_count'], rows[0]['clone_count']), ('3', '2'))
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
        self.assertEqual(self.client.get(f"/api/treemap/runs/{run['id']}/files/not-an-artifact").status_code, 404)
        with patch.object(postanalysis, 'version', side_effect=AssertionError('history must not stat every artifact')):
            history = self.client.get('/api/treemap/runs')
        self.assertEqual(history.status_code, 200)
        summary = history.json()['runs'][0]
        self.assertEqual(summary['progress'], {'done': 1, 'total': 1})
        self.assertEqual((summary['items'], summary['files']), ([], []))

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
        self.assertEqual(composites, [{'TRB'}])
        self.assertEqual(self.client.post(f"/api/treemap/runs/{run['id']}/retry", json={}).status_code, 200)
        def capture(row, request):
            if request['kind'] == 'combined':
                composites.append(set(request['images']))
            return actual(row, request)
        with patch.object(self.service, 'work_unit', side_effect=capture):
            complete = self.deliver(run['id'])
        self.assertEqual(complete['status'], 'succeeded', complete)
        self.assertEqual(composites[-1], {'IGH', 'TRB'})
        self.assertEqual(len([f for f in complete['files'] if f['kind'] == 'combined']), 1)

    def test_empty_data_is_successful_no_data_result_without_blank_png(self):
        self.airr(empty=True)
        run = self.create_run()
        result = self.deliver(run['id'])
        self.assertEqual(result['status'], 'empty', result)
        self.assertEqual((result['items'][0]['productive_count'], result['items'][0]['clone_count']), (0, 0))
        self.assertEqual([f['kind'] for f in result['files']], ['stats'])

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
